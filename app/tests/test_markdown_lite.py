"""Safe Markdown for the daily overviews (REQ-006)."""
import pytest

from markdown_lite import render_markdown


@pytest.mark.parametrize("source,expected", [
    ("**Neu:** drei Aufgaben", "<p><strong>Neu:</strong> drei Aufgaben</p>"),
    ("## Dringend\ntext", "<h4>Dringend</h4><p>text</p>"),
    ("- eins\n- zwei", "<ul><li>eins</li><li>zwei</li></ul>"),
    ("1. a\n2. b", "<ol><li>a</li><li>b</li></ol>"),
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


@pytest.mark.parametrize("source,expected", [
    ("- a\n  - b\n  - c\n- d", "<ul><li>a<ul><li>b</li><li>c</li></ul></li><li>d</li></ul>"),
    ("- [ ] offen\n- [x] fertig", '<ul><li><span class="box">☐</span> offen</li><li><span class="box done">☑</span> fertig</li></ul>'),
    ("> **Datum:** heute\n> Ort: hier", "<blockquote><p><strong>Datum:</strong> heute<br>Ort: hier</p></blockquote>"),
    ("> 1. eins\n> 2. zwei", "<blockquote><ol><li>eins</li><li>zwei</li></ol></blockquote>"),
    ("a\n---\nb", "<p>a</p><hr><p>b</p>"),
    ("Teilnehmer: A, B ---\n## Notizen", "<p>Teilnehmer: A, B</p><hr><h4>Notizen</h4>"),
    ("| A | B |\n|---|---|\n| 1 | **2** |", "<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td><strong>2</strong></td></tr></tbody></table>"),
    ("### 1. Titel", "<h5>1. Titel</h5>"),
])
def test_d033_markdown_covers_what_plaud_summaries_use(source, expected):
    assert render_markdown(source) == expected


@pytest.mark.parametrize("source", ["> <script>x</script>", "| <img src=x> | b |", "- [ ] <b>x</b>"])
def test_d033_new_blocks_still_escape_html(source):
    html = str(render_markdown(source))
    assert "<script" not in html and "<img" not in html and "<b>" not in html
