from app.db import get_db


def _setup(client, app):
    """Create a workspace and a single report. Returns (ws_id, todo_id, report_id)."""
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace WHERE name = 'WS'").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name = 'Todo'").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "Initial", "board_id": todo_id},
    )
    with app.app_context():
        report_id = get_db().execute("SELECT id FROM report").fetchone()["id"]
    return ws_id, todo_id, report_id


def _board_id(app, name):
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM board WHERE name = ?", (name,)
        ).fetchone()["id"]


def _importance_id(app, name):
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM importance_level WHERE name = ?", (name,)
        ).fetchone()["id"]


def _get_report(app, report_id):
    with app.app_context():
        return get_db().execute(
            "SELECT * FROM report WHERE id = ?", (report_id,)
        ).fetchone()


def _linked_tags(app, report_id):
    with app.app_context():
        rows = get_db().execute(
            "SELECT t.name FROM tag t JOIN report_tag rt ON rt.tag_id = t.id "
            "WHERE rt.report_id = ? ORDER BY t.name",
            (report_id,),
        ).fetchall()
    return [r["name"] for r in rows]


def test_get_report_renders_title_content_board_importance_tags(client, app):
    _, todo_id, report_id = _setup(client, app)
    client.post(
        f"/reports/{report_id}",
        data={
            "title": "MyTitle",
            "board_id": _board_id(app, "Complete"),
            "importance_id": _importance_id(app, "High"),
            "tags": "alpha, beta",
            "content": "Some **markdown** text",
        },
    )

    body = client.get(f"/reports/{report_id}").get_data(as_text=True)
    assert "MyTitle" in body
    assert "Some **markdown** text" in body
    assert "alpha" in body and "beta" in body
    # The selected board option should be marked selected
    done_id = _board_id(app, "Complete")
    assert f'value="{done_id}" selected' in body
    high_id = _importance_id(app, "High")
    assert f'value="{high_id}" selected' in body


def test_get_unknown_report_returns_404(client):
    response = client.get("/reports/999")
    assert response.status_code == 404


def test_save_updates_fields_and_bumps_updated_at_only(client, app):
    _, todo_id, report_id = _setup(client, app)
    before = _get_report(app, report_id)

    done_id = _board_id(app, "Complete")
    high_id = _importance_id(app, "High")

    response = client.post(
        f"/reports/{report_id}",
        data={
            "title": "NewTitle",
            "board_id": done_id,
            "importance_id": high_id,
            "tags": "",
            "content": "new content",
        },
    )
    assert response.status_code in (200, 302)

    after = _get_report(app, report_id)
    assert after["title"] == "NewTitle"
    assert after["board_id"] == done_id
    assert after["importance_id"] == high_id
    assert after["content"] == "new content"
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]


def test_tag_reconciliation_swaps_correctly_and_preserves_unlinked_tag_rows(client, app):
    _, todo_id, report_id = _setup(client, app)

    client.post(
        f"/reports/{report_id}",
        data={"title": "T", "board_id": todo_id, "tags": "alpha, beta", "content": ""},
    )
    assert _linked_tags(app, report_id) == ["alpha", "beta"]

    client.post(
        f"/reports/{report_id}",
        data={"title": "T", "board_id": todo_id, "tags": "beta, gamma", "content": ""},
    )
    assert _linked_tags(app, report_id) == ["beta", "gamma"]

    with app.app_context():
        all_tags = [
            r["name"]
            for r in get_db().execute("SELECT name FROM tag ORDER BY name")
        ]
    assert "alpha" in all_tags  # row preserved even though now unlinked


def test_tags_are_case_insensitive_and_collapse_to_one_row(client, app):
    _, todo_id, report_id = _setup(client, app)

    client.post(
        f"/reports/{report_id}",
        data={"title": "T", "board_id": todo_id, "tags": "Alpha, alpha, ALPHA", "content": ""},
    )

    with app.app_context():
        tag_count = get_db().execute(
            "SELECT COUNT(*) AS c FROM tag WHERE name = 'alpha' COLLATE NOCASE"
        ).fetchone()["c"]
        link_count = get_db().execute(
            "SELECT COUNT(*) AS c FROM report_tag WHERE report_id = ?",
            (report_id,),
        ).fetchone()["c"]

    assert tag_count == 1
    assert link_count == 1


