"""Checks your Bitpanda API key without trading: `python -m tradingbotty.live_check` from backend/."""
import asyncio

from .brokers.bitpanda import BitpandaBroker
from .config import load_settings


async def main() -> None:
    s = load_settings()
    if not s.bitpanda_api_key:
        print("No BITPANDA_API_KEY in .env")
        return
    b = BitpandaBroker(s.bitpanda_api_key, s["live"]["currency"])
    info = await b.connect()
    print(f"Connected. {info['assets']} assets, {s['live']['currency']} id {info['currency_id']}")
    wanted = s["markets"]["crypto"]
    print("Tradable from our list:", [c for c in wanted if c in b.asset_ids])
    print("Missing:", [c for c in wanted if c not in b.asset_ids])
    bal = await b.balances()
    print(f"Fiat available: {bal.get('FIAT', 0):.2f} {s['live']['currency']}")
    held = {sym: bal[aid] for sym, aid in b.asset_ids.items() if bal.get(aid)}
    print("Holdings:", held or "none")


if __name__ == "__main__":
    asyncio.run(main())
