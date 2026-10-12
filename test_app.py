from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"
import pandas as pd
from unittest.mock import patch
import pytest


def test_empty_app_loads_all_three_tabs():
    app = AppTest.from_file(APP).run(timeout=30)
    assert not app.exception
    assert len(app.tabs) == 3
    assert app.title[0].value == "Portfolio Analyzer"
    assert [tab.label for tab in app.tabs] == ["Upload", "Allocation", "Historical performance"]


def test_bad_csv_shows_an_error_instead_of_crashing():
    app = AppTest.from_file(APP).run(timeout=30)
    app.file_uploader[0].set_value(("junk.csv", b"a,b\n1,2\n", "text/csv")).run(timeout=30)
    assert not app.exception
    assert any("Unrecognized CSV" in e.value for e in app.error)


def test_local_rbc_import_shows_cad_metrics():
    if not any(APP.parent.glob("Activity*.csv")):
        pytest.skip("Personal example export is not distributed")
    def fake_market(symbols, currencies, start):
        days = pd.bdate_range(pd.Timestamp(start) - pd.Timedelta(days=10), pd.Timestamp.today().normalize())
        closes = pd.DataFrame(10., index=days, columns=symbols)
        splits = pd.DataFrame(0., index=days, columns=symbols)
        fx = pd.DataFrame({c: 1.4 for c in currencies if c != "CAD"}, index=days)
        return closes, splits, fx
    with patch("market.download", side_effect=fake_market):
        app = AppTest.from_file(APP).run(timeout=30)
        button = next(b for b in app.button if b.label == "Use my October 5 RBC activity CSV")
        button.click().run(timeout=30)
        assert not app.exception
        assert any("49 activities" in s.value for s in app.success)
        assert any(m.label == "Total sales (CAD)" and m.value.startswith("$") for m in app.metric)
        assert any("Investment over export period" == m.label for m in app.metric)
        app.file_uploader[0].set_value(("holdings.csv", b"ticker,currency,quantity,average_cost\nNVDA,USD,400,5\n", "text/csv")).run(timeout=30)
        assert not app.exception
        shown = next(d.value for d in app.dataframe if "price_cad" in d.value.columns)
        assert shown.price_cad.iloc[0] == 14
        assert shown.average_cost_cad.iloc[0] == 7
        assert "price" not in shown.columns
