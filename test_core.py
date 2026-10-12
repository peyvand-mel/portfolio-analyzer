"""Offline tests: synthetic data only, no network or OCR engine needed."""
import numpy as np
import pandas as pd
import pytest

import analytics
import importers
import market
import ocr

RBC = '''"Activity Export as of Oct 5, 2026 at 12:18:22 pm ET"

"Account: 00000000 - RRSP"

"Date","Activity","Symbol","Symbol Description","Quantity","Price","Settlement Date","Account","Value","Currency","Description"
"September 2, 2026","Reorganization","AAA","AAA CORP","10","","September 4, 2026","1","0","CAD","STK SPLIT"
"June 2, 2026","Dividends","AAA","AAA CORP","","","June 2, 2026","1","5.5","CAD","DIV"
"May 4, 2026","Sell","OLD","OLD CO","-20","50","May 5, 2026","1","1380","CAD","OLD CO UNSOLICITED CA EXCHANGE RATE       1.38000000"
"April 24, 2026","Transfers","MOV","MOV CO","-5","","April 24, 2026","1","0","CAD","TFR TO U$"
"April 24, 2026","Transfers","MOV","MOV CO","5","","April 24, 2026","1","0","USD","TFR FROM C$"
"April 23, 2026","Sell","MOV","MOV CO","-5","12","April 24, 2026","1","60","USD","MOV CO UNSOLICITED CA"
"March 2, 2026","Buy","MOV","MOV CO","5","10","March 3, 2026","1","-70","CAD","MOV CO UNSOLICITED DA EXCHANGE RATE       1.40000000"
"February 2, 2026","Buy","TSX","TSX CO","4","25","February 3, 2026","1","-100","CAD","TSX CO UNSOLICITED DA"
"January 5, 2026","Buy","AAA","AAA CORP","10","20","January 6, 2026","1","-280","CAD","AAA UNSOLICITED DA EXCHANGE RATE       1.40000000"
"January 5, 2026","Transfers","","","","","January 5, 2026","1","-40","USD","TRF - FOREIGN EXCHANGE"

"Disclaimers"
"Practice accounts are for educational purposes only."
'''


@pytest.fixture
def activity():
    kind, frame = importers.read_csv(RBC.encode())
    assert kind == "activity"
    return frame


def test_rbc_activity_parsing(activity):
    assert len(activity) == 10
    assert activity.type.value_counts().to_dict() == {
        "TRANSFER": 2, "SELL": 2, "BUY": 3, "REORG": 1, "DIVIDEND": 1, "CASH": 1}
    sell = activity[(activity.symbol == "OLD")].iloc[0]
    assert sell.quantity == -20 and sell.value == 1380 and sell.fx_rate == pytest.approx(1.38)
    assert activity.date.is_monotonic_increasing


def test_generic_transactions_and_holdings():
    kind, tx = importers.read_csv(importers.template_activity().to_csv(index=False).encode())
    assert kind == "activity"
    assert list(tx.type) == ["BUY", "DIVIDEND", "SELL"]
    assert tx.value.tolist() == pytest.approx([-(400 * 14.65 + 9.95), 4.0, 20 * 150.10 - 9.95])
    assert tx.quantity.tolist() == [400, 0, -20]
    kind, holdings = importers.read_csv(b"Symbol,Shares,Book Cost\nmsft,10,\"$1,000.00\"\n")
    assert kind == "holdings"
    assert holdings.iloc[0].to_dict() == {"symbol": "MSFT", "currency": "CAD", "quantity": 10.0, "average_cost": 100.0}


def test_holdings_validation():
    with pytest.raises(ValueError, match="one row per ticker"):
        importers.clean_holdings(pd.DataFrame([["A", "CAD", 1, 1], ["A", "CAD", 2, 2]], columns=importers.HOLDING_COLS))
    same_ticker_two_sides = pd.DataFrame([["A", "CAD", 1, 1], ["A", "USD", 2, 2]], columns=importers.HOLDING_COLS)
    assert len(importers.clean_holdings(same_ticker_two_sides)) == 2


def test_combine_activity_drops_overlap_but_keeps_real_repeats(activity):
    doubled = pd.concat([activity, activity.iloc[[0]]], ignore_index=True)
    merged = importers.combine_activity([doubled, activity])
    assert len(merged) == len(activity) + 1


