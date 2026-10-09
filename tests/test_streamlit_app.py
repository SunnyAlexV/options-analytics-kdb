"""The Streamlit Community Cloud demo starts and renders every page without an exception.

Runs the real app (streamlit_app/app.py) on its committed bundle with Streamlit's headless
AppTest. Skipped when Streamlit is not installed (it is only needed for the demo:
pip install -r streamlit_app/requirements.txt).
"""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "streamlit_app" / "app.py"
BUNDLE = APP.parent / "bundle_all"

pytestmark = pytest.mark.skipif(not (BUNDLE / "surface.parquet").exists(), reason="no demo bundle committed")


def errors(at):
    return [e.value for e in at.exception]


def test_every_page_renders_live_and_paused():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    assert errors(at) == []
    assert len(at.get("plotly_chart")) >= 10                      # Market: smile, density, surface, ...
    for page, min_charts in (("Risk", 9), ("System", 2)):
        at.segmented_control(key="page").set_value(page).run()
        assert errors(at) == [], page
        assert len(at.get("plotly_chart")) >= min_charts, page
    at.segmented_control(key="page").set_value("Market").run()
    at.toggle(key="live").set_value(False).run()                   # paused at a chosen minute
    assert errors(at) == []
    assert at.select_slider(key="moment").value is not None


def test_all_coin_session_lists_every_asset_and_each_renders():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    assets = list(at.selectbox(key="asset").options)
    assert {"BTC", "ETH", "SOL_USDC", "XRP_USDC"} <= set(assets), assets
    for a in ("ETH", "SOL_USDC"):
        at.selectbox(key="asset").set_value(a).run()
        assert errors(at) == [], a
        assert len(at.get("plotly_chart")) >= 10, a
        at.segmented_control(key="page").set_value("Risk").run()      # no book for alts: a note, no error
        assert errors(at) == [], a
        at.segmented_control(key="page").set_value("Market").run()
    at.selectbox(key="session").set_value("BTC · 4 hours").run()      # the long BTC session
    assert errors(at) == []
    assert len(at.get("plotly_chart")) >= 10
