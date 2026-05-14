"""Canonical Delta ↔ Markdown converter.

Constrained to a feature subset that round-trips losslessly:

  Inline attributes:  bold, italic, strike, code, link
  Block attributes:   header (1-6), list (bullet | ordered), blockquote,
                      code-block (with optional language)
  Embeds:             image, video, formula (inline LaTeX)

Video embeds are preserved in markdown via a link with a sentinel title:

    [video](https://example.com/clip.mp4 "video-embed")

A markdown reader sees a labelled link to the video URL; `md_to_delta`
recognises the sentinel title and re-emits a video embed.

Formula embeds use the standard ``$...$`` math syntax. Literal ``$`` in
body text is escaped to ``\\$`` so it doesn't accidentally open a math
span on re-parse.

Both functions are deterministic. On the supported feature set,
`md_to_delta(delta_to_md(d))` equals `d` after canonicalisation
(attribute order, op merging, trailing newline).

This module intentionally does NOT handle:
  - underline / color / font / size / background / sub-super / align /
    direction (drop them at the editor / paste-matcher level)
  - indent (drop nested lists from the toolbar)
  - custom embeds beyond image/video
"""

import json
import re


VIDEO_LINK_TITLE = "video-embed"

# Attribute key sets — used for canonicalising op attributes.
_INLINE_ATTRS = ("bold", "code", "italic", "link", "strike")
_BLOCK_ATTRS = ("blockquote", "code-block", "header", "list")


# ============================================================================
#  Delta → Markdown
# ============================================================================

def delta_to_md(delta) -> str:
    """Convert a Quill Delta (dict or JSON string) to canonical markdown."""
    if isinstance(delta, str):
        delta = json.loads(delta) if delta.strip() else {"ops": []}

    # Stage 1: tokenize ops into a flat stream of segments.
    #   ('text', text, attrs) | ('embed', (kind, url), attrs) | ('newline', attrs)
    tokens = []
    for op in delta.get("ops", []):
        insert = op.get("insert", "")
        attrs = op.get("attributes") or {}
        if isinstance(insert, dict):
            if "image" in insert:
                tokens.append(("embed", ("image", insert["image"]), attrs))
            elif "video" in insert:
                tokens.append(("embed", ("video", insert["video"]), attrs))
            elif "formula" in insert:
                tokens.append(("embed", ("formula", insert["formula"]), attrs))
            # Unknown embeds dropped silently
            continue
        text = str(insert)
        if not text:
            continue
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if part:
                tokens.append(("text", part, attrs))
            if i < len(parts) - 1:
                tokens.append(("newline", attrs))

    # Stage 2: group tokens into logical lines: (inline_segments, line_attrs).
    lines = []
    current = []
    for tok in tokens:
        if tok[0] == "newline":
            lines.append((current, tok[1]))
            current = []
        else:
            current.append(tok)
    if current:
        lines.append((current, {}))

    # Stage 3: group consecutive same-kind lines into blocks; render each.
    blocks = []
    i = 0
    while i < len(lines):
        kind = _line_block_kind(lines[i][1])
        if kind[0] in ("list", "blockquote", "code-block"):
            j = i
            while j < len(lines) and _line_block_kind(lines[j][1]) == kind:
                j += 1
            blocks.append(_render_block(kind, lines[i:j]))
            i = j
        else:
            blocks.append(_render_block(kind, [lines[i]]))
            i += 1

    # Strip empty blocks (blank paragraphs) — canonical markdown uses a single
    # blank line between non-empty blocks.
    blocks = [b for b in blocks if b != ""]
    return "\n\n".join(blocks)


def _line_block_kind(line_attrs):
    """Tuple key describing a line's block class. Used to group consecutive
    lines into a single rendered block."""
    if line_attrs.get("code-block"):
        cb = line_attrs.get("code-block")
        if isinstance(cb, str) and cb not in ("true", "plain", ""):
            lang = cb
        else:
            lang = line_attrs.get("code-block-lang", "") or ""
        return ("code-block", lang)
    if line_attrs.get("list") in ("bullet", "ordered"):
        return ("list", line_attrs["list"])
    if line_attrs.get("blockquote"):
        return ("blockquote",)
    return ("para",)


