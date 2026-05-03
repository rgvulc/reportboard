"""Attachment storage and cleanup helpers.

Pure functions (no Flask context) live here so they can be unit-tested without
fixtures. The IO helpers below need an active Flask app context because the
storage root comes from `current_app.config["ATTACHMENTS_DIR"]`.

Cleanup model: an attachment is "referenced" if the substring
`<report_id>/<filename>` appears anywhere in the report's saved markdown
content. The substring is format-agnostic — it matches `![alt](path)`,
`[name](path)`, or a bare URL inside a code block — and the report-id prefix
prevents a reference to another report's file from masking the orphan.
"""

import re
import shutil
import uuid
from pathlib import Path

from flask import current_app


_VALID_EXT_RE = re.compile(r"^\.[a-z0-9]{1,16}$")


# --- Pure functions (no Flask context) ---

def safe_extension(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    return suffix if _VALID_EXT_RE.fullmatch(suffix) else ""


def unique_filename(original_name: str) -> str:
    return f"{uuid.uuid4().hex}{safe_extension(original_name)}"


def find_unreferenced(
    report_id: int, content: str, filenames: list[str]
) -> list[str]:
    """Filenames whose `<report_id>/<filename>` substring is absent from content."""
    return [f for f in filenames if f"{report_id}/{f}" not in content]


# --- Flask-aware IO helpers ---

def report_dir(report_id: int) -> Path:
    return Path(current_app.config["ATTACHMENTS_DIR"]) / str(report_id)


def attachment_path(report_id: int, filename: str) -> Path:
    return report_dir(report_id) / filename


def store_upload(report_id: int, file_storage) -> tuple[str, int]:
    """Save the upload under the per-report directory and return (filename, size)."""
    filename = unique_filename(file_storage.filename or "")
    target_dir = report_dir(report_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    file_storage.save(str(target))
    return filename, target.stat().st_size


def delete_files(report_id: int, filenames: list[str]) -> None:
    for fname in filenames:
        try:
            attachment_path(report_id, fname).unlink(missing_ok=True)
        except OSError:
            pass


def delete_report_directory(report_id: int) -> None:
    target = report_dir(report_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
