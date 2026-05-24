"""Inline SVG horizontal bar chart renderer for article markdown.

Claude emits fenced ```chart blocks in article markdown. This module parses
those blocks and renders them as inline SVG wrapped in a <figure> element.

Integration with publish.py:
  1. Call preprocess_charts(md) to extract chart blocks and replace them with
     CHARTBLOCK_N placeholders. Returns (modified_md, [rendered_svg_html, ...]).
  2. Run md_to_html(modified_md) on the placeholder-bearing markdown.
  3. Call inject_charts(html, rendered) to swap placeholders back in.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_chart_block(block_text: str) -> dict:
    """Parse a raw ```chart block body into a structured dict.

    Args:
        block_text: Everything between the opening ```chart and closing ```
                    fence markers (excluding the fence lines themselves).

    Returns:
        {
            "title": str,          # from `title: ...` line, defaults to ""
            "unit":  str,          # from `unit: ...` line, defaults to ""
            "rows":  [(label, float), ...],  # remaining Label: value lines
        }
    """
    title = ""
    unit = ""
    rows: list[tuple[str, float]] = []

    for raw_line in block_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.lower().startswith("title:"):
            title = line[len("title:"):].strip()
        elif line.lower().startswith("unit:"):
            unit = line[len("unit:"):].strip()
        else:
            # Expect "Label: value" — last colon-delimited segment is the number
            if ":" in line:
                idx = line.rfind(":")
                label = line[:idx].strip()
                value_str = line[idx + 1:].strip()
                try:
                    value = float(value_str)
                    rows.append((label, value))
                except ValueError:
                    # Not a data row — skip silently
                    pass

    return {"title": title, "unit": unit, "rows": rows}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Design constants
_BG_COLOR = "#faf7f0"
_BAR_COLOR = "#c9b27a"
_TRACK_COLOR = "#f0ebe0"
_FONT = "system-ui, sans-serif"

_LABEL_WIDTH = 170   # px — left column for labels
_BAR_AREA = 320      # px — right column for bars
_BAR_HEIGHT = 26     # px
_ROW_GAP = 10        # px — vertical gap between bar rows
_TITLE_H = 26        # px — vertical space consumed by title text row
_UNIT_H = 18         # px — vertical space consumed by unit text row
_TOP_PAD = 12        # px — padding above title
_BOTTOM_PAD = 16     # px — padding below last bar
_LEFT_PAD = 16       # px — padding left of label column
_RIGHT_PAD = 16      # px — padding right of bar area
_VALUE_GAP = 6       # px — gap between bar end and value label


def _escape_xml(s: str) -> str:
    """Minimal XML attribute / text content escaping."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def render_chart_html(chart: dict) -> str:
    """Render a parsed chart dict into a <figure> wrapping an inline SVG.

    Args:
        chart: Output of parse_chart_block().

    Returns:
        A self-contained HTML string starting with <figure ...>.
    """
    title: str = chart.get("title", "")
    unit: str = chart.get("unit", "")
    rows: list[tuple[str, float]] = chart.get("rows", [])

    # Nothing to render
    if not rows:
        return ""

    max_value = max(v for _, v in rows) or 1.0

    # Compute total SVG height
    header_h = _TOP_PAD
    if title:
        header_h += _TITLE_H
    if unit:
        header_h += _UNIT_H
    if title or unit:
        header_h += 8  # gap between header block and first bar

    n = len(rows)
    bars_h = n * _BAR_HEIGHT + max(0, n - 1) * _ROW_GAP
    total_h = header_h + bars_h + _BOTTOM_PAD

    # Viewbox / canvas width
    total_w = _LEFT_PAD + _LABEL_WIDTH + _BAR_AREA + _RIGHT_PAD

    svg_parts: list[str] = []
    svg_parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" '
        f'width="100%" '
        f'style="display:block;font-family:{_FONT};">'
    )

    # Background
    svg_parts.append(
        f'<rect width="{total_w}" height="{total_h}" '
        f'fill="{_BG_COLOR}"/>'
    )

    # Title
    y_cursor = _TOP_PAD
    if title:
        y_cursor += _TITLE_H - 6  # baseline offset so text sits inside the row
        svg_parts.append(
            f'<text x="{total_w / 2:.1f}" y="{y_cursor}" '
            f'text-anchor="middle" '
            f'font-size="15" font-weight="600" fill="#222">'
            f'{_escape_xml(title)}</text>'
        )
        y_cursor += 6  # bring cursor back to bottom of title slot

    # Unit
    if unit:
        y_cursor += _UNIT_H - 4
        svg_parts.append(
            f'<text x="{total_w / 2:.1f}" y="{y_cursor}" '
            f'text-anchor="middle" '
            f'font-size="11" fill="#888">'
            f'{_escape_xml(unit)}</text>'
        )
        y_cursor += 4

    if title or unit:
        y_cursor += 8  # gap before first bar

    # Bars
    bar_x = _LEFT_PAD + _LABEL_WIDTH  # x position where bars start

    for idx, (label, value) in enumerate(rows):
        bar_top = y_cursor + idx * (_BAR_HEIGHT + _ROW_GAP)
        bar_mid_y = bar_top + _BAR_HEIGHT / 2  # vertical midpoint of this row

        # Label (right-aligned into label column)
        label_y = bar_mid_y + 4  # +4 for rough baseline centering at 13px
        svg_parts.append(
            f'<text x="{_LEFT_PAD + _LABEL_WIDTH - 8}" y="{label_y:.1f}" '
            f'text-anchor="end" '
            f'font-size="13" fill="#444">'
            f'{_escape_xml(label)}</text>'
        )

        # Track
        svg_parts.append(
            f'<rect x="{bar_x}" y="{bar_top:.1f}" '
            f'width="{_BAR_AREA}" height="{_BAR_HEIGHT}" '
            f'fill="{_TRACK_COLOR}" rx="3"/>'
        )

        # Bar
        bar_w = max(4.0, (value / max_value) * _BAR_AREA)
        svg_parts.append(
            f'<rect x="{bar_x}" y="{bar_top:.1f}" '
            f'width="{bar_w:.1f}" height="{_BAR_HEIGHT}" '
            f'fill="{_BAR_COLOR}" rx="3"/>'
        )

        # Value label after bar
        value_x = bar_x + bar_w + _VALUE_GAP
        value_str = str(int(value)) if value == int(value) else f"{value:g}"
        svg_parts.append(
            f'<text x="{value_x:.1f}" y="{label_y:.1f}" '
            f'font-size="12" fill="#666">'
            f'{_escape_xml(value_str)}</text>'
        )

    svg_parts.append("</svg>")

    svg_html = "\n".join(svg_parts)
    figure_style = (
        "margin:1.5rem 0;"
        "padding:1.25rem;"
        f"background:{_BG_COLOR};"
        "border-radius:6px;"
    )
    return f'<figure style="{figure_style}">\n{svg_html}\n</figure>'


# ---------------------------------------------------------------------------
# Markdown pre-processing
# ---------------------------------------------------------------------------

# Matches ```chart ... ``` fenced blocks (non-greedy, DOTALL)
_CHART_FENCE_RE = re.compile(r'```chart\s*\n(.*?)```', re.DOTALL)

_PLACEHOLDER_PREFIX = "CHARTBLOCK_"


def preprocess_charts(md: str) -> tuple[str, list[str]]:
    """Replace ```chart fenced blocks with CHARTBLOCK_N placeholders.

    Parses each chart block and pre-renders its SVG HTML so the rendered
    markup never passes through md_to_html() (which would mangle the SVG).

    Args:
        md: Raw article markdown that may contain ```chart ... ``` blocks.

    Returns:
        A 2-tuple:
            modified_md  — markdown with chart fences replaced by plain-text
                           placeholder tokens like ``CHARTBLOCK_0``.
            rendered     — list of rendered SVG HTML strings in match order.
                           Index N corresponds to placeholder CHARTBLOCK_N.
    """
    rendered: list[str] = []
    counter = 0

    def _replace(m: re.Match) -> str:
        nonlocal counter
        block_body = m.group(1)
        chart = parse_chart_block(block_body)
        svg_html = render_chart_html(chart)
        rendered.append(svg_html)
        placeholder = f"{_PLACEHOLDER_PREFIX}{counter}"
        counter += 1
        return placeholder

    modified_md = _CHART_FENCE_RE.sub(_replace, md)
    return modified_md, rendered


def inject_charts(html: str, rendered: list[str]) -> str:
    """Swap CHARTBLOCK_N placeholder tokens back into the converted HTML.

    Call this after md_to_html() has processed the placeholder-bearing markdown.

    Args:
        html:     HTML output from md_to_html(modified_md).
        rendered: The list returned by preprocess_charts().

    Returns:
        Final HTML with chart SVGs in place of placeholders.
    """
    for idx, svg_html in enumerate(rendered):
        placeholder = f"{_PLACEHOLDER_PREFIX}{idx}"
        # md_to_html may have wrapped the placeholder in a <p> tag; unwrap it
        html = html.replace(f"<p>{placeholder}</p>", svg_html)
        # Also handle the case where it wasn't wrapped
        html = html.replace(placeholder, svg_html)
    return html