def _render_block(kind, block_lines):
    if kind[0] == "code-block":
        lang = kind[1]
        body = "\n".join(
            _render_inline(segs, in_code=True) for segs, _ in block_lines
        )
        # Use a long-enough fence to escape any internal fences in the body.
        fence = _safe_code_fence(body)
        return f"{fence}{lang}\n{body}\n{fence}"

    if kind[0] == "list":
        list_type = kind[1]
        out = []
        for idx, (segs, _attrs) in enumerate(block_lines):
            inline = _render_inline(segs)
            prefix = "- " if list_type == "bullet" else f"{idx + 1}. "
            out.append(prefix + inline)
        return "\n".join(out)

    if kind[0] == "blockquote":
        return "\n".join(f"> {_render_inline(segs)}" for segs, _ in block_lines)

    segs, attrs = block_lines[0]
    inline = _render_inline(segs)
    if attrs.get("header"):
        n = max(1, min(6, int(attrs["header"])))
        return ("#" * n) + " " + inline
    return inline


def _safe_code_fence(body: str) -> str:
    """Return a fence of backticks longer than any run in `body`."""
    longest = 0
    cur = 0
    for ch in body:
        if ch == "`":
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    n = max(3, longest + 1)
    return "`" * n


# --- Inline rendering --------------------------------------------------------

# Characters that need escaping in regular markdown body text. The set is
# intentionally conservative — over-escaping is benign because the parser
# strips backslash-escapes. `$` is here so literal currency-style text
# doesn't accidentally open an inline-math span on re-parse.
_INLINE_ESCAPE_RE = re.compile(r"([\\`*_\[\]#>~!$])")


def _escape_md(text: str) -> str:
    return _INLINE_ESCAPE_RE.sub(r"\\\1", text)


def _wrap_inline_code(text: str) -> str:
    """Wrap text in just enough backticks to be valid inline code."""
    longest = 0
    cur = 0
    for ch in text:
        if ch == "`":
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    n = longest + 1
    fence = "`" * n
    pad_left = " " if text.startswith("`") else ""
    pad_right = " " if text.endswith("`") else ""
    return f"{fence}{pad_left}{text}{pad_right}{fence}"


def _render_inline(segments, in_code: bool = False) -> str:
    out = []
    for seg in segments:
        kind = seg[0]
        if kind == "embed":
            embed_kind, value = seg[1]
            if embed_kind == "image":
                out.append(f"![]({value})")
            elif embed_kind == "video":
                out.append(f'[video]({value} "{VIDEO_LINK_TITLE}")')
            elif embed_kind == "formula":
                # `value` is the LaTeX source; emit as inline math.
                out.append(f"${value}$")
            continue
        # text
        text = seg[1]
        attrs = seg[2]
        if in_code:
            out.append(text)  # no formatting / escaping inside code blocks
            continue
        # Apply formatting innermost → outermost: code, strike, italic, bold, link.
        if attrs.get("code"):
            text = _wrap_inline_code(text)
        else:
            text = _escape_md(text)
        if attrs.get("strike"):
            text = f"~~{text}~~"
        if attrs.get("italic"):
            text = f"*{text}*"
        if attrs.get("bold"):
            text = f"**{text}**"
        if attrs.get("link"):
            out.append(f"[{text}]({attrs['link']})")
        else:
            out.append(text)
    return "".join(out)


# ============================================================================
#  Markdown → Delta
# ============================================================================

def md_to_delta(md: str) -> dict:
    """Convert canonical markdown back to a Quill Delta dict."""
    md = (md or "").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not md:
        return {"ops": [{"insert": "\n"}]}

    ops: list[dict] = []
    blocks = _split_blocks(md)
    for block_text in blocks:
        _parse_block(block_text, ops)

    # Ensure trailing newline (Quill canonical form).
    if not ops:
        ops.append({"insert": "\n"})
    elif isinstance(ops[-1].get("insert"), str):
        if not ops[-1]["insert"].endswith("\n"):
            ops.append({"insert": "\n"})
    else:
        ops.append({"insert": "\n"})

    return {"ops": _normalize_ops(ops)}


def _split_blocks(md: str) -> list[str]:
    """Split markdown into blocks separated by blank lines, preserving
    code-fence contents (which may contain blank lines internally)."""
    blocks = []
    lines = md.split("\n")
    buf = []
    in_fence = False
    fence_marker = ""
    for line in lines:
        if not in_fence and line.startswith("```"):
            # Start of a fenced code block — flush any pending paragraph first.
            if buf:
                # Strip trailing blank lines from buf
                while buf and buf[-1] == "":
                    buf.pop()
                if buf:
                    blocks.append("\n".join(buf))
                buf = []
            buf.append(line)
            in_fence = True
            fence_marker = line.rstrip()
            continue
        if in_fence:
            buf.append(line)
            # End of fence when we see a line of backticks of equal length.
            if line.startswith("```") and len(line.rstrip()) >= len(fence_marker):
                blocks.append("\n".join(buf))
                buf = []
                in_fence = False
                fence_marker = ""
            continue
        if line.strip() == "":
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return blocks


