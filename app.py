"""
app.py — Marex vs. the US structured-note market (Marex design system).

Reads data/notes.csv, maintained nightly by edgar_market.py via GitHub Actions.
If the store is behind the last business day, the app catches up on open.

    streamlit run app.py
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import edgar_data as ed
import edgar_market as em
import edgar_notes as en

# --------------------------------------------------------------------------- #
# Marex design tokens
# --------------------------------------------------------------------------- #

M = {
    "purple": "#793690", "purple_deep": "#8123a3", "ink": "#1C1B4A", "ink_deep": "#0E0D24",
    "violet": "#843FDC", "white": "#FFFFFF", "off_white": "#FAFAFC", "cloud": "#F5F5F8",
    "mist": "#ECECF2", "light_grey": "#DADAE7", "light_grey_2": "#CBCBD2",
    "mid_grey": "#858596", "mid_grey_2": "#A7A7B3", "slate": "#505068",
    "amber": "#FFB443", "coral": "#FF846A", "cyan": "#05A0BF", "green": "#2D8D79", "stone": "#9E9188",
}
# Peers are drawn in a restrained palette so Marex (purple) is the only thing that pops.
PEER_COLOURS = [M["ink"], M["cyan"], M["coral"], M["amber"], M["green"], M["stone"],
                M["slate"], M["violet"], "#4C6EF5", "#C2185B", "#00897B", "#EF6C00",
                M["mid_grey"], "#6D4C41"]
FONT = "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
  html, body, [class*="css"], .stApp, .stMarkdown, .stDataFrame, .stSelectbox, .stMultiSelect,
  .stRadio, .stDateInput, .stButton, .stMetric {{ font-family: {FONT} !important; letter-spacing: -0.015em; color: {M['ink']}; }}
  .stApp {{ background: {M['cloud']}; }}
  .block-container {{ padding-top: 2.5rem; padding-bottom: 4rem; max-width: 1320px; }}
  section[data-testid="stSidebar"] {{ background: {M['white']}; border-right: 1px solid {M['light_grey']}; }}
  section[data-testid="stSidebar"] .block-container {{ padding-top: 2rem; }}
  h1 {{ font-weight: 300 !important; font-size: 40px !important; line-height: 1.12 !important; letter-spacing: -0.035em !important; color: {M['ink']}; margin: 0 0 4px 0 !important; }}
  h2 {{ font-weight: 400 !important; font-size: 22px !important; line-height: 1.3 !important; letter-spacing: -0.03em !important; color: {M['ink']}; margin: 0 0 4px 0 !important; }}
  .mx-wordmark {{ font-weight: 800; font-size: 20px; line-height: 1; letter-spacing: -0.02em; color: {M['ink']}; }}
  .mx-eyebrow {{ font-weight: 500; font-size: 12px; line-height: 1.2; letter-spacing: 0.08em; text-transform: uppercase; color: {M['mid_grey']}; }}
  .mx-caption {{ font-size: 12px; line-height: 1.4; color: {M['mid_grey']}; }}
  .mx-lead {{ font-weight: 300; font-size: 18px; line-height: 1.45; letter-spacing: -0.02em; color: {M['slate']}; }}
  .mx-card {{ background: {M['white']}; border: 1px solid {M['light_grey']}; border-radius: 15px; padding: 20px 24px; height: 100%; }}
  .mx-metric {{ font-weight: 300; font-size: 34px; line-height: 1.05; letter-spacing: -0.035em; color: {M['ink']}; margin-top: 8px; }}
  .mx-metric-sub {{ font-size: 13px; color: {M['slate']}; margin-top: 6px; }}
  .mx-up {{ color: {M['green']}; font-weight: 500; }} .mx-down {{ color: {M['coral']}; font-weight: 500; }}
  .mx-header {{ display: flex; align-items: center; justify-content: space-between; padding-bottom: 20px; margin-bottom: 28px; border-bottom: 1px solid {M['light_grey']}; }}
  .mx-pill {{ display: inline-block; padding: 4px 12px; border-radius: 999px; background: {M['mist']}; color: {M['slate']}; font-size: 12px; font-weight: 500; }}
  .mx-pill-accent {{ background: {M['white']}; border: 1px solid {M['purple']}; color: {M['purple']}; }}
  div[data-baseweb="select"] > div, .stDateInput input, .stTextInput input {{ border-radius: 8px !important; border-color: {M['light_grey']} !important; background: {M['white']}; }}
  .stButton > button {{ background: {M['purple']}; color: {M['white']}; border: none; border-radius: 999px; padding: 0.5rem 1.25rem; font-weight: 500; }}
  .stButton > button:hover {{ background: {M['purple_deep']}; color: {M['white']}; }}
  .stDownloadButton > button {{ background: {M['white']}; color: {M['ink']}; border: 1px solid {M['light_grey_2']}; border-radius: 999px; font-weight: 500; }}
  .stDownloadButton > button:hover {{ border-color: {M['purple']}; color: {M['purple']}; }}
  .stDataFrame {{ border: 1px solid {M['light_grey']}; border-radius: 12px; overflow: hidden; }}
  a {{ color: {M['purple']}; text-decoration: none; }} a:hover {{ color: {M['purple_deep']}; }}
  #MainMenu, footer {{ visibility: hidden; }}
</style>
"""

