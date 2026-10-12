"""Read holdings out of brokerage screenshots with local OCR (nothing leaves the machine).

Each holding row is expected to start with the ticker, followed left-to-right by
quantity, average cost, last price, book cost and market value, as in RBC Direct
Investing's holdings page. Section headers like "USD Holdings" and "Sub Total in CAD"
set the account currency of the rows around them.
"""
import re
from functools import lru_cache
from io import BytesIO

import numpy as np
import pandas as pd

from importers import DEFAULT_CURRENCY, HOLDING_COLS

TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
SKIP_WORDS = ("total", "holdings", "unrealized", "symbol", "quantity", "expand")


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _lines(image_bytes):
    """Group OCR text boxes into visual lines, each a left-to-right list of tokens."""
    from PIL import Image
    image = np.array(Image.open(BytesIO(image_bytes)).convert("RGB"))
    result, _ = _engine()(image)
    boxes = []
    for box, text, _conf in result or []:
        pts = np.array(box)
        boxes.append((pts[:, 1].mean(), pts[:, 0].min(), pts[:, 1].max() - pts[:, 1].min(), text.strip()))
    boxes.sort()
    lines = []
    for y, x, height, text in boxes:
        tolerance = max(8.0, 0.45 * height)
        if lines and abs(lines[-1]["y"] - y) <= tolerance:
            lines[-1]["parts"].append((x, text))
        else:
            lines.append({"y": y, "parts": [(x, text)]})
    return [[t for _, t in sorted(line["parts"])] for line in lines]


def parse_lines(lines, currency=None):
    """Turn OCR lines into holdings rows. Returns (rows, currency_in_effect_at_end, notes)."""
    rows, notes, pending = [], [], []
    for tokens in lines:
        text = " ".join(tokens)
        lower = text.lower()
        header = re.search(r"\b([A-Z]{3}) Holdings\b", text)
        if header:
            currency = header.group(1)
            continue
        subtotal = re.search(r"Sub Total in ([A-Z]{3})", text)
        if subtotal:
            for row in pending:
                row["currency"] = subtotal.group(1)
            pending = []
            continue
        if any(word in lower for word in SKIP_WORDS):
            continue
        words = [t.strip("+ ").strip() for t in tokens if t.strip("+ ").strip()]
        if not words or not TICKER.fullmatch(words[0]):
            continue
        numbers = [float(w.replace("$", "").replace(",", "")) for w in words[1:] if NUMBER.fullmatch(w)]
        if len(numbers) < 2:
            continue
        quantity, average_cost = numbers[0], numbers[1]
        row = {"symbol": words[0], "currency": currency, "quantity": quantity, "average_cost": average_cost}
        if len(numbers) >= 4 and quantity > 0:
            book = numbers[3]
            if abs(quantity * average_cost - book) > max(1.0, 0.01 * book):
                notes.append(f"{words[0]}: quantity x average cost does not match book cost {book:,.2f}. Please check this row.")
        rows.append(row)
        if currency is None:
            pending.append(row)
    return rows, currency, notes


def screenshot_holdings(images):
    """Read holdings from screenshots given in page order. Returns (DataFrame, raw_text, notes)."""
    # Screenshots are consecutive slices of one page, so a "Sub Total in CAD" in a later
    # image labels rows from earlier ones: parse all lines as one stream.
    all_lines, raw = [], []
    for image_bytes in images:
        lines = _lines(image_bytes)
        raw.append("\n".join(" | ".join(t) for t in lines))
        all_lines += lines
    rows, _, notes = parse_lines(all_lines)
    frame = pd.DataFrame(rows, columns=HOLDING_COLS)
    unknown = frame.currency.isna()
    if unknown.any():
        notes.append(f"No currency header found for {', '.join(frame.symbol[unknown])}. Assumed {DEFAULT_CURRENCY}.")
        frame.loc[unknown, "currency"] = DEFAULT_CURRENCY
    # Overlapping screenshots repeat rows; keep one copy.
    frame = frame.drop_duplicates().reset_index(drop=True)
    return frame, "\n\n".join(raw), notes
