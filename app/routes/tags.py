from flask import Blueprint, jsonify, request

from ..db import get_db


bp = Blueprint("tags", __name__)

AUTOCOMPLETE_LIMIT = 10


@bp.get("/tags/autocomplete")
def autocomplete():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])

    # Escape LIKE wildcards in the user-supplied prefix so % and _ behave literally.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = escaped + "%"

    rows = get_db().execute(
        "SELECT name FROM tag "
        "WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE "
        "ORDER BY name LIMIT ?",
        (pattern, AUTOCOMPLETE_LIMIT),
    ).fetchall()
    return jsonify([r["name"] for r in rows])
