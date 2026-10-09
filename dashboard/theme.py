"""Dark trading-desk theme: one set of colour tokens used by every chart and component.

Colours are the dark-mode steps of a validated palette (lightness band, chroma floor,
colour-blind separation and contrast against the chart surface all checked with a validator):
categorical series in a fixed order, a blue <-> red diverging pair with a neutral grey midpoint
for P&L (blue = gain, red = loss), and reserved status colours that never stand in for a series.
"""
import plotly.graph_objects as go
import plotly.io as pio

PAGE = "#0d0d0d"
SURFACE = "#1a1a19"
INK = "#ffffff"
INK2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"
BORDER = "rgba(255,255,255,0.10)"

# categorical, fixed order (never cycled): blue, orange, aqua, yellow, magenta, violet
S = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"]
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, VIOLET = S
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"

# diverging P&L scale: loss (red) -> neutral grey -> gain (blue); zero pinned to the middle
DIVERGING = [[0.0, "#e66767"], [0.25, "#a5524f"], [0.5, AXIS], [0.75, "#2a5d9c"], [1.0, BLUE]]
# sequential (one hue, dark -> light on a dark surface) for the vol surface
SEQUENTIAL = [[0.0, "#0d366b"], [0.25, "#184f95"], [0.5, "#256abf"], [0.75, "#5598e7"], [1.0, "#b7d3f6"]]

FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

pio.templates["desk"] = go.layout.Template(layout=dict(
    paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
    font=dict(family=FONT, color=INK2, size=12),
    title=dict(font=dict(color=INK, size=14), x=0.01, xanchor="left", y=0.98, yanchor="top"),
    colorway=S,
    margin=dict(l=56, r=20, t=72, b=44),
    xaxis=dict(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickcolor=AXIS,
               tickfont=dict(color=MUTED), title=dict(font=dict(color=MUTED))),
    yaxis=dict(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickcolor=AXIS,
               tickfont=dict(color=MUTED), title=dict(font=dict(color=MUTED))),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=INK2, size=11), orientation="h",
                yanchor="bottom", y=1.01, xanchor="left", x=0.0),
    hoverlabel=dict(bgcolor="#262624", bordercolor=BORDER, font=dict(color=INK, family=FONT)),
    hovermode="closest",
))
pio.templates.default = "desk"

CSS = f"""
:root {{ color-scheme: dark; }}
body {{ background:{PAGE}; color:{INK}; font-family:{FONT}; margin:0; }}
.wrap {{ padding: 12px 16px 32px; max-width: 1800px; margin: 0 auto; }}
.top {{ display:flex; align-items:baseline; gap:16px; flex-wrap:wrap; padding:6px 0 10px;
        border-bottom:1px solid {GRID}; margin-bottom:10px; }}
.top .Select {{ font-size:12px; }}
.top h1 {{ font-size:18px; font-weight:600; margin:0; color:{INK}; }}
.top .meta {{ color:{MUTED}; font-size:12px; }}
.pill {{ font-size:11px; padding:2px 8px; border-radius:10px; border:1px solid {BORDER}; color:{INK2}; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:8px; margin:8px 0 12px; }}
.tile {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px; padding:10px 12px; }}
.tile .lbl {{ color:{MUTED}; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }}
.tile .val {{ color:{INK}; font-size:20px; font-weight:600; margin-top:2px; }}
.tile .sub {{ color:{INK2}; font-size:11px; margin-top:2px; }}
.grid {{ display:grid; gap:10px; margin-bottom:10px; }}
.g2 {{ grid-template-columns: 2fr 1fr; }} .g11 {{ grid-template-columns: 1fr 1fr; }}
.g3 {{ grid-template-columns: 1fr 1fr 1fr; }} .g4 {{ grid-template-columns: repeat(4, 1fr); }}
.g211 {{ grid-template-columns: 2fr 1fr 1fr; }}
@media (max-width: 1100px) {{ .g2, .g11, .g3, .g4, .g211 {{ grid-template-columns: 1fr; }} }}
.card {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px; padding:6px; min-width:0; }}
.card h3 {{ font-size:13px; font-weight:600; color:{INK}; margin:4px 8px 2px; }}
.card .note {{ font-size:11px; color:{MUTED}; margin:0 8px 6px; }}
.ctrl {{ display:flex; gap:12px; align-items:center; margin:4px 0 8px; color:{INK2}; font-size:12px; }}
.Select-control, .Select-menu-outer {{ background:{SURFACE} !important; color:{INK} !important; border-color:{AXIS} !important; }}
.Select-value-label, .Select-option {{ color:{INK} !important; }}
.Select-option {{ background:{SURFACE} !important; }} .Select-option.is-focused {{ background:#262624 !important; }}
.tab {{ background:{PAGE} !important; color:{MUTED} !important; border:none !important;
        border-bottom:2px solid transparent !important; padding:8px 16px !important; }}
.tab--selected {{ color:{INK} !important; border-bottom:2px solid {BLUE} !important; }}
.warn {{ color:{WARNING}; font-size:12px; margin:4px 0; }}
"""

TABLE_STYLE = dict(
    style_table={"overflowX": "auto", "maxHeight": "420px", "overflowY": "auto"},
    style_header={"backgroundColor": "#22221f", "color": INK2, "fontWeight": "600", "border": f"1px solid {GRID}",
                  "fontSize": "11px"},
    style_cell={"backgroundColor": SURFACE, "color": INK, "border": f"1px solid {GRID}", "fontSize": "12px",
                "fontFamily": FONT, "padding": "3px 8px", "fontVariantNumeric": "tabular-nums",
                "textAlign": "right", "minWidth": "60px"},
    fixed_rows={"headers": True},
)