def test_yahoo_mapping(activity):
    keys = [("AAA", "CAD"), ("TSX", "CAD"), ("MOV", "USD"), ("NOTRADE", "CAD"), ("BRK.B", "USD")]
    mapping = market.default_mapping(keys, activity).set_index(["symbol", "currency"])
    assert mapping.loc[("AAA", "CAD"), "yahoo"] == "AAA"        # converted at an exchange rate: US listing
    assert mapping.loc[("TSX", "CAD"), "yahoo"] == "TSX.TO"     # CAD trade with no rate: TSX listing
    assert mapping.loc[("TSX", "CAD"), "quote_currency"] == "CAD"
    assert mapping.loc[("NOTRADE", "CAD"), "yahoo"] == "NOTRADE"
    assert mapping.loc[("BRK.B", "USD"), "yahoo"] == "BRK-B"


def test_opening_positions(activity):
    holdings = pd.DataFrame([["AAA", "CAD", 20, 14], ["TSX", "CAD", 4, 25], ["KEEP", "USD", 7, 3]],
                            columns=importers.HOLDING_COLS)
    opening, notes = analytics.opening_positions(activity, holdings)
    got = {(r.symbol, r.currency): r.quantity for r in opening.itertuples()}
    # OLD was sold without a buy; KEEP was held throughout; MOV's transfer nets out.
    assert got == {("OLD", "CAD"): 20, ("KEEP", "USD"): 7}
    assert notes == []
    without_snapshot, _ = analytics.opening_positions(activity)
    assert {(r.symbol, r.currency): r.quantity for r in without_snapshot.itertuples()} == {("OLD", "CAD"): 20}


def test_unadjust_splits():
    idx = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"])
    close = pd.Series([50.0, 51.0, 102.0], index=idx)       # Yahoo back-adjusts pre-split closes
    splits = pd.Series([0.0, 0.0, 2.0], index=idx)
    assert market.unadjust_splits(close, splits).tolist() == [100.0, 102.0, 102.0]


def test_xirr_known_value():
    rate = analytics.xirr(["2025-01-01", "2026-01-01"], [-1000, 1100])
    assert rate == pytest.approx(0.10, abs=1e-6)
    assert analytics.xirr(["2025-01-01"], [-1000]) is None
    assert analytics.xirr(["2025-01-01", "2026-01-01"], [-100, 100]) == pytest.approx(0)


def _market(days, symbols, usdcad=1.4):
    closes = pd.DataFrame({s: 10.0 for s in symbols}, index=days)
    splits = pd.DataFrame(0.0, index=days, columns=symbols)
    fx = pd.DataFrame({"USD": usdcad}, index=days)
    return closes, splits, fx


def test_history_and_metrics():
    activity = pd.DataFrame([
        ["2026-01-05", "BUY", "AAA", "USD", 10, 10.0, -100.0],
        ["2026-03-02", "SELL", "AAA", "USD", -5, 10.0, 50.0],
        ["2026-04-01", "DIVIDEND", "AAA", "USD", 0, np.nan, 2.0],
    ], columns=["date", "type", "symbol", "currency", "quantity", "price", "value"])
    activity["date"] = pd.to_datetime(activity.date)
    activity["settlement_date"] = activity.date
    activity["fx_rate"] = np.nan
    activity["description"] = ""
    mapping = market.default_mapping([("AAA", "USD")], activity)
    days = pd.bdate_range("2025-12-20", "2026-06-30")
    closes, splits, fx = _market(days, ["AAA"])
    closes.loc["2026-05-01":, "AAA"] = 20.0
    opening, _ = analytics.opening_positions(activity)
    history = analytics.build_history(activity, opening, mapping, closes, splits, fx, today="2026-06-30")
    daily = history["daily"]
    assert daily.value.iloc[0] == pytest.approx(10 * 10 * 1.4)
    assert daily.value.iloc[-1] == pytest.approx(5 * 20 * 1.4)
    m = analytics.summary_metrics(history, today="2026-06-30")
    assert m["invested"] == pytest.approx(140)
    assert m["sales_count"] == 1
    assert m["proceeds"] == pytest.approx(70)
    assert m["dividends"] == pytest.approx(2.8)
    assert m["total_return"] == pytest.approx(140 + 70 + 2.8 - 140)
    assert m["xirr"] > 0


