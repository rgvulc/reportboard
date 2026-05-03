from app.db import get_db


def _all_boards(app):
    with app.app_context():
        return get_db().execute(
            "SELECT id, name, position FROM board ORDER BY position, id"
        ).fetchall()


def _all_importance(app):
    with app.app_context():
        return get_db().execute(
            "SELECT id, name, position FROM importance_level ORDER BY position, id"
        ).fetchall()


def _seed_report_with_importance(client, app, importance_name="High"):
    """Create one workspace + one report on Todo with the given importance.
    Returns (report_id, todo_id, importance_id)."""
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name='Todo'").fetchone()["id"]
        importance_id = db.execute(
            "SELECT id FROM importance_level WHERE name = ?", (importance_name,)
        ).fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "x", "board_id": todo_id},
    )
    with app.app_context():
        report_id = get_db().execute("SELECT id FROM report").fetchone()["id"]
    client.post(
        f"/reports/{report_id}",
        data={
            "title": "x",
            "board_id": todo_id,
            "importance_id": importance_id,
            "tags": "",
            "content": "",
        },
    )
    return report_id, todo_id, importance_id


# --- GET /settings ---

def test_get_settings_page_renders_boards_and_importance(client):
    body = client.get("/settings").get_data(as_text=True)
    assert body.count("Boards") >= 1
    assert "Importance" in body
    for name in ["Todo", "In Progress", "Done", "On Hold"]:
        assert name in body
    for name in ["Low", "Medium", "High"]:
        assert name in body


# --- Board operations ---

def test_add_board_appends_with_position_max_plus_one(client, app):
    initial = _all_boards(app)
    response = client.post("/settings/boards", data={"name": "Sprint Goals"})
    assert response.status_code in (200, 302)

    boards = _all_boards(app)
    assert len(boards) == len(initial) + 1
    new = next(b for b in boards if b["name"] == "Sprint Goals")
    assert new["position"] == len(initial)


def test_add_board_with_blank_name_returns_400(client, app):
    response = client.post("/settings/boards", data={"name": "  "})
    assert response.status_code == 400


def test_add_board_with_duplicate_name_returns_400(client, app):
    response = client.post("/settings/boards", data={"name": "Todo"})
    assert response.status_code == 400


def test_add_board_with_case_variant_duplicate_returns_400(client, app):
    response = client.post("/settings/boards", data={"name": "TODO"})
    assert response.status_code == 400


def test_rename_board_updates_name_and_preserves_report_fk(client, app):
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name='Todo'").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "stuck", "board_id": todo_id},
    )

    response = client.post(
        f"/settings/boards/{todo_id}/rename",
        data={"name": "Doing"},
    )
    assert response.status_code in (200, 302)

    with app.app_context():
        db = get_db()
        renamed = db.execute(
            "SELECT name FROM board WHERE id = ?", (todo_id,)
        ).fetchone()
        report = db.execute("SELECT board_id FROM report").fetchone()
    assert renamed["name"] == "Doing"
    assert report["board_id"] == todo_id


def test_rename_board_to_duplicate_returns_400(client, app):
    todo_id = next(b["id"] for b in _all_boards(app) if b["name"] == "Todo")
    response = client.post(
        f"/settings/boards/{todo_id}/rename",
        data={"name": "Done"},
    )
    assert response.status_code == 400


def test_rename_board_blank_name_returns_400(client, app):
    todo_id = next(b["id"] for b in _all_boards(app) if b["name"] == "Todo")
    response = client.post(
        f"/settings/boards/{todo_id}/rename",
        data={"name": "  "},
    )
    assert response.status_code == 400


def test_rename_unknown_board_returns_404(client):
    response = client.post(
        "/settings/boards/9999/rename", data={"name": "x"}
    )
    assert response.status_code == 404


def test_delete_empty_board_succeeds(client, app):
    to_organize_id = next(
        b["id"] for b in _all_boards(app) if b["name"] == "To Organize"
    )
    response = client.post(f"/settings/boards/{to_organize_id}/delete")
    assert response.status_code in (200, 302)
    assert all(b["id"] != to_organize_id for b in _all_boards(app))


def test_delete_board_with_reports_is_rejected_and_state_unchanged(client, app):
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name='Todo'").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "stuck", "board_id": todo_id},
    )

    response = client.post(f"/settings/boards/{todo_id}/delete")
    assert response.status_code == 400

    with app.app_context():
        db = get_db()
        assert db.execute(
            "SELECT 1 FROM board WHERE id = ?", (todo_id,)
        ).fetchone() is not None
        report = db.execute("SELECT board_id FROM report").fetchone()
        assert report["board_id"] == todo_id


def test_delete_unknown_board_returns_404(client):
    response = client.post("/settings/boards/9999/delete")
    assert response.status_code == 404


def test_reorder_boards_renumbers_contiguously(client, app):
    boards = _all_boards(app)
    new_order = list(reversed([b["id"] for b in boards]))

    response = client.post(
        "/settings/boards/reorder",
        data={"board_ids": [str(i) for i in new_order]},
    )
    assert response.status_code == 204

    after = _all_boards(app)
    assert [b["id"] for b in after] == new_order
    assert [b["position"] for b in after] == list(range(len(after)))


