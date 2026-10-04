# TradingBotty

A playful multi-agent trading bot with a cyber dashboard. It trades crypto on real live prices, starts on paper
money, and can switch to real money on Bitpanda with one (well-guarded) switch.

**It can lose your stake. It can never put you in debt:** it only places spot buy and sell orders, only buys with
cash it has, and only sells coins it holds. There is no code for margin, leverage, shorting, futures or CFDs.

## Start it

You need Python 3.11 or newer ([python.org](https://www.python.org/downloads/)). The dashboard is already built,
so you don't need Node.

- **Windows:** double-click `start.bat`.
- **Mac/Linux:** run `./start.sh` in a terminal.

Then open http://localhost:8000. The first start takes a minute to install packages.
Want to look around without internet data? Use `start.bat --simulate` or `./start.sh --simulate` (random-walk prices,
marked SIMULATED DATA, stored in a separate database).

Without any keys the bot runs in **free math mode**: all agents work except the two AI ones (News Hunter falls back to
keyword rules, the Professor rests). Add keys in `.env` (created on first start from `.env.example`):

- `ANTHROPIC_API_KEY`: turns on the AI agents. Also set a spend limit in the Anthropic console as a second safety net.
- `BITPANDA_API_KEY`: only needed for live trading. Give it **Read + Trade, never Withdraw**.

## What you see

Press 1-5 to switch tabs.

- **Cockpit:** a stat strip (equity, cash, realized P&L, fees, trades, win rate, mood, Fear & Greed, risk appetite,
  US market status), the sphere with the Professor's latest take, a price chart of the selected symbol with buy/sell
  markers, the champion's equity, every score with its buy and sell lines, and a "why" breakdown showing which
  signals push the selected score up or down and why it isn't buying. Below: positions and trades, the agent feed
  (filter by trades, AI or problems) and rated news with social buzz. Click any symbol anywhere to focus it.
- **Markets:** a tile per coin and stock: price, 24h move, 1-hour sparkline, score, all seven signals, buzz and news.
  Sort and filter, click a tile for a big chart (1h to 7d) with every strategy's trades.
- **Agents:** the node graph plus an inspector: each agent's job in plain words, what it reads and hands on, an
  on/off switch for optional agents, and for the AI agents their Claude model and editable instructions.
- **Lab:** backtest any strategy on recorded prices, auto-tune it (tries many variations and ranks them), adopt a
  winner as a new paper strategy, and compare all running strategies on the live leaderboard.
- **Controls:** sliders for every bot setting (risk limits, fees, speed, AI, Optimizer) and every strategy setting
  (when to buy, how much, when to sell, signal weights, crypto/stocks), with a 24h backtest button. Plus your
  sources: add or remove coins, stocks, subreddits and RSS feeds (each is tested before it's used).
- **Top bar:** AI budget meter, PAPER/LIVE switch, KILL switch (stops all new buys instantly).

Settings changed in the dashboard are saved in `data/` and win over `config.toml` until you reset them.

## Live trading venue

The default live broker is **Bitpanda Fusion** (same Bitpanda account, order API, about 0.25% per trade instead of
about 1.5% in the app). Put `BITPANDA_FUSION_API_KEY` in `.env` (Read + Trade only, never withdrawals) and run
`python run.py --check-live`. To use the old app-quote broker set `broker = "bitpanda"` in `config.toml`.

**Market Radar** (tab 2): every few minutes one Kraken request reads the 24h stats of every coin with a USD market,
keeps what Fusion can trade, filters out thin, wide-spread and already-pumped coins, and adds the hottest to the
watchlist so every strategy can trade them. Your own coin list always stays; radar coins leave again once they
cool off, unless a strategy holds them.

**Live money brakes** (Controls → Live money): a cap on how much is in coins at once, a cap per order, a spread check
on Fusion's order book before every buy, and leftover coins of an old champion get sold. By default the bot only
sells coins it bought itself. Switch on "Bot may also use my existing coins" to let it sell your other coins for cash
when it needs it; worst case you lose what's in the account, never more, because nothing can borrow.

Stocks (US, Swiss `.SW`, German `.DE`, and other European suffixes) are paper-only: Bitpanda has no stock trading
API. Every strategy is measured against the **Buy & Hold** yardstick in the Lab.

## Your own visuals

Drop a background video, a logo or agent portraits into the `media/` folder (see `media/README.md`) and reload the
page. Image and video generators like Higgsfield are good for this.

## The team

| Agent | What it does | Costs AI money |
|---|---|---|
| Crypto Analyst | Momentum, trend, dips and breakouts per coin from 1-minute prices | no |
| Market Analyst | S&P 500 / Nasdaq / BTC trend and Fear & Greed: risk-on or risk-off | no |
| Hype Scout | Reddit buzz, CoinGecko trending, sudden chatter spikes | no |
| Hype vs Price Detective | Early hype (price still flat) is a signal; hype after a pump is a trap | no |
| News Hunter | Rates every headline's direction and impact (mergers, ETFs, hacks, regulation, macro) | yes, Haiku (cheap) |
| The Professor | A few times a day: big-picture review, sets risk appetite, names coins to avoid, proposes ideas | yes, Opus (rare) |
| Predictor | Blends all signals into one score per coin, per strategy variant | no |
| Risk Officer | Plain code, not AI: size caps, daily loss stop, cash-only, kill switch | no |
| Buyer | Places paper orders for every variant, and live orders for the champion when LIVE is on | no |
| Optimizer | Breeds new variants from the best, retires losers, suggests (or auto-promotes) a new champion | no |

## Money rules (config.toml)

- **AI budget:** hard cap of 20 USD in total, spread over 30 days (about 0.67 USD a day). 10% of realized profit is
  added to the budget, so the bot can spend more on AI only when it earns more. When the budget is used up, the AI
  agents fall back to free rules and everything keeps running.
- **Risk:** max 25% of equity per position, max 5 positions, no new buys after losing 30% within a day.
  Percentages, so the limits grow with the account. The Optimizer can't change these.
- **Fees:** paper trading charges 1.5% per side, matching Bitpanda app quotes, so paper results are honest about
  costs. Fees are the biggest enemy of a small account: the strategies are tuned to trade rarely.

## Going live (do this together the first time)

1. Let it run on paper for at least a few days and watch the Experiments tab.
2. Fusion uses the same wallet as the Bitpanda app. The bot trades from `currency` in `config.toml` (CHF by default).
3. Add `BITPANDA_FUSION_API_KEY` to `.env`, then check it without trading: `start.bat --check-live` or
   `./start.sh --check-live`. It lists your balance and which coins are tradable.
4. Set the live caps in Controls → Live money, then click PAPER and type `REAL MONEY`. From then on the champion's
   trades are copied to Fusion in the same proportions, inside your caps.
5. Three live errors in a row switch it back to paper automatically.

The Bitpanda connector is built from Bitpanda's public API docs and tested against a fake server, not yet against
your real account. Make the first live trade a tiny one and check it in the Bitpanda app.

## Experimenting

- Edit `backend/tradingbotty/strategy.py` → `SEED_VARIANTS` to add your own strategy personalities.
- Add an agent: subclass `Agent` in `backend/tradingbotty/agents/team.py`, set `inputs` to the nodes it reads, write
  to the `Blackboard`, and add it to the team list in `engine.py`. It appears in the node view automatically.
- Run the safety tests after changes: `.venv/bin/python -m pytest tests`.
- Stocks: strategies with "Trade US stocks" on paper-trade your watchlist during the US session (9:30-16:00 New York).
  Live trading stays crypto-only.

## Data sources

Kraken public API (crypto prices), Yahoo Finance chart API (US stocks), Reddit, CoinGecko trending,
alternative.me Fear & Greed, and news RSS from CoinDesk, Cointelegraph, Yahoo Finance, CNBC, Decrypt and
MarketWatch. All free, and all editable in Controls > Sources.
X (Twitter) is not used yet because reading posts costs about 0.005 USD each.
