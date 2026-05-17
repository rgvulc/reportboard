"""Delta ↔ Markdown converter tests.

Two halves: direction-specific assertions (delta_to_md output is exact) and
round-trip assertions (md_to_delta(delta_to_md(d)) == canonical(d)).
"""

import pytest

from app.delta_md import (
    canonicalize_delta,
    delta_to_md,
    md_to_delta,
)


def D(*ops):
    """Convenience builder for Deltas."""
    return {"ops": list(ops)}


def text(t, **attrs):
    op = {"insert": t}
    if attrs:
        op["attributes"] = dict(sorted(attrs.items()))
    return op


def nl(**attrs):
    op = {"insert": "\n"}
    if attrs:
        op["attributes"] = dict(sorted(attrs.items()))
    return op


def image(url):
    return {"insert": {"image": url}}


def video(url):
    return {"insert": {"video": url}}


def formula(latex):
    return {"insert": {"formula": latex}}


# ============================================================================
#  Delta → Markdown
# ============================================================================

class TestDeltaToMd:
    def test_empty(self):
        assert delta_to_md(D()) == ""
        assert delta_to_md({"ops": []}) == ""

    def test_plain_paragraph(self):
        assert delta_to_md(D(text("Hello world"), nl())) == "Hello world"

    def test_two_paragraphs_separated_by_blank_line(self):
        assert delta_to_md(D(
            text("First"), nl(),
            text("Second"), nl(),
        )) == "First\n\nSecond"

    def test_heading_levels(self):
        for lvl in range(1, 7):
            md = delta_to_md(D(text("Title"), nl(header=lvl)))
            assert md == ("#" * lvl) + " Title"

    def test_bold_italic_strike_code(self):
        out = delta_to_md(D(
            text("plain "),
            text("b", bold=True), text(" "),
            text("i", italic=True), text(" "),
            text("s", strike=True), text(" "),
            text("c", code=True),
            nl(),
        ))
        assert out == "plain **b** *i* ~~s~~ `c`"

    def test_bold_italic_together(self):
        out = delta_to_md(D(text("x", bold=True, italic=True), nl()))
        # Innermost-first ordering: code → strike → italic → bold → link
        assert out == "***x***"

    def test_link_wraps_formatting(self):
        out = delta_to_md(D(
            text("see "),
            text("here", bold=True, link="https://example.com"),
            nl(),
        ))
        assert out == "see [**here**](https://example.com)"

    def test_bullet_list_block(self):
        out = delta_to_md(D(
            text("apple"), nl(list="bullet"),
            text("banana"), nl(list="bullet"),
            text("cherry"), nl(list="bullet"),
        ))
        assert out == "- apple\n- banana\n- cherry"

    def test_ordered_list_renumbers(self):
        out = delta_to_md(D(
            text("first"), nl(list="ordered"),
            text("second"), nl(list="ordered"),
            text("third"), nl(list="ordered"),
        ))
        assert out == "1. first\n2. second\n3. third"

    def test_blockquote(self):
        out = delta_to_md(D(
            text("quote line 1"), nl(blockquote=True),
            text("quote line 2"), nl(blockquote=True),
        ))
        assert out == "> quote line 1\n> quote line 2"

    def test_code_block_with_language(self):
        out = delta_to_md(D(
            text("def f():"), nl(**{"code-block": "python"}),
            text("    pass"), nl(**{"code-block": "python"}),
        ))
        assert out == "```python\ndef f():\n    pass\n```"

    def test_code_block_no_language(self):
        out = delta_to_md(D(
            text("raw text"), nl(**{"code-block": "plain"}),
        ))
        assert out == "```\nraw text\n```"

    def test_image_embed_is_own_block(self):
        out = delta_to_md(D(
            text("Before"), nl(),
            image("/attachments/1/foo.png"), nl(),
            text("After"), nl(),
        ))
        assert out == "Before\n\n![](/attachments/1/foo.png)\n\nAfter"

    def test_video_embed_uses_link_marker(self):
        out = delta_to_md(D(
            video("https://example.com/clip.mp4"), nl(),
        ))
        assert out == '[video](https://example.com/clip.mp4 "video-embed")'

    def test_formula_embed_renders_as_inline_math(self):
        out = delta_to_md(D(
            text("Einstein: "), formula("E=mc^2"), nl(),
        ))
        assert out == "Einstein: $E=mc^2$"

    def test_literal_dollar_in_body_text_is_escaped(self):
        out = delta_to_md(D(text("That costs $100"), nl()))
        assert out == "That costs \\$100"

    def test_special_characters_are_escaped(self):
        out = delta_to_md(D(text("a*b_c[d]"), nl()))
        assert "\\*" in out and "\\_" in out and "\\[" in out and "\\]" in out


# ============================================================================
#  Markdown → Delta
# ============================================================================

