"""Tiny, safe Markdown renderer for overviews, recording summaries and task details (REQ-006, D-033).

Text is HTML-escaped first, then headings, paragraphs, bullet and numbered lists (nested by
indentation), task boxes, quotes, rules, simple tables, bold, italics and inline code are turned
into tags. Nothing else is interpreted, so no raw HTML, links or images get through.
"""
import re

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^\s*&gt;\s?(.*)$")  # the line is escaped already
_ROW = re.compile(r"^\s*\|.*\|\s*$")
_DIVIDER = re.compile(r"^\s*\|?(\s*:?-{2,}:?\s*\|)+\s*:?-*:?\s*\|?\s*$")
_TRAILING_RULE = re.compile(r"\s+-{3,}\s*$")  # "Teilnehmer: A, B ---" as Plaud writes it


def _inline(text: str) -> str:
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


def _item_html(text: str) -> str:
    task = _TASK.match(text)
    if task:
        done = task.group(1).lower() == "x"
        return f'<span class="box{" done" if done else ""}">{"☑" if done else "☐"}</span> {_inline(task.group(2))}'
    return _inline(text)


def _lists(items: list) -> str:
    """items: (indent, ordered, html). Deeper indentation nests inside the previous item."""
    out, stack = [], []  # stack of (indent, tag)
    for indent, ordered, html in items:
        tag = "ol" if ordered else "ul"
        while stack and indent < stack[-1][0]:
            out.append(f"</li></{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            out.append(f"<{tag}><li>")
            stack.append((indent, tag))
        else:
            out.append("</li><li>")
        out.append(html)
    while stack:
        out.append(f"</li></{stack.pop()[1]}>")
    return "".join(out)


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(rows: list) -> str:
    head, body = rows[0], rows[2:] if len(rows) > 1 and _DIVIDER.match(rows[1]) else rows[1:]
    html = "<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in _cells(head)) + "</tr></thead><tbody>"
    for row in body:
        html += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(row)) + "</tr>"
    return html + "</tbody></table>"


def _blocks(lines: list) -> str:
    out, paragraph, items, quote, table = [], [], [], [], []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            out.append(_lists(items))
            items.clear()
        if quote:
            out.append("<blockquote>" + _blocks(quote) + "</blockquote>")
            quote.clear()
        if table:
            out.append(_table(table))
            table.clear()

    for line in lines:
        quoted = _QUOTE.match(line)
        if quoted:
            if not quote:
                flush()
            quote.append(quoted.group(1))
            continue
        if quote:
            flush()
        if _ROW.match(line):
            if not table:
                flush()
            table.append(line)
            continue
        if table:
            flush()
        heading, item = _HEADING.match(line), _ITEM.match(line)
        if not line.strip():
            flush()
        elif _RULE.match(line):
            flush()
            out.append("<hr>")
        elif heading:
            flush()
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif item:
            if paragraph:
                flush()
            indent = len(item.group(1).expandtabs(4))
            items.append((indent, item.group(2)[0].isdigit(), _item_html(item.group(3))))
        elif items and line.startswith((" ", "\t")):
            indent, ordered, html = items[-1]  # a wrapped line of the last item
            items[-1] = (indent, ordered, html + " " + _inline(line.strip()))
        else:
            if items:
                flush()
            paragraph.append(_inline(line.strip()))
    flush()
    return "".join(out)


def render_markdown(source: str) -> Markup:
    lines = [str(escape(raw.rstrip())) for raw in str(source or "").splitlines()]
    trailing = []
    for line in lines:
        if _TRAILING_RULE.search(line) and not _RULE.match(line):
            trailing += [_TRAILING_RULE.sub("", line), "---"]
        else:
            trailing.append(line)
    return Markup(_blocks(trailing))
