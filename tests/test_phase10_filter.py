"""Board-page filter rendering: importance + tag data attributes, panel markup."""

from app.db import get_db


def _seed_board_with_mix(app):
    """Workspace with three reports across two boards, varied importance + tags."""
    with app.app_context():
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO workspace (id, name, position) VALUES (1, 'WS', 0)"
            )
            todo_id = db.execute(
                "SELECT id FROM board WHERE name='Todo'"
            ).fetchone()["id"]
            done_id = db.execute(
                "SELECT id FROM board WHERE name='Done'"
            ).fetchone()["id"]
            high_id = db.execute(
                "SELECT id FROM importance_level WHERE name='High'"
            ).fetchone()["id"]
            low_id = db.execute(
                "SELECT id FROM importance_level WHERE name='Low'"
            ).fetchone()["id"]

            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, position) VALUES (10, 1, ?, ?, 'Urgent', 0)",
                (todo_id, high_id),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, position) VALUES (11, 1, ?, ?, 'Minor', 1)",
                (todo_id, low_id),
            )
            db.execute(
                "INSERT INTO report (id, workspace_id, board_id, importance_id, "
                "title, position) VALUES (12, 1, ?, NULL, 'Untagged', 0)",
                (done_id,),
            )

            db.execute("INSERT INTO tag (id, name) VALUES (100, 'work')")
            db.execute("INSERT INTO tag (id, name) VALUES (101, 'home')")
            db.execute(
                "INSERT INTO report_tag (report_id, tag_id) VALUES (10, 100)"
            )
            db.execute(
                "INSERT INTO report_tag (report_id, tag_id) VALUES (11, 101)"
            )

        # A tag that exists globally but isn't used by any report in this workspace —
        # it should NOT appear in the filter panel.
        db.execute("INSERT INTO tag (id, name) VALUES (102, 'global-orphan')")
        db.commit()
        return 1


class TestBoardFilterRendering:
    def test_filter_button_and_panel_present(self, client, app):
        _seed_board_with_mix(app)
        body = client.get("/workspaces/1").get_data(as_text=True)
        assert 'id="filter-toggle"' in body
        assert 'id="filter-panel"' in body
        assert 'data-workspace-id="1"' in body

    def test_importance_section_lists_all_levels(self, client, app):
        _seed_board_with_mix(app)
        body = client.get("/workspaces/1").get_data(as_text=True)
        for name in ("Low", "Medium", "High"):
            assert f'value="{name}" checked' in body, f"importance {name!r}"
        # The "No importance" pseudo-option
        assert 'No importance' in body

    def test_tags_section_scoped_to_workspace(self, client, app):
        _seed_board_with_mix(app)
        body = client.get("/workspaces/1").get_data(as_text=True)
        assert 'value="work" checked' in body
        assert 'value="home" checked' in body
        # Tags global to the DB but unused by this workspace must not appear.
        assert "global-orphan" not in body
        # The "No tags" pseudo-option
        assert 'No tags' in body

    def test_card_data_attributes(self, client, app):
        _seed_board_with_mix(app)
        body = client.get("/workspaces/1").get_data(as_text=True)
        assert 'data-importance="High"' in body
        assert 'data-importance="Low"' in body
        assert 'data-importance=""' in body  # the unimportance'd one
        assert 'data-tags="work"' in body
        assert 'data-tags="home"' in body
        assert 'data-tags=""' in body         # the untagged one

    def test_empty_workspace_renders_panel_without_tags(self, client, app):
        with app.app_context():
            db = get_db()
            with db:
                db.execute(
                    "INSERT INTO workspace (id, name, position) "
                    "VALUES (1, 'Empty', 0)"
                )
        body = client.get("/workspaces/1").get_data(as_text=True)
        assert 'id="filter-panel"' in body
        # No tags → the search input is suppressed but "No tags" stays.
        assert 'filter-tag-search' not in body
        assert 'No tags' in body
        # Importance section still lists all three default levels.
        assert 'value="High" checked' in body

    def test_tags_alphabetical_case_insensitive(self, client, app):
        with app.app_context():
            db = get_db()
            with db:
                db.execute(
                    "INSERT INTO workspace (id, name, position) "
                    "VALUES (1, 'WS', 0)"
                )
                todo_id = db.execute(
                    "SELECT id FROM board WHERE name='Todo'"
                ).fetchone()["id"]
                db.execute(
                    "INSERT INTO report (id, workspace_id, board_id, title, "
                    "position) VALUES (5, 1, ?, 't', 0)",
                    (todo_id,),
                )
                for tid, name in [(200, "zebra"), (201, "Apple"), (202, "banana")]:
                    db.execute("INSERT INTO tag (id, name) VALUES (?, ?)", (tid, name))
                    db.execute(
                        "INSERT INTO report_tag (report_id, tag_id) VALUES (5, ?)",
                        (tid,),
                    )

        body = client.get("/workspaces/1").get_data(as_text=True)
        i_apple  = body.index('value="Apple"')
        i_banana = body.index('value="banana"')
        i_zebra  = body.index('value="zebra"')
        assert i_apple < i_banana < i_zebra