STORE = os.environ.get("EDGAR_STORE", str(em.NOTES_PATH))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown("\n".join(l for l in markup.splitlines() if l.strip()), unsafe_allow_html=True)


def eyebrow(t: str) -> None:
    html(f'<div class="mx-eyebrow">{t}</div>')


def h2(t: str) -> None:
    html(f"<h2>{t}</h2>")


def gap(px: int = 24) -> None:
    html(f"<div style='height:{px}px'></div>")


def usd(v, d: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    if abs(v) >= 1e9:
        return f"${v / 1e9:,.{d}f}bn"
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.{d}f}m"
    return f"${v / 1e3:,.0f}k"


def metric_card(label: str, value: str, sub: str = "") -> None:
    html(f'<div class="mx-card"><div class="mx-eyebrow">{label}</div>'
         f'<div class="mx-metric">{value}</div><div class="mx-metric-sub">{sub}</div></div>')


def delta_html(cur, prev, fmt, higher_is_better=True) -> str:
    if prev is None or pd.isna(prev) or pd.isna(cur):
        return "no prior period"
    d = cur - prev
    if d == 0:
        return "unchanged vs prior period"
    good = (d > 0) == higher_is_better
    cls = "mx-up" if good else "mx-down"
    arrow = "▲" if d > 0 else "▼"
    return f'<span class="{cls}">{arrow} {fmt(abs(d))}</span> vs prior period'


def layout(fig: go.Figure, height: int = 400, legend_below: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=M["ink"], size=12),
        legend=dict(orientation="h", y=-0.18, x=0, font=dict(size=12, color=M["slate"]),
                    bgcolor="rgba(0,0,0,0)") if legend_below else
        dict(orientation="v", x=1.02, y=1, font=dict(size=12, color=M["slate"]), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=M["ink"], font=dict(family=FONT, color=M["white"], size=12), bordercolor=M["ink"]),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, linecolor=M["light_grey"], tickfont=dict(color=M["slate"]))
    fig.update_yaxes(gridcolor=M["mist"], zerolinecolor=M["light_grey"], tickfont=dict(color=M["slate"]))
    return fig


def week_start(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s)
    return (dt - pd.to_timedelta(dt.dt.weekday, unit="D")).dt.normalize()


@st.cache_data(show_spinner=False)
def load(path: str, mtime: float) -> pd.DataFrame:
    return ed.load_market(path, structured_only=True)


def default_ua() -> str:
    ua = os.environ.get("EDGAR_UA", "")
    if not ua:
        try:
            ua = st.secrets.get("EDGAR_UA", "")
        except Exception:
            ua = ""
    return ua


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

st.set_page_config(page_title="Marex · US structured notes market", page_icon="◆",
                   layout="wide", initial_sidebar_state="expanded")
html(CSS)

status = ed.store_status(STORE)
df_all = load(STORE, status.get("mtime", 0.0))

# ---- Catch-up: store behind the last business day? --------------------------- #
lbd = ed.last_business_day()
store_last = date.fromisoformat(status["last"]) if status.get("exists") else None
behind = store_last is None or store_last < lbd
if behind and not st.session_state.get("caught_up"):
    st.session_state["caught_up"] = True
    ua = default_ua()
    if ua:
        start = (store_last + timedelta(days=1)) if store_last else lbd - timedelta(days=14)
        start = max(start, lbd - timedelta(days=30))       # never more than a month inline
        with st.status(f"Catching up {start} → {lbd} from EDGAR…", expanded=True) as box:
            try:
                en.UA = ua
                res = em.crawl(start, lbd, {"424B2"}, workers=10, use_fee=False,
                               log=lambda m: box.write(m))
                box.update(label=f"Up to date — {res['parsed']} new supplements", state="complete",
                           expanded=False)
                st.cache_data.clear()
                status = ed.store_status(STORE)
                df_all = load(STORE, status.get("mtime", 0.0))
            except Exception as exc:
                box.update(label="Catch-up failed — showing stored data", state="error")
                st.warning(f"EDGAR fetch failed: {exc}")
    else:
        st.info("Store is behind the last business day and no EDGAR_UA is configured, "
                "so it can't catch up. Add EDGAR_UA to Streamlit secrets.")

