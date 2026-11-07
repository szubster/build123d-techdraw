"""Theme definitions and CSS custom property palettes for 2D technical drawings."""

from dataclasses import dataclass
from enum import Enum


class Theme(Enum):
    DARK_BLUEPRINT = "dark_blueprint"
    ISO_WHITE = "iso_white"
    PRINT = "iso_white"


@dataclass(frozen=True)
class ThemeColors:
    bg: str
    grid: str
    border: str
    border_sub: str
    title_bg: str
    title_border: str
    text: str
    text_muted: str
    text_accent: str
    line_visible: str
    line_hidden: str
    line_center: str
    line_section: str
    hatch: str
    dim_line: str
    dim_text: str
    balloon_bg: str
    balloon_border: str
    balloon_text: str
    table_header_bg: str
    table_row_alt: str


THEME_COLORS: dict[Theme, ThemeColors] = {
    Theme.DARK_BLUEPRINT: ThemeColors(
        bg="#0B192C",
        grid="#38BDF812",
        border="#38BDF8",
        border_sub="#1E3A5F",
        title_bg="#0F172AE6",
        title_border="#38BDF8",
        text="#F8FAFC",
        text_muted="#94A3B8",
        text_accent="#38BDF8",
        line_visible="#FFFFFF",
        line_hidden="#475569",
        line_center="#38BDF8",
        line_section="#F43F5E",
        hatch="#0284C7",
        dim_line="#38BDF8",
        dim_text="#38BDF8",
        balloon_bg="#0F172A",
        balloon_border="#F43F5E",
        balloon_text="#FFFFFF",
        table_header_bg="#1E293B",
        table_row_alt="#1E293B99",
    ),
    Theme.ISO_WHITE: ThemeColors(
        bg="#FFFFFF",
        grid="#00000008",
        border="#0F172A",
        border_sub="#CBD5E1",
        title_bg="#F8FAFC",
        title_border="#0F172A",
        text="#0F172A",
        text_muted="#475569",
        text_accent="#0284C7",
        line_visible="#000000",
        line_hidden="#94A3B8",
        line_center="#2563EB",
        line_section="#DC2626",
        hatch="#64748B",
        dim_line="#DC2626",
        dim_text="#DC2626",
        balloon_bg="#FFFFFF",
        balloon_border="#DC2626",
        balloon_text="#0F172A",
        table_header_bg="#E2E8F0",
        table_row_alt="#F1F5F9",
    ),
}


