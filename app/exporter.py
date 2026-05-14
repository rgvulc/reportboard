"""Export the database to a folder of markdown files (or a zip thereof).

The exporter is structured as a small set of pure helpers (slug generation,
URL rewriting, frontmatter rendering) and three orchestrators that walk the
database. The orchestrators take an open `sqlite3.Connection` so the export
can run inside a request handler, a CLI command, or a test without touching
the Flask context.

On-disk layout:

    manifest.json
    workspaces/
      <NNN>-<workspace-slug>/
        _workspace.json
        <board-slug>/
          <NNN>-<report-slug>/
            report.md            (YAML frontmatter + body)
            attachments/<uuid>.<ext>

Attachment URLs in the body are rewritten from the absolute runtime form
(`/attachments/<id>/<file>`) to the relative form (`attachments/<file>`).
The importer reverses that exactly so the substring-match cleanup invariant
in `attachments.find_unreferenced` keeps holding after a round-trip.
"""

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .delta_md import delta_to_md


# v3 (current): workspace dir contains _workspace.json + flat .md files +
#               a single attachments/ folder whose subfolders are named by
#               report id. Body URLs are `attachments/<id>/<file>`.
# v2 (legacy):  ws_dir/<NNN>-<slug>/{report.md, attachments/<file>}.
# v1 (legacy):  ws_dir/<board>/<NNN>-<slug>/{report.md, attachments/<file>}.
SCHEMA_VERSION = 3

_SLUG_MAX_LEN = 60
_SLUG_BAD_CHARS_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_LEADING_TRAILING_DASHES_RE = re.compile(r"^[-_.]+|[-_.]+$")


# --- Pure helpers ---

