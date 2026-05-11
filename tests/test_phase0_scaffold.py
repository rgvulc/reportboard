from app import create_app
from app.db import get_db


EXPECTED_TABLES = {
    "board",
    "importance_level",
    "workspace",
    "report",
    "tag",
    "report_tag",
    "checklist_item",
    "attachment",
}


def test_app_factory_builds_with_test_config():
    app = create_app({"TESTING": True, "DATABASE": ":memory:"})
    assert app.config["TESTING"] is True
    assert app.config["DATABASE"] == ":memory:"


def test_init_db_creates_all_tables(app):
    with app.app_context():
        rows = get_db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    table_names = {row["name"] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names)


def test_seed_inserts_default_boards_in_order(app):
    with app.app_context():
        rows = get_db().execute(
            "SELECT name, position FROM board ORDER BY position"
        ).fetchall()

    expected = [
        ("Todo", 0),
        ("In Progress", 1),
        ("Complete", 2),
    ]
    assert [(r["name"], r["position"]) for r in rows] == expected


def test_seed_inserts_default_importance_levels(app):
    with app.app_context():
        rows = get_db().execute(
            "SELECT name, position FROM importance_level ORDER BY position"
        ).fetchall()

    expected = [
        ("Low", 0),
        ("Medium", 1),
        ("High", 2),
        ("Backlog", 3),
        ("Abandoned", 4),
    ]
    assert [(r["name"], r["position"]) for r in rows] == expected


def test_foreign_keys_pragma_is_enabled(app):
    with app.app_context():
        result = get_db().execute("PRAGMA foreign_keys").fetchone()
    assert result[0] == 1


def test_index_responds(client):
    response = client.get("/")
    assert response.status_code == 200