class TestMdToDelta:
    def test_empty(self):
        assert md_to_delta("") == {"ops": [{"insert": "\n"}]}

    def test_plain_paragraph(self):
        assert md_to_delta("Hello") == {"ops": [
            {"insert": "Hello\n"},
        ]}

    def test_bold(self):
        assert md_to_delta("**x**") == {"ops": [
            {"insert": "x", "attributes": {"bold": True}},
            {"insert": "\n"},
        ]}

    def test_heading(self):
        assert md_to_delta("## Title") == {"ops": [
            {"insert": "Title"},
            {"insert": "\n", "attributes": {"header": 2}},
        ]}

    def test_bullet_list(self):
        out = md_to_delta("- a\n- b")
        assert out == {"ops": [
            {"insert": "a"},
            {"insert": "\n", "attributes": {"list": "bullet"}},
            {"insert": "b"},
            {"insert": "\n", "attributes": {"list": "bullet"}},
        ]}

    def test_image_block(self):
        out = md_to_delta("![](/attachments/1/foo.png)")
        assert out == {"ops": [
            {"insert": {"image": "/attachments/1/foo.png"}},
            {"insert": "\n"},
        ]}

    def test_video_link_marker(self):
        out = md_to_delta('[video](https://example.com/clip.mp4 "video-embed")')
        assert out == {"ops": [
            {"insert": {"video": "https://example.com/clip.mp4"}},
            {"insert": "\n"},
        ]}

    def test_code_block_with_lang(self):
        out = md_to_delta("```python\ndef f():\n    pass\n```")
        assert out == {"ops": [
            {"insert": "def f():"},
            {"insert": "\n", "attributes": {"code-block": "python"}},
            {"insert": "    pass"},
            {"insert": "\n", "attributes": {"code-block": "python"}},
        ]}

    def test_inline_math(self):
        out = md_to_delta("Einstein: $E=mc^2$")
        assert out == {"ops": [
            {"insert": "Einstein: "},
            {"insert": {"formula": "E=mc^2"}},
            {"insert": "\n"},
        ]}

    def test_escaped_dollar_is_literal(self):
        """Escaped `\\$` doesn't open a math span — it's a literal dollar."""
        out = md_to_delta("That costs \\$100")
        assert out == {"ops": [
            {"insert": "That costs $100\n"},
        ]}


# ============================================================================
#  Round trips — the load-bearing tests
# ============================================================================

ROUND_TRIP_CASES = [
    pytest.param(D(text("Hello world"), nl()),
                 id="plain"),
    pytest.param(D(text("First"), nl(), text("Second"), nl()),
                 id="two paragraphs"),
    pytest.param(D(text("bold ", bold=True), text("normal "),
                    text("italic", italic=True), nl()),
                 id="bold+italic inline"),
    pytest.param(D(text("x", bold=True, italic=True), nl()),
                 id="bold+italic on same span"),
    pytest.param(D(text("strike", strike=True), nl()),
                 id="strikethrough"),
    pytest.param(D(text("inline ", ),
                    text("code", code=True), nl()),
                 id="inline code"),
    pytest.param(D(text("see "), text("here", link="https://example.com"),
                    text(" for details"), nl()),
                 id="link"),
    pytest.param(D(text("Title"), nl(header=1),
                    text("Body"), nl()),
                 id="heading + body"),
    pytest.param(D(text("a"), nl(list="bullet"),
                    text("b"), nl(list="bullet"),
                    text("c"), nl(list="bullet")),
                 id="bullet list"),
    pytest.param(D(text("one"), nl(list="ordered"),
                    text("two"), nl(list="ordered"),
                    text("three"), nl(list="ordered")),
                 id="ordered list"),
    pytest.param(D(text("q1"), nl(blockquote=True),
                    text("q2"), nl(blockquote=True)),
                 id="blockquote"),
    pytest.param(D(text("def f():"), nl(**{"code-block": "python"}),
                    text("    pass"), nl(**{"code-block": "python"})),
                 id="code block with language"),
    pytest.param(D(text("just code"), nl(**{"code-block": "plain"})),
                 id="code block no language"),
    pytest.param(D(text("Before"), nl(),
                    image("/attachments/1/foo.png"), nl(),
                    text("After"), nl()),
                 id="image embed surrounded by paragraphs"),
    pytest.param(D(video("https://example.com/clip.mp4"), nl()),
                 id="video embed"),
    pytest.param(D(text("Einstein: "), formula("E=mc^2"), nl()),
                 id="inline formula"),
    pytest.param(D(text("a "), formula("\\frac{1}{2}"),
                    text(" plus "), formula("\\sqrt{x}"), nl()),
                 id="two formulas in one line"),
    pytest.param(D(text("Buy at $100 then $200."), nl()),
                 id="dollar signs in body text are escaped, not math"),
    pytest.param(D(text("text with "), text("emphasised link", bold=True,
                                            link="https://example.com"),
                    text(" in it"), nl()),
                 id="link wrapping bold"),
    pytest.param(D(text("# header-looking text in paragraph"), nl()),
                 id="paragraph that starts with # is escaped"),
]


class TestRoundTrip:
    @pytest.mark.parametrize("delta", ROUND_TRIP_CASES)
    def test_delta_md_delta_is_canonical_identity(self, delta):
        md = delta_to_md(delta)
        back = md_to_delta(md)
        assert back == canonicalize_delta(delta), \
            f"\nMD: {md!r}\n got: {back}\nwant: {canonicalize_delta(delta)}"

    def test_video_link_marker_round_trips_url_intact(self):
        url = "https://example.com/path?x=1&y=2"
        d = D(video(url), nl())
        md = delta_to_md(d)
        assert url in md
        back = md_to_delta(md)
        assert back["ops"][0]["insert"] == {"video": url}

    def test_image_attachment_url_substring_preserved(self):
        """Attachment cleanup substring scan (`<id>/<filename>`) must keep
        working — the markdown form must still contain the URL substring."""
        d = D(image("/attachments/42/abc.png"), nl())
        md = delta_to_md(d)
        assert "42/abc.png" in md
