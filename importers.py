"""Turn uploaded CSV files into two normalized tables.

holdings: one row per position -> symbol, currency, quantity, average_cost
    (currency is the account side the position sits in; average_cost is in that currency)
activity: one row per broker event -> date, settlement_date, type, symbol, currency,
    quantity (signed, sells negative), price (quote currency), value (signed cash in the
    account currency, buys negative), fx_rate (broker conversion rate, if any), description
"""
import csv
import io
import re
from datetime import timedelta

import numpy as np
import pandas as pd

HOLDING_COLS = ["symbol", "currency", "quantity", "average_cost"]
ACTIVITY_COLS = ["date", "settlement_date", "type", "symbol", "currency", "quantity",
                 "price", "value", "fx_rate", "description"]
DEFAULT_CURRENCY = "CAD"

RBC_TYPES = {"buy": "BUY", "sell": "SELL", "dividends": "DIVIDEND", "dividend": "DIVIDEND",
             "fees": "FEE", "fee": "FEE", "reorganization": "REORG", "transfers": "TRANSFER",
             "transfer": "TRANSFER"}

ALIASES = {
    "ticker": "symbol", "ticker_symbol": "symbol", "symbol_name": "symbol",
    "qty": "quantity", "shares": "quantity", "units": "quantity",
    "average_cost_basis": "average_cost", "avg_cost": "average_cost", "avg_price": "average_cost",
    "average_price": "average_cost", "cost_per_share": "average_cost",
    "book_value": "book_cost", "total_cost": "book_cost",
    "action": "type", "activity": "type", "transaction_type": "type",
    "trade_date": "date", "transaction_date": "date",
    "amount": "value", "net_amount": "value", "commission": "fees", "fee": "fees",
}


def _snake(name):
    key = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    return ALIASES.get(key, key)


def _number(series):
    cleaned = series.astype(str).str.replace(r"[$,£€\s]", "", regex=True).str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    return pd.to_numeric(cleaned.replace({"": np.nan, "nan": np.nan, "None": np.nan}), errors="coerce")


def _symbol(series):
    return series.fillna("").astype(str).str.strip().str.upper()


def _decode(data):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not read the file's text encoding.")


def read_csv(data):
    """Return ("holdings" | "activity", DataFrame) for an uploaded CSV file's bytes."""
    text = _decode(data)
    lines = text.splitlines()
    for i, line in enumerate(lines[:60]):
        if line.replace('"', "").startswith("Date,Activity,Symbol"):
            return "activity", parse_rbc_activity(lines[i:])
    frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    columns = {_snake(c) for c in frame.columns}
    if {"date", "type"} <= columns:
        return "activity", parse_generic_activity(frame)
    if "symbol" in columns and "quantity" in columns:
        return "holdings", parse_holdings(frame)
    raise ValueError("Unrecognized CSV. Expected an RBC activity export, a transactions file "
                     "(date, ticker, type, quantity, price), or a holdings file (ticker, quantity, average cost).")


def parse_rbc_activity(lines):
    """Parse the RBC Direct Investing 'Activity Export' table, starting at its header line."""
    table = []
    for line in lines:
        if not line.strip():
            break
        table.append(line)
    rows = list(csv.DictReader(io.StringIO("\n".join(table))))
    if not rows:
        raise ValueError("The activity export has no rows.")
    raw = pd.DataFrame(rows)
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["Date"], format="%B %d, %Y"),
        "settlement_date": pd.to_datetime(raw["Settlement Date"], format="%B %d, %Y", errors="coerce"),
        "symbol": _symbol(raw["Symbol"]),
        "currency": raw["Currency"].str.strip().str.upper().replace("", DEFAULT_CURRENCY),
        "quantity": _number(raw["Quantity"]).fillna(0.0),
        "price": _number(raw["Price"]),
        "value": _number(raw["Value"]).fillna(0.0),
        "description": raw["Description"].fillna(""),
    })
    out["settlement_date"] = out.settlement_date.fillna(out.date)
    kind = raw["Activity"].str.strip().str.lower().map(RBC_TYPES).fillna("OTHER")
    out["type"] = np.where(out.symbol.eq(""), "CASH", kind)
    out["fx_rate"] = _number(out.description.str.extract(r"EXCHANGE RATE\s+([\d.]+)", expand=False))
    return _finish_activity(out)


