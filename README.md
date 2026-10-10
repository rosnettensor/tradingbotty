# TradingBotty

A crypto bot with a cyber dashboard and one shared trading core for Bitpanda Fusion. Daily and Fast are strategy modules with separate capital allocations in the same account. Agents provide data, research and monitoring. Volatility and Think Tank remain research-only.

**It can lose your stake. The bot is cash-only:** it only places spot buy and sell orders, only buys with
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

## Shared trading core

The architecture, migration and operational recovery procedure are documented in [ARCHITECTURE.md](ARCHITECTURE.md).
`Portfolio` owns capital allocation and position attribution; `StrategyRegistry` owns automatic-entry evidence;
`ExecutionService` is the only caller of broker buy/sell methods. Daily, Fast, chat, checks and emergency exits
share its account lock and order journal. A confirmed fill, position update and Fast accounting commit together.
An interrupted or uncertain submission freezes execution instead of blindly sending the order again.

Existing positions, selected strategies, limits, live/standby mode and activation settings are retained on upgrade.
Legacy startup routines no longer rewrite the user's limits or Fast strategy. The update adds no AI calls or AI budget.

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

Press 1-5 to switch tabs.

- **Cockpit:** the existing animated sphere, an explicit execution/readiness status, four account metrics and a
  12-hour observation. Five sections organize the overview, positions/history, market/signals, operation/diagnostics
  and evaluation/learning. Full charts, tables, the trade diary, bank controls, agent feed and reviews remain available.
- **Agents:** the node graph and an inspector that shows what each agent just did, step by step, its key facts and
  tables, its own log, and for the AI agents their model and instructions.
- **Research:** starts with the **Handelsplan**, showing the two live-capable strategies, their current admission
  status and allocations, and the research-only modules. Daily and Fast settings retain their detailed labs.
  Volatility's virtual forward experiment and Think Tank's candidates do not automatically become live strategies.
- **Controls:** money limits, AI switches, news feeds, and the Telegram briefing.

## Volatility scanner (Research → VOLATILITY)

The scanner reads all Fusion spot pairs every two minutes. It ranks the **24-hour high–low range**, not realized
volatility or a promised return. For the top ten it checks actual Fusion spread and displayed bid/ask depth within
1%. Reported ticker volume is retained in the API; its unit is not assumed to be CHF. Newly observed pairs are
flagged as observations, not verified listing announcements.

The isolated forward experiment starts with 1,000 virtual units of your account currency. Entries require ≥8%
range, ≥2% momentum since a fresh previous scan, price above the previous scan's 24h high, spread within your limit,
at least 1,000 displayed depth and an exchange minimum ≤100. Three slots, ≤100 per entry, configured fees/slippage.
Exits: −8%, +20%, or 24h, at the next observed price. Missing quotes preserve the last mark and never invent fills.
A scan gap >10 minutes invalidates entry momentum. No key means no scanner data. A kill switch blocks paper buys.

This is a forward test, not a backtest or a guarantee of 10×/100× returns. The existing historical labs remain
available. **Scanner signals never submit live orders or automatically become a live strategy.** Options, futures,
margin and leverage have no execution adapter in this update.

## Money rules

- Every purchase path (Daily Brain, fast pot, chat and system check) shares an independent cash-only gate:
  **100 maximum per order**, **200 gross daily losses** and the configured maximum open positions, across all bot
  books. Amounts are in **account quote currency: CHF by default**, not an implicit FX conversion. Your smaller
  single-order setting wins. Losses include negative realized P/L since 00:00 UTC plus current unrealized downside;
  profits don't refill the loss budget. This stops buys; sells stay possible. Stops do not guarantee a loss ceiling.
- Missing risk prices or missing/invalid spreads block new buys. Buys and sells are serialized to avoid cash races.
- Only confirmed quantities are booked. Partial sells retain the unsold ownership and proportional cost. An ambiguous
  order/POST timeout sets standby and the kill switch, stores `unresolved_order` in the database, and blocks buys
  until the order and ownership have been reconciled by the operator. Never blindly retry an uncertain order.

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


### Readiness upgrade

Automatic Daily Brain and Fast Pot entries now require a passed backtest for the selected strategy with `simulated=false` and a timestamp no older than 48 hours. Bank blends require evidence for every allocated strategy. Missing evidence blocks new entries, including top-ups; ordinary exit rules remain active. The system check shows evidence separately from account connectivity. A passed backtest is not a prediction of live profitability.

Daily decisions are checked every 60 seconds, but still use closed daily candles. Fast decisions use closed 4-hour candles plus the existing 120-second settlement buffer. A decision blocked by research is reconsidered when evidence becomes available, without waiting for the next candle. Labs are checked every five minutes; a failed daily lab does not prevent the fast lab from running.

Controls offers a reviewed **Kontorahmen übernehmen** profile: 98% of the latest account value (up to 2,000 in account currency), 100 per order, 1% spread ceiling, and permission to fund Daily Brain buys from existing coins. The existing Fast Pot reserve is respected. This profile requires a fresh balance and explicit user action; it does not enable LIVE or a strategy. Saved server settings are not silently overwritten by deployment.

The volatility radar remains an isolated paper experiment. Its dashboard includes filtering, exclusion reasons, equity history, stale position marks and cumulative metrics from the upgrade onwards. Paper daily-loss accounting survives journal truncation. Model fees and slippage are estimates, not verified live fills. Fusion booking uses reported filled amounts without cent rounding, and quantity rounding never rounds available holdings upwards.

### Cockpit clarity upgrade

`GET /api/operations` is read-only and uses the dashboard password gate. It separates global execution permission
from each trader's activation, reference-data checks and budget. Signal-ready means entry checks may run; an order
still needs a strategy signal, fresh prices, available balance and all per-order risk checks. Budget conflicts and
stale account data are surfaced rather than silently changing saved settings.

**Bank Probe already uses real money**, for a limited candidate allocation, when global LIVE and Daily Brain are
enabled. Bank Live uses the bank's full target mix. Bank Shadow only observes its own allocation; Daily Brain may
still execute its selected strategy. The volatility radar, shadow books and ghost trades stay virtual. Deploying
this release does not activate trading, change bank mode, increase AI budgets or remove evidence requirements.

The first 12-hour observation starts when this release first opens the database and survives restarts. Afterwards
it becomes a rolling 12-hour window. It counts recorded live strategy orders, excluding technical test orders and
simulations; booked realized P/L, trade fees and AI costs in USD are displayed separately. Open gains/losses remain
in the account/position view. It is an operational observation, not proof of profitability or continuous human monitoring.

Daily and Fast purchases now reject coins owned by the other book under the shared order lock. Existing overlapping
holdings are reported, not automatically liquidated. Fast exits refresh quotes when the dashboard account snapshot
is stale; an unavailable fresh quote cannot turn an obsolete balance into a floor liquidation. The original sphere
renderer and agent visuals remain unchanged. Technical test buys and bank mode changes are under diagnostic/details
sections and retain their explicit confirmations.