def test_whitespace_only_tags_are_ignored(client, app):
    _, todo_id, report_id = _setup(client, app)

    client.post(
        f"/reports/{report_id}",
        data={
            "title": "T",
            "board_id": todo_id,
            "tags": "  , alpha,   , beta,  ",
            "content": "",
        },
    )

    assert _linked_tags(app, report_id) == ["alpha", "beta"]


def test_delete_cascades_to_report_tag_and_checklist_and_preserves_tag_rows(client, app):
    _, todo_id, report_id = _setup(client, app)

    client.post(
        f"/reports/{report_id}",
        data={"title": "T", "board_id": todo_id, "tags": "alpha", "content": ""},
    )

    # Insert a checklist row directly to verify cascade (Phase 5 adds the route).
    with app.app_context():
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO checklist_item (report_id, text, position) VALUES (?, ?, 0)",
                (report_id, "step 1"),
            )

    response = client.post(f"/reports/{report_id}/delete")
    assert response.status_code in (200, 302)

    with app.app_context():
        db = get_db()
        report_count = db.execute("SELECT COUNT(*) AS c FROM report").fetchone()["c"]
        rt_count = db.execute("SELECT COUNT(*) AS c FROM report_tag").fetchone()["c"]
        cl_count = db.execute("SELECT COUNT(*) AS c FROM checklist_item").fetchone()["c"]
        tag_count = db.execute(
            "SELECT COUNT(*) AS c FROM tag WHERE name = 'alpha'"
        ).fetchone()["c"]

    assert report_count == 0
    assert rt_count == 0
    assert cl_count == 0
    assert tag_count == 1


def test_save_with_blank_title_returns_400(client, app):
    _, todo_id, report_id = _setup(client, app)

    response = client.post(
        f"/reports/{report_id}",
        data={"title": "  ", "board_id": todo_id, "tags": "", "content": "x"},
    )
    assert response.status_code == 400


def test_save_unknown_report_returns_404(client, app):
    todo_id = _board_id(app, "Todo")
    response = client.post(
        "/reports/999",
        data={"title": "x", "board_id": todo_id, "tags": "", "content": ""},
    )
    assert response.status_code == 404


def test_save_with_unknown_board_id_returns_400(client, app):
    _, _, report_id = _setup(client, app)
    response = client.post(
        f"/reports/{report_id}",
        data={"title": "T", "board_id": 999999, "tags": "", "content": ""},
    )
    assert response.status_code == 400


def test_save_with_unknown_importance_id_returns_400(client, app):
    _, todo_id, report_id = _setup(client, app)
    response = client.post(
        f"/reports/{report_id}",
        data={
            "title": "T",
            "board_id": todo_id,
            "importance_id": 999999,
            "tags": "",
            "content": "",
        },
    )
    assert response.status_code == 400


def test_save_with_empty_importance_clears_it(client, app):
    _, todo_id, report_id = _setup(client, app)
    high_id = _importance_id(app, "High")

    client.post(
        f"/reports/{report_id}",
        data={
            "title": "T",
            "board_id": todo_id,
            "importance_id": high_id,
            "tags": "",
            "content": "",
        },
    )
    assert _get_report(app, report_id)["importance_id"] == high_id

    client.post(
        f"/reports/{report_id}",
        data={
            "title": "T",
            "board_id": todo_id,
            "importance_id": "",
            "tags": "",
            "content": "",
        },
    )
    assert _get_report(app, report_id)["importance_id"] is None


def test_delete_unknown_report_returns_404(client):
    response = client.post("/reports/999/delete")
    assert response.status_code == 404


def test_delete_redirects_to_workspace_view(client, app):
    ws_id, _, report_id = _setup(client, app)
    response = client.post(f"/reports/{report_id}/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/workspaces/{ws_id}")
