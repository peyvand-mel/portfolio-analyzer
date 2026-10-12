"""Portfolio Analyzer: upload holdings and activity, see allocation and historical performance."""
from datetime import timedelta
from pathlib import Path
import hashlib

import pandas as pd
import streamlit as st

import analytics
import charts
import importers
import market
import ocr
from charts import BAD, GOOD, INK_2

st.set_page_config(page_title="Portfolio Analyzer", page_icon="📈", layout="wide")


def money(x, digits=0):
    if x is None or pd.isna(x):
        return "n/a"
    return f"{'-' if x < 0 else ''}${abs(x):,.{digits}f}"


def pct(x, signed=True):
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


# ---------------------------------------------------------------- cached work

@st.cache_data(show_spinner="Reading screenshots…")
def read_screenshots(images):
    return ocr.screenshot_holdings(list(images))


@st.cache_data(show_spinner=False)
def read_csv(data):
    return importers.read_csv(data)


@st.cache_data(ttl=300, show_spinner="Fetching prices from Yahoo Finance…")
def fetch_market(yahoo, currencies, start):
    return market.download(list(yahoo), set(currencies), start)


# ---------------------------------------------------------------- layout

st.title("Portfolio Analyzer")
st.caption("All totals are in CAD. Prices come from Yahoo Finance and refresh every five minutes.")
st.markdown("<style>.block-container{padding-top:2rem;max-width:1500px} [data-testid='stMetric']{background:#162236;padding:18px;border-radius:12px} [data-baseweb='tab-list']{gap:24px}</style>", unsafe_allow_html=True)
if st.button("Refresh prices and exchange rates"):
    fetch_market.clear()
    st.rerun()
upload_tab, allocation_tab, history_tab = st.tabs(["Upload", "Allocation", "Historical performance"])

holdings, activity, mapping = None, None, None

