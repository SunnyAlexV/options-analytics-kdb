"""The public demo as a WSGI app: a recorded bundle replayed through the live dashboard.

    gunicorn dashboard.demo:server -b 0.0.0.0:7860       (what the Hugging Face Space runs)

BUNDLE (env) is the bundle folder; its history.csv (daily index/DVOL) feeds the VRP chart,
so the demo needs no network access and no kdb+, C++ or licence.
"""
import os
from pathlib import Path

import pandas as pd

from .app import create_app
from .sources import ReplaySource

BUNDLE = Path(os.environ.get("BUNDLE", "bundle"))
SPEED = float(os.environ.get("SPEED", "10"))

_hist_file = BUNDLE / "history.csv"
_hist = pd.read_csv(_hist_file, parse_dates=["date"]) if _hist_file.exists() else None
app = create_app(ReplaySource(BUNDLE, speed=SPEED), _hist)
server = app.server
