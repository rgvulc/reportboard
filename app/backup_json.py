"""JSON-format backup — the canonical migration format.

Layout in the zip:

    data.json                            # everything textual
    attachments/<report_id>/<filename>   # binary attachment files

`data.json` is a single nested document containing every workspace, every
report (with its Delta as a JSON object, not a string), and every reference
table (boards, importance levels, tags). The exporter produces deterministic
output (stable orderings) so diffing two exports is meaningful.

Replace-mode import: validate the entire archive first, then wipe + reinsert.
A malformed zip leaves the live DB untouched.
"""

import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .importer import ImportError as _MarkdownImportError, safe_extract_zip


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {2}


class ImportError(_MarkdownImportError):
    """Raised on validation failure. Inherits the markdown importer's
    exception so callers can catch one type for either backup format."""


# ============================================================================
#  Export
# ============================================================================

def build_export_dict(conn: sqlite3.Connection) -> dict:
    """Build the in-memory dict that `data.json` serialises from."""
    boards = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name, position FROM board ORDER BY position, id"
    )]
    levels = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name, position FROM importance_level ORDER BY position, id"
    )]
    tags = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name FROM tag ORDER BY id"
    )]

    board_name = {b["id"]: b["name"] for b in boards}
    level_name = {l["id"]: l["name"] for l in levels}

    workspaces = []
    for ws_row in conn.execute(
        "SELECT id, name, position, created_at, updated_at "
        "FROM workspace ORDER BY position, id"
    ):
        ws = _row_to_dict(ws_row)
        ws["reports"] = []

        report_rows = list(conn.execute(
            "SELECT id, board_id, importance_id, title, content_delta, "
            "position, created_at, updated_at FROM report "
            "WHERE workspace_id = ? ORDER BY board_id, position, id",
            (ws["id"],),
        ))
        for r_row in report_rows:
            r = _row_to_dict(r_row)
            rid = r["id"]
            r["board"] = board_name[r.pop("board_id")]
            r["importance"] = level_name.get(r.pop("importance_id"))
            r["content_delta"] = _parse_delta(r["content_delta"])
            r["tags"] = [row["name"] for row in conn.execute(
                "SELECT t.name FROM tag t JOIN report_tag rt ON rt.tag_id = t.id "
                "WHERE rt.report_id = ? ORDER BY t.name COLLATE NOCASE",
                (rid,),
            )]
            r["checklist"] = [
                {"text": ci["text"], "done": bool(ci["done"]),
                 "position": ci["position"]}
                for ci in conn.execute(
                    "SELECT text, done, position FROM checklist_item "
                    "WHERE report_id = ? ORDER BY position, id",
                    (rid,),
                )
            ]
            r["attachments"] = [_row_to_dict(a) for a in conn.execute(
                "SELECT filename, original_name, mime_type, size_bytes, "
                "created_at FROM attachment WHERE report_id = ? ORDER BY filename",
                (rid,),
            )]
            ws["reports"].append(r)
        workspaces.append(ws)

    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "boards": boards,
        "importance_levels": levels,
        "tags": tags,
        "workspaces": workspaces,
    }


def export_to_zip(
    conn: sqlite3.Connection,
    attachments_root: Path,
    zip_path: Path,
) -> None:
    """Write the entire DB to `zip_path` as a JSON-format backup."""
    data = build_export_dict(conn)

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "export"
        staging.mkdir()
        (staging / "data.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Copy attachment binaries into `attachments/<id>/<file>`.
        for ws in data["workspaces"]:
            for report in ws["reports"]:
                for att in report["attachments"]:
                    src = attachments_root / str(report["id"]) / att["filename"]
                    if not src.exists():
                        continue
                    dest = (staging / "attachments"
                            / str(report["id"]) / att["filename"])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)

        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(staging))


# ============================================================================
#  Import (replace mode)
# ============================================================================

@dataclass
class ParsedJsonArchive:
    data: dict
    attachments_dir: Path   # absolute path to the extracted attachments root