with upload_tab:
    st.subheader("Upload your portfolio")
    st.write("Add a **holdings snapshot** (CSV or screenshots of your holdings page) for the Allocation tab, "
             "and your broker's **activity export** for Historical performance. Upload both for the most accurate results.")
    files = st.file_uploader("CSV files or screenshots", type=["csv", "png", "jpg", "jpeg"],
                             accept_multiple_files=True,
                             help="Upload screenshots in page order so the CAD and USD sections are read correctly.")

    snapshot_frames, activity_frames, problems, ocr_notes, ocr_text = [], [], [], [], ""
    local_activity = next(iter(sorted(Path(__file__).parent.glob("Activity*.csv"))), None)
    if local_activity is not None and st.button("Load my saved portfolio", type="primary"):
        st.session_state["use_local_activity"] = True
        st.session_state["use_local_screenshots"] = True
    if local_activity is not None:
        if st.button("Use my October 5 RBC activity CSV"):
            st.session_state["use_local_activity"] = True
        if st.session_state.get("use_local_activity"):
            try:
                _, frame = read_csv(local_activity.read_bytes())
                activity_frames.append(frame)
                st.caption("Your October 5 activity CSV is loaded. Add holdings screenshots to reconcile positions held before the export window.")
            except Exception as error:
                problems.append(f"Local activity CSV: {error}")
    if st.button("Clear loaded local activity"):
        st.session_state.pop("use_local_activity", None)
        st.rerun()
    if st.session_state.get("use_local_screenshots") and st.button("Clear loaded local screenshots"):
        st.session_state.pop("use_local_screenshots", None)
        st.rerun()
    local_images = sorted(Path(__file__).parent.glob("Screenshot*.png"))
    if local_images:
        if st.button("Use my existing holdings screenshots"):
            st.session_state["use_local_screenshots"] = True
        if st.session_state.get("use_local_screenshots"):
            try:
                frame, ocr_text, ocr_notes = read_screenshots(tuple(p.read_bytes() for p in local_images))
                snapshot_frames.append(frame)
            except Exception as error:
                problems.append(f"Local screenshots: {error}")
    images = [f for f in files or [] if f.type.startswith("image/")]
    for f in files or []:
        if f.type.startswith("image/"):
            continue
        try:
            kind, frame = read_csv(f.getvalue())
            (activity_frames if kind == "activity" else snapshot_frames).append(frame)
        except Exception as error:  # show parse problems next to the file name
            problems.append(f"**{f.name}**: {error}")
    if images:
        try:
            frame, ocr_text, ocr_notes = read_screenshots(tuple(f.getvalue() for f in images))
            snapshot_frames.append(frame)
        except Exception as error:
            problems.append(f"**Screenshots**: {error}")
    for problem in problems:
        st.error(problem)

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("#### Holdings snapshot")
        manual = st.checkbox("Enter holdings manually", value=False)
        if snapshot_frames or manual:
            combined = pd.concat(snapshot_frames, ignore_index=True).drop_duplicates().reset_index(drop=True) if snapshot_frames else pd.DataFrame(columns=importers.HOLDING_COLS)
            editor_id = hashlib.sha256(combined.to_csv(index=False).encode()).hexdigest()[:12]
            for note in ocr_notes:
                st.warning(note)
            st.caption("Check the rows below and fix anything that was misread. Average cost is in the row's currency.")
            edited = st.data_editor(
                combined, num_rows="dynamic", width="stretch", key=f"holdings_editor_{editor_id}",
                column_config={
                    "symbol": st.column_config.TextColumn("Ticker", required=True),
                    "currency": st.column_config.SelectboxColumn("Currency", options=["CAD", "USD"], required=True),
                    "quantity": st.column_config.NumberColumn("Quantity", min_value=0, format="%.4f"),
                    "average_cost": st.column_config.NumberColumn("Average cost", min_value=0, format="%.4f"),
                })
            try:
                holdings = importers.clean_holdings(edited)
                if holdings.empty:
                    holdings = None
                else:
                    st.download_button("Export reviewed holdings", holdings.to_csv(index=False), "reviewed_holdings.csv", "text/csv")
            except ValueError as error:
                st.error(f"Holdings table: {error}")
            if ocr_text:
                with st.expander("Raw text read from the screenshots"):
                    st.code(ocr_text, language=None)
        else:
            st.info("No holdings yet. Upload a holdings CSV or screenshots of your holdings page.")

    with right:
        st.markdown("#### Activity history")
        if activity_frames:
            activity = importers.combine_activity(activity_frames)
            counts = activity.type.value_counts()
            st.success(f"{len(activity)} activities from {activity.date.min():%b %d, %Y} to {activity.date.max():%b %d, %Y}.")
            st.dataframe(counts.rename_axis("Type").reset_index(name="Rows"), hide_index=True, width="stretch")
            with st.expander("View all activity"):
                st.dataframe(activity, hide_index=True, width="stretch")
        else:
            st.info("No activity yet. Upload your broker's activity export (RBC Direct Investing format "
                    "or the simple transactions template).")
        st.markdown("#### Templates")
        st.download_button("Holdings template (CSV)", importers.template_holdings().to_csv(index=False),
                           "holdings_template.csv", "text/csv")
        st.download_button("Transactions template (CSV)", importers.template_activity().to_csv(index=False),
                           "transactions_template.csv", "text/csv")

    # Yahoo symbol for every position seen in the holdings or the activity.
    keys = set()
    if holdings is not None:
        keys |= set(map(tuple, holdings[analytics.KEYS].to_numpy()))
    if activity is not None:
        keys |= set(map(tuple, activity.loc[activity.symbol.ne(""), analytics.KEYS].to_numpy()))
    if keys:
        default = market.default_mapping(sorted(keys), activity)
        saved = st.session_state.get("yahoo_overrides", {})
        default["yahoo"] = [saved.get((s, c), y) for s, c, y in default[["symbol", "currency", "yahoo"]].to_numpy()]
        with st.expander("Ticker mapping to Yahoo Finance"):
            st.caption("US-listed shares held on the CAD side keep their US ticker and are converted to CAD. "
                       "Canadian listings need a suffix such as .TO. Edit a Yahoo symbol if a price looks wrong.")
            edited_map = st.data_editor(
                default[["symbol", "currency", "yahoo"]], hide_index=True, width="stretch",
                disabled=["symbol", "currency"], key="mapping_editor",
                column_config={"symbol": "Broker ticker", "currency": "Account", "yahoo": "Yahoo symbol"})
        edited_map["yahoo"] = edited_map.yahoo.fillna("").str.strip().str.upper()
        edited_map.loc[edited_map.yahoo.eq(""), "yahoo"] = default.yahoo
        st.session_state["yahoo_overrides"] = {(s, c): y for s, c, y in edited_map.to_numpy()}
        mapping = edited_map.assign(quote_currency=edited_map.yahoo.map(market.quote_currency))


