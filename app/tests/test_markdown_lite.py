"""Safe Markdown for the daily overviews (REQ-006)."""
import pytest

from markdown_lite import render_markdown


@pytest.mark.parametrize("source,expected", [
    ("**Neu:** drei Aufgaben", "<p><strong>Neu:</strong> drei Aufgaben</p>"),
    ("## Dringend\ntext", "<h4>Dringend</h4><p>text</p>"),
    ("- eins\n- zwei", "<ul><li>eins</li><li>zwei</li></ul>"),
    ("1. a\n2. b", "<ul><li>a</li><li>b</li></ul>"),
    ("a\n\nb", "<p>a</p><p>b</p>"),
    ("zeile eins\nzeile zwei", "<p>zeile eins<br>zeile zwei</p>"),
    ("*schief* und `code`", "<p><em>schief</em> und <code>code</code></p>"),
    ("", ""),
])
def test_markdown_renders_the_supported_subset(source, expected):
    assert render_markdown(source) == expected


@pytest.mark.parametrize("source", [
    "<script>alert(1)</script>", "**<img src=x onerror=alert(1)>**", "- <b>x</b>", "# <i>x</i>",
    "[link](javascript:alert(1))",
])
def test_markdown_never_lets_html_through(source):
    html = str(render_markdown(source))
    assert "<script" not in html and "<img" not in html and "<b>" not in html and "<i>" not in html
    assert "<a " not in html
