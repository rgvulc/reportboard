import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db


bp = Blueprint("workspaces", __name__)


@bp.get("/")
def index():
    rows = get_db().execute(
        "SELECT id, name, position FROM workspace ORDER BY position, id"
    ).fetchall()
    return render_template("workspaces/list.html", workspaces=rows)


@bp.get("/workspaces/<int:workspace_id>")
def view(workspace_id: int):
    db = get_db()
    workspace = db.execute(
        "SELECT id, name FROM workspace WHERE id = ?", (workspace_id,)
    ).fetchone()
    if workspace is None:
        abort(404)

    boards = db.execute(
        "SELECT id, name, position FROM board ORDER BY position, id"
    ).fetchall()

    reports = db.execute(
        "SELECT id, board_id, title, position "
        "FROM report WHERE workspace_id = ? "
        "ORDER BY board_id, position, id",
        (workspace_id,),
    ).fetchall()

    reports_by_board: dict[int, list] = {b["id"]: [] for b in boards}
    for r in reports:
        reports_by_board.setdefault(r["board_id"], []).append(r)

    return render_template(
        "workspaces/board.html",
        workspace=workspace,
        boards=boards,
        reports_by_board=reports_by_board,
    )


@bp.post("/workspaces")
def create():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Workspace name is required.", "error")
        return redirect(url_for("workspaces.index")), 400

    db = get_db()
    next_pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM workspace"
    ).fetchone()["next"]
    try:
        with db:
            db.execute(
                "INSERT INTO workspace (name, position) VALUES (?, ?)",
                (name, next_pos),
            )
    except sqlite3.IntegrityError:
        flash(f"A workspace named {name!r} already exists.", "error")
        return redirect(url_for("workspaces.index")), 400

    return redirect(url_for("workspaces.index"))


@bp.post("/workspaces/<int:workspace_id>/rename")
def rename(workspace_id: int):
    new_name = (request.form.get("name") or "").strip()
    if not new_name:
        flash("Workspace name is required.", "error")
        return redirect(url_for("workspaces.index")), 400

    db = get_db()
    if db.execute("SELECT 1 FROM workspace WHERE id = ?", (workspace_id,)).fetchone() is None:
        abort(404)

    try:
        with db:
            db.execute(
                "UPDATE workspace "
                "SET name = ?, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now') "
                "WHERE id = ?",
                (new_name, workspace_id),
            )
    except sqlite3.IntegrityError:
        flash(f"A workspace named {new_name!r} already exists.", "error")
        return redirect(url_for("workspaces.index")), 400

    return redirect(url_for("workspaces.index"))


@bp.post("/workspaces/<int:workspace_id>/delete")
def delete(workspace_id: int):
    db = get_db()
    if db.execute("SELECT 1 FROM workspace WHERE id = ?", (workspace_id,)).fetchone() is None:
        abort(404)
    with db:
        db.execute("DELETE FROM workspace WHERE id = ?", (workspace_id,))
    return redirect(url_for("workspaces.index"))


@bp.post("/workspaces/reorder")
def reorder():
    raw_ids = request.form.getlist("workspace_ids")
    try:
        new_ids = [int(i) for i in raw_ids]
    except ValueError:
        abort(400)

    if len(set(new_ids)) != len(new_ids):
        abort(400)

    db = get_db()
    existing_ids = {
        r["id"] for r in db.execute("SELECT id FROM workspace").fetchall()
    }
    if set(new_ids) != existing_ids:
        abort(400)

    with db:
        for pos, ws_id in enumerate(new_ids):
            db.execute(
                "UPDATE workspace SET position = ? WHERE id = ?",
                (pos, ws_id),
            )

    return ("", 204)


@bp.post("/workspaces/<int:workspace_id>/move")
def move(workspace_id: int):
    direction = request.form.get("direction")
    if direction not in {"up", "down"}:
        abort(400)

    db = get_db()
    current = db.execute(
        "SELECT id, position FROM workspace WHERE id = ?", (workspace_id,)
    ).fetchone()
    if current is None:
        abort(404)

    if direction == "up":
        neighbor = db.execute(
            "SELECT id, position FROM workspace "
            "WHERE position < ? ORDER BY position DESC LIMIT 1",
            (current["position"],),
        ).fetchone()
    else:
        neighbor = db.execute(
            "SELECT id, position FROM workspace "
            "WHERE position > ? ORDER BY position ASC LIMIT 1",
            (current["position"],),
        ).fetchone()

    if neighbor is None:
        return redirect(url_for("workspaces.index"))

    with db:
        db.execute(
            "UPDATE workspace SET position = ? WHERE id = ?",
            (neighbor["position"], current["id"]),
        )
        db.execute(
            "UPDATE workspace SET position = ? WHERE id = ?",
            (current["position"], neighbor["id"]),
        )

    return redirect(url_for("workspaces.index"))
