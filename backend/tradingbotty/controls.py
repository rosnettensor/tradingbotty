"""Bot-wide settings you can change from the dashboard while it runs.

Each control maps to a value in config.toml. Changes are saved in the database and win over config.toml,
until you press reset. The ranges below are hard limits: nothing here can enable margin, leverage or shorting.
"""
from __future__ import annotations

CONTROLS = [
    # ---- live money: the Risk Officer enforces these on every real order
    {"key": "live.max_invest", "label": "Daily: maximale Kapitalzuteilung", "min": 2, "max": 2000, "step": 1,
     "group": "Live money", "help": "Obergrenze für die Daily-Strategie in Kontowährung. Fast hat eine eigene Zuteilung; "
     "der gemeinsame Portfolio-Kern zieht diese und belegte Fast-Positionen zuerst vom Kontowert ab. "
     "Ein hoher Wert erzeugt kein zusätzliches Kapital und keinen Kredit."},
    {"key": "live.max_order", "label": "Biggest single order", "min": 1, "max": 100, "step": 1,
     "group": "Live money", "help": "No real buy is bigger than this, in account currency (default CHF). All traders share this limit; the absolute code ceiling is 100 in account currency."},
    {"key": "live.max_spread_pct", "label": "Skip a buy when the spread is above (%)", "min": 0.1, "max": 5, "step": 0.1,
     "group": "Live money", "help": "Before every real buy the Risk Officer reads Fusion's order book. If the gap between "
     "buy and sell price is wider than this, the buy is skipped: you'd lose that gap the moment you buy."},
    {"key": "live.use_my_coins", "label": "Bot may also use my existing coins", "bool": True, "group": "Live money",
     "help": "On = your whole Bitpanda account is the bot's money: when it needs cash for a buy, it sells your other "
     "coins first (biggest first). Worst case you lose what's in the account, never more: no debt is possible."},
    # ---- AI
    {"key": "ai.news_ai", "label": "News Hunter uses Claude", "bool": True, "group": "AI",
     "help": "Off = free keyword rules instead of Claude Haiku (less precise at telling a real hack from noise)."},
    {"key": "ai.professor_on", "label": "Professor reviews each daily decision", "bool": True, "group": "AI",
     "help": "One deep review a day, a few cents. It writes your briefing and may block a coin for 24 hours."},
    {"key": "ai_budget.spread_over_days", "label": "Spread AI budget over (days)", "min": 3, "max": 120, "step": 1,
     "int": True, "group": "AI", "help": "Daily AI allowance = budget / days. The total budget itself stays your hard cap."},
    # ---- phone
    {"key": "phone.morning_hour", "label": "Morning briefing at (hour, Swiss time)", "min": 5, "max": 12, "step": 1,
     "int": True, "group": "Phone", "help": "Once a day by WhatsApp and/or Telegram: account, the bot's own gain, "
     "the night's decision, the fast pot, real trades of the last 24h and the Professor's review."},
    {"key": "phone.trades", "label": "Message me on every real trade", "bool": True, "group": "Phone",
     "help": "A short message the moment the Daily Brain or the fast pot buys or sells with real money."},
    {"key": "phone.weekly", "label": "Weekly report card on Sundays", "bool": True, "group": "Phone",
     "help": "Every Sunday at 19:00 Swiss time: the bot's own result this week with a grade A to F, both traders' "
     "trades with wins and losses, the best and worst closed trade, Bitcoin's week and the Think Tank."},
    # ---- speed
    {"key": "engine.news_poll_seconds", "label": "Read news every (seconds)", "min": 60, "max": 3600, "step": 60, "int": True,
     "group": "Speed", "help": "More often = the Guardian hears about a hack sooner, and a little more AI spend."},
]

DEFAULTS = {
    "ai.news_ai": True,
    "ai.professor_on": True,
    "live.max_invest": 25.0,
    "live.max_order": 100.0,
    "live.max_spread_pct": 1.0,
    "live.use_my_coins": False,
    "phone.morning_hour": 7,
    "phone.trades": True,
    "phone.weekly": True,
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
    import math
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{key} must be finite")
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
