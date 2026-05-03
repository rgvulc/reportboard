from app.db import get_db


def _setup_workspace(client, app, name="WS"):
    client.post("/workspaces", data={"name": name})
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM workspace WHERE name = ?", (name,)
        ).fetchone()["id"]


def _board_id(app, name):
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM board WHERE name = ?", (name,)
        ).fetchone()["id"]


def _column(app, ws_id, board_name):
    with app.app_context():
        return get_db().execute(
            "SELECT r.id, r.title, r.position FROM report r "
            "JOIN board b ON b.id = r.board_id "
            "WHERE r.workspace_id = ? AND b.name = ? "
            "ORDER BY r.position",
            (ws_id, board_name),
        ).fetchall()


def _create_reports(client, ws_id, board_id, titles):
    for t in titles:
        client.post(
            f"/workspaces/{ws_id}/reports",
            data={"title": t, "board_id": board_id},
        )


# --- report move within same column ---

def test_move_within_column_to_specific_index_renumbers_contiguously(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A", "B", "C", "D"])

    initial = _column(app, ws_id, "Todo")
    a_id = next(r["id"] for r in initial if r["title"] == "A")

    response = client.post(
        f"/reports/{a_id}/move",
        data={"board_id": todo_id, "position": 2},
    )
    assert response.status_code == 204

    after = _column(app, ws_id, "Todo")
    assert [(r["title"], r["position"]) for r in after] == [
        ("B", 0), ("C", 1), ("A", 2), ("D", 3),
    ]


def test_move_to_top_of_same_column(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A", "B", "C"])
    c_id = next(r["id"] for r in _column(app, ws_id, "Todo") if r["title"] == "C")

    client.post(f"/reports/{c_id}/move", data={"board_id": todo_id, "position": 0})

    assert [(r["title"], r["position"]) for r in _column(app, ws_id, "Todo")] == [
        ("C", 0), ("A", 1), ("B", 2),
    ]


def test_move_to_end_of_same_column_clamps_oversized_position(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A", "B", "C"])
    a_id = next(r["id"] for r in _column(app, ws_id, "Todo") if r["title"] == "A")

    # Position 99 in a 3-item column should clamp to the end.
    client.post(f"/reports/{a_id}/move", data={"board_id": todo_id, "position": 99})

    assert [(r["title"], r["position"]) for r in _column(app, ws_id, "Todo")] == [
        ("B", 0), ("C", 1), ("A", 2),
    ]


# --- report move across columns ---

def test_move_across_columns_renumbers_both_and_preserves_total(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    done_id = _board_id(app, "Done")
    _create_reports(client, ws_id, todo_id, ["A", "B", "C"])
    _create_reports(client, ws_id, done_id, ["D", "E"])

    b_id = next(r["id"] for r in _column(app, ws_id, "Todo") if r["title"] == "B")

    client.post(f"/reports/{b_id}/move", data={"board_id": done_id, "position": 1})

    assert [(r["title"], r["position"]) for r in _column(app, ws_id, "Todo")] == [
        ("A", 0), ("C", 1),
    ]
    assert [(r["title"], r["position"]) for r in _column(app, ws_id, "Done")] == [
        ("D", 0), ("B", 1), ("E", 2),
    ]

    with app.app_context():
        total = get_db().execute(
            "SELECT COUNT(*) AS c FROM report WHERE workspace_id = ?", (ws_id,)
        ).fetchone()["c"]
    assert total == 5


def test_move_across_columns_to_top(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    done_id = _board_id(app, "Done")
    _create_reports(client, ws_id, todo_id, ["A"])
    _create_reports(client, ws_id, done_id, ["X", "Y"])

    a_id = next(r["id"] for r in _column(app, ws_id, "Todo") if r["title"] == "A")
    client.post(f"/reports/{a_id}/move", data={"board_id": done_id, "position": 0})

    assert _column(app, ws_id, "Todo") == []
    assert [(r["title"], r["position"]) for r in _column(app, ws_id, "Done")] == [
        ("A", 0), ("X", 1), ("Y", 2),
    ]


def test_move_replayed_is_idempotent(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    done_id = _board_id(app, "Done")
    _create_reports(client, ws_id, todo_id, ["A", "B"])
    _create_reports(client, ws_id, done_id, ["X"])
    b_id = next(r["id"] for r in _column(app, ws_id, "Todo") if r["title"] == "B")

    move_args = {"board_id": done_id, "position": 0}
    client.post(f"/reports/{b_id}/move", data=move_args)
    snapshot_a = (_column(app, ws_id, "Todo"), _column(app, ws_id, "Done"))

    client.post(f"/reports/{b_id}/move", data=move_args)
    snapshot_b = (_column(app, ws_id, "Todo"), _column(app, ws_id, "Done"))

    assert [
        [(r["title"], r["position"]) for r in col] for col in snapshot_a
    ] == [
        [(r["title"], r["position"]) for r in col] for col in snapshot_b
    ]


# --- error paths for report move ---

def test_move_unknown_report_returns_404(client, app):
    todo_id = _board_id(app, "Todo")
    response = client.post(
        "/reports/999/move",
        data={"board_id": todo_id, "position": 0},
    )
    assert response.status_code == 404


def test_move_with_unknown_board_id_returns_400(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A"])
    a_id = _column(app, ws_id, "Todo")[0]["id"]

    response = client.post(
        f"/reports/{a_id}/move",
        data={"board_id": 999999, "position": 0},
    )
    assert response.status_code == 400


def test_move_with_negative_position_returns_400(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A"])
    a_id = _column(app, ws_id, "Todo")[0]["id"]

    response = client.post(
        f"/reports/{a_id}/move",
        data={"board_id": todo_id, "position": -1},
    )
    assert response.status_code == 400


def test_move_with_non_integer_data_returns_400(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A"])
    a_id = _column(app, ws_id, "Todo")[0]["id"]

    response = client.post(
        f"/reports/{a_id}/move",
        data={"board_id": "abc", "position": "0"},
    )
    assert response.status_code == 400


# --- workspace reorder ---

def _all_workspaces_ordered(app):
    with app.app_context():
        return get_db().execute(
            "SELECT id, name, position FROM workspace ORDER BY position, id"
        ).fetchall()


def test_workspace_reorder_renumbers_contiguously(client, app):
    for n in ["Alpha", "Beta", "Gamma"]:
        client.post("/workspaces", data={"name": n})
    rows = _all_workspaces_ordered(app)
    by_name = {r["name"]: r["id"] for r in rows}

    new_order = [by_name["Gamma"], by_name["Alpha"], by_name["Beta"]]
    response = client.post(
        "/workspaces/reorder",
        data={"workspace_ids": [str(i) for i in new_order]},
    )
    assert response.status_code == 204

    after = _all_workspaces_ordered(app)
    assert [(r["name"], r["position"]) for r in after] == [
        ("Gamma", 0), ("Alpha", 1), ("Beta", 2),
    ]


def test_workspace_reorder_with_duplicate_ids_returns_400(client, app):
    for n in ["A", "B"]:
        client.post("/workspaces", data={"name": n})
    rows = _all_workspaces_ordered(app)
    a_id = rows[0]["id"]
    b_id = rows[1]["id"]

    response = client.post(
        "/workspaces/reorder",
        data={"workspace_ids": [str(a_id), str(a_id), str(b_id)]},
    )
    assert response.status_code == 400


def test_workspace_reorder_with_unknown_id_returns_400(client, app):
    client.post("/workspaces", data={"name": "A"})
    a_id = _all_workspaces_ordered(app)[0]["id"]

    response = client.post(
        "/workspaces/reorder",
        data={"workspace_ids": [str(a_id), "999"]},
    )
    assert response.status_code == 400


def test_workspace_reorder_with_incomplete_set_returns_400(client, app):
    for n in ["A", "B", "C"]:
        client.post("/workspaces", data={"name": n})
    rows = _all_workspaces_ordered(app)
    a_id = rows[0]["id"]
    b_id = rows[1]["id"]

    response = client.post(
        "/workspaces/reorder",
        data={"workspace_ids": [str(a_id), str(b_id)]},
    )
    assert response.status_code == 400


def test_workspace_reorder_with_non_integer_ids_returns_400(client, app):
    client.post("/workspaces", data={"name": "A"})
    response = client.post(
        "/workspaces/reorder",
        data={"workspace_ids": ["abc"]},
    )
    assert response.status_code == 400


# --- rendered HTML carries the data attributes SortableJS needs ---

def test_kanban_html_carries_required_data_attributes(client, app):
    ws_id = _setup_workspace(client, app)
    todo_id = _board_id(app, "Todo")
    _create_reports(client, ws_id, todo_id, ["A"])

    body = client.get(f"/workspaces/{ws_id}").get_data(as_text=True)
    assert 'class="board-cards"' in body
    assert f'data-board-id="{todo_id}"' in body
    assert 'data-report-id=' in body


def test_workspace_list_carries_drag_handle_and_data_id(client, app):
    client.post("/workspaces", data={"name": "WS"})
    body = client.get("/").get_data(as_text=True)
    assert 'workspace-drag-handle' in body
    assert 'data-id=' in body