# ---------------------------------------------------------------- market data

closes = splits = fx = None
if mapping is not None:
    start = activity.date.min() if activity is not None else pd.Timestamp.today().normalize() - timedelta(days=10)
    currencies = tuple(sorted(set(mapping.currency) | set(mapping.quote_currency)))
    try:
        closes, splits, fx = fetch_market(tuple(sorted(mapping.yahoo.unique())), currencies, start)
        retried, changed = market.with_fallback_suffix(mapping, closes)
        if changed:
            mapping = retried
            currencies = tuple(sorted(set(mapping.currency) | set(mapping.quote_currency)))
            closes, splits, fx = fetch_market(tuple(sorted(mapping.yahoo.unique())), currencies, start)
    except Exception as error:
        st.error(f"Could not load prices from Yahoo Finance: {error}")
        closes = None

opening, opening_notes = (None, [])
if activity is not None:
    opening, opening_notes = analytics.opening_positions(activity, holdings)


# ---------------------------------------------------------------- allocation tab

with allocation_tab:
    view = holdings
    if view is None and activity is not None and closes is not None:
        prices = analytics.price_table(closes, analytics.attach_yahoo(analytics.share_moves(activity), mapping),
                                       closes.index, list(mapping.yahoo.unique()))
        try:
            view = analytics.holdings_from_activity(activity, opening, mapping, prices, fx)
        except (ValueError, KeyError) as error:
            st.error(f"Could not reconstruct holdings: {error}")
            view = None
        st.warning("No holdings snapshot was uploaded, so holdings were rebuilt from the activity export. "
                   "Positions bought before the export window may be missing, and their cost is estimated.")
    if view is None or closes is None or view.empty:
        st.info("Upload your holdings on the Upload tab to see your allocation.")
    else:
        last_fx = market.latest(fx) if len(fx.columns) else pd.Series(dtype=float)
        last_fx[market.BASE] = 1.0
        table = analytics.holdings_table(view, mapping, market.latest(closes), last_fx)
        unpriced = table[table.price.isna()]
        if len(unpriced) or table.market_value_cad.isna().any() or table.book_cost_cad.isna().any():
            st.error("Current prices or exchange rates are missing. Portfolio totals are withheld. Check ticker mappings and refresh prices.")
            st.stop()
        total_mv, total_book = table.market_value_cad.sum(), table.book_cost_cad.sum()
        gain = total_mv - total_book
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Market value", money(total_mv))
        k2.metric("Book cost", money(total_book))
        k3.metric("Unrealized gain", money(gain), pct(gain / total_book) if total_book else None)
        k4.metric("Positions", f"{len(table)}")
        if "USD" in last_fx:
            st.caption(f"USD converted at {last_fx['USD']:.4f} CAD. USD book cost is converted at today's rate.")
        st.caption(f"Latest price data: {closes.index.max():%b %d, %Y}. Quotes may be delayed; market value excludes cash.")

        table["label"] = [f"{s} ({c})" if (table.symbol == s).sum() > 1 else s
                          for s, c in zip(table.symbol, table.currency)]
        chart_col, table_col = st.columns([2, 3], gap="large")
        with chart_col:
            st.markdown("#### Allocation")
            st.plotly_chart(charts.allocation_donut(table, money(total_mv)), width="stretch")
            if len(table) > charts.MAX_SLICES:
                st.caption(f"Other: {', '.join(table.label.iloc[charts.MAX_SLICES:])}.")
        with table_col:
            st.markdown("#### Positions")
            shown = table[["label", "currency", "quantity", "average_cost_cad", "price_cad", "book_cost_cad",
                           "market_value_cad", "gain_cad", "gain_pct", "weight"]].copy()
            shown["gain_pct"] *= 100
            shown["weight"] *= 100
            styled = (shown.style
                      .format({"quantity": "{:,.6g}", "average_cost_cad": "{:,.2f}", "price_cad": "{:,.2f}",
                               "book_cost_cad": "{:,.2f}", "gain_cad": "{:+,.2f}",
                               "gain_pct": "{:+.1f}%", "market_value_cad": "{:,.0f}", "weight": "{:.1f}%"},
                              na_rep="n/a")
                      .map(lambda v: f"color: {GOOD if v > 0 else BAD if v < 0 else INK_2}",
                           subset=["gain_cad", "gain_pct"]))
            st.dataframe(styled, hide_index=True, width="stretch", height=min(35 * (len(shown) + 1) + 4, 560),
                         column_config={
                             "label": "Ticker", "currency": "Account", "quantity": "Quantity",
                             "average_cost_cad": "Avg cost (CAD)", "price_cad": "Price (CAD)", "book_cost_cad": "Book cost (CAD)",
                             "gain_cad": "Gain (CAD)", "gain_pct": "Gain %",
                             "market_value_cad": "Value (CAD)", "weight": "Weight",
                         })
            st.caption("All displayed amounts are CAD. Account indicates the original broker currency. Snapshot USD cost basis uses the current exchange rate; it is not historical CAD cost basis.")