if df_all.empty:
    html('<div class="mx-card" style="text-align:center;padding:64px 24px"><div class="mx-eyebrow">No data yet</div>'
         '<div class="mx-lead" style="margin-top:12px">Run the backfill (python edgar_market.py --start 2025-01-01) '
         'and commit data/notes.csv.</div></div>')
    st.stop()

# ---- Sidebar ---------------------------------------------------------------- #
with st.sidebar:
    html('<div class="mx-wordmark">MAREX</div>'
         '<div class="mx-caption" style="margin:4px 0 24px">Structured Products · US market monitor</div>')

    eyebrow("Period")
    preset = st.selectbox("Period", ["Last 30 days", "Quarter to date", "Year to date",
                                     "Last 12 months", "Since Jan 2025", "Custom"],
                          index=2, label_visibility="collapsed")
    today = date.today()
    if preset == "Last 30 days":
        p_start, p_end = today - timedelta(days=30), today
    elif preset == "Quarter to date":
        p_start, p_end = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    elif preset == "Year to date":
        p_start, p_end = date(today.year, 1, 1), today
    elif preset == "Last 12 months":
        p_start, p_end = today - timedelta(days=365), today
    elif preset == "Since Jan 2025":
        p_start, p_end = date(2025, 1, 1), today
    else:
        picked = st.date_input("Range", value=(date(today.year, 1, 1), today), max_value=today,
                               label_visibility="collapsed")
        p_start, p_end = (picked if isinstance(picked, tuple) and len(picked) == 2
                          else (picked[0], picked[0]) if isinstance(picked, tuple) else (picked, picked))

    gap(16)
    eyebrow("Measure")
    measure = st.radio("Measure", ["Notional", "Number of notes"], horizontal=True, label_visibility="collapsed")
    by_notional = measure == "Notional"
    val_col = "size_usd" if by_notional else "accession"
    agg = "sum" if by_notional else "count"

    gap(16)
    eyebrow("Smoothing")
    window = st.select_slider("Rolling window", options=[4, 8, 13, 26], value=13,
                              format_func=lambda w: f"{w} weeks", label_visibility="collapsed")

    gap(16)
    eyebrow("Scope")
    exclude_prelim = st.toggle("Priced notes only (exclude preliminary)", value=True)

    # peer set defaults to the largest issuers in the selected period
    base = df_all[~df_all["preliminary"]] if exclude_prelim else df_all
    in_period = base[(base["filed"] >= p_start) & (base["filed"] <= p_end)]
    top_default = (in_period[~in_period["is_marex"]].groupby("issuer")[val_col].agg(agg)
                   .sort_values(ascending=False).head(8).index.tolist())
    all_peers = sorted(df_all.loc[~df_all["is_marex"], "issuer"].unique())
    peers = st.multiselect("Peers to plot", all_peers, default=[p for p in top_default if p in all_peers],
                           help="Marex is always shown. The market total always includes every issuer.")

    gap(24)
    eyebrow("Data")
    html(f'<div class="mx-caption">Store: <b>{status["first"]}</b> → <b>{status["last"]}</b><br>'
         f'{status["rows"]:,} filings · {status["issuers"]} issuers · updated nightly</div>')

# ---- Working frames --------------------------------------------------------- #
df = df_all[~df_all["preliminary"]] if exclude_prelim else df_all
df = df[df["filed"].notna()].copy()
df["week"] = week_start(df["filed"])
df["month"] = pd.to_datetime(df["filed"]).dt.to_period("M").dt.to_timestamp()

marex_name = df.loc[df["is_marex"], "issuer"].iloc[0] if df["is_marex"].any() else "Marex"
period = df[(df["filed"] >= p_start) & (df["filed"] <= p_end)]
span = (p_end - p_start).days + 1
prior = df[(df["filed"] >= p_start - timedelta(days=span)) & (df["filed"] < p_start)]


