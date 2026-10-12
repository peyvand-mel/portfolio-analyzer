"""Portfolio math. All money totals are reported in CAD (market.BASE).

A broker export covers a limited window (RBC: 15 months). Shares already held when the
window opens are reconstructed as "opening positions" and treated as if bought at their
market value on the first day of the window, so every metric is consistent over that window.
"""
import numpy as np
import pandas as pd
from scipy.optimize import brentq

from market import BASE

KEYS = ["symbol", "currency"]
NO_SHARES = ["DIVIDEND", "FEE", "CASH"]
CASH_FLOW_TYPES = ["BUY", "SELL", "DIVIDEND", "FEE", "OTHER"]


# ---------------------------------------------------------------- positions

def share_moves(activity):
    """Rows that change a share count, in an order where same-day transfers and splits
    land before trades (RBC books a journal the day after the sale it funds)."""
    moves = activity[activity.symbol.ne("") & ~activity.type.isin(NO_SHARES) & activity.quantity.ne(0)].copy()
    moves["_first"] = np.where(moves.type.isin(["TRANSFER", "REORG"]), 0, 1)
    return moves.sort_values(["settlement_date", "_first", "date"], kind="stable").drop(columns="_first")


def opening_positions(activity, holdings=None):
    """Shares per (symbol, currency) held before the first activity row.

    With a holdings snapshot: snapshot minus everything the activity added or removed.
    Without one: the smallest count that keeps every position from going negative,
    which recovers positions that were sold during the window but not ones still held.
    """
    moves = share_moves(activity)
    moves["running"] = moves.groupby(KEYS).quantity.cumsum()
    need = (-moves.groupby(KEYS).running.min()).clip(lower=0)
    net = moves.groupby(KEYS).quantity.sum()
    notes = []
    if holdings is not None and len(holdings):
        snap = holdings.set_index(KEYS).quantity
        index = snap.index.union(net.index).union(need.index)
        opening = snap.reindex(index, fill_value=0) - net.reindex(index, fill_value=0)
        need = need.reindex(index, fill_value=0)
        for (symbol, currency) in index[opening < need - 1e-6]:
            notes.append(f"{symbol} ({currency}): the snapshot and the activity disagree on share counts. "
                         "Opening shares were set to the minimum the activity requires.")
        opening = np.maximum(opening, need)
    else:
        opening = need
    opening = opening[opening > 1e-9].rename("quantity").reset_index()
    return opening[KEYS + ["quantity"]], notes


def attach_yahoo(frame, mapping):
    return frame.merge(mapping[KEYS + ["yahoo", "quote_currency"]], on=KEYS, how="left")


# ---------------------------------------------------------------- prices & FX

def rates_on(fx, dates, currencies):
    """FX-to-CAD rate for each (date, currency) pair, using the last known rate."""
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    out = np.ones(len(dates))
    for currency in set(currencies) - {BASE}:
        if currency not in fx:
            raise ValueError(f"No exchange rate available for {currency}.")
        series = fx[currency].dropna().sort_index()
        series = series[~series.index.duplicated(keep="last")]
        mask = np.asarray(pd.Series(list(currencies)) == currency)
        wanted = dates[mask]
        filled = series.reindex(series.index.union(wanted.unique())).ffill()
        rates = filled.reindex(wanted).to_numpy()
        if not np.isfinite(rates).all() or (rates <= 0).any():
            raise ValueError(f"Missing historical {currency}/CAD exchange rates.")
        out[mask] = rates
    return out


def price_table(closes, moves, days, symbols):
    """Daily quote-currency prices per Yahoo symbol. Gaps before Yahoo has data are
    filled from the broker's own trade prices. Leading gaps stay NaN."""
    full_index = closes.index.union(days).union(pd.DatetimeIndex(moves.date.unique()))
    prices = closes.reindex(index=full_index, columns=sorted(set(closes.columns) | set(symbols))).sort_index()
    trades = moves[moves.type.isin(["BUY", "SELL"]) & moves.price.notna()]
    for symbol, group in trades.groupby("yahoo"):
        implied = group.groupby("date").price.last()
        column = prices[symbol] if symbol in prices else pd.Series(np.nan, index=full_index)
        prices[symbol] = column.fillna(implied.reindex(full_index))
    return prices.ffill()


def last_close_before(prices, day):
    before = prices[prices.index < day]
    return before.iloc[-1] if len(before) else prices.iloc[0]