def parse_generic_activity(frame):
    """Parse a simple transactions file: date, ticker, type, quantity, price[, fees, currency, value]."""
    frame = frame.rename(columns=_snake)
    missing = {"date", "symbol", "type"} - set(frame.columns)
    if missing:
        raise ValueError("Transactions file is missing columns: " + ", ".join(sorted(missing)))
    out = pd.DataFrame({
        "date": pd.to_datetime(frame["date"]).dt.normalize(),
        "symbol": _symbol(frame["symbol"]),
        "currency": (frame["currency"].str.strip().str.upper().replace("", DEFAULT_CURRENCY)
                     if "currency" in frame else DEFAULT_CURRENCY),
        "quantity": _number(frame["quantity"]).fillna(0.0).abs() if "quantity" in frame else 0.0,
        "price": _number(frame["price"]) if "price" in frame else np.nan,
        "description": frame["description"] if "description" in frame else "",
    })
    empty = pd.Series(np.nan, index=frame.index)
    fees = (_number(frame["fees"]) if "fees" in frame else empty).fillna(0.0).abs()
    given = _number(frame["value"]) if "value" in frame else empty
    kind = frame["type"].str.strip().str.lower()
    kind = kind.replace({"bought": "buy", "sold": "sell", "div": "dividend", "dividends": "dividend",
                         "split": "reorganization", "fees": "fee"})
    out["type"] = kind.map({"buy": "BUY", "sell": "SELL", "dividend": "DIVIDEND", "fee": "FEE",
                            "reorganization": "REORG", "transfer": "TRANSFER"}).fillna("OTHER")
    is_buy, is_sell, is_fee = out.type.eq("BUY"), out.type.eq("SELL"), out.type.eq("FEE")
    gross = out.quantity * out.price.fillna(0.0)
    computed = np.select([is_buy, is_sell, is_fee], [-(gross + fees), gross - fees, -fees], default=0.0)
    signed_given = np.where(is_buy | is_fee, -given.abs(), given.abs())
    out["value"] = np.where(given.notna(), signed_given, computed)
    if (out.type.eq("DIVIDEND") & given.isna()).any():
        raise ValueError("Dividend rows need the cash amount in a 'value' or 'amount' column.")
    out["quantity"] = np.where(is_sell, -out.quantity, out.quantity)
    out["settlement_date"] = out.date
    out["fx_rate"] = np.nan
    if (out.symbol.eq("") & out.type.ne("OTHER")).any():
        raise ValueError("Every buy, sell, dividend, or fee row needs a ticker.")
    if out.type.isin(["BUY", "SELL"]).any() and out.loc[out.type.isin(["BUY", "SELL"]), "price"].isna().any():
        raise ValueError("Every buy and sell row needs a price.")
    return _finish_activity(out)


def _finish_activity(out):
    if out.date.isna().any():
        raise ValueError("Some activity rows have an unreadable date.")
    cutoff = pd.Timestamp.today().normalize() + timedelta(days=1)
    if out["date"].gt(cutoff).any():
        raise ValueError("Activity dates cannot be in the future.")
    out["description"] = out["description"].fillna("").astype(str)
    return out[ACTIVITY_COLS].sort_values(["date", "settlement_date"], kind="stable").reset_index(drop=True)


def parse_holdings(frame):
    frame = frame.rename(columns=_snake)
    out = pd.DataFrame({"symbol": _symbol(frame["symbol"])})
    out["currency"] = (frame["currency"].str.strip().str.upper().replace("", DEFAULT_CURRENCY)
                       if "currency" in frame else DEFAULT_CURRENCY)
    out["quantity"] = _number(frame["quantity"])
    if "average_cost" in frame:
        out["average_cost"] = _number(frame["average_cost"])
    elif "book_cost" in frame:
        out["average_cost"] = _number(frame["book_cost"]) / out.quantity
    else:
        raise ValueError("Holdings file needs an average cost (or book cost) column.")
    return clean_holdings(out)


def clean_holdings(frame):
    """Validate a holdings table, e.g. after the user edits it."""
    out = frame.copy()
    out = out[~(out.symbol.fillna("").astype(str).str.strip().eq("") & out.quantity.isna())]
    out["symbol"] = _symbol(out.symbol)
    out["currency"] = out.currency.fillna(DEFAULT_CURRENCY).astype(str).str.strip().str.upper().replace("", DEFAULT_CURRENCY)
    out["quantity"] = pd.to_numeric(out.quantity, errors="coerce")
    out["average_cost"] = pd.to_numeric(out.average_cost, errors="coerce")
    if not out.symbol.str.fullmatch(r"[A-Z0-9^][A-Z0-9.\-^=]{0,24}").all():
        raise ValueError("Every holding needs a valid ticker symbol.")
    if not out.currency.str.fullmatch(r"[A-Z]{3}").all():
        raise ValueError("Currency must be a 3-letter code such as CAD or USD.")
    for col in ["quantity", "average_cost"]:
        if not np.isfinite(out[col]).all() or (out[col] < 0).any():
            raise ValueError(f"{col.replace('_', ' ').title()} must contain nonnegative numbers.")
    if (out.quantity <= 0).any():
        raise ValueError("Quantity must be greater than zero.")
    if out.duplicated(["symbol", "currency"]).any():
        raise ValueError("Use one row per ticker and currency.")
    return out[HOLDING_COLS].reset_index(drop=True)


def combine_activity(frames):
    """Merge several activity exports, dropping rows repeated across overlapping files
    while keeping genuinely repeated trades inside one file."""
    if not frames:
        return pd.DataFrame(columns=ACTIVITY_COLS)
    keyed = []
    for frame in frames:
        frame = frame.copy()
        frame["_n"] = frame.groupby(ACTIVITY_COLS, dropna=False).cumcount()
        keyed.append(frame)
    merged = pd.concat(keyed, ignore_index=True).drop_duplicates(ACTIVITY_COLS + ["_n"])
    return merged.drop(columns="_n").sort_values(["date", "settlement_date"], kind="stable").reset_index(drop=True)


def template_holdings():
    return pd.DataFrame([["NVDA", "USD", 400, 14.6468], ["SHOP", "CAD", 100, 51.9335]], columns=HOLDING_COLS)


def template_activity():
    return pd.DataFrame([
        ["2025-01-15", "NVDA", "BUY", 400, 14.65, 9.95, "", "USD"],
        ["2025-06-02", "NVDA", "DIVIDEND", "", "", "", 4.00, "USD"],
        ["2025-09-10", "SHOP", "SELL", 20, 150.10, 9.95, "", "CAD"],
    ], columns=["date", "ticker", "type", "quantity", "price", "fees", "value", "currency"])