def _find_archive_root(extracted: Path) -> Path:
    """Tolerate zips that wrap everything in a single top-level folder."""
    if (extracted / "data.json").exists():
        return extracted
    children = [p for p in extracted.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() \
            and (children[0] / "data.json").exists():
        return children[0]
    raise ImportError("data.json not found at the archive root")


def load_archive(root: Path) -> ParsedJsonArchive:
    archive_root = _find_archive_root(root)
    try:
        data = json.loads((archive_root / "data.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ImportError(f"data.json is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ImportError("data.json must be a JSON object")
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ImportError(
            f"Unsupported schema_version {version!r}; "
            f"this build supports {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    return ParsedJsonArchive(data=data,
                              attachments_dir=archive_root / "attachments")


def validate(archive: ParsedJsonArchive) -> None:
    data = archive.data

    for key in ("boards", "importance_levels", "tags", "workspaces"):
        if not isinstance(data.get(key), list):
            raise ImportError(f"data.json: {key!r} must be a list")

    board_names = {b["name"] for b in data["boards"]}
    importance_names = {i["name"] for i in data["importance_levels"]}

    seen_workspace_ids: set[int] = set()
    seen_report_ids: set[int] = set()

    for ws in data["workspaces"]:
        ws_id = ws.get("id")
        if not isinstance(ws_id, int):
            raise ImportError(f"workspace.id must be an integer, got {ws_id!r}")
        if ws_id in seen_workspace_ids:
            raise ImportError(f"duplicate workspace id {ws_id}")
        seen_workspace_ids.add(ws_id)
        for key in ("name", "position", "created_at", "updated_at"):
            if key not in ws:
                raise ImportError(f"workspace {ws_id}: missing field {key!r}")

        for report in ws.get("reports", []):
            rid = report.get("id")
            if not isinstance(rid, int):
                raise ImportError(f"report.id must be an integer, got {rid!r}")
            if rid in seen_report_ids:
                raise ImportError(f"duplicate report id {rid}")
            seen_report_ids.add(rid)

            for key in ("title", "board", "position", "created_at",
                        "updated_at", "content_delta"):
                if key not in report:
                    raise ImportError(f"report {rid}: missing field {key!r}")

            if report["board"] not in board_names:
                raise ImportError(
                    f"report {rid}: board {report['board']!r} not in manifest"
                )
            if report.get("importance") is not None \
                    and report["importance"] not in importance_names:
                raise ImportError(
                    f"report {rid}: importance {report['importance']!r} not in manifest"
                )
            if not isinstance(report["content_delta"], dict):
                raise ImportError(
                    f"report {rid}: content_delta must be a JSON object"
                )

            for tag in report.get("tags") or []:
                if not isinstance(tag, str):
                    raise ImportError(
                        f"report {rid}: tag entries must be strings, got {tag!r}"
                    )

            for item in report.get("checklist") or []:
                if not isinstance(item, dict) \
                        or "text" not in item \
                        or "done" not in item \
                        or "position" not in item:
                    raise ImportError(
                        f"report {rid}: checklist entry missing required keys"
                    )

            for att in report.get("attachments") or []:
                fname = att.get("filename")
                if not isinstance(fname, str) or not fname:
                    raise ImportError(
                        f"report {rid}: attachment missing 'filename'"
                    )
                disk_path = archive.attachments_dir / str(rid) / fname
                if not disk_path.is_file():
                    raise ImportError(
                        f"attachment file missing on disk: "
                        f"attachments/{rid}/{fname}"
                    )


def _wipe(conn: sqlite3.Connection, attachments_root: Path) -> None:
    conn.execute("DELETE FROM workspace")
    conn.execute("DELETE FROM tag")
    conn.execute("DELETE FROM board")
    conn.execute("DELETE FROM importance_level")
    if attachments_root.exists():
        shutil.rmtree(attachments_root)
    attachments_root.mkdir(parents=True, exist_ok=True)


def apply(
    conn: sqlite3.Connection,
    attachments_root: Path,
    archive: ParsedJsonArchive,
) -> None:
    data = archive.data
    board_id_by_name: dict[str, int] = {}
    importance_id_by_name: dict[str, int] = {}
    tag_id_by_name: dict[str, int] = {}
    pending_files: list[tuple[Path, Path]] = []

    with conn:
        _wipe(conn, attachments_root)

        for b in data["boards"]:
            conn.execute(
                "INSERT INTO board (id, name, position) VALUES (?, ?, ?)",
                (b["id"], b["name"], b["position"]),
            )
            board_id_by_name[b["name"]] = b["id"]

        for i in data["importance_levels"]:
            conn.execute(
                "INSERT INTO importance_level (id, name, position) "
                "VALUES (?, ?, ?)",
                (i["id"], i["name"], i["position"]),
            )
            importance_id_by_name[i["name"]] = i["id"]

        for t in data["tags"]:
            conn.execute(
                "INSERT INTO tag (id, name) VALUES (?, ?)", (t["id"], t["name"]),
            )
            tag_id_by_name[t["name"].lower()] = t["id"]

        for ws in data["workspaces"]:
            conn.execute(
                "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ws["id"], ws["name"], ws["position"],
                 ws["created_at"], ws["updated_at"]),
            )

            for report in ws.get("reports", []):
                rid = report["id"]
                board_id = board_id_by_name[report["board"]]
                importance_id = (
                    importance_id_by_name[report["importance"]]
                    if report.get("importance") is not None else None
                )
                content_delta = json.dumps(report["content_delta"],
                                            ensure_ascii=False)

                conn.execute(
                    "INSERT INTO report "
                    "(id, workspace_id, board_id, importance_id, title, "
                    "content_delta, position, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, ws["id"], board_id, importance_id, report["title"],
                     content_delta, report["position"],
                     report["created_at"], report["updated_at"]),
                )

                for tag_name in report.get("tags") or []:
                    tag_id = tag_id_by_name.get(tag_name.lower())
                    if tag_id is None:
                        cur = conn.execute(
                            "INSERT INTO tag (name) VALUES (?)", (tag_name,)
                        )
                        tag_id = cur.lastrowid
                        tag_id_by_name[tag_name.lower()] = tag_id
                    conn.execute(
                        "INSERT OR IGNORE INTO report_tag (report_id, tag_id) "
                        "VALUES (?, ?)",
                        (rid, tag_id),
                    )

                for item in report.get("checklist") or []:
                    conn.execute(
                        "INSERT INTO checklist_item "
                        "(report_id, text, done, position) "
                        "VALUES (?, ?, ?, ?)",
                        (rid, item["text"],
                         1 if item["done"] else 0, item["position"]),
                    )

                for att in report.get("attachments") or []:
                    conn.execute(
                        "INSERT INTO attachment "
                        "(report_id, filename, original_name, mime_type, "
                        "size_bytes, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (rid, att["filename"], att["original_name"],
                         att["mime_type"], att["size_bytes"], att["created_at"]),
                    )
                    src = archive.attachments_dir / str(rid) / att["filename"]
                    dest = attachments_root / str(rid) / att["filename"]
                    pending_files.append((src, dest))

    for src, dest in pending_files:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def import_from_zip(
    conn: sqlite3.Connection,
    attachments_root: Path,
    zip_path: Path,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp) / "extracted"
        extracted.mkdir()
        safe_extract_zip(zip_path, extracted)
        archive = load_archive(extracted)
        validate(archive)
        apply(conn, attachments_root, archive)


# ============================================================================
#  Helpers
# ============================================================================

def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _parse_delta(raw: str | None) -> dict:
    if not raw or not raw.strip():
        return {"ops": [{"insert": "\n"}]}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Corrupt Delta — return empty so the export remains usable instead
        # of crashing. The verify command will flag this row.
        return {"ops": [{"insert": "\n"}]}
