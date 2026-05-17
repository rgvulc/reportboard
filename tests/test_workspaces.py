from app.db import get_db


def _all_workspaces(app):
    with app.app_context():
        return get_db().execute(
            "SELECT id, name, position, created_at, updated_at "
            "FROM workspace ORDER BY position, id"
        ).fetchall()


def _create(client, name):
    return client.post("/workspaces", data={"name": name}, follow_redirects=False)


def test_create_persists_with_position_max_plus_one(client, app):
    _create(client, "Alpha")
    _create(client, "Beta")
    _create(client, "Gamma")

    rows = _all_workspaces(app)
    assert [(r["name"], r["position"]) for r in rows] == [
        ("Alpha", 0),
        ("Beta", 1),
        ("Gamma", 2),
    ]


def test_create_first_workspace_gets_position_zero(client, app):
    _create(client, "Solo")
    rows = _all_workspaces(app)
    assert rows[0]["position"] == 0


def test_index_lists_workspaces_in_position_order(client):
    _create(client, "Beta")
    _create(client, "Alpha")
    _create(client, "Gamma")

    body = client.get("/").get_data(as_text=True)
    pos_beta = body.index("Beta")
    pos_alpha = body.index("Alpha")
    pos_gamma = body.index("Gamma")
    assert pos_beta < pos_alpha < pos_gamma


def test_create_with_blank_name_returns_400(client, app):
    response = client.post("/workspaces", data={"name": "   "})
    assert response.status_code == 400
    assert _all_workspaces(app) == []


def test_duplicate_name_returns_400_not_500(client, app):
    _create(client, "Alpha")
    response = _create(client, "Alpha")
    assert response.status_code == 400

    rows = _all_workspaces(app)
    assert len(rows) == 1


def test_rename_updates_name_and_updated_at_but_not_position(client, app):
    _create(client, "Alpha")
    before = _all_workspaces(app)[0]

    response = client.post(
        f"/workspaces/{before['id']}/rename",
        data={"name": "Renamed"},
    )
    assert response.status_code in (200, 302)

    after = _all_workspaces(app)[0]
    assert after["name"] == "Renamed"
    assert after["position"] == before["position"]
    assert after["created_at"] == before["created_at"]
    # updated_at should be >= created_at; CURRENT_TIMESTAMP has 1s resolution,
    # so we accept equal-or-later rather than strictly greater.
    assert after["updated_at"] >= before["updated_at"]


def test_rename_to_duplicate_name_returns_400(client, app):
    _create(client, "Alpha")
    _create(client, "Beta")
    rows = _all_workspaces(app)
    beta_id = next(r["id"] for r in rows if r["name"] == "Beta")

    response = client.post(f"/workspaces/{beta_id}/rename", data={"name": "Alpha"})
    assert response.status_code == 400

    names = {r["name"] for r in _all_workspaces(app)}
    assert names == {"Alpha", "Beta"}


def test_rename_unknown_workspace_returns_404(client):
    response = client.post("/workspaces/999/rename", data={"name": "x"})
    assert response.status_code == 404


def test_delete_removes_row(client, app):
    _create(client, "Alpha")
    ws_id = _all_workspaces(app)[0]["id"]

    response = client.post(f"/workspaces/{ws_id}/delete")
    assert response.status_code in (200, 302)
    assert _all_workspaces(app) == []


def test_delete_unknown_workspace_returns_404(client):
    response = client.post("/workspaces/999/delete")
    assert response.status_code == 404


def test_move_up_swaps_with_previous(client, app):
    _create(client, "A")
    _create(client, "B")
    _create(client, "C")

    rows = _all_workspaces(app)
    b_id = next(r["id"] for r in rows if r["name"] == "B")

    client.post(f"/workspaces/{b_id}/move", data={"direction": "up"})

    after = _all_workspaces(app)
    assert [r["name"] for r in after] == ["B", "A", "C"]


def test_move_down_swaps_with_next(client, app):
    _create(client, "A")
    _create(client, "B")
    _create(client, "C")

    rows = _all_workspaces(app)
    b_id = next(r["id"] for r in rows if r["name"] == "B")

    client.post(f"/workspaces/{b_id}/move", data={"direction": "down"})

    after = _all_workspaces(app)
    assert [r["name"] for r in after] == ["A", "C", "B"]


def test_move_up_at_top_is_noop(client, app):
    _create(client, "A")
    _create(client, "B")
    rows = _all_workspaces(app)
    a_id = next(r["id"] for r in rows if r["name"] == "A")

    client.post(f"/workspaces/{a_id}/move", data={"direction": "up"})

    assert [r["name"] for r in _all_workspaces(app)] == ["A", "B"]


def test_move_down_at_bottom_is_noop(client, app):
    _create(client, "A")
    _create(client, "B")
    rows = _all_workspaces(app)
    b_id = next(r["id"] for r in rows if r["name"] == "B")

    client.post(f"/workspaces/{b_id}/move", data={"direction": "down"})

    assert [r["name"] for r in _all_workspaces(app)] == ["A", "B"]


def test_move_with_invalid_direction_returns_400(client):
    _create(client, "A")
    response = client.post("/workspaces/1/move", data={"direction": "sideways"})
    assert response.status_code == 400


def test_move_unknown_workspace_returns_404(client):
    response = client.post("/workspaces/999/move", data={"direction": "up"})
    assert response.status_code == 404
