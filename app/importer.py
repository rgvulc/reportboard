"""Import a backup zip, replacing all current data.

Two-phase: parse + validate the entire archive into in-memory structures
first, raise `ImportError` on any inconsistency, and only then mutate the
live database. DB writes happen in a single transaction; attachment files
are copied into a staged directory and `os.replace`-d into the live
attachments root after the transaction commits.
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml


SUPPORTED_SCHEMA_VERSIONS = {1}

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_REQUIRED_REPORT_FIELDS = {
    "id", "board", "position", "title",
    "created_at", "updated_at", "tags", "checklist", "attachments",
}


class ImportError(Exception):
    """Raised when an archive fails validation. Message is user-safe."""


@dataclass
class ParsedReport:
    frontmatter: dict
    body: str
    attachment_files: dict[str, Path]   # filename -> source path on disk


@dataclass
class ParsedWorkspace:
    workspace_meta: dict
    reports: list[ParsedReport] = field(default_factory=list)


@dataclass
class ParsedArchive:
    manifest: dict
    workspaces: list[ParsedWorkspace] = field(default_factory=list)


# --- Pure helpers ---

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a `---\\n...\\n---\\n<body>` document.

    Raises ImportError if the document doesn't start with frontmatter or
    the YAML block isn't a mapping.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ImportError("report.md is missing YAML frontmatter")
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise ImportError(f"report.md frontmatter is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise ImportError("report.md frontmatter must be a YAML mapping")
    return data, m.group(2)


def rewrite_urls_for_import(content: str, report_id: int) -> str:
    """Reverse of exporter.rewrite_urls_for_export.

    Only rewrites the relative form (`attachments/<file>`) we emitted. We
    deliberately don't touch absolute `/attachments/...` URLs in the body,
    because they would have been rewritten on export.
    """
    return content.replace("attachments/", f"/attachments/{report_id}/")


# --- Zip extraction (zip-slip safe) ---

def safe_extract_zip(zip_path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(dest_resolved) + os.sep) \
                    and target != dest_resolved:
                raise ImportError(
                    f"Refusing to extract member outside dest: {member.filename!r}"
                )
        zf.extractall(dest)


# --- Parsing ---

def _find_archive_root(extracted: Path) -> Path:
    """Tolerate zips that wrap everything in a single top-level folder."""
    if (extracted / "manifest.json").exists():
        return extracted
    children = [p for p in extracted.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() \
            and (children[0] / "manifest.json").exists():
        return children[0]
    raise ImportError("manifest.json not found at the archive root")


def _load_manifest(root: Path) -> dict:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ImportError(f"manifest.json is not valid JSON: {e}")
    if not isinstance(manifest, dict):
        raise ImportError("manifest.json must be a JSON object")
    version = manifest.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ImportError(
            f"Unsupported schema_version {version!r}; "
            f"this build supports {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    for key in ("boards", "importance_levels", "tags", "workspaces"):
        if not isinstance(manifest.get(key), list):
            raise ImportError(f"manifest.json: {key!r} must be a list")
    return manifest


def _load_report(report_dir: Path) -> ParsedReport:
    md_path = report_dir / "report.md"
    if not md_path.exists():
        raise ImportError(f"missing report.md in {report_dir}")
    fm, body = parse_frontmatter(md_path.read_text(encoding="utf-8"))

    missing = _REQUIRED_REPORT_FIELDS - fm.keys()
    if missing:
        raise ImportError(
            f"report.md at {report_dir.name!r} missing fields: "
            f"{sorted(missing)}"
        )

    att_dir = report_dir / "attachments"
    attachment_files: dict[str, Path] = {}
    declared = fm.get("attachments") or []
    if not isinstance(declared, list):
        raise ImportError(f"attachments in {report_dir.name!r} must be a list")
    for entry in declared:
        if not isinstance(entry, dict) or "filename" not in entry:
            raise ImportError(
                f"attachment entry in {report_dir.name!r} missing 'filename'"
            )
        src = att_dir / entry["filename"]
        if not src.is_file():
            raise ImportError(
                f"attachment file missing on disk: "
                f"{report_dir.name}/attachments/{entry['filename']}"
            )
        attachment_files[entry["filename"]] = src

    return ParsedReport(frontmatter=fm, body=body, attachment_files=attachment_files)


def _load_workspace(ws_dir: Path) -> ParsedWorkspace:
    meta_path = ws_dir / "_workspace.json"
    if not meta_path.exists():
        raise ImportError(f"missing _workspace.json in {ws_dir.name!r}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ImportError(f"_workspace.json in {ws_dir.name!r}: {e}")
    for key in ("id", "name", "position", "created_at", "updated_at"):
        if key not in meta:
            raise ImportError(
                f"_workspace.json in {ws_dir.name!r} missing {key!r}"
            )

    parsed = ParsedWorkspace(workspace_meta=meta)
    for board_dir in sorted(p for p in ws_dir.iterdir() if p.is_dir()):
        for report_dir in sorted(p for p in board_dir.iterdir() if p.is_dir()):
            parsed.reports.append(_load_report(report_dir))
    return parsed


def load_archive(root: Path) -> ParsedArchive:
    archive_root = _find_archive_root(root)
    manifest = _load_manifest(archive_root)
    archive = ParsedArchive(manifest=manifest)
    workspaces_dir = archive_root / "workspaces"
    if workspaces_dir.exists():
        for ws_dir in sorted(p for p in workspaces_dir.iterdir() if p.is_dir()):
            archive.workspaces.append(_load_workspace(ws_dir))
    return archive


# --- Validation ---

def validate(archive: ParsedArchive) -> None:
    manifest = archive.manifest
    board_names = {b["name"] for b in manifest["boards"]}
    importance_names = {i["name"] for i in manifest["importance_levels"]}
    manifest_ws_ids = {w["id"] for w in manifest["workspaces"]}

    seen_report_ids: set[int] = set()
    seen_workspace_ids: set[int] = set()

    for ws in archive.workspaces:
        ws_id = ws.workspace_meta["id"]
        if ws_id in seen_workspace_ids:
            raise ImportError(f"duplicate workspace id {ws_id}")
        seen_workspace_ids.add(ws_id)
        if ws_id not in manifest_ws_ids:
            raise ImportError(
                f"workspace id {ws_id} not declared in manifest.workspaces"
            )

        for report in ws.reports:
            fm = report.frontmatter
            rid = fm["id"]
            if not isinstance(rid, int):
                raise ImportError(f"report.id must be an integer, got {rid!r}")
            if rid in seen_report_ids:
                raise ImportError(f"duplicate report id {rid}")
            seen_report_ids.add(rid)

            if fm["board"] not in board_names:
                raise ImportError(
                    f"report {rid}: board {fm['board']!r} not in manifest"
                )
            if fm.get("importance") is not None \
                    and fm["importance"] not in importance_names:
                raise ImportError(
                    f"report {rid}: importance {fm['importance']!r} not in manifest"
                )
            for tag in fm.get("tags") or []:
                if not isinstance(tag, str):
                    raise ImportError(
                        f"report {rid}: tag entries must be strings, got {tag!r}"
                    )

            checklist = fm.get("checklist") or []
            for item in checklist:
                if not isinstance(item, dict) \
                        or "text" not in item \
                        or "done" not in item \
                        or "position" not in item:
                    raise ImportError(
                        f"report {rid}: checklist entry missing required keys"
                    )


# --- Apply (replace mode) ---

def _wipe(conn: sqlite3.Connection, attachments_root: Path) -> None:
    # workspace cascade clears report, report_tag, checklist_item, attachment.
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
    archive: ParsedArchive,
) -> None:
    manifest = archive.manifest

    tag_id_by_name: dict[str, int] = {}
    importance_id_by_name: dict[str, int] = {}
    board_id_by_name: dict[str, int] = {}

    pending_files: list[tuple[Path, Path]] = []   # (src, dest)

    with conn:
        _wipe(conn, attachments_root)

        for b in manifest["boards"]:
            conn.execute(
                "INSERT INTO board (id, name, position) VALUES (?, ?, ?)",
                (b["id"], b["name"], b["position"]),
            )
            board_id_by_name[b["name"]] = b["id"]

        for i in manifest["importance_levels"]:
            conn.execute(
                "INSERT INTO importance_level (id, name, position) "
                "VALUES (?, ?, ?)",
                (i["id"], i["name"], i["position"]),
            )
            importance_id_by_name[i["name"]] = i["id"]

        for t in manifest["tags"]:
            conn.execute(
                "INSERT INTO tag (id, name) VALUES (?, ?)",
                (t["id"], t["name"]),
            )
            tag_id_by_name[t["name"].lower()] = t["id"]

        for ws in archive.workspaces:
            meta = ws.workspace_meta
            conn.execute(
                "INSERT INTO workspace (id, name, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (meta["id"], meta["name"], meta["position"],
                 meta["created_at"], meta["updated_at"]),
            )

            for report in ws.reports:
                fm = report.frontmatter
                rid = fm["id"]
                board_id = board_id_by_name[fm["board"]]
                importance_id = (
                    importance_id_by_name[fm["importance"]]
                    if fm.get("importance") is not None else None
                )
                rebuilt_content = rewrite_urls_for_import(report.body, rid)

                conn.execute(
                    "INSERT INTO report "
                    "(id, workspace_id, board_id, importance_id, title, "
                    "content, position, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, meta["id"], board_id, importance_id, fm["title"],
                     rebuilt_content, fm["position"],
                     fm["created_at"], fm["updated_at"]),
                )

                for tag_name in fm.get("tags") or []:
                    tag_id = tag_id_by_name.get(tag_name.lower())
                    if tag_id is None:
                        # Tag not declared in manifest; create it on the fly so
                        # imports remain forgiving when a report references a
                        # tag that was dropped from the manifest list.
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

                for item in fm.get("checklist") or []:
                    conn.execute(
                        "INSERT INTO checklist_item "
                        "(report_id, text, done, position) VALUES (?, ?, ?, ?)",
                        (rid, item["text"],
                         1 if item["done"] else 0, item["position"]),
                    )

                for entry in fm.get("attachments") or []:
                    conn.execute(
                        "INSERT INTO attachment "
                        "(report_id, filename, original_name, mime_type, "
                        "size_bytes, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (rid, entry["filename"], entry["original_name"],
                         entry["mime_type"], entry["size_bytes"],
                         entry["created_at"]),
                    )
                    src = report.attachment_files[entry["filename"]]
                    dest = attachments_root / str(rid) / entry["filename"]
                    pending_files.append((src, dest))

    # Transaction committed; copy attachment files into place.
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
