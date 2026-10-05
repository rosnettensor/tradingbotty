"""Bot-wide settings you can change from the dashboard while it runs.

Each control maps to a value in config.toml. Changes are saved in the database and win over config.toml,
until you press reset. The ranges below are hard limits: nothing here can enable margin, leverage or shorting.
"""
from __future__ import annotations

CONTROLS = [
    # ---- risk: enforced in plain code by the Risk Officer for every strategy
    {"key": "risk.max_position_pct", "label": "Biggest single position (% of equity)", "min": 5, "max": 50, "step": 1,
     "group": "Risk limits", "help": "Hard cap for any one coin or stock, whatever a strategy asks for."},
    {"key": "risk.max_open_positions", "label": "Max open positions", "min": 1, "max": 12, "step": 1, "int": True,
     "group": "Risk limits", "help": "Hard cap on how many things one strategy may hold at once."},
    {"key": "risk.max_daily_loss_pct", "label": "Daily loss stop (%)", "min": 5, "max": 100, "step": 5,
     "group": "Risk limits", "help": "After losing this much within a day, no new buys until the next day. Sells still work."},
    {"key": "risk.min_cash_reserve_usd", "label": "Cash always kept (USD)", "min": 0, "max": 80, "step": 1,
     "group": "Risk limits", "help": "This much cash is never invested."},
    # ---- costs
    {"key": "paper.fee_pct", "label": "Crypto fee per trade (%)", "min": 0, "max": 3, "step": 0.05,
     "group": "Costs", "help": "Paper fee for each buy and each sell. 0.25 = Bitpanda Fusion, 0.4-0.8 = Kraken Pro, 1.5 = Bitpanda app."},
    {"key": "paper.stock_fee_pct", "label": "Stock fee per trade (%)", "min": 0, "max": 2, "step": 0.05,
     "group": "Costs", "help": "Paper fee for stocks. About 0.05 to 0.1 matches a cheap broker like Interactive Brokers."},
    {"key": "paper.stock_min_fee_usd", "label": "Minimum stock fee per trade (USD)", "min": 0, "max": 10, "step": 0.05,
     "group": "Costs", "help": "Brokers charge a minimum per order (Interactive Brokers: about 0.35 USD in the US, 1.5 CHF on SIX). On tiny orders this matters more than the percentage."},
    {"key": "paper.slippage_pct", "label": "Slippage (%)", "min": 0, "max": 1, "step": 0.01,
     "group": "Costs", "help": "How much worse than the quoted price each order fills."},
    # ---- speed
    {"key": "engine.tick_seconds", "label": "Think every (seconds)", "min": 5, "max": 300, "step": 5, "int": True,
     "group": "Speed", "help": "How often the whole team runs and the Buyer checks for trades."},
    {"key": "engine.news_poll_seconds", "label": "Read news every (seconds)", "min": 60, "max": 3600, "step": 60, "int": True,
     "group": "Speed", "help": "More often = fresher news, and more AI spend when news is busy."},
    {"key": "engine.hype_poll_seconds", "label": "Check Reddit every (seconds)", "min": 120, "max": 3600, "step": 60, "int": True,
     "group": "Speed", "help": "Reddit rate-limits fast polling, so stay above 2 minutes."},
    # ---- live money: only matters once you switch to LIVE
    {"key": "live.max_invest", "label": "Most live money in coins at once", "min": 2, "max": 2000, "step": 1,
     "group": "Live money", "help": "Hard cap, in your Bitpanda currency (CHF). The bot never has more than this in coins. "
     "Above your account total (e.g. 2000) = the whole account; the daily brain splits it into equal slots."},
    {"key": "live.max_order", "label": "Biggest single live order", "min": 1, "max": 500, "step": 1,
     "group": "Live money", "help": "No live buy is bigger than this, in CHF."},
    {"key": "live.max_spread_pct", "label": "Skip coins with a spread above (%)", "min": 0.1, "max": 5, "step": 0.1,
     "group": "Live money", "help": "Before every live buy the bot reads Fusion's order book. If the gap between buy and "
     "sell price is wider than this, the buy is skipped: you'd lose that gap the moment you buy."},
    {"key": "live.sell_orphans", "label": "Sell live coins the champion no longer holds", "bool": True, "group": "Live money",
     "help": "When a new champion takes over, it first takes over the bot's real coins and sells them by its own "
     "rules. This only sells what is left over after that (a coin the champion sold on paper, or one it couldn't "
     "take over), so nothing sits unmanaged. Only coins the bot bought."},
    {"key": "live.use_my_coins", "label": "Bot may also use my existing coins", "bool": True, "group": "Live money",
     "help": "On = your whole Bitpanda account is the bot's money: when it needs cash for a buy, it sells coins the "
     "champion doesn't hold (biggest first). Worst case you lose what's in the account, never more: no debt is possible. "
     "Raise the live cap above to match your account."},
    # ---- Market Radar: which coins get watched
    {"key": "scanner.on", "label": "Market Radar scans every coin", "bool": True, "group": "Market Radar",
     "help": "Scans every coin with a USD market on Kraken (and on Fusion, once your key is in) and adds the hottest "
     "to the watchlist, so the strategies can trade them. Off = only your own coin list."},
    {"key": "scanner.max_hot", "label": "Hot coins added to the watchlist", "min": 0, "max": 40, "step": 1, "int": True,
     "group": "Market Radar", "help": "On top of your own list. More coins = more chances, more data to watch."},
    {"key": "scanner.min_volume_musd", "label": "Minimum 24h trading (million USD)", "min": 0.05, "max": 100, "step": 0.05,
     "group": "Market Radar", "help": "Thinly traded coins jump around and are hard to sell. Higher = safer, fewer coins."},
    {"key": "scanner.max_spread_pct", "label": "Maximum spread for the radar (%)", "min": 0.05, "max": 3, "step": 0.05,
     "group": "Market Radar", "help": "Coins with a wider gap between buy and sell price are ignored."},
    {"key": "scanner.every_minutes", "label": "Scan every (minutes)", "min": 2, "max": 120, "step": 1, "int": True,
     "group": "Market Radar", "help": "One scan reads every coin at once, so it's cheap."},
    # ---- AI
    {"key": "ai.news_ai", "label": "News Hunter uses Claude", "bool": True, "group": "AI",
     "help": "Off = free keyword rules instead of Claude Haiku."},
    {"key": "ai.professor_on", "label": "Professor reviews", "bool": True, "group": "AI",
     "help": "The Professor's deep reviews set risk appetite and an avoid list."},
    {"key": "engine.professor_every_minutes", "label": "Professor reviews every (minutes)", "min": 30, "max": 1440,
     "step": 30, "int": True, "group": "AI", "help": "Each review costs a few cents."},
    {"key": "ai_budget.spread_over_days", "label": "Spread AI budget over (days)", "min": 3, "max": 120, "step": 1,
     "int": True, "group": "AI", "help": "Daily AI allowance = budget / days. The total budget itself stays your hard cap."},
    # ---- optimizer
    {"key": "optimizer.max_variants", "label": "Strategies running in parallel", "min": 2, "max": 16, "step": 1,
     "int": True, "group": "Optimizer", "help": "More strategies = more experiments at once. They cost no AI."},
    {"key": "optimizer.every_minutes", "label": "Evolve every (minutes)", "min": 15, "max": 720, "step": 15, "int": True,
     "group": "Optimizer", "help": "How often the Optimizer breeds a new strategy and retires a loser."},
    {"key": "optimizer.screen_candidates", "label": "Backtest candidates per evolution", "min": 1, "max": 60, "step": 1,
     "int": True, "group": "Optimizer",
     "help": "The Optimizer backtests this many mutations on recent history and only adds the best one. 1 = no screening."},
    {"key": "optimizer.min_age_to_judge_h", "label": "Judge strategies after (hours)", "min": 1, "max": 72, "step": 1,
     "group": "Optimizer", "help": "Younger strategies are never retired."},
    {"key": "optimizer.min_age_to_promote_h", "label": "Promote after (hours)", "min": 2, "max": 336, "step": 2,
     "group": "Optimizer", "help": "A challenger must run this long before it can replace the champion."},
]

