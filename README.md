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

- **Cockpit:** the pulsing sphere (color = profit, wobble = market energy, shockwave = a trade), the champion's
  equity curve, open positions, the Predictor's scores per coin, the live agent feed and the trade list.
- **Nodes:** every data source and agent as a node with wires showing what feeds what. Wires light up when data
  flows. Click a node to see its last output. Drag nodes around; the layout is remembered.
- **Experiments:** every strategy variant trades on paper with the same live data. Compare their return curves,
  drawdown, fees and win rate. Promote a variant to champion, or mutate one to spawn a new experiment.
- **Top bar:** AI budget meter, PAPER/LIVE switch, KILL switch (stops all new buys instantly).

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
2. Put the stake you're willing to lose (e.g. 100 EUR) in your Bitpanda EUR wallet. Set `currency` in `config.toml`
   if you use CHF.
3. Add `BITPANDA_API_KEY` to `.env`, then check it without trading: `start.bat --check-live` or
   `./start.sh --check-live`. It lists your balance and which coins are tradable.
4. Click PAPER, type `REAL MONEY`. From then on the champion's trades are copied to Bitpanda in the same
   proportions (if the champion puts 20% of its equity into SOL, live does the same with your EUR).
5. Three live errors in a row switch it back to paper automatically.

The Bitpanda connector is built from Bitpanda's public API docs and tested against a fake server, not yet against
your real account. Make the first live trade a tiny one and check it in the Bitpanda app.

## Experimenting

- Edit `backend/tradingbotty/strategy.py` → `SEED_VARIANTS` to add your own strategy personalities.
- Add an agent: subclass `Agent` in `backend/tradingbotty/agents/team.py`, set `inputs` to the nodes it reads, write
  to the `Blackboard`, and add it to the team list in `engine.py`. It appears in the node view automatically.
- Run the safety tests after changes: `.venv/bin/python -m pytest tests`.

## Data sources

Kraken public API (crypto prices), Yahoo Finance chart API (US stocks), Reddit, CoinGecko trending,
alternative.me Fear & Greed, and news RSS from CoinDesk, Cointelegraph, Yahoo Finance and CNBC. All free.
X (Twitter) is not used yet because reading posts costs about 0.005 USD each.
