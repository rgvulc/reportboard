import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import attachments
from ..db import get_db
from ..delta_md import canonicalize_delta


bp = Blueprint("reports", __name__)

NOW_SQL = "strftime('%Y-%m-%d %H:%M:%f', 'now')"


def _normalize_content_delta(form) -> str:
    """Pull the canonical Delta JSON out of the submitted form.

    The browser editor posts `content_delta` (Quill Delta JSON). An absent
    or empty value yields the canonical empty document. Returns the
    canonicalised Delta as a JSON string.
    """
    raw = (form.get("content_delta") or "").strip()
    if not raw:
        return json.dumps({"ops": [{"insert": "\n"}]}, ensure_ascii=False)
    try:
        return json.dumps(canonicalize_delta(json.loads(raw)),
                          ensure_ascii=False, sort_keys=False)
    except json.JSONDecodeError:
        abort(400, description="content_delta is not valid JSON")


def _parse_tags(raw: str) -> list[str]:
    """Split a comma-separated string into a list of tag names.

    Strips whitespace, ignores empty entries, and dedupes case-insensitively
    while preserving the first-seen casing.
    """
    seen_lower: set[str] = set()
    result: list[str] = []
    for piece in raw.split(","):
        name = piece.strip()
        if not name:
            continue
        lower = name.lower()
        if lower in seen_lower:
            continue
        seen_lower.add(lower)
        result.append(name)
    return result


@bp.post("/workspaces/<int:workspace_id>/reports")
def create(workspace_id: int):
    title = (request.form.get("title") or "").strip()
    board_id_raw = request.form.get("board_id")

    db = get_db()
    if db.execute("SELECT 1 FROM workspace WHERE id = ?", (workspace_id,)).fetchone() is None:
        abort(404)

    if not title:
        flash("Report title is required.", "error")
        return redirect(url_for("workspaces.view", workspace_id=workspace_id)), 400

    try:
        board_id = int(board_id_raw)
    except (TypeError, ValueError):
        abort(400)

    if db.execute("SELECT 1 FROM board WHERE id = ?", (board_id,)).fetchone() is None:
        abort(400)

    next_pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next "
        "FROM report WHERE workspace_id = ? AND board_id = ?",
        (workspace_id, board_id),
    ).fetchone()["next"]

    # Default new reports to the "Medium" importance level when it exists.
    # If the user renamed/removed it, fall back to NULL — the report just
    # has no importance set, same as before.
    medium = db.execute(
        "SELECT id FROM importance_level WHERE name = 'Medium' COLLATE NOCASE"
    ).fetchone()
    default_importance = medium["id"] if medium else None

    with db:
        db.execute(
            "INSERT INTO report (workspace_id, board_id, importance_id, "
            "title, position) VALUES (?, ?, ?, ?, ?)",
            (workspace_id, board_id, default_importance, title, next_pos),
        )

    return redirect(url_for("workspaces.view", workspace_id=workspace_id))


@bp.get("/reports/<int:report_id>")
def detail(report_id: int):
    db = get_db()
    report = db.execute(
        "SELECT id, workspace_id, board_id, importance_id, title, "
        "content_delta, created_at, updated_at "
        "FROM report WHERE id = ?",
        (report_id,),
    ).fetchone()
    if report is None:
        abort(404)

    workspace = db.execute(
        "SELECT id, name FROM workspace WHERE id = ?",
        (report["workspace_id"],),
    ).fetchone()
    boards = db.execute(
        "SELECT id, name FROM board ORDER BY position, id"
    ).fetchall()
    importance_levels = db.execute(
        "SELECT id, name FROM importance_level ORDER BY position, id"
    ).fetchall()
    tag_rows = db.execute(
        "SELECT t.name FROM tag t "
        "JOIN report_tag rt ON rt.tag_id = t.id "
        "WHERE rt.report_id = ? ORDER BY t.name",
        (report_id,),
    ).fetchall()
    checklist_items = db.execute(
        "SELECT id, text, done, position FROM checklist_item "
        "WHERE report_id = ? ORDER BY position, id",
        (report_id,),
    ).fetchall()
    all_tag_names = [
        r["name"]
        for r in db.execute(
            "SELECT name FROM tag ORDER BY name COLLATE NOCASE"
        ).fetchall()
    ]

    return render_template(
        "reports/detail.html",
        report=report,
        workspace=workspace,
        boards=boards,
        importance_levels=importance_levels,
        tags_display=", ".join(t["name"] for t in tag_rows),
        checklist_items=checklist_items,
        all_tag_names=all_tag_names,
    )


