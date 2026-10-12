# Portfolio Analyzer

A three-tab Streamlit app for a brokerage portfolio, priced with Yahoo Finance through `yfinance` and managed with uv. All totals are in CAD.

1. **Upload**: holdings snapshot (CSV or screenshots of the holdings page, read locally with OCR) and activity history (RBC Direct Investing "Activity Export" CSV, or the generic transactions template).
2. **Allocation**: donut chart of portfolio weight, plus per-position quantity, average cost, current price, book cost, market value and gain.
3. **Historical performance**: investment over the imported period, sales receipts, proceeds including dividends, current value, total return, XIRR, fees, and a daily value trend against net cash invested.

## Run

Double-click **Start Portfolio Analyzer.bat**, then open http://localhost:8501. Keep the launcher window open. It uses the installed virtual environment directly and sets up dependencies with UV if the environment is missing. If your local activity CSV and screenshots are present, click **Load my saved portfolio** to read them together. Personal brokerage files are excluded from the repository. Review OCR results in Upload before relying on them. Manual holdings entry and export of reviewed holdings are also available.

```powershell
.\.tools\uv.exe sync
.\.tools\uv.exe run streamlit run app.py
```

Use plain `uv` instead if uv is installed on your PATH. Run the tests with `.\.tools\uv.exe run pytest`.

On Windows, double-click **Start Portfolio Analyzer.bat**. On the Upload tab, **Use my October 5 RBC activity CSV** appears when the matching local file exists. **Use my existing holdings screenshots** loads local screenshots for reconciliation; review the editable rows. You can also upload screenshots in page order. Use **Refresh prices and exchange rates** to clear the five-minute cache.

## Inputs

- **Holdings CSV** has the columns `ticker, quantity, average_cost` and an optional `currency` (CAD by default). A `book_cost` column can replace `average_cost`.
- **Screenshots** should be uploaded in page order. The reader expects ticker, quantity, average cost, last price, book cost and market value from left to right. It uses the "USD Holdings" and "Sub Total in CAD" lines to tag each row's account currency. Always review the editable table after upload.
- **Activity**: the RBC export is detected automatically. Several exports can be uploaded together, and overlapping rows are dropped. The generic template has `date, ticker, type, quantity, price, fees, value, currency`, and dividends need `value`.

## How the numbers work

- **Yahoo symbols.** RBC holds US-listed shares on the CAD side and converts each trade at an exchange rate. Those positions use the US ticker, converted with `USDCAD=X`. A CAD-side trade without an exchange rate is a TSX listing and gets `.TO`. You can override any mapping on the Upload tab.
- **Export window.** RBC exports cover 15 months. Shares already held when the window opens are rebuilt from the snapshot minus later activity. They count as invested at their market value on the first day. All tab 3 metrics cover that window.
- **Investment over the export period** is the starting holdings value plus every purchase, commissions included. **Proceeds** is net sales receipts plus dividends. **Total sales** is net sales receipts; its tooltip includes the number of trades. These are period metrics, not lifetime totals: true lifetime performance needs inception records.
- **CAD display.** Average cost, current price, book cost, market value, gain and all return metrics are shown in CAD. Original account currency is retained as metadata for accurate conversion. USD activity cash flows use the historical USD/CAD rate on each transaction date. USD snapshot book cost uses the current FX rate, so it is a translated snapshot cost, not historical CAD cost basis.
- **Total return** is current value plus proceeds plus dividends, minus investment and fees. **XIRR** is computed on the same dated cash flows plus today's value.
- **Splits.** Yahoo back-adjusts prices for splits. The app undoes that adjustment and moves the broker's split row to Yahoo's split date, so the share count and the price change on the same day.
- **Not modeled.** Cash balances, deposits, withdrawals and taxes are not modeled. Sale proceeds leave the tracked portfolio, so the value line drops on sale dates while the net-invested line drops with it.

Uploaded files stay in the Streamlit session only. Screenshots are processed on this machine. Only ticker symbols are sent to Yahoo Finance.

Missing current prices or FX withhold allocation totals. Missing prices while a security was held or missing historical FX withhold historical results rather than inventing prices. Delisted symbols may need a corrected Yahoo mapping or supplemental historical data.
