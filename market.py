"""Yahoo Finance access (via yfinance) and broker-symbol -> Yahoo-symbol mapping."""
from datetime import timedelta
from pathlib import Path
import logging

import pandas as pd

BASE = "CAD"
CAD_SUFFIXES = (".TO", ".V", ".NE", ".CN")
MAPPING_COLS = ["symbol", "currency", "yahoo", "quote_currency"]


def quote_currency(yahoo):
    return "CAD" if yahoo.upper().endswith(CAD_SUFFIXES) else "USD"


def fx_symbol(currency):
    return None if currency == BASE else f"{currency}{BASE}=X"


def guess_yahoo(symbol, currency, activity):
    """Pick the Yahoo listing for a broker position.

    RBC holds US-listed shares on the CAD side and converts each trade at an exchange
    rate printed in the description. A CAD-side trade *without* a rate is a Canadian
    listing (e.g. XNDU on the TSX). With no trades to go by, assume the US listing,
    which is how RBC prices US names on the CAD side.
    """
    if symbol.endswith(CAD_SUFFIXES):
        return symbol
    base = symbol.replace(".", "-")
    if currency != "CAD":
        return base
    if activity is not None and len(activity):
        trades = activity[(activity.symbol == symbol) & (activity.currency == currency)
                          & activity.type.isin(["BUY", "SELL"])]
        if len(trades):
            return base if trades.fx_rate.notna().any() else base + ".TO"
    return base


def default_mapping(positions, activity):
    rows = []
    for symbol, currency in positions:
        yahoo = guess_yahoo(symbol, currency, activity)
        rows.append([symbol, currency, yahoo, quote_currency(yahoo)])
    return pd.DataFrame(rows, columns=MAPPING_COLS)


def unadjust_splits(close, splits):
    """yfinance back-adjusts closes for splits; undo that so prices match share counts
    as the broker recorded them on each date."""
    factor = splits.replace(0, 1).fillna(1)
    after = factor[::-1].cumprod()[::-1].shift(-1).fillna(1)
    return close * after


def download(yahoo_symbols, currencies, start):
    """Daily closes in quote currency (split-unadjusted), split events, and FX to CAD.

    Returns (closes, splits, fx) where fx has one column per non-CAD currency.
    """
    import yfinance as yf

    cache_dir = Path(__file__).parent / ".cache" / "yfinance"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))

    fx_tickers = sorted({fx_symbol(c) for c in currencies if fx_symbol(c)})
    tickers = sorted(set(yahoo_symbols)) + fx_tickers
    if not tickers:
        empty = pd.DataFrame(index=pd.DatetimeIndex([]))
        return empty, empty, empty
    messages = []
    class DownloadErrors(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                messages.append(record.getMessage())
    handler = DownloadErrors()
    logger = logging.getLogger("yfinance")
    logger.addHandler(handler)
    try:
        data = yf.download(tickers, start=pd.Timestamp(start) - timedelta(days=10), auto_adjust=False,
                           actions=True, progress=False, group_by="column", threads=False, timeout=20)
    finally:
        logger.removeHandler(handler)
    if data is None or data.empty:
        detail = " | ".join(dict.fromkeys(messages)) or "No error details were returned by yfinance."
        raise ConnectionError(f"Yahoo Finance returned no data for {', '.join(tickers)}. Details: {detail}")
    close = data["Close"].reindex(columns=tickers)
    if close.isna().all().all():
        detail = " | ".join(dict.fromkeys(messages)) or "All price columns were empty."
        raise ConnectionError(f"Yahoo Finance returned empty prices. Details: {detail}")
    split_events = (data["Stock Splits"] if "Stock Splits" in data else pd.DataFrame(index=data.index)).reindex(columns=tickers).fillna(0)
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    split_events.index = close.index
    closes = pd.DataFrame({t: unadjust_splits(close[t], split_events[t]) for t in yahoo_symbols}, index=close.index)
    fx = pd.DataFrame({t[:3]: close[t] for t in fx_tickers}, index=close.index)
    return closes, split_events[list(yahoo_symbols)] if len(yahoo_symbols) else split_events, fx


def latest(series_frame):
    """Last available value per column."""
    return series_frame.ffill().iloc[-1] if len(series_frame) else pd.Series(dtype=float)


def fx_rates(fx, days):
    """FX-to-CAD table on the given days, with a constant 1.0 column for CAD."""
    table = fx.reindex(fx.index.union(days)).sort_index().ffill().bfill().reindex(days) if len(fx.columns) else pd.DataFrame(index=days)
    table[BASE] = 1.0
    return table


def missing_symbols(closes):
    return [c for c in closes.columns if closes[c].isna().all()]


def with_fallback_suffix(mapping, closes):
    """CAD-side symbols that Yahoo doesn't know as US listings get retried as TSX listings."""
    bad = set(missing_symbols(closes))
    retry = mapping.yahoo.isin(bad) & mapping.currency.eq("CAD") & ~mapping.yahoo.str.upper().str.endswith(CAD_SUFFIXES)
    fixed = mapping.copy()
    fixed.loc[retry, "yahoo"] = fixed.loc[retry, "yahoo"] + ".TO"
    fixed["quote_currency"] = fixed.yahoo.map(quote_currency)
    return fixed, bool(retry.any())