@bp.post("/reports/<int:report_id>")
def save(report_id: int):
    db = get_db()
    if db.execute("SELECT 1 FROM report WHERE id = ?", (report_id,)).fetchone() is None:
        abort(404)

    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Title is required.", "error")
        return redirect(url_for("reports.detail", report_id=report_id)), 400

    try:
        board_id = int(request.form.get("board_id"))
    except (TypeError, ValueError):
        abort(400)
    if db.execute("SELECT 1 FROM board WHERE id = ?", (board_id,)).fetchone() is None:
        abort(400)

    importance_raw = request.form.get("importance_id") or ""
    if importance_raw == "":
        importance_id = None
    else:
        try:
            importance_id = int(importance_raw)
        except (TypeError, ValueError):
            abort(400)
        if db.execute(
            "SELECT 1 FROM importance_level WHERE id = ?", (importance_id,)
        ).fetchone() is None:
            abort(400)

    content_delta = _normalize_content_delta(request.form)
    tag_names = _parse_tags(request.form.get("tags") or "")

    with db:
        db.execute(
            f"UPDATE report SET title = ?, board_id = ?, importance_id = ?, "
            f"content_delta = ?, updated_at = {NOW_SQL} WHERE id = ?",
            (title, board_id, importance_id, content_delta, report_id),
        )
        db.execute("DELETE FROM report_tag WHERE report_id = ?", (report_id,))
        for name in tag_names:
            db.execute("INSERT OR IGNORE INTO tag (name) VALUES (?)", (name,))
            row = db.execute("SELECT id FROM tag WHERE name = ?", (name,)).fetchone()
            db.execute(
                "INSERT INTO report_tag (report_id, tag_id) VALUES (?, ?)",
                (report_id, row["id"]),
            )

        attachment_rows = db.execute(
            "SELECT filename FROM attachment WHERE report_id = ?",
            (report_id,),
        ).fetchall()
        # Substring scan works on Delta JSON directly: an image embed appears
        # as {"image":"/attachments/<id>/<file>"}, so "<id>/<file>" is present.
        orphan_filenames = attachments.find_unreferenced(
            report_id, content_delta, [r["filename"] for r in attachment_rows]
        )
        if orphan_filenames:
            db.executemany(
                "DELETE FROM attachment WHERE report_id = ? AND filename = ?",
                [(report_id, f) for f in orphan_filenames],
            )

    if orphan_filenames:
        attachments.delete_files(report_id, orphan_filenames)

    return redirect(url_for("reports.detail", report_id=report_id))


@bp.post("/reports/<int:report_id>/move")
def move(report_id: int):
    db = get_db()
    row = db.execute(
        "SELECT workspace_id, board_id FROM report WHERE id = ?", (report_id,)
    ).fetchone()
    if row is None:
        abort(404)
    workspace_id = row["workspace_id"]
    src_board_id = row["board_id"]

    try:
        dst_board_id = int(request.form.get("board_id"))
        target_pos = int(request.form.get("position"))
    except (TypeError, ValueError):
        abort(400)

    if target_pos < 0:
        abort(400)
    if db.execute("SELECT 1 FROM board WHERE id = ?", (dst_board_id,)).fetchone() is None:
        abort(400)

    with db:
        source_ids = [
            r["id"]
            for r in db.execute(
                "SELECT id FROM report "
                "WHERE workspace_id = ? AND board_id = ? AND id != ? "
                "ORDER BY position, id",
                (workspace_id, src_board_id, report_id),
            ).fetchall()
        ]

        if src_board_id == dst_board_id:
            target_pos = min(target_pos, len(source_ids))
            new_order = source_ids[:target_pos] + [report_id] + source_ids[target_pos:]
            for pos, rid in enumerate(new_order):
                db.execute(
                    "UPDATE report SET position = ? WHERE id = ?", (pos, rid)
                )
        else:
            for pos, rid in enumerate(source_ids):
                db.execute(
                    "UPDATE report SET position = ? WHERE id = ?", (pos, rid)
                )
            dest_ids = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM report "
                    "WHERE workspace_id = ? AND board_id = ? AND id != ? "
                    "ORDER BY position, id",
                    (workspace_id, dst_board_id, report_id),
                ).fetchall()
            ]
            target_pos = min(target_pos, len(dest_ids))
            new_dest = dest_ids[:target_pos] + [report_id] + dest_ids[target_pos:]
            db.execute(
                "UPDATE report SET board_id = ? WHERE id = ?",
                (dst_board_id, report_id),
            )
            for pos, rid in enumerate(new_dest):
                db.execute(
                    "UPDATE report SET position = ? WHERE id = ?", (pos, rid)
                )

    return ("", 204)


@bp.post("/reports/<int:report_id>/delete")
def delete(report_id: int):
    db = get_db()
    row = db.execute(
        "SELECT workspace_id FROM report WHERE id = ?", (report_id,)
    ).fetchone()
    if row is None:
        abort(404)
    workspace_id = row["workspace_id"]
    with db:
        db.execute("DELETE FROM report WHERE id = ?", (report_id,))
    attachments.delete_report_directory(report_id)
    return redirect(url_for("workspaces.view", workspace_id=workspace_id))