def generate_theme_css(default_theme: Theme = Theme.DARK_BLUEPRINT) -> str:
    """Generate dynamic CSS stylesheets supporting both Dark Blueprint and Clean White ISO themes."""
    c_dark = THEME_COLORS[Theme.DARK_BLUEPRINT]
    c_white = THEME_COLORS[Theme.ISO_WHITE]

    primary = c_dark if default_theme == Theme.DARK_BLUEPRINT else c_white
    alt = c_white if default_theme == Theme.DARK_BLUEPRINT else c_dark
    primary_class = (
        "theme-dark-blueprint" if default_theme == Theme.DARK_BLUEPRINT else "theme-iso-white"
    )
    alt_class = (
        "theme-iso-white" if default_theme == Theme.DARK_BLUEPRINT else "theme-dark-blueprint"
    )

    return f"""
    :root, .{primary_class} {{
        --bg-color: {primary.bg};
        --grid-color: {primary.grid};
        --border-main: {primary.border};
        --border-sub: {primary.border_sub};
        --title-bg: {primary.title_bg};
        --title-border: {primary.title_border};
        --text-main: {primary.text};
        --text-muted: {primary.text_muted};
        --text-accent: {primary.text_accent};
        --line-visible: {primary.line_visible};
        --line-hidden: {primary.line_hidden};
        --line-center: {primary.line_center};
        --line-section: {primary.line_section};
        --hatch-color: {primary.hatch};
        --dim-color: {primary.dim_line};
        --dim-text: {primary.dim_text};
        --balloon-bg: {primary.balloon_bg};
        --balloon-border: {primary.balloon_border};
        --balloon-text: {primary.balloon_text};
        --table-header-bg: {primary.table_header_bg};
        --table-row-alt: {primary.table_row_alt};
    }}
    .{alt_class} {{
        --bg-color: {alt.bg};
        --grid-color: {alt.grid};
        --border-main: {alt.border};
        --border-sub: {alt.border_sub};
        --title-bg: {alt.title_bg};
        --title-border: {alt.title_border};
        --text-main: {alt.text};
        --text-muted: {alt.text_muted};
        --text-accent: {alt.text_accent};
        --line-visible: {alt.line_visible};
        --line-hidden: {alt.line_hidden};
        --line-center: {alt.line_center};
        --line-section: {alt.line_section};
        --hatch-color: {alt.hatch};
        --dim-color: {alt.dim_line};
        --dim-text: {alt.dim_text};
        --balloon-bg: {alt.balloon_bg};
        --balloon-border: {alt.balloon_border};
        --balloon-text: {alt.balloon_text};
        --table-header-bg: {alt.table_header_bg};
        --table-row-alt: {alt.table_row_alt};
    }}
    svg {{
        font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Liberation Sans', sans-serif;
        background-color: var(--bg-color);
    }}
    .sheet-bg {{ fill: var(--bg-color); }}
    .border-main {{ stroke: var(--border-main); fill: none; }}
    .border-sub {{ stroke: var(--border-sub); fill: none; }}
    .title-bg {{ fill: var(--title-bg); stroke: var(--border-main); }}
    .table-header-bg {{ fill: var(--table-header-bg); }}
    .table-row-alt {{ fill: var(--table-row-alt); }}
    .zone-text {{ fill: var(--text-muted); font-size: 2.8px; font-weight: bold; text-anchor: middle; }}
    .tb-field-label {{ fill: var(--text-muted); font-size: 2.1px; font-weight: 600; letter-spacing: 0.2px; }}
    .tb-field-val-sm {{ fill: var(--text-main); font-size: 2.6px; font-weight: 500; }}
    .tb-field-val-bold {{ fill: var(--text-main); font-size: 3.4px; font-weight: 700; }}
    .tb-title {{ fill: var(--text-accent); font-size: 3.0px; font-weight: 700; letter-spacing: 0.2px; }}
    .notes-text {{ fill: var(--text-muted); font-family: 'JetBrains Mono', monospace; font-size: 2.4px; }}
    .view-title {{ fill: var(--text-main); letter-spacing: 0.5px; }}
    .view-scale {{ fill: var(--text-muted); }}
    .line-visible, #layer_visible, #layer_visible * {{ stroke: var(--line-visible); fill: none; }}
    .line-hidden, #layer_hidden, #layer_hidden * {{ stroke: var(--line-hidden); stroke-dasharray: 2.0, 1.2; fill: none; }}
    .line-center {{ stroke: var(--line-center); fill: none; }}
    .line-section-thick {{ stroke: var(--line-section); fill: none; }}
    .section-arrow-head {{ fill: var(--line-section); }}
    .section-label-text {{ fill: var(--line-section); }}
    .dim-line {{ stroke: var(--dim-color); fill: none; }}
    .dim-arrow {{ fill: var(--dim-color); }}
    .dim-text {{ fill: var(--dim-text); font-weight: 600; font-family: 'JetBrains Mono', monospace; }}
    .leader-dot {{ fill: var(--dim-color); }}
    .leader-text {{ fill: var(--text-main); font-family: 'JetBrains Mono', monospace; font-size: 2.7px; font-weight: 500; }}
    .balloon-dot {{ fill: var(--balloon-border); }}
    .balloon-leader {{ stroke: var(--balloon-border); fill: none; }}
    .balloon-circle {{ fill: var(--balloon-bg); stroke: var(--balloon-border); }}
    .balloon-text {{ fill: var(--balloon-text); }}
    .theme-iso-white .blueprint-grid {{ display: none; }}
    .theme-iso-white .line-visible, .theme-iso-white #layer_visible, .theme-iso-white #layer_visible * {{ stroke: #000000; stroke-width: 0.5px; }}
    .theme-iso-white .line-hidden, .theme-iso-white #layer_hidden, .theme-iso-white #layer_hidden * {{ stroke: #64748B; stroke-width: 0.25px; stroke-dasharray: 2.0, 1.2; }}
    .theme-iso-white .line-center {{ stroke: #0F172A; stroke-width: 0.25px; stroke-dasharray: 8.0, 1.5, 1.5, 1.5; }}
    .theme-iso-white .dim-line {{ stroke: #000000; stroke-width: 0.25px; }}
    .theme-iso-white .dim-arrow {{ fill: #000000; }}
    .theme-iso-white .dim-text {{ fill: #000000; }}
    """
