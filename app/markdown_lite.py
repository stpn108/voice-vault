"""Tiny, safe Markdown renderer for the daily overviews (REQ-006).

Text is HTML-escaped first, then headings, bullet lists, bold, italics and inline code are
turned into tags. Nothing else is interpreted, so no raw HTML, links or images get through.
"""
import re

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$")


def _inline(text: str) -> str:
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


def render_markdown(source: str) -> Markup:
    out, paragraph, items = [], [], []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for raw in str(source).splitlines():
        line = str(escape(raw.rstrip()))
        heading, bullet = _HEADING.match(line), _BULLET.match(line)
        if not line.strip():
            flush()
        elif heading:
            flush()
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet:
            if paragraph:
                flush()
            items.append(_inline(bullet.group(1)))
        else:
            if items:
                flush()
            paragraph.append(_inline(line))
    flush()
    return Markup("".join(out))