DEFAULTS = {
    "paper.stock_fee_pct": 0.05,
    "paper.stock_min_fee_usd": 1.0,
    "ai.news_ai": True,
    "ai.professor_on": True,
    "live.max_invest": 25.0,
    "live.max_order": 10.0,
    "live.max_spread_pct": 1.0,
    "live.sell_orphans": True,
    "live.use_my_coins": False,
    "scanner.on": True,
    "scanner.max_hot": 15,
    "scanner.min_volume_musd": 1.0,
    "scanner.max_spread_pct": 0.5,
    "scanner.every_minutes": 10,
    "optimizer.max_variants": 8,
    "optimizer.every_minutes": 60,
    "optimizer.screen_candidates": 12,
    "optimizer.min_age_to_judge_h": 6,
    "optimizer.min_age_to_promote_h": 48,
}

BY_KEY = {c["key"]: c for c in CONTROLS}


def get(raw: dict, key: str):
    section, name = key.split(".")
    return raw.get(section, {}).get(name, DEFAULTS.get(key))


def coerce(key: str, value):
    """Validate one dashboard value against its control's range. Raises ValueError for unknown keys or bad types."""
    c = BY_KEY.get(key)
    if not c:
        raise ValueError(f"unknown setting {key}")
    if c.get("bool"):
        return bool(value)
    v = float(value)
    v = max(c["min"], min(c["max"], v))
    return int(round(v)) if c.get("int") else round(v, 4)


def apply(raw: dict, overrides: dict) -> None:
    """Write defaults, then overrides, into the settings dict the whole bot reads from."""
    for key, value in DEFAULTS.items():
        section, name = key.split(".")
        raw.setdefault(section, {}).setdefault(name, value)
    for key, value in overrides.items():
        if key in BY_KEY:
            section, name = key.split(".")
            raw.setdefault(section, {})[name] = coerce(key, value)


def describe(raw: dict, overrides: dict) -> list[dict]:
    return [{**c, "value": get(raw, c["key"]), "changed": c["key"] in overrides} for c in CONTROLS]
