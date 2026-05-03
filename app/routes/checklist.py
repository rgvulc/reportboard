from flask import Blueprint, abort, render_template, request

from ..db import get_db


bp = Blueprint("checklist", __name__)


def _render_fragment(report_id: int) -> str:
    items = get_db().execute(
        "SELECT id, text, done, position FROM checklist_item "
        "WHERE report_id = ? ORDER BY position, id",
        (report_id,),
    ).fetchall()
    return render_template(
        "reports/_checklist.html",
        items=items,
        report_id=report_id,
    )


@bp.post("/reports/<int:report_id>/checklist")
def add(report_id: int):
    db = get_db()
    if db.execute("SELECT 1 FROM report WHERE id = ?", (report_id,)).fetchone() is None:
        abort(404)

    text = (request.form.get("text") or "").strip()
    if not text:
        abort(400)

    next_pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next "
        "FROM checklist_item WHERE report_id = ?",
        (report_id,),
    ).fetchone()["next"]

    with db:
        db.execute(
            "INSERT INTO checklist_item (report_id, text, position) VALUES (?, ?, ?)",
            (report_id, text, next_pos),
        )

    return _render_fragment(report_id)


@bp.post("/checklist/<int:item_id>/toggle")
def toggle(item_id: int):
    db = get_db()
    row = db.execute(
        "SELECT report_id, done FROM checklist_item WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        abort(404)

    new_done = 0 if row["done"] else 1
    with db:
        db.execute(
            "UPDATE checklist_item SET done = ? WHERE id = ?",
            (new_done, item_id),
        )

    return _render_fragment(row["report_id"])


@bp.post("/checklist/<int:item_id>/delete")
def delete(item_id: int):
    db = get_db()
    row = db.execute(
        "SELECT report_id FROM checklist_item WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        abort(404)

    with db:
        db.execute("DELETE FROM checklist_item WHERE id = ?", (item_id,))

    return _render_fragment(row["report_id"])


@bp.post("/reports/<int:report_id>/checklist/reorder")
def reorder(report_id: int):
    db = get_db()
    if db.execute("SELECT 1 FROM report WHERE id = ?", (report_id,)).fetchone() is None:
        abort(404)

    raw_ids = request.form.getlist("item_ids")
    try:
        item_ids = [int(i) for i in raw_ids]
    except ValueError:
        abort(400)

    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        rows = db.execute(
            f"SELECT id FROM checklist_item "
            f"WHERE report_id = ? AND id IN ({placeholders})",
            [report_id, *item_ids],
        ).fetchall()
        if len(rows) != len(item_ids):
            abort(400)

    with db:
        for pos, item_id in enumerate(item_ids):
            db.execute(
                "UPDATE checklist_item SET position = ? WHERE id = ?",
                (pos, item_id),
            )

    return _render_fragment(report_id)
