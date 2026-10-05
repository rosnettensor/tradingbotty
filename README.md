# TradingBotty

A playful multi-agent crypto bot with a cyber dashboard that trades real money on Bitpanda Fusion. There is no
paper money: every agent either feeds the real trades or the history test that picks the strategy for them.

**It can lose your stake. It can never put you in debt:** it only places spot buy and sell orders, only buys with
cash it has, and only sells coins it holds. There is no code for margin, leverage, shorting, futures or CFDs.

## Start it

You need Python 3.11 or newer. The dashboard is already built, so you don't need Node.

- **Mac/Linux:** `./start.sh` (on a Mac that should stay awake: `caffeinate -i ./start.sh`)
- **Windows:** double-click `start.bat`

Then open http://localhost:8000. `--simulate` runs on random-walk prices (marked SIMULATED DATA, separate database).
`--check-live` reads your Fusion balance without trading.

Keys go in `.env` (created on first start from `.env.example`), never in a chat:

- `BITPANDA_FUSION_API_KEY`: Read + Trade, **never Withdraw**.
- `ANTHROPIC_API_KEY`: turns on the News Hunter (Haiku) and the Professor (Opus). Without it they use free rules.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: the daily briefing on your phone (optional).

## How the real money is traded

1. **Research.** Every night the Researcher re-runs about 37 strategies on all daily candles since 2017 for 22 big
   coins, with real fees and spread, against simply holding Bitcoin. "Robust" means it wins in both halves of
   history, in most calendar years, and passes a correction for testing many strategies at once.
2. **Decision.** Once a day, right after 00:00 UTC, the Daily Brain runs the picked strategy (default: buy 20-day-high
   breakouts, at most 3 coins, only while Bitcoin is above its 50-day average; sell under the 10-day low or a
   trailing stop). If its strategy fails the robustness check three nights in a row, it switches to the best robust one.
3. **Guards.** The Guardian blocks buys after hack or delisting news or a crash, and sells a held coin early when two
   witnesses agree. The Professor reviews each decision and may veto a buy for 24 hours, never force a sell.
4. **Orders.** The Risk Officer checks every order (kill switch, caps, Fusion's minimum, spread, cash with fee room)
   before the Live Desk sends it.

## The team

| Agent | What it does for the real trades | AI |
|---|---|---|
| Fusion Scout | Which coins Fusion really trades, minimum orders, crash alerts (≤ -20% in 24h and ≤ -15% vs Bitcoin) | no |
| Trend Watch | Runs the brain's exact rules on live prices every 2 minutes: tonight's likely trades, distance to each line | no |
| Data Collector | Free daily data with years of history: Fear & Greed, futures funding, Wikipedia views, stablecoins, hash rate | no |
| News Hunter | Rates headlines and spots hacks and delistings for the Guardian | Haiku |
| Pattern Hunter | Tests every free signal against next week's price, with random controls and multiple-testing correction | no |
| Researcher | The nightly history test; warns and self-heals when the live strategy stops being robust | no |
| Guardian | 72-hour buy blocks, two-witness emergency sells (never BTC or ETH) | no |
| The Professor | Daily review and briefing, 24-hour buy veto | Opus |
| Daily Brain | Decides once a day what the bot holds | no |
| Risk Officer | Hard limits on every order, in plain code | no |
| Live Desk | Sends orders to Fusion, reads your account every 30 seconds | no |

## The dashboard

Press 1-4 to switch tabs.

- **Cockpit:** your account, the bot's own gain or loss (coin price swings excluded), the Daily Brain and tonight's
  likely trades, the watchlist with each coin's distance to its buy and sell line, Guardian blocks, the Professor's
  review, a daily chart per coin with the brain's lines and real trades, real trades, the agent feed and rated news.
- **Agents:** the node graph and an inspector that shows what each agent just did, step by step, its key facts and
  tables, its own log, and for the AI agents their model and instructions.
- **Research:** the history test (pick which strategy trades live), the Pattern Hunt and the free data.
- **Controls:** money limits, AI switches, news feeds, and the Telegram briefing.

## Money rules

- **AI budget:** 20 USD in total, spread over 30 days, plus 10% of the bot's gains. When it runs out, the AI agents
  fall back to free rules and everything keeps running.
- **Fees:** Fusion charges about 0.25% per side; the history test charges 0.4% per side to include spread.
- Three failed orders in a row switch the bot to standby.

## Experimenting

- Strategies live in `backend/tradingbotty/research.py`, agents in `backend/tradingbotty/agents/crew.py`.
- Run the tests after changes: `.venv/bin/python -m pytest tests`.
- Your own visuals: drop a background, logo or agent portraits into `media/` (see `media/README.md`).

## Data sources (all free)

Bitpanda Fusion (your account), Kraken (live prices), Binance, Coinbase and Kraken (daily history), alternative.me
(Fear & Greed), Binance futures (funding), Wikimedia (page views), DefiLlama (stablecoins), blockchain.com (hash rate),
and news RSS from CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine, CryptoSlate, CryptoPotato,
The Defiant, Yahoo Finance, CNBC and MarketWatch (editable in Controls).