def slugify(name: str, max_len: int = _SLUG_MAX_LEN) -> str:
    """Filesystem-safe slug.

    ASCII-folds, replaces runs of unsafe chars with `-`, trims, and caps
    length. If the slug ends up empty (e.g. all-emoji name) or had to be
    truncated, a short hash of the original is appended to preserve
    uniqueness across collisions caused by the slug step itself.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = _SLUG_BAD_CHARS_RE.sub("-", folded)
    cleaned = _LEADING_TRAILING_DASHES_RE.sub("", cleaned)

    needs_hash = not cleaned or len(cleaned) > max_len
    if needs_hash:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        if not cleaned:
            return digest
        return f"{cleaned[: max_len - 9].rstrip('-_.')}-{digest}"
    return cleaned


def unique_dirname(parent: Path, base: str) -> str:
    """Pick a directory name under `parent` that doesn't already exist.

    Appends `-2`, `-3`, ... if needed. The exporter creates parents first,
    so `parent` always exists when this is called.
    """
    candidate = base
    n = 2
    while (parent / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def rewrite_urls_for_export(content: str) -> str:
    """Strip the leading slash from attachment URLs so they're relative to
    the workspace folder. `/attachments/<id>/<file>` → `attachments/<id>/<file>`."""
    return content.replace("/attachments/", "attachments/")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def render_report_md(
    *,
    report: dict,
    board_name: str,
    importance_name: str | None,
    tags: list[str],
    checklist: list[dict],
    attachments: list[dict],
) -> str:
    """Build the report.md text (frontmatter + body)."""
    frontmatter = {
        "id": report["id"],
        "board": board_name,
        "importance": importance_name,
        "position": report["position"],
        "title": report["title"],
        "created_at": report["created_at"],
        "updated_at": report["updated_at"],
        "tags": tags,
        "checklist": checklist,
        "attachments": attachments,
    }
    # Delta is canonical storage; render to markdown for the export.
    delta_json = report.get("content_delta") or ""
    markdown_body = delta_to_md(delta_json) if delta_json.strip() else ""
    body = rewrite_urls_for_export(markdown_body)
    yaml_text = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{yaml_text}---\n{body}"


# --- Orchestrators ---

def write_manifest(conn: sqlite3.Connection, dest: Path) -> None:
    boards = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name, position FROM board ORDER BY position, id"
    )]
    importance_levels = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name, position FROM importance_level ORDER BY position, id"
    )]
    tags = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name FROM tag ORDER BY id"
    )]
    workspaces = [_row_to_dict(r) for r in conn.execute(
        "SELECT id, name, position, created_at, updated_at "
        "FROM workspace ORDER BY position, id"
    )]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "boards": boards,
        "importance_levels": importance_levels,
        "tags": tags,
        "workspaces": workspaces,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _board_lookup(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM board")}


def _importance_lookup(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM importance_level"
    )}


def _report_payload(conn: sqlite3.Connection, report_id: int) -> tuple[list[str], list[dict], list[dict]]:
    tags = [r["name"] for r in conn.execute(
        "SELECT t.name FROM tag t JOIN report_tag rt ON rt.tag_id = t.id "
        "WHERE rt.report_id = ? ORDER BY t.name COLLATE NOCASE",
        (report_id,),
    )]
    checklist = [_row_to_dict(r) for r in conn.execute(
        "SELECT text, done, position FROM checklist_item "
        "WHERE report_id = ? ORDER BY position, id",
        (report_id,),
    )]
    for item in checklist:
        item["done"] = bool(item["done"])
    attachments = [_row_to_dict(r) for r in conn.execute(
        "SELECT filename, original_name, mime_type, size_bytes, created_at "
        "FROM attachment WHERE report_id = ? ORDER BY filename",
        (report_id,),
    )]
    return tags, checklist, attachments


def write_workspace(
    conn: sqlite3.Connection,
    workspace: dict,
    dest_root: Path,
    attachments_root: Path,
    board_names: dict[int, str],
    importance_names: dict[int, str],
) -> None:
    """Write a single workspace tree under dest_root/workspaces/."""
    workspaces_dir = dest_root / "workspaces"
    workspaces_dir.mkdir(parents=True, exist_ok=True)

    ws_dirname = unique_dirname(
        workspaces_dir,
        f"{workspace['position']:03d}-{slugify(workspace['name'])}",
    )
    ws_dir = workspaces_dir / ws_dirname
    ws_dir.mkdir()

    (ws_dir / "_workspace.json").write_text(
        json.dumps(workspace, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Ordered by board column then position-within-column so the filesystem
    # listing matches the natural kanban reading order.
    reports = conn.execute(
        "SELECT r.id, r.workspace_id, r.board_id, r.importance_id, r.title, "
        "r.content_delta, r.position, r.created_at, r.updated_at "
        "FROM report r JOIN board b ON b.id = r.board_id "
        "WHERE r.workspace_id = ? "
        "ORDER BY b.position, b.id, r.position, r.id",
        (workspace["id"],),
    ).fetchall()

    # All attachments for this workspace go under one shared folder, with
    # per-report subdirs named by report id (stable across renames).
    ws_attachments_dir = ws_dir / "attachments"

    for idx, r in enumerate(reports):
        report = _row_to_dict(r)
        board_name = board_names[report["board_id"]]
        importance_name = (
            importance_names.get(report["importance_id"])
            if report["importance_id"] is not None else None
        )

        md_filename = unique_dirname(
            ws_dir,
            f"{idx:03d}-{slugify(report['title'])}.md",
        )

        tags, checklist, attachments = _report_payload(conn, report["id"])

        md_text = render_report_md(
            report=report,
            board_name=board_name,
            importance_name=importance_name,
            tags=tags,
            checklist=checklist,
            attachments=attachments,
        )
        (ws_dir / md_filename).write_text(md_text, encoding="utf-8")

        if attachments:
            dest_subdir = ws_attachments_dir / str(report["id"])
            dest_subdir.mkdir(parents=True, exist_ok=True)
            src_dir = attachments_root / str(report["id"])
            for a in attachments:
                src = src_dir / a["filename"]
                if src.exists():
                    shutil.copy2(src, dest_subdir / a["filename"])


def export_to_dir(
    conn: sqlite3.Connection,
    attachments_root: Path,
    dest: Path,
) -> None:
    """Write the export tree into `dest` (must already exist and be empty)."""
    write_manifest(conn, dest)
    board_names = _board_lookup(conn)
    importance_names = _importance_lookup(conn)
    workspaces = conn.execute(
        "SELECT id, name, position, created_at, updated_at "
        "FROM workspace ORDER BY position, id"
    ).fetchall()
    for ws in workspaces:
        write_workspace(
            conn, _row_to_dict(ws), dest, attachments_root,
            board_names, importance_names,
        )


def export_to_zip(
    conn: sqlite3.Connection,
    attachments_root: Path,
    zip_path: Path,
) -> None:
    """Export to a zip file at `zip_path`."""
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "export"
        staging.mkdir()
        export_to_dir(conn, attachments_root, staging)

        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(staging))