_HEADING_RE = re.compile(r"^(#{1,6}) +(.*)$")
_BULLET_RE = re.compile(r"^- (.*)$")
_ORDERED_RE = re.compile(r"^\d+\. (.*)$")
_BLOCKQUOTE_RE = re.compile(r"^> ?(.*)$")
# Alt text is accepted but discarded — Quill's image embed has no alt slot.
_IMAGE_BLOCK_RE = re.compile(r"^!\[[^\]]*\]\(([^)\s]+)\)\s*$")
_VIDEO_BLOCK_RE = re.compile(r'^\[video\]\(([^)\s]+) "video-embed"\)\s*$')


def _parse_block(block_text: str, ops: list) -> None:
    """Append ops for one block."""
    lines = block_text.split("\n")

    # Fenced code block?
    if lines[0].startswith("```"):
        fence = lines[0][:len(lines[0]) - len(lines[0].lstrip("`"))]
        lang = lines[0][len(fence):].strip()
        # Body is everything between the opening and closing fence.
        body_lines = []
        for line in lines[1:]:
            if line.startswith("```") and len(line.rstrip()) >= len(fence):
                break
            body_lines.append(line)
        for i, line in enumerate(body_lines):
            if line:
                ops.append({"insert": line})
            attrs = {"code-block": lang if lang else "plain"}
            ops.append({"insert": "\n", "attributes": attrs})
        return

    # Image embed on its own line — must be a single-line block.
    if len(lines) == 1:
        m = _IMAGE_BLOCK_RE.match(lines[0])
        if m:
            ops.append({"insert": {"image": m.group(1)}})
            ops.append({"insert": "\n"})
            return
        m = _VIDEO_BLOCK_RE.match(lines[0])
        if m:
            ops.append({"insert": {"video": m.group(1)}})
            ops.append({"insert": "\n"})
            return

    # Heading (single-line block starting with #).
    if len(lines) == 1:
        m = _HEADING_RE.match(lines[0])
        if m:
            _emit_inline(m.group(2), ops)
            ops.append({"insert": "\n", "attributes": {"header": len(m.group(1))}})
            return

    # Bullet list — every line starts with "- ".
    if all(_BULLET_RE.match(line) for line in lines):
        for line in lines:
            _emit_inline(_BULLET_RE.match(line).group(1), ops)
            ops.append({"insert": "\n", "attributes": {"list": "bullet"}})
        return

    # Ordered list — every line is "N. text".
    if all(_ORDERED_RE.match(line) for line in lines):
        for line in lines:
            _emit_inline(_ORDERED_RE.match(line).group(1), ops)
            ops.append({"insert": "\n", "attributes": {"list": "ordered"}})
        return

    # Blockquote — every line starts with "> " (or just ">").
    if all(_BLOCKQUOTE_RE.match(line) for line in lines):
        for line in lines:
            _emit_inline(_BLOCKQUOTE_RE.match(line).group(1), ops)
            ops.append({"insert": "\n", "attributes": {"blockquote": True}})
        return

    # Default: a paragraph. Multi-line paragraphs join with hard newlines that
    # don't carry block attributes — represented by a plain "\n" insert.
    for i, line in enumerate(lines):
        _emit_inline(line, ops)
        ops.append({"insert": "\n"})


# --- Inline parsing ----------------------------------------------------------

def _emit_inline(text: str, ops: list) -> None:
    for kind, payload, attrs in _parse_inline(text):
        op: dict = {"insert": payload}
        if attrs:
            op["attributes"] = dict(sorted(attrs.items()))
        ops.append(op)


