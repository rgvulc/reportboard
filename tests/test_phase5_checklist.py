from app.db import get_db


def _setup(client, app):
    """Create one workspace + one report. Returns (ws_id, report_id, todo_board_id)."""
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace WHERE name='WS'").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name='Todo'").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "T", "board_id": todo_id},
    )
    with app.app_context():
        report_id = get_db().execute("SELECT id FROM report").fetchone()["id"]
    return ws_id, report_id, todo_id


def _items(app, report_id):
    with app.app_context():
        return get_db().execute(
            "SELECT id, text, done, position FROM checklist_item "
            "WHERE report_id = ? ORDER BY position, id",
            (report_id,),
        ).fetchall()


def test_add_appends_with_position_max_plus_one_and_done_zero(client, app):
    _, report_id, _ = _setup(client, app)

    client.post(f"/reports/{report_id}/checklist", data={"text": "step 1"})
    client.post(f"/reports/{report_id}/checklist", data={"text": "step 2"})
    client.post(f"/reports/{report_id}/checklist", data={"text": "step 3"})

    rows = _items(app, report_id)
    assert [(r["text"], r["done"], r["position"]) for r in rows] == [
        ("step 1", 0, 0),
        ("step 2", 0, 1),
        ("step 3", 0, 2),
    ]


def test_add_with_blank_text_returns_400(client, app):
    _, report_id, _ = _setup(client, app)
    response = client.post(f"/reports/{report_id}/checklist", data={"text": "   "})
    assert response.status_code == 400
    assert _items(app, report_id) == []


def test_add_to_unknown_report_returns_404(client):
    response = client.post("/reports/999/checklist", data={"text": "x"})
    assert response.status_code == 404


def test_toggle_flips_and_toggling_twice_restores_original_state(client, app):
    _, report_id, _ = _setup(client, app)
    client.post(f"/reports/{report_id}/checklist", data={"text": "x"})
    item_id = _items(app, report_id)[0]["id"]

    def done():
        with app.app_context():
            return get_db().execute(
                "SELECT done FROM checklist_item WHERE id = ?", (item_id,)
            ).fetchone()["done"]

    assert done() == 0
    client.post(f"/checklist/{item_id}/toggle")
    assert done() == 1
    client.post(f"/checklist/{item_id}/toggle")
    assert done() == 0


def test_toggle_unknown_item_returns_404(client):
    response = client.post("/checklist/999/toggle")
    assert response.status_code == 404


def test_delete_removes_row_and_keeps_others_in_relative_order(client, app):
    _, report_id, _ = _setup(client, app)
    for t in ["A", "B", "C"]:
        client.post(f"/reports/{report_id}/checklist", data={"text": t})

    items = _items(app, report_id)
    b_id = next(r["id"] for r in items if r["text"] == "B")

    client.post(f"/checklist/{b_id}/delete")

    after = _items(app, report_id)
    assert [r["text"] for r in after] == ["A", "C"]


def test_delete_unknown_item_returns_404(client):
    response = client.post("/checklist/999/delete")
    assert response.status_code == 404


def test_reorder_renumbers_items_contiguously(client, app):
    _, report_id, _ = _setup(client, app)
    for t in ["A", "B", "C", "D"]:
        client.post(f"/reports/{report_id}/checklist", data={"text": t})

    by_text = {r["text"]: r["id"] for r in _items(app, report_id)}
    new_order = [by_text["D"], by_text["B"], by_text["A"], by_text["C"]]

    response = client.post(
        f"/reports/{report_id}/checklist/reorder",
        data={"item_ids": [str(i) for i in new_order]},
    )
    assert response.status_code == 200

    result = _items(app, report_id)
    assert [(r["text"], r["position"]) for r in result] == [
        ("D", 0), ("B", 1), ("A", 2), ("C", 3)
    ]


def test_reorder_with_unknown_report_returns_404(client):
    response = client.post(
        "/reports/999/checklist/reorder",
        data={"item_ids": ["1"]},
    )
    assert response.status_code == 404


def test_reorder_rejects_item_belonging_to_another_report(client, app):
    _, report_a, todo_id = _setup(client, app)

    with app.app_context():
        ws_id = get_db().execute("SELECT id FROM workspace").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "B", "board_id": todo_id},
    )
    with app.app_context():
        report_b = get_db().execute(
            "SELECT id FROM report WHERE title='B'"
        ).fetchone()["id"]

    client.post(f"/reports/{report_a}/checklist", data={"text": "a1"})
    client.post(f"/reports/{report_b}/checklist", data={"text": "b1"})
    a_id = _items(app, report_a)[0]["id"]
    b_id = _items(app, report_b)[0]["id"]

    response = client.post(
        f"/reports/{report_a}/checklist/reorder",
        data={"item_ids": [str(a_id), str(b_id)]},
    )
    assert response.status_code == 400


def test_endpoints_return_html_fragment_not_full_page(client, app):
    _, report_id, _ = _setup(client, app)

    response = client.post(f"/reports/{report_id}/checklist", data={"text": "fragment"})
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()
    assert 'id="checklist"' in body
    assert "fragment" in body


def test_toggle_fragment_reflects_new_state(client, app):
    _, report_id, _ = _setup(client, app)
    client.post(f"/reports/{report_id}/checklist", data={"text": "x"})
    item_id = _items(app, report_id)[0]["id"]

    response = client.post(f"/checklist/{item_id}/toggle")
    body = response.get_data(as_text=True)
    assert "checked" in body
    assert "checklist-text done" in body


def test_other_reports_checklist_is_unaffected(client, app):
    _, report_a, todo_id = _setup(client, app)

    with app.app_context():
        ws_id = get_db().execute("SELECT id FROM workspace").fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "B", "board_id": todo_id},
    )
    with app.app_context():
        report_b = get_db().execute(
            "SELECT id FROM report WHERE title='B'"
        ).fetchone()["id"]

    client.post(f"/reports/{report_a}/checklist", data={"text": "a-step"})
    assert len(_items(app, report_b)) == 0

    a_id = _items(app, report_a)[0]["id"]
    client.post(f"/checklist/{a_id}/toggle")
    assert len(_items(app, report_b)) == 0

    client.post(f"/checklist/{a_id}/delete")
    assert len(_items(app, report_b)) == 0


def test_detail_page_includes_existing_checklist_items(client, app):
    _, report_id, _ = _setup(client, app)
    client.post(f"/reports/{report_id}/checklist", data={"text": "first item"})
    client.post(f"/reports/{report_id}/checklist", data={"text": "second item"})

    body = client.get(f"/reports/{report_id}").get_data(as_text=True)
    assert "first item" in body
    assert "second item" in body
    assert 'id="checklist"' in body