# ---------------------------------------------------------------- history tab

with history_tab:
    if activity is None:
        st.info("Upload your activity export on the Upload tab to see historical performance. "
                "A holdings snapshot alone has no dates, so returns over time can't be calculated from it.")
    elif closes is None:
        st.info("Prices are needed for this tab. See the message above.")
    else:
        try:
            history = analytics.build_history(activity, opening, mapping, closes, splits, fx)
        except ValueError as error:
            st.error(str(error))
            st.stop()
        m = analytics.summary_metrics(history)
        st.caption(f"Covers {m['start']:%b %d, %Y} to today, the period in your activity export. "
                   f"Shares you already held on {m['start']:%b %d, %Y} count as invested at their "
                   f"{money(m['opening_value'])} market value that day.")
        for note in opening_notes + history["notes"]:
            st.warning(note)
        if holdings is None:
            st.warning("Without a holdings snapshot, positions you held for the whole period are not included.")

        r1 = st.columns(4)
        r1[0].metric("Investment over export period", money(m["invested"]),
                     help="Starting holdings value plus every purchase, including commissions.")
        r1[1].metric("Total sales (CAD)", money(m["sales"]), help=f"Net receipts from {m['sales_count']} sell trades.")
        r1[2].metric("Proceeds over export period", money(m["proceeds"] + m["dividends"]), help="Net sales receipts plus dividends, converted to CAD on their transaction dates.")
        r1[3].metric("Current portfolio value", money(m["current_value"]))
        r2 = st.columns(4)
        r2[0].metric("Total return", money(m["total_return"]), pct(m["total_return_pct"]),
                     help="Current value + proceeds + dividends − investment − fees. "
                          "The percentage is relative to investment over the export period.")
        r2[1].metric("XIRR", pct(m["xirr"]) if m["xirr"] is not None else "n/a",
                     help="Annualized return that accounts for the timing of every purchase, sale and dividend.")
        r2[2].metric("Dividends received", money(m["dividends"]))
        r2[3].metric("Fees", money(m["fees"], 2), help="Account fees such as ADR custody fees. Commissions are inside each trade.")

        st.markdown("#### Portfolio value over time")
        daily = history["daily"]
        st.plotly_chart(charts.value_trend(daily), width="stretch")
        st.caption("The gap between the lines is your gain. Net cash invested rises with purchases "
                   "and falls with sales and dividends.")
        st.caption("Lifetime metrics require records from account inception. This analysis covers the imported period, excludes uninvested cash, and treats purchases as investment outflows and sales/dividends as withdrawn proceeds. Internal currency transfers are not investment flows.")

        with st.expander("Cash flows used for these metrics (CAD)"):
            flows = history["flows"].copy()
            flows["type"] = flows.type.str.title()
            st.dataframe(flows.drop(columns="value").rename(columns={"cad": "amount (CAD)", "currency": "Source currency"}),
                         hide_index=True, width="stretch")
        with st.expander("Daily values"):
            st.dataframe(daily.rename(columns={"value": "Portfolio value", "net_invested": "Net cash invested"}),
                         width="stretch")