def test_history_prices_split_on_yahoo_date():
    activity = pd.DataFrame([
        ["2026-01-05", "BUY", "AAA", "USD", 10, 10.0, -100.0],
        ["2026-03-02", "REORG", "AAA", "USD", 10, np.nan, 0.0],   # broker books split a day early
    ], columns=["date", "type", "symbol", "currency", "quantity", "price", "value"])
    activity["date"] = pd.to_datetime(activity.date)
    activity["settlement_date"] = activity.date
    activity["fx_rate"] = np.nan
    activity["description"] = ""
    mapping = market.default_mapping([("AAA", "USD")], activity)
    days = pd.bdate_range("2026-01-01", "2026-03-31")
    closes, splits, fx = _market(days, ["AAA"], usdcad=1.0)
    closes.loc[:"2026-03-02", "AAA"] = 20.0      # unadjusted pre-split price
    splits.loc["2026-03-03", "AAA"] = 2.0
    opening, _ = analytics.opening_positions(activity)
    history = analytics.build_history(activity, opening, mapping, closes, splits, fx, today="2026-03-31")
    assert history["daily"].value.loc["2026-03-02"] == pytest.approx(200)
    assert history["daily"].value.loc["2026-03-03"] == pytest.approx(200)


def test_holdings_table_converts_currencies():
    holdings = pd.DataFrame([["AAA", "CAD", 10, 14.0], ["BBB", "USD", 2, 50.0]], columns=importers.HOLDING_COLS)
    mapping = market.default_mapping([("AAA", "CAD"), ("BBB", "USD")], None)
    table = analytics.holdings_table(holdings, mapping, pd.Series({"AAA": 20.0, "BBB": 100.0}),
                                     pd.Series({"USD": 1.5, "CAD": 1.0})).set_index("symbol")
    assert table.loc["AAA", "price"] == pytest.approx(30.0)          # US listing shown in CAD
    assert table.loc["BBB", "price"] == pytest.approx(100.0)         # USD side stays in USD
    assert table.loc["BBB", "market_value_cad"] == pytest.approx(300.0)
    assert table.loc["BBB", "price_cad"] == pytest.approx(150.0)
    assert table.loc["BBB", "average_cost_cad"] == pytest.approx(75.0)
    assert table.loc["BBB", "gain_cad"] == pytest.approx(150.0)
    assert table.weight.sum() == pytest.approx(1.0)


def test_screenshot_line_parsing():
    lines = [
        ["+", "APH", "100", "105.9943", "123.928", "10,599.43", "12,392.80", "+1,793.37", "Buy I Sell"],
        ["AMPHENOL", "(+16.92%)"],
        ["ARM HOLDINGS", "(+162.75%)"],
        ["Sub Total in CAD:", "10,599.43", "12,392.80"],
        ["USD Holdings", "87,550.32 (+1,210.32%)", "7,233.68", "94,784.00"],
        ["SYMBOL/NAME", "QUANTITY", "AVERAGE", "LAST"],
        ["+", "NVDA", "400", "14.6468", "233.95", "5,858.73", "93,580.00"],
        ["XNDU", "280", "4.9105", "4.30", "9,999.00", "1,204.00"],
    ]
    rows, currency, notes = ocr.parse_lines(lines)
    assert [(r["symbol"], r["currency"], r["quantity"], r["average_cost"]) for r in rows] == [
        ("APH", "CAD", 100, 105.9943), ("NVDA", "USD", 400, 14.6468), ("XNDU", "USD", 280, 4.9105)]
    assert currency == "USD"
    assert len(notes) == 1 and notes[0].startswith("XNDU")


def test_actual_rbc_export_and_mapping():
    from pathlib import Path
    path = next(iter(sorted(Path(__file__).resolve().parents[1].glob("Activity*.csv"))), None)
    if path is None:
        pytest.skip("Personal example export is not distributed")
    kind, tx = importers.read_csv(path.read_bytes())
    assert kind == "activity" and len(tx) == 49
    assert tx.type.value_counts().to_dict() == {"DIVIDEND": 17, "BUY": 13, "SELL": 11, "FEE": 3, "TRANSFER": 2, "CASH": 2, "REORG": 1}
    keys = sorted(set(map(tuple, tx.loc[tx.symbol.ne(""), analytics.KEYS].to_numpy())))
    mapping = market.default_mapping(keys, tx).set_index(analytics.KEYS)
    assert mapping.loc[("XNDU", "CAD"), "yahoo"] == "XNDU.TO"
    assert mapping.loc[("XNDU", "USD"), "yahoo"] == "XNDU"
    assert mapping.loc[("ARM", "CAD"), "yahoo"] == "ARM"


def test_missing_historical_fx_is_rejected():
    fx = pd.DataFrame({"USD": [1.4]}, index=pd.to_datetime(["2026-02-01"]))
    with pytest.raises(ValueError, match="Missing historical"):
        analytics.rates_on(fx, ["2026-01-01"], ["USD"])


def test_suffixed_ticker_preserved():
    assert market.guess_yahoo("SHOP.TO", "CAD", None) == "SHOP.TO"
