"""Checks your live trading key without trading: `python run.py --check-live`."""
import asyncio

from .config import load_settings
from .engine import make_live_broker


async def main() -> None:
    s = load_settings()
    try:
        b = make_live_broker(s)
    except ValueError as e:
        print(e)
        return
    info = await b.connect()
    cur = s["live"]["currency"]
    print(f"Connected to {b.name}. {info['assets']} tradable assets in {cur}.")
    known = getattr(b, "pairs", None) or getattr(b, "asset_ids", {})
    wanted = s["markets"]["crypto"]
    print("Tradable from our list:", [c for c in wanted if c in known])
    print("Missing:", [c for c in wanted if c not in known])
    bal = await b.balances()
    print(f"Fiat available: {bal.get('FIAT', 0):.2f} {cur}")
    held = {k: v for k, v in bal.items() if v and k not in ("FIAT", cur)}
    print("Holdings:", held or "none")
    if hasattr(b, "prices"):
        px = await b.prices()
        coins = sum(v * px.get(k, 0) for k, v in held.items())
        print(f"Account total: {bal.get('FIAT', 0) + coins:.2f} {cur} ({bal.get('FIAT', 0):.2f} cash + {coins:.2f} in coins)")


if __name__ == "__main__":
    asyncio.run(main())
