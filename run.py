"""Start TradingBotty: `python run.py`, then open http://localhost:8000

Options:
  --simulate   offline demo with random-walk prices (no internet data)
  --port N     web port (default 8000)
  --check-live test your Bitpanda key without trading
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--check-live", action="store_true")
    args = ap.parse_args()
    if args.check_live:
        import asyncio

        from tradingbotty.live_check import main

        asyncio.run(main())
        sys.exit(0)
    if args.simulate:
        os.environ["TB_SIMULATE"] = "1"
        os.environ.setdefault("TB_DB", str(ROOT / "data" / "simulated.db"))
    if args.host not in ("127.0.0.1", "localhost"):
        from tradingbotty.config import load_settings

        if not load_settings().password:
            sys.exit("Refusing to start: reachable from the network without a password. Set TB_PASSWORD first.")
    import uvicorn

    print(f"\n  TradingBotty is starting: open http://localhost:{args.port}\n")
    uvicorn.run("tradingbotty.server:app", host=args.host, port=args.port, log_level="warning")
