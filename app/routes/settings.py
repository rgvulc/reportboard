import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db


bp = Blueprint("settings", __name__)


# Configuration for each editable settings list. Keeps add/rename/delete/reorder
# logic generic without exposing user-supplied identifiers to the SQL.
_TABLES = {
    "boards": {
        "table": "board",
        "label": "Board",
        "ref_column": "board_id",
        "ref_table": "report",
    },
    "importance": {
        "table": "importance_level",
        "label": "Importance level",
        "ref_column": "importance_id",
        "ref_table": "report",
    },
}


def _config(kind: str) -> dict:
    if kind not in _TABLES:
        abort(404)
    return _TABLES[kind]


def _usage_counts(kind: str) -> dict[int, int]:
    cfg = _config(kind)
    rows = get_db().execute(
        f"SELECT t.id AS id, COUNT(r.id) AS c FROM {cfg['table']} t "
        f"LEFT JOIN {cfg['ref_table']} r ON r.{cfg['ref_column']} = t.id "
        f"GROUP BY t.id"
    ).fetchall()
    return {r["id"]: r["c"] for r in rows}


@bp.get("/settings")
def index():
    db = get_db()
    boards = db.execute(
        "SELECT id, name, position FROM board ORDER BY position, id"
    ).fetchall()
    importance_levels = db.execute(
        "SELECT id, name, position FROM importance_level ORDER BY position, id"
    ).fetchall()
    return render_template(
        "settings/index.html",
        boards=boards,
        importance_levels=importance_levels,
        board_counts=_usage_counts("boards"),
        importance_counts=_usage_counts("importance"),
    )


def _add(kind: str):
    cfg = _config(kind)
    db = get_db()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash(f"{cfg['label']} name is required.", "error")
        return redirect(url_for("settings.index")), 400

    next_pos = db.execute(
        f"SELECT COALESCE(MAX(position), -1) + 1 AS next FROM {cfg['table']}"
    ).fetchone()["next"]
    try:
        with db:
            db.execute(
                f"INSERT INTO {cfg['table']} (name, position) VALUES (?, ?)",
                (name, next_pos),
            )
    except sqlite3.IntegrityError:
        flash(
            f"A {cfg['label'].lower()} named {name!r} already exists.",
            "error",
        )
        return redirect(url_for("settings.index")), 400
    return redirect(url_for("settings.index"))


def _rename(kind: str, item_id: int):
    cfg = _config(kind)
    db = get_db()
    if db.execute(
        f"SELECT 1 FROM {cfg['table']} WHERE id = ?", (item_id,)
    ).fetchone() is None:
        abort(404)

    new_name = (request.form.get("name") or "").strip()
    if not new_name:
        flash(f"{cfg['label']} name is required.", "error")
        return redirect(url_for("settings.index")), 400

    try:
        with db:
            db.execute(
                f"UPDATE {cfg['table']} SET name = ? WHERE id = ?",
                (new_name, item_id),
            )
    except sqlite3.IntegrityError:
        flash(
            f"A {cfg['label'].lower()} named {new_name!r} already exists.",
            "error",
        )
        return redirect(url_for("settings.index")), 400
    return redirect(url_for("settings.index"))


def _delete(kind: str, item_id: int):
    cfg = _config(kind)
    db = get_db()
    if db.execute(
        f"SELECT 1 FROM {cfg['table']} WHERE id = ?", (item_id,)
    ).fetchone() is None:
        abort(404)

    use_count = db.execute(
        f"SELECT COUNT(*) AS c FROM {cfg['ref_table']} "
        f"WHERE {cfg['ref_column']} = ?",
        (item_id,),
    ).fetchone()["c"]
    if use_count > 0:
        flash(
            f"This {cfg['label'].lower()} is used by {use_count} "
            f"report{'s' if use_count != 1 else ''}; reassign them before deleting.",
            "error",
        )
        return redirect(url_for("settings.index")), 400

    with db:
        db.execute(f"DELETE FROM {cfg['table']} WHERE id = ?", (item_id,))
    return redirect(url_for("settings.index"))


def _reorder(kind: str, field_name: str):
    cfg = _config(kind)
    raw_ids = request.form.getlist(field_name)
    try:
        new_ids = [int(i) for i in raw_ids]
    except ValueError:
        abort(400)

    if len(set(new_ids)) != len(new_ids):
        abort(400)

    db = get_db()
    existing = {
        r["id"]
        for r in db.execute(f"SELECT id FROM {cfg['table']}").fetchall()
    }
    if set(new_ids) != existing:
        abort(400)

    with db:
        for pos, item_id in enumerate(new_ids):
            db.execute(
                f"UPDATE {cfg['table']} SET position = ? WHERE id = ?",
                (pos, item_id),
            )
    return ("", 204)


# --- Boards ---

@bp.post("/settings/boards")
def add_board():
    return _add("boards")


@bp.post("/settings/boards/<int:board_id>/rename")
def rename_board(board_id: int):
    return _rename("boards", board_id)


@bp.post("/settings/boards/<int:board_id>/delete")
def delete_board(board_id: int):
    return _delete("boards", board_id)


@bp.post("/settings/boards/reorder")
def reorder_boards():
    return _reorder("boards", "board_ids")


# --- Importance levels ---

@bp.post("/settings/importance")
def add_importance():
    return _add("importance")


@bp.post("/settings/importance/<int:importance_id>/rename")
def rename_importance(importance_id: int):
    return _rename("importance", importance_id)


@bp.post("/settings/importance/<int:importance_id>/delete")
def delete_importance(importance_id: int):
    return _delete("importance", importance_id)


@bp.post("/settings/importance/reorder")
def reorder_importance():
    return _reorder("importance", "importance_ids")