def _parse_inline(text: str) -> list:
    """Parse a string of inline markdown into a list of
    (kind, payload, attrs) tuples. kind is 'text' or 'embed'."""
    result = []
    i = 0
    n = len(text)
    while i < n:
        # 1. Image:  ![alt](url) — alt text accepted but discarded.
        m = re.match(r"!\[[^\]]*\]\(([^)\s]+)\)", text[i:])
        if m:
            result.append(("embed", {"image": m.group(1)}, {}))
            i += m.end()
            continue
        # 1b. Inline math: $...$ — content cannot contain $ or newline.
        m = re.match(r"\$([^$\n]+)\$", text[i:])
        if m:
            result.append(("embed", {"formula": m.group(1)}, {}))
            i += m.end()
            continue
        # 2. Video link with sentinel title.
        m = re.match(r'\[video\]\(([^)\s]+) "video-embed"\)', text[i:])
        if m:
            result.append(("embed", {"video": m.group(1)}, {}))
            i += m.end()
            continue
        # 3. Link:  [text](url)
        m = re.match(r"\[((?:[^\]\\]|\\.)*)\]\(([^)\s]+)\)", text[i:])
        if m:
            inner = _parse_inline(m.group(1))
            url = m.group(2)
            for sub_kind, sub_payload, sub_attrs in inner:
                merged = dict(sub_attrs)
                merged["link"] = url
                result.append((sub_kind, sub_payload, merged))
            i += m.end()
            continue
        # 4. Inline code (with possibly multi-backtick fences). Try longest first.
        m = _match_inline_code(text, i)
        if m is not None:
            content, end = m
            result.append(("text", content, {"code": True}))
            i = end
            continue
        # 5. Bold + italic together: ***text***
        m = re.match(r"\*\*\*((?:[^*\\]|\\.)+)\*\*\*", text[i:])
        if m:
            inner = _parse_inline(m.group(1))
            for sk, sp, sa in inner:
                merged = dict(sa)
                merged["bold"] = True
                merged["italic"] = True
                result.append((sk, sp, merged))
            i += m.end()
            continue
        # 6. Bold: **text**
        m = re.match(r"\*\*((?:[^*\\]|\\.)+)\*\*", text[i:])
        if m:
            inner = _parse_inline(m.group(1))
            for sk, sp, sa in inner:
                merged = dict(sa)
                merged["bold"] = True
                result.append((sk, sp, merged))
            i += m.end()
            continue
        # 7. Italic: *text*
        m = re.match(r"\*((?:[^*\\]|\\.)+)\*", text[i:])
        if m:
            inner = _parse_inline(m.group(1))
            for sk, sp, sa in inner:
                merged = dict(sa)
                merged["italic"] = True
                result.append((sk, sp, merged))
            i += m.end()
            continue
        # 8. Strike: ~~text~~
        m = re.match(r"~~((?:[^~\\]|\\.)+)~~", text[i:])
        if m:
            inner = _parse_inline(m.group(1))
            for sk, sp, sa in inner:
                merged = dict(sa)
                merged["strike"] = True
                result.append((sk, sp, merged))
            i += m.end()
            continue
        # 9. Escaped character: backslash + next char is a literal.
        if text[i] == "\\" and i + 1 < n:
            result.append(("text", text[i + 1], {}))
            i += 2
            continue
        # 10. Plain character.
        result.append(("text", text[i], {}))
        i += 1
    return _merge_text_runs(result)


def _match_inline_code(text: str, i: int):
    """Match inline code starting at `text[i]` (one or more backticks).
    Returns (content, end_index) or None."""
    if text[i] != "`":
        return None
    j = i
    while j < len(text) and text[j] == "`":
        j += 1
    fence = text[i:j]  # the opening run of backticks
    # Find a closing run of exactly the same length.
    end = j
    while True:
        k = text.find(fence, end)
        if k == -1:
            return None
        # Closing fence must not be part of a longer run.
        if k + len(fence) < len(text) and text[k + len(fence)] == "`":
            end = k + 1
            continue
        content = text[j:k]
        # Strip a single pad space on each side (the wrap function adds them
        # when the content starts/ends with a backtick).
        if content.startswith(" ") and content.endswith(" ") and content.strip(" "):
            content = content[1:-1]
        return content, k + len(fence)


def _merge_text_runs(segments: list) -> list:
    """Merge consecutive text segments with identical attributes."""
    if not segments:
        return segments
    out = [segments[0]]
    for seg in segments[1:]:
        last = out[-1]
        if (last[0] == "text" and seg[0] == "text" and last[2] == seg[2]):
            out[-1] = ("text", last[1] + seg[1], last[2])
        else:
            out.append(seg)
    return out


# ============================================================================
#  Canonicalisation
# ============================================================================

def _normalize_ops(ops: list[dict]) -> list[dict]:
    """Canonicalise a list of ops: drop empty attribute dicts, sort attribute
    keys, and merge contiguous same-formatted text inserts."""
    cleaned = []
    for op in ops:
        op = dict(op)
        if "attributes" in op:
            if not op["attributes"]:
                del op["attributes"]
            else:
                op["attributes"] = dict(sorted(op["attributes"].items()))
        cleaned.append(op)
    merged = []
    for op in cleaned:
        if not merged:
            merged.append(op)
            continue
        last = merged[-1]
        if (isinstance(last.get("insert"), str)
                and isinstance(op.get("insert"), str)
                and last.get("attributes") == op.get("attributes")):
            last["insert"] = last["insert"] + op["insert"]
        else:
            merged.append(op)
    return merged


def canonicalize_delta(delta) -> dict:
    """Public canonicaliser — useful for normalising client-supplied Deltas
    before storage so byte-for-byte comparisons are meaningful."""
    if isinstance(delta, str):
        delta = json.loads(delta) if delta.strip() else {"ops": []}
    ops = delta.get("ops", [])
    return {"ops": _normalize_ops(ops)}