def league_table(frame: pd.DataFrame) -> pd.DataFrame:
    t = (frame.groupby("issuer").agg(notes=("accession", "count"), notional=("size_usd", "sum"),
                                     median_ticket=("size_usd", "median"), is_marex=("is_marex", "max"))
         .sort_values("notional" if by_notional else "notes", ascending=False))
    key = "notional" if by_notional else "notes"
    t["share"] = t[key] / t[key].sum() if t[key].sum() else 0.0
    t["rank"] = range(1, len(t) + 1)
    return t


lt, lt_prev = league_table(period), league_table(prior)
mx = lt[lt["is_marex"]]
mx_prev = lt_prev[lt_prev["is_marex"]]
mx_rank = int(mx["rank"].iloc[0]) if len(mx) else None
mx_rank_prev = int(mx_prev["rank"].iloc[0]) if len(mx_prev) else None
mx_share = float(mx["share"].iloc[0]) if len(mx) else 0.0
mx_share_prev = float(mx_prev["share"].iloc[0]) if len(mx_prev) else None
mx_notional = float(mx["notional"].iloc[0]) if len(mx) else 0.0
mx_notes = int(mx["notes"].iloc[0]) if len(mx) else 0

# ---- Header ----------------------------------------------------------------- #
def fmt_day(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


standing = (f"{marex_name} ranks <b>#{mx_rank}</b> of {len(lt)} issuers with <b>{mx_share:.1%}</b> "
            f"of {measure.lower()}" if mx_rank else f"No {marex_name} issuance in this period")
html(f'<div class="mx-header"><div>'
     f'<div class="mx-eyebrow">US SEC-registered structured notes · {fmt_day(p_start)} – {fmt_day(p_end)}</div>'
     f'<h1>Where Marex stands</h1><div class="mx-lead">{standing}</div></div>'
     f'<div class="mx-wordmark">MAREX</div></div>')

# ---- KPI cards -------------------------------------------------------------- #
c1, c2, c3, c4 = st.columns(4)
with c1:
    metric_card("Marex notional", usd(mx_notional),
                delta_html(mx_notional, float(mx_prev["notional"].iloc[0]) if len(mx_prev) else None, usd))
with c2:
    metric_card("Marex notes", f"{mx_notes:,}",
                delta_html(mx_notes, int(mx_prev["notes"].iloc[0]) if len(mx_prev) else None, lambda v: f"{v:,.0f}"))
with c3:
    metric_card("Market share", f"{mx_share:.1%}",
                delta_html(mx_share, mx_share_prev, lambda v: f"{v * 100:.1f} pts"))
with c4:
    metric_card("Rank", f"#{mx_rank}" if mx_rank else "—",
                delta_html(mx_rank, mx_rank_prev, lambda v: f"{v:.0f} places", higher_is_better=False)
                if mx_rank else f"{len(lt)} issuers active")
gap(32)

# ---- Chart 1: rolling market share ------------------------------------------ #
eyebrow(f"Rolling {window}-week share of {measure.lower()} · all issuers in denominator")
h2("Market share over time")

weekly = (df.groupby(["week", "issuer"])[val_col].agg(agg).unstack(fill_value=0)
          .asfreq("W-MON", fill_value=0))
rolling = weekly.rolling(window, min_periods=1).sum()
share = rolling.div(rolling.sum(axis=1).replace(0, pd.NA), axis=0).astype(float) * 100
plot_from = pd.Timestamp(p_start) - pd.Timedelta(weeks=window)      # let the window warm up
share = share[share.index >= max(plot_from, share.index.min())]

fig = go.Figure()
for i, p in enumerate(peers):
    if p in share:
        fig.add_trace(go.Scatter(x=share.index, y=share[p], name=p, mode="lines",
                                 line=dict(color=PEER_COLOURS[i % len(PEER_COLOURS)], width=1.5),
                                 hovertemplate="%{y:.1f}%<extra>" + p + "</extra>"))
if marex_name in share:
    fig.add_trace(go.Scatter(x=share.index, y=share[marex_name], name=marex_name, mode="lines",
                             line=dict(color=M["purple"], width=3.5),
                             hovertemplate="%{y:.1f}%<extra>" + marex_name + "</extra>"))
fig.add_vrect(x0=pd.Timestamp(p_start), x1=pd.Timestamp(p_end), fillcolor=M["mist"], opacity=0.35,
              line_width=0, layer="below")
fig.update_yaxes(ticksuffix="%", rangemode="tozero")
st.plotly_chart(layout(fig, 420), use_container_width=True, config={"displayModeBar": False})
html(f'<div class="mx-caption">Shaded band is the selected period. Lines use a trailing {window}-week window, '
     f'so the start of the series includes issuance from before the period.</div>')
gap(32)

# ---- Chart 2 + 3: rank over time, cumulative YTD ----------------------------- #
left, right = st.columns(2, gap="large")

with left:
    eyebrow("Monthly league-table position")
    h2("Marex rank over time")
    monthly = df.groupby(["month", "issuer"])[val_col].agg(agg).unstack(fill_value=0)
    ranks = monthly.rank(axis=1, ascending=False, method="min")
    active = (monthly > 0).sum(axis=1)
    if marex_name in ranks:
        r = ranks[marex_name].where(monthly[marex_name] > 0)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=active.index, y=active, name="Active issuers", mode="lines",
                                 line=dict(color=M["light_grey_2"], width=1, dash="dot"),
                                 hovertemplate="%{y} active<extra></extra>"))
        fig.add_trace(go.Scatter(x=r.index, y=r, name=marex_name, mode="lines+markers",
                                 line=dict(color=M["purple"], width=3), marker=dict(size=7),
                                 connectgaps=False, hovertemplate="#%{y}<extra></extra>"))
        fig.update_yaxes(autorange="reversed", dtick=2, title=None)
        st.plotly_chart(layout(fig, 360), use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("No Marex issuance in the store yet.")

with right:
    eyebrow(f"Cumulative {measure.lower()} · {p_end.year} vs {p_end.year - 1}")
    h2("Year-to-date pace")
    yr, py = p_end.year, p_end.year - 1
    fig = go.Figure()

    def cum_series(frame: pd.DataFrame, year: int) -> pd.Series:
        f = frame[pd.to_datetime(frame["filed"]).dt.year == year]
        s = f.groupby("week")[val_col].agg(agg).sort_index().cumsum()
        s.index = s.index.dayofyear
        return s

    for i, p in enumerate(peers[:6]):
        s = cum_series(df[df["issuer"] == p], yr)
        if len(s):
            fig.add_trace(go.Scatter(x=s.index, y=s.values, name=p, mode="lines",
                                     line=dict(color=PEER_COLOURS[i % len(PEER_COLOURS)], width=1.2),
                                     hovertemplate=("%{y:$,.0f}" if by_notional else "%{y}") + "<extra>" + p + "</extra>"))
    mxf = df[df["is_marex"]]
    s_prev = cum_series(mxf, py)
    if len(s_prev):
        fig.add_trace(go.Scatter(x=s_prev.index, y=s_prev.values, name=f"{marex_name} {py}", mode="lines",
                                 line=dict(color=M["purple"], width=2, dash="dash"),
                                 hovertemplate=("%{y:$,.0f}" if by_notional else "%{y}") + f"<extra>{marex_name} {py}</extra>"))
    s_cur = cum_series(mxf, yr)
    if len(s_cur):
        fig.add_trace(go.Scatter(x=s_cur.index, y=s_cur.values, name=f"{marex_name} {yr}", mode="lines",
                                 line=dict(color=M["purple"], width=3.5), fill="tozeroy",
                                 fillcolor="rgba(121,54,144,0.06)",
                                 hovertemplate=("%{y:$,.0f}" if by_notional else "%{y}") + f"<extra>{marex_name} {yr}</extra>"))
    fig.update_xaxes(title="Day of year", range=[0, 366])
    if by_notional:
        fig.update_yaxes(tickprefix="$", tickformat="~s")
    st.plotly_chart(layout(fig, 360), use_container_width=True, config={"displayModeBar": False})

gap(32)

# ---- League table + product mix ------------------------------------------------ #
left, right = st.columns([6, 5], gap="large")

with left:
    eyebrow(f"{fmt_day(p_start)} – {fmt_day(p_end)} · ranked by {measure.lower()}")
    h2("League table")
    tbl = lt.reset_index()
    tbl["Δ rank"] = tbl["issuer"].map(lt_prev["rank"]).sub(tbl["rank"])   # positive = moved up
    tbl = tbl[["rank", "issuer", "notes", "notional", "share", "median_ticket", "Δ rank", "is_marex"]]
    tbl.columns = ["#", "Issuer", "Notes", "Notional", "Share", "Median ticket", "Δ rank", "is_marex"]

    def highlight(row):
        if row["is_marex"]:
            return [f"background-color: {M['mist']}; color: {M['purple']}; font-weight: 600"] * len(row)
        return [""] * len(row)

    styled = (tbl.style.apply(highlight, axis=1)
              .format({"Notional": lambda v: usd(v), "Median ticket": lambda v: usd(v, 2),
                       "Share": "{:.1%}", "Δ rank": lambda v: "" if pd.isna(v) else f"{v:+.0f}"})
              .hide(axis="columns", subset=["is_marex"]))
    st.dataframe(styled, hide_index=True, use_container_width=True, height=min(60 + 35 * len(tbl), 560))

with right:
    eyebrow("Marex vs market · share of own issuance")
    h2("Product mix")
    mkt = period[~period["is_marex"]]
    mxp = period[period["is_marex"]]
    field = st.radio("Mix by", ["Payoff family", "Asset class"], horizontal=True, label_visibility="collapsed")
    col = "family" if field == "Payoff family" else "asset_class"

    def mix(frame):
        s = frame.groupby(col)[val_col].agg(agg)
        return (s / s.sum() * 100) if s.sum() else s

    mm, xm = mix(mkt), mix(mxp)
    cats = mm.add(xm, fill_value=0).sort_values(ascending=True).index
    fig = go.Figure()
    fig.add_trace(go.Bar(y=cats, x=mm.reindex(cats).fillna(0), name="Market", orientation="h",
                         marker_color=M["light_grey_2"], hovertemplate="%{x:.1f}%<extra>Market</extra>"))
    fig.add_trace(go.Bar(y=cats, x=xm.reindex(cats).fillna(0), name=marex_name, orientation="h",
                         marker_color=M["purple"], hovertemplate="%{x:.1f}%<extra>" + marex_name + "</extra>"))
    fig.update_layout(barmode="group", bargap=0.25)
    fig.update_xaxes(ticksuffix="%")
    fig = layout(fig, min(60 + 42 * len(cats), 520))
    fig.update_layout(hovermode="y unified")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

gap(32)

# ---- Drill-down ---------------------------------------------------------------- #
eyebrow("Individual notes in the period")
h2("Products issued")
who = st.selectbox("Issuer", [marex_name, "All issuers"] + [p for p in lt.index if p != marex_name],
                   label_visibility="collapsed")
detail = period if who == "All issuers" else period[period["issuer"] == who]
show = ["isin", "issuer", "product", "family", "asset_class", "size_usd", "trade_date", "maturity_date",
        "tenor_years", "estimated_initial_value", "filed", "url"]
view = (detail[show].sort_values("filed", ascending=False)
        .rename(columns={"isin": "ISIN", "issuer": "Issuer", "product": "Product", "family": "Payoff",
                         "asset_class": "Asset class", "size_usd": "Size", "trade_date": "Trade",
                         "maturity_date": "Maturity", "tenor_years": "Tenor (y)",
                         "estimated_initial_value": "Est. value", "filed": "Filed", "url": "EDGAR"}))
st.dataframe(view, hide_index=True, use_container_width=True, height=min(60 + 35 * len(view), 600),
             column_config={"Size": st.column_config.NumberColumn(format="$%,.0f"),
                            "Tenor (y)": st.column_config.NumberColumn(format="%.1f"),
                            "Product": st.column_config.TextColumn(width="large"),
                            "EDGAR": st.column_config.LinkColumn(display_text="Open")})

gap(12)
e1, e2, _ = st.columns([2, 2, 8])
stem = f"{p_start.isoformat()}_{p_end.isoformat()}"
with e1:
    st.download_button("Download notes (CSV)", detail.drop(columns=["week", "month"]).to_csv(index=False).encode(),
                       f"notes_{stem}.csv", "text/csv", use_container_width=True)
with e2:
    st.download_button("Download league table (CSV)", tbl.drop(columns=["is_marex"]).to_csv(index=False).encode(),
                       f"league_{stem}.csv", "text/csv", use_container_width=True)

html(f'<div class="mx-caption" style="margin-top:40px;padding-top:20px;border-top:1px solid {M["light_grey"]}">'
     'Source: SEC EDGAR 424(b)(2) pricing supplements, all registrants, parsed from cover pages; vanilla debt '
     'excluded. Notional is the stated aggregate principal amount; notes without a stated size are counted but not '
     'summed. Preliminary and final supplements for the same ISIN are merged. Market share denominators include '
     'every issuer, not only the plotted peers.</div>')
