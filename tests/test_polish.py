from app.db import get_db


def _seed_tags(app, names):
    with app.app_context():
        db = get_db()
        with db:
            for n in names:
                db.execute("INSERT INTO tag (name) VALUES (?)", (n,))


def test_autocomplete_returns_prefix_matches(client, app):
    _seed_tags(app, ["alpha", "beta", "gamma", "delta"])

    response = client.get("/tags/autocomplete?q=al")
    assert response.status_code == 200
    assert response.get_json() == ["alpha"]


def test_autocomplete_is_case_insensitive(client, app):
    _seed_tags(app, ["Alpha", "Beta"])

    upper = client.get("/tags/autocomplete?q=AL").get_json()
    lower = client.get("/tags/autocomplete?q=al").get_json()
    mixed = client.get("/tags/autocomplete?q=Al").get_json()

    assert upper == lower == mixed == ["Alpha"]


def test_autocomplete_returns_multiple_matches(client, app):
    _seed_tags(app, ["python", "pytest", "pyramid", "ruby"])

    matches = client.get("/tags/autocomplete?q=py").get_json()
    assert sorted(matches) == ["pyramid", "pytest", "python"]


def test_autocomplete_caps_results_at_limit(client, app):
    _seed_tags(app, [f"tag{i:02}" for i in range(20)])

    matches = client.get("/tags/autocomplete?q=tag").get_json()
    assert len(matches) == 10


def test_autocomplete_empty_q_returns_empty_list(client, app):
    _seed_tags(app, ["alpha"])

    assert client.get("/tags/autocomplete?q=").get_json() == []
    assert client.get("/tags/autocomplete?q=   ").get_json() == []


def test_autocomplete_missing_q_param_returns_empty_list(client, app):
    _seed_tags(app, ["alpha"])
    assert client.get("/tags/autocomplete").get_json() == []


def test_autocomplete_no_matches_returns_empty_list(client, app):
    _seed_tags(app, ["alpha", "beta"])
    assert client.get("/tags/autocomplete?q=zzz").get_json() == []


def test_autocomplete_treats_like_wildcards_as_literal(client, app):
    _seed_tags(app, ["alpha", "100%complete", "snake_case", "camelCase"])

    # "%" should match the literal % character, not act as wildcard.
    pct = client.get("/tags/autocomplete?q=100%25").get_json()
    assert pct == ["100%complete"]

    # "_" should match the literal _ character, not "any single character".
    underscore = client.get("/tags/autocomplete?q=snake_").get_json()
    assert underscore == ["snake_case"]


def test_detail_page_renders_datalist_with_existing_tags(client, app):
    # Set up: workspace + report + saved tags
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        db = get_db()
        ws_id = db.execute("SELECT id FROM workspace").fetchone()["id"]
        todo_id = db.execute("SELECT id FROM board WHERE name='Todo'").fetchone()["id"]
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
            "tags": "ml, transformers",
            "content": "",
        },
    )

    body = client.get(f"/reports/{report_id}").get_data(as_text=True)
    assert 'id="tag-suggestions"' in body
    assert 'list="tag-suggestions"' in body
    assert '<option value="ml"></option>' in body
    assert '<option value="transformers"></option>' in body


def test_detail_page_ships_auto_linkify(client, app):
    """The editor must auto-link bare URLs. This wiring was once silently
    dropped in a refactor, leaving typed/pasted URLs as unstyled plain text
    with no link tooltip; guard against that regressing again."""
    client.post("/workspaces", data={"name": "WS"})
    with app.app_context():
        ws_id = get_db().execute("SELECT id FROM workspace").fetchone()["id"]
        todo_id = get_db().execute(
            "SELECT id FROM board WHERE name='Todo'"
        ).fetchone()["id"]
    client.post(
        f"/workspaces/{ws_id}/reports",
        data={"title": "x", "board_id": todo_id},
    )
    with app.app_context():
        report_id = get_db().execute("SELECT id FROM report").fetchone()["id"]

    body = client.get(f"/reports/{report_id}").get_data(as_text=True)

    # The linkify routine is defined...
    assert "function linkifyDocument()" in body
    # ...run once after hydration so stored bare URLs get linked...
    assert body.count("linkifyDocument()") >= 3
    # ...wired to user edits...
    assert "quill.on('text-change'" in body
    # ...and run on submit so a trailing-terminator-less URL still links.
    assert "form.addEventListener('submit'" in body
    # 'link' must survive the paste attribute filter for links to persist.
    assert "'bold', 'italic', 'strike', 'code', 'link'," in body
