"""Unit tests for the pure functions in app.attachments.

These cover the cleanup contract — when files should be considered orphaned
versus referenced — without touching Flask, the DB, or the filesystem.
"""

from app.attachments import find_unreferenced, safe_extension, unique_filename


# --- find_unreferenced ---

def test_reference_present_keeps_file():
    content = "see ![pic](/attachments/1/abc.png)"
    assert find_unreferenced(1, content, ["abc.png"]) == []


def test_reference_absent_marks_orphan():
    content = "no attachments here"
    assert find_unreferenced(1, content, ["abc.png"]) == ["abc.png"]


def test_file_renamed_in_markdown_old_becomes_orphan_new_is_tolerated():
    # Simulates the user replacing one image reference with another.
    content = "![pic](/attachments/1/new.png)"
    # Both rows still exist in DB at this moment.
    orphans = find_unreferenced(1, content, ["old.png", "new.png"])
    assert orphans == ["old.png"]


def test_multiple_references_to_same_file_keep_it():
    content = (
        "![pic](/attachments/1/abc.png)\n\n"
        "see also (/attachments/1/abc.png)"
    )
    assert find_unreferenced(1, content, ["abc.png"]) == []


def test_cross_report_reference_does_not_keep_other_reports_file():
    # Report 1's content mentions report 2's file. Report 1's own file is
    # still orphaned because the substring "1/abc.png" is absent.
    content = "see [other](/attachments/2/abc.png)"
    assert find_unreferenced(1, content, ["abc.png"]) == ["abc.png"]


def test_image_link_and_bare_substring_all_count_as_references():
    content_image = "![](/attachments/1/abc.png)"
    content_link = "[file](/attachments/1/abc.pdf)"
    content_bare = "raw URL: /attachments/1/abc.zip in a code block"

    assert find_unreferenced(1, content_image, ["abc.png"]) == []
    assert find_unreferenced(1, content_link, ["abc.pdf"]) == []
    assert find_unreferenced(1, content_bare, ["abc.zip"]) == []


def test_idempotent_on_unchanged_content():
    content = "![pic](/attachments/1/abc.png)"
    first = find_unreferenced(1, content, ["abc.png"])
    second = find_unreferenced(1, content, ["abc.png"])
    assert first == second == []


def test_empty_filenames_returns_empty():
    assert find_unreferenced(1, "anything", []) == []


def test_empty_content_marks_all_as_orphans():
    assert find_unreferenced(1, "", ["a.png", "b.pdf"]) == ["a.png", "b.pdf"]


# --- safe_extension ---

def test_safe_extension_keeps_typical_extensions():
    assert safe_extension("photo.png") == ".png"
    assert safe_extension("doc.PDF") == ".pdf"
    assert safe_extension("archive.zip") == ".zip"


def test_safe_extension_handles_no_extension():
    assert safe_extension("README") == ""
    assert safe_extension("") == ""


def test_safe_extension_drops_bizarre_extensions():
    # Excessively long suffixes get rejected.
    assert safe_extension("foo." + "x" * 32) == ""
    # Non-alphanumeric suffixes get rejected.
    assert safe_extension("foo.tar.gz space") == ""


def test_safe_extension_strips_path_components():
    # Path traversal in the original name still yields just the extension.
    assert safe_extension("../../etc/passwd.png") == ".png"
    assert safe_extension("/etc/secrets") == ""


# --- unique_filename ---

def test_unique_filename_uses_uuid_and_keeps_extension():
    name = unique_filename("photo.png")
    assert name.endswith(".png")
    assert len(name) == 32 + len(".png")  # uuid hex is 32 chars


def test_unique_filename_collision_resistance():
    names = {unique_filename("x.png") for _ in range(200)}
    assert len(names) == 200


def test_unique_filename_drops_unsafe_extensions():
    name = unique_filename("evil.../passwd")
    assert "." not in name  # no extension preserved
