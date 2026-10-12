"""Plotly figures, styled for the app's dark theme (.streamlit/config.toml)."""
import plotly.graph_objects as go

# Categorical slots stepped for a dark surface, assigned in fixed order (never cycled).
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]
OTHER_GRAY = "#6b6a66"
MUTED = "#898781"
INK_2 = "#c3c2b7"
SURFACE = "#0b1220"
PANEL = "#162236"
GRID = "rgba(255,255,255,0.07)"
GOOD, BAD = "#0ca30c", "#e66767"
MAX_SLICES = len(SERIES)


def _layout(fig, height):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK_2, size=13),
        hoverlabel=dict(bgcolor=PANEL, bordercolor="rgba(255,255,255,0.15)", font_color="#ffffff"),
    )
    return fig


def allocation_donut(table, total_label):
    """Donut of CAD market value. The largest positions get their own slice; the tail folds into Other.
    `table` needs columns label and market_value_cad, sorted largest first."""
    top, rest = table.head(MAX_SLICES), table.iloc[MAX_SLICES:]
    labels = list(top.label) + (["Other"] if len(rest) else [])
    values = list(top.market_value_cad) + ([rest.market_value_cad.sum()] if len(rest) else [])
    colors = SERIES[:len(top)] + ([OTHER_GRAY] if len(rest) else [])
    details = [""] * len(top) + ([", ".join(rest.label)] if len(rest) else [])
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.6, sort=False, direction="clockwise",
        marker=dict(colors=colors, line=dict(color=SURFACE, width=2)),
        texttemplate="%{label}<br>%{percent:.1%}", textposition="outside", textfont=dict(color=INK_2),
        customdata=details,
        hovertemplate="<b>%{label}</b><br>%{value:$,.0f} CAD<br>%{percent:.1%} of portfolio<br>%{customdata}<extra></extra>",
    ))
    fig.update_layout(showlegend=False, annotations=[dict(
        text=f"<b>{total_label}</b><br><span style='font-size:12px;color:{MUTED}'>CAD</span>",
        showarrow=False, font=dict(size=20, color="#ffffff"))])
    return _layout(fig, 420)


def value_trend(daily):
    """Portfolio value against the net cash put in, one shared dollar axis."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily.index, y=daily.net_invested, name="Net cash invested",
                             line=dict(color=MUTED, width=2, dash="dot", shape="hv"),
                             hovertemplate="%{y:$,.0f}"))
    fig.add_trace(go.Scatter(x=daily.index, y=daily.value, name="Portfolio value",
                             line=dict(color=SERIES[0], width=2), hovertemplate="%{y:$,.0f}"))
    fig.update_layout(
        hovermode="x unified",
        legend=dict(orientation="h", y=1.1, x=0, traceorder="reversed"),
        xaxis=dict(showgrid=False, linecolor="#383835", rangeselector=dict(
            buttons=[dict(count=1, label="1M", step="month", stepmode="backward"),
                     dict(count=3, label="3M", step="month", stepmode="backward"),
                     dict(count=6, label="6M", step="month", stepmode="backward"),
                     dict(count=1, label="YTD", step="year", stepmode="todate"),
                     dict(label="All", step="all")],
            bgcolor=PANEL, activecolor="#24344f", font=dict(color=INK_2), y=1.1, x=1, xanchor="right")),
        yaxis=dict(gridcolor=GRID, zeroline=False, tickprefix="$", tickformat=",.0f"),
    )
    return _layout(fig, 440)