def align_splits(moves, splits):
    """Move broker split rows onto Yahoo's split date so share count and price change together."""
    moves = moves.copy()
    for i in moves.index[moves.type.eq("REORG")]:
        symbol = moves.at[i, "yahoo"]
        if symbol not in splits:
            continue
        events = splits.index[splits[symbol].fillna(0).ne(0)]
        if len(events):
            nearest = events[np.argmin(np.abs((events - moves.at[i, "date"]).days))]
            if abs((nearest - moves.at[i, "date"]).days) <= 10:
                moves.at[i, "date"] = nearest
    return moves


# ---------------------------------------------------------------- tab 2

def holdings_table(holdings, mapping, last_prices, last_fx):
    """Per-position prices and gains in the account currency, plus CAD totals and weights."""
    table = attach_yahoo(holdings, mapping)
    to_cad_quote = table.quote_currency.map(last_fx)
    to_cad_account = table.currency.map(last_fx)
    table["price"] = table.yahoo.map(last_prices) * to_cad_quote / to_cad_account
    table["book_cost"] = table.quantity * table.average_cost
    table["market_value"] = table.quantity * table.price
    table["gain"] = table.market_value - table.book_cost
    table["gain_pct"] = table.gain / table.book_cost.replace(0, np.nan)
    table["book_cost_cad"] = table.book_cost * to_cad_account
    table["market_value_cad"] = table.market_value * to_cad_account
    table["gain_cad"] = table.market_value_cad - table.book_cost_cad
    table["average_cost_cad"] = table.average_cost * to_cad_account
    table["price_cad"] = table.price * to_cad_account
    table["weight"] = table.market_value_cad / table.market_value_cad.sum()
    return table.sort_values("market_value_cad", ascending=False).reset_index(drop=True)


def holdings_from_activity(activity, opening, mapping, prices, fx):
    """Rebuild current holdings from activity alone (average-cost method). Shares held
    before the window are costed at their market value when the window opens."""
    start = activity.date.min()
    opening = attach_yahoo(opening, mapping)
    ledger, transfers = {}, {}
    open_prices = last_close_before(prices.bfill(), start)
    for row in opening.itertuples():
        rate = rates_on(fx, [start], [row.quote_currency])[0] / rates_on(fx, [start], [row.currency])[0]
        ledger[(row.symbol, row.currency)] = [row.quantity, row.quantity * open_prices[row.yahoo] * rate]
    for row in share_moves(activity).itertuples():
        qty, cost = ledger.get((row.symbol, row.currency), [0.0, 0.0])
        if row.type == "BUY":
            qty, cost = qty + row.quantity, cost - row.value
        elif row.quantity < 0:
            removed = cost * min(1.0, -row.quantity / qty) if qty > 0 else 0.0
            if row.type == "TRANSFER":
                transfers.setdefault(row.symbol, []).append(removed * rates_on(fx, [row.date], [row.currency])[0])
            qty, cost = qty + row.quantity, cost - removed
        else:
            if row.type == "TRANSFER" and transfers.get(row.symbol):
                cost += transfers[row.symbol].pop(0) / rates_on(fx, [row.date], [row.currency])[0]
            qty += row.quantity
        ledger[(row.symbol, row.currency)] = [qty, cost]
    rows = [{"symbol": s, "currency": c, "quantity": q, "average_cost": cost / q}
            for (s, c), (q, cost) in ledger.items() if q > 1e-8]
    return pd.DataFrame(rows, columns=KEYS + ["quantity", "average_cost"])


# ---------------------------------------------------------------- tab 3