def test_reorder_boards_incomplete_set_returns_400(client, app):
    boards = _all_boards(app)
    response = client.post(
        "/settings/boards/reorder",
        data={"board_ids": [str(boards[0]["id"])]},
    )
    assert response.status_code == 400


def test_reorder_boards_with_unknown_id_returns_400(client, app):
    boards = _all_boards(app)
    ids = [str(b["id"]) for b in boards] + ["9999"]
    response = client.post(
        "/settings/boards/reorder",
        data={"board_ids": ids},
    )
    assert response.status_code == 400


def test_reorder_boards_with_duplicates_returns_400(client, app):
    boards = _all_boards(app)
    ids = [str(b["id"]) for b in boards]
    ids[0] = ids[1]  # introduce duplicate
    response = client.post(
        "/settings/boards/reorder",
        data={"board_ids": ids},
    )
    assert response.status_code == 400


# --- Importance level operations ---

def test_add_importance_level(client, app):
    response = client.post("/settings/importance", data={"name": "Critical"})
    assert response.status_code in (200, 302)
    assert any(l["name"] == "Critical" for l in _all_importance(app))


def test_add_importance_with_duplicate_returns_400(client):
    response = client.post("/settings/importance", data={"name": "High"})
    assert response.status_code == 400


def test_add_importance_with_blank_name_returns_400(client):
    response = client.post("/settings/importance", data={"name": "  "})
    assert response.status_code == 400


def test_rename_importance_updates_name_and_preserves_report_fk(client, app):
    report_id, _, high_id = _seed_report_with_importance(client, app, "High")

    response = client.post(
        f"/settings/importance/{high_id}/rename",
        data={"name": "Urgent"},
    )
    assert response.status_code in (200, 302)

    with app.app_context():
        db = get_db()
        renamed = db.execute(
            "SELECT name FROM importance_level WHERE id = ?", (high_id,)
        ).fetchone()
        report = db.execute(
            "SELECT importance_id FROM report WHERE id = ?", (report_id,)
        ).fetchone()
    assert renamed["name"] == "Urgent"
    assert report["importance_id"] == high_id


def test_rename_importance_to_duplicate_returns_400(client, app):
    high_id = next(
        l["id"] for l in _all_importance(app) if l["name"] == "High"
    )
    response = client.post(
        f"/settings/importance/{high_id}/rename",
        data={"name": "Low"},
    )
    assert response.status_code == 400


def test_rename_unknown_importance_returns_404(client):
    response = client.post(
        "/settings/importance/9999/rename", data={"name": "x"}
    )
    assert response.status_code == 404


def test_delete_unused_importance_succeeds(client, app):
    client.post("/settings/importance", data={"name": "Trivial"})
    with app.app_context():
        trivial_id = get_db().execute(
            "SELECT id FROM importance_level WHERE name = 'Trivial'"
        ).fetchone()["id"]

    response = client.post(f"/settings/importance/{trivial_id}/delete")
    assert response.status_code in (200, 302)

    with app.app_context():
        assert get_db().execute(
            "SELECT 1 FROM importance_level WHERE id = ?", (trivial_id,)
        ).fetchone() is None


def test_delete_importance_in_use_is_rejected_and_state_unchanged(client, app):
    report_id, _, high_id = _seed_report_with_importance(client, app, "High")

    response = client.post(f"/settings/importance/{high_id}/delete")
    assert response.status_code == 400

    with app.app_context():
        db = get_db()
        assert db.execute(
            "SELECT 1 FROM importance_level WHERE id = ?", (high_id,)
        ).fetchone() is not None
        report = db.execute(
            "SELECT importance_id FROM report WHERE id = ?", (report_id,)
        ).fetchone()
        assert report["importance_id"] == high_id


def test_delete_unknown_importance_returns_404(client):
    response = client.post("/settings/importance/9999/delete")
    assert response.status_code == 404


def test_reorder_importance_renumbers_contiguously(client, app):
    levels = _all_importance(app)
    new_order = list(reversed([l["id"] for l in levels]))

    response = client.post(
        "/settings/importance/reorder",
        data={"importance_ids": [str(i) for i in new_order]},
    )
    assert response.status_code == 204

    after = _all_importance(app)
    assert [l["id"] for l in after] == new_order
    assert [l["position"] for l in after] == list(range(len(after)))


def test_reorder_importance_with_bad_set_returns_400(client, app):
    levels = _all_importance(app)
    response = client.post(
        "/settings/importance/reorder",
        data={"importance_ids": [str(levels[0]["id"])]},
    )
    assert response.status_code == 400


def test_settings_page_disables_delete_for_in_use_items(client, app):
    _seed_report_with_importance(client, app, "High")

    body = client.get("/settings").get_data(as_text=True)
    # The High delete button is disabled because it's in use.
    assert 'disabled' in body
    # Counts are visible.
    assert "1 report" in body
