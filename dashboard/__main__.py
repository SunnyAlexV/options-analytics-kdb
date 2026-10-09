"""Run the dashboard.

    python -m dashboard                          # live: the kdb+ gateway on port 5013
    python -m dashboard --gw 5013 --port 8050
    python -m dashboard --replay demo/bundle     # replay a recorded bundle (the public demo)

Then open http://localhost:8050
"""
import argparse
import faulthandler
import os
from pathlib import Path

faulthandler.enable()          # a crash in native code prints where it happened, not just "Segmentation fault"


def main():
    ap = argparse.ArgumentParser(description="BTC options desk dashboard")
    ap.add_argument("--gw", type=int, default=5013, help="gateway port (live mode)")
    ap.add_argument("--replay", help="bundle folder to replay instead of the live system")
    ap.add_argument("--speed", type=float, default=10.0, help="replay speed (x real time)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8050)))
    ap.add_argument("--history", default=os.environ.get("DASH_HISTORY", "~/kdbdata/history/btc_daily.csv"),
                    help="daily index/DVOL cache for the variance-risk-premium chart")
    a = ap.parse_args()

    from risk.history import fetch_history
    from .app import create_app
    from .sources import GatewaySource, ReplaySource

    src = ReplaySource(a.replay, speed=a.speed) if a.replay else GatewaySource(a.gw)
    try:
        cache = Path(a.history).expanduser()
        hist = fetch_history("BTC", cache=cache)
    except Exception as e:                                   # offline: the chart says so
        print(f"Dashboard: no BTC history ({type(e).__name__}); the VRP chart will be empty")
        hist = None
    app = create_app(src, hist)
    print(f"Dashboard ({src.name}) on http://{a.host}:{a.port}")
    app.run(host=a.host, port=a.port, debug=False)


if __name__ == "__main__":
    main()