def build_history(activity, opening, mapping, closes, splits, fx, today=None):
    """Daily CAD portfolio value and the CAD cash flows behind it.

    Returns dict with: daily (DataFrame value/net_invested), flows (DataFrame),
    opening_value, start, notes.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    start = activity.date.min()
    days = pd.bdate_range(start, today)
    if today not in days:
        days = days.append(pd.DatetimeIndex([today]))
    moves = align_splits(attach_yahoo(share_moves(activity), mapping), splits)
    opening = attach_yahoo(opening, mapping)
    symbols = sorted(set(opening.yahoo) | set(moves.yahoo))
    prices = price_table(closes, moves, days, symbols)
    notes = []
    quote = pd.concat([opening[["yahoo", "quote_currency"]], moves[["yahoo", "quote_currency"]]]).drop_duplicates("yahoo").set_index("yahoo").quote_currency
    fx_days = {c: rates_on(fx, days, [c] * len(days)) for c in set(quote)}

    open_qty = opening.groupby("yahoo").quantity.sum()
    open_px = last_close_before(prices, start)
    value = pd.Series(0.0, index=days)
    opening_value = 0.0
    for symbol in symbols:
        changes = moves[moves.yahoo == symbol].groupby("date").quantity.sum()
        start_qty = open_qty.get(symbol, 0.0)
        steps = changes.reindex(changes.index.union(days), fill_value=0.0).sort_index().cumsum() + start_qty
        qty = steps.reindex(days, method="ffill").clip(lower=0)
        px = prices[symbol].reindex(days)
        if (px.isna() & (qty > 1e-8)).any():
            raise ValueError(f"Missing historical prices while {symbol} was held. Correct its Yahoo mapping or supply earlier data; historical totals are withheld.")
        value += (qty * px).where(qty > 1e-8, 0.0).to_numpy() * fx_days[quote[symbol]]
        if start_qty:
            first_px = open_px[symbol]
            if pd.isna(first_px):
                raise ValueError(f"Missing opening valuation for {symbol}; historical totals are withheld.")
            opening_value += start_qty * first_px * rates_on(fx, [start], [quote[symbol]])[0]

    flows = activity[activity.type.isin(CASH_FLOW_TYPES) & activity.symbol.ne("") & activity.value.ne(0)]
    flows = flows[["date", "type", "symbol", "currency", "value"]].copy()
    flows["cad"] = flows.value * rates_on(fx, flows.date, flows.currency)
    if opening_value > 0:
        opening_row = pd.DataFrame([{"date": start, "type": "OPENING", "symbol": "", "currency": BASE,
                                     "value": -opening_value, "cad": -opening_value}])
        flows = pd.concat([opening_row, flows], ignore_index=True)
    invested = (-flows.groupby("date").cad.sum()).sort_index().cumsum()
    net_invested = invested.reindex(invested.index.union(days)).ffill().fillna(0.0).reindex(days)
    daily = pd.DataFrame({"value": value, "net_invested": net_invested})
    return {"daily": daily, "flows": flows, "opening_value": opening_value, "start": start, "notes": notes}


def summary_metrics(history, today=None):
    flows, daily = history["flows"], history["daily"]
    by_type = flows.groupby("type").cad.sum()
    current = float(daily.value.iloc[-1])
    invested = history["opening_value"] - by_type.get("BUY", 0.0)
    total_return = current + flows.cad.sum()
    today = pd.Timestamp(today or daily.index[-1])
    rate = xirr(list(flows.date) + [today], list(flows.cad) + [current])
    return {
        "start": history["start"],
        "opening_value": history["opening_value"],
        "invested": invested,
        "sales_count": int((flows.type == "SELL").sum()),
        "sales": by_type.get("SELL", 0.0),
        "proceeds": by_type.get("SELL", 0.0),
        "dividends": by_type.get("DIVIDEND", 0.0),
        "fees": -by_type.get("FEE", 0.0),
        "current_value": current,
        "total_return": total_return,
        "total_return_pct": total_return / invested if invested else np.nan,
        "xirr": rate,
    }


def xirr(dates, amounts):
    """Annualized internal rate of return for dated cash flows (negative = money in).
    Returns None when it is undefined or has more than one solution."""
    dates = pd.to_datetime(pd.Series(dates))
    amounts = np.asarray(amounts, dtype=float)
    if not (np.any(amounts < 0) and np.any(amounts > 0)):
        return None
    years = np.asarray((dates - dates.min()).dt.days) / 365.0
    if years.max() == 0:
        return None

    def npv(log_rate):  # solve in log(1 + rate) space
        return np.sum(amounts * np.exp(np.clip(-log_rate * years, -700, 700)))

    grid = np.linspace(-15, 15, 601)
    roots = []
    for left, right in zip(grid[:-1], grid[1:]):
        if abs(npv(left)) < 1e-8 or npv(left) * npv(right) < 0:
            root = np.expm1(left if abs(npv(left)) < 1e-8 else brentq(npv, left, right))
            if not roots or abs(root - roots[-1]) > 1e-6:
                roots.append(root)
    return roots[0] if len(roots) == 1 else None
