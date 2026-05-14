from app.db import get_db


BOARDS_IN_ORDER = [
    "Todo",
    "In Progress",
    "Complete",
]


def _create_workspace(client, name):
    client.post("/workspaces", data={"name": name})


def _workspace_id(app, name):
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM workspace WHERE name = ?", (name,)
        ).fetchone()["id"]


def _board_id(app, name):
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM board WHERE name = ?", (name,)
        ).fetchone()["id"]


def _create_report(client, workspace_id, board_id, title):
    return client.post(
        f"/workspaces/{workspace_id}/reports",
        data={"title": title, "board_id": board_id},
    )


def test_board_view_returns_200_with_all_boards_in_position_order(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")

    response = client.get(f"/workspaces/{ws_id}")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    positions = [body.index(name) for name in BOARDS_IN_ORDER]
    assert positions == sorted(positions), (
        f"Board headers out of order. Found at positions: "
        f"{list(zip(BOARDS_IN_ORDER, positions))}"
    )


def test_board_view_unknown_workspace_returns_404(client):
    response = client.get("/workspaces/999")
    assert response.status_code == 404


def test_create_report_assigns_position_max_plus_one(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")

    _create_report(client, ws_id, todo_id, "WidgetA")
    _create_report(client, ws_id, todo_id, "WidgetB")
    _create_report(client, ws_id, todo_id, "WidgetC")

    with app.app_context():
        rows = get_db().execute(
            "SELECT title, position FROM report "
            "WHERE workspace_id = ? AND board_id = ? "
            "ORDER BY position",
            (ws_id, todo_id),
        ).fetchall()
    assert [(r["title"], r["position"]) for r in rows] == [
        ("WidgetA", 0),
        ("WidgetB", 1),
        ("WidgetC", 2),
    ]


def test_new_report_defaults_to_medium_importance(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")

    _create_report(client, ws_id, todo_id, "Test")

    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT il.name FROM report r "
            "JOIN importance_level il ON il.id = r.importance_id "
            "WHERE r.workspace_id = ?", (ws_id,),
        ).fetchone()
    assert row is not None
    assert row["name"] == "Medium"


def test_new_report_falls_back_to_null_when_medium_absent(client, app):
    """If the user has renamed/removed Medium, new reports keep working —
    importance stays NULL rather than failing."""
    with app.app_context():
        db = get_db()
        with db:
            db.execute("DELETE FROM importance_level WHERE name = 'Medium'")

    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")

    response = _create_report(client, ws_id, todo_id, "Test")
    assert response.status_code in (200, 302)

    with app.app_context():
        row = get_db().execute(
            "SELECT importance_id FROM report WHERE workspace_id = ?",
            (ws_id,),
        ).fetchone()
    assert row["importance_id"] is None


def test_first_report_in_a_column_gets_position_zero(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")
    done_id = _board_id(app, "Complete")

    _create_report(client, ws_id, todo_id, "InTodo")
    _create_report(client, ws_id, done_id, "InDone")

    with app.app_context():
        rows = get_db().execute(
            "SELECT title, position FROM report ORDER BY title"
        ).fetchall()
    assert [(r["title"], r["position"]) for r in rows] == [
        ("InDone", 0),
        ("InTodo", 0),
    ]


def test_reports_render_in_their_own_column(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")
    done_id = _board_id(app, "Complete")

    _create_report(client, ws_id, todo_id, "WidgetA")
    _create_report(client, ws_id, done_id, "WidgetZ")

    body = client.get(f"/workspaces/{ws_id}").get_data(as_text=True)

    todo_pos = body.index(">Todo<")
    next_col_pos = body.index(">In Progress<")
    widget_a_pos = body.index("WidgetA")
    assert todo_pos < widget_a_pos < next_col_pos

    done_pos = body.index(">Complete<")
    widget_z_pos = body.index("WidgetZ")
    assert done_pos < widget_z_pos


def test_reports_within_column_render_in_position_order(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")

    _create_report(client, ws_id, todo_id, "WidgetA")
    _create_report(client, ws_id, todo_id, "WidgetB")
    _create_report(client, ws_id, todo_id, "WidgetC")

    body = client.get(f"/workspaces/{ws_id}").get_data(as_text=True)
    assert body.index("WidgetA") < body.index("WidgetB") < body.index("WidgetC")


def test_reports_from_other_workspaces_do_not_leak(client, app):
    _create_workspace(client, "WS1")
    _create_workspace(client, "WS2")
    ws1_id = _workspace_id(app, "WS1")
    ws2_id = _workspace_id(app, "WS2")
    todo_id = _board_id(app, "Todo")

    _create_report(client, ws1_id, todo_id, "OnlyInWS1")
    _create_report(client, ws2_id, todo_id, "OnlyInWS2")

    body1 = client.get(f"/workspaces/{ws1_id}").get_data(as_text=True)
    body2 = client.get(f"/workspaces/{ws2_id}").get_data(as_text=True)

    assert "OnlyInWS1" in body1 and "OnlyInWS2" not in body1
    assert "OnlyInWS2" in body2 and "OnlyInWS1" not in body2


def test_create_report_with_blank_title_returns_400(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")
    todo_id = _board_id(app, "Todo")

    response = _create_report(client, ws_id, todo_id, "   ")
    assert response.status_code == 400

    with app.app_context():
        count = get_db().execute("SELECT COUNT(*) AS c FROM report").fetchone()["c"]
    assert count == 0


def test_create_report_with_unknown_board_id_returns_400(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")

    response = _create_report(client, ws_id, 99999, "Widget")
    assert response.status_code == 400


def test_create_report_with_non_integer_board_id_returns_400(client, app):
    _create_workspace(client, "WS")
    ws_id = _workspace_id(app, "WS")

    response = client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "Widget", "board_id": "not-an-int"},
    )
    assert response.status_code == 400


def test_create_report_in_unknown_workspace_returns_404(client, app):
    todo_id = _board_id(app, "Todo")
    response = client.post(
        "/workspaces/999/reports",
        data={"title": "Widget", "board_id": todo_id},
    )
    assert response.status_code == 404
