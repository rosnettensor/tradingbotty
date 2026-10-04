"""Free hype and news sources: Reddit, CoinGecko trending, Crypto Fear & Greed, and news RSS feeds.

X (Twitter) is left out on purpose for now: reading posts costs about 0.005 USD each.
"""
from __future__ import annotations

import random
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

UA = {"User-Agent": "TradingBotty/0.1 (hobby research bot)"}

SUBREDDITS = ["CryptoCurrency", "CryptoMarkets", "wallstreetbets", "stocks", "investing"]
RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
}

ALIASES = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "eth", "ether"], "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"], "DOGE": ["doge", "dogecoin"], "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"], "LINK": ["chainlink", "link"], "DOT": ["polkadot", "dot"],
    "PEPE": ["pepe"], "SPY": ["s&p", "s&p 500", "spy", "sp500"], "QQQ": ["nasdaq", "qqq"],
    "NVDA": ["nvidia", "nvda"], "TSLA": ["tesla", "tsla"], "AAPL": ["apple", "aapl"],
    "MSFT": ["microsoft", "msft"], "AMD": ["amd"], "COIN": ["coinbase"],
}
# short tickers that are also common words only count in upper case ($DOT, DOT)
CASE_SENSITIVE = {"dot", "link", "sol", "ada", "coin", "eth"}


def mentions(text: str, symbols: list[str]) -> list[str]:
    found = []
    low = text.lower()
    for s in symbols:
        for alias in ALIASES.get(s, [s.lower()]):
            if alias in CASE_SENSITIVE:
                if re.search(rf"(\$|\b){alias.upper()}\b", text):
                    found.append(s)
                    break
            elif re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
                found.append(s)
                break
    return found


@dataclass
class Headline:
    source: str
    title: str
    link: str
    ts: float
    symbols: list[str] = field(default_factory=list)


class SocialFeed:
    def __init__(self, symbols: list[str], simulate: bool = False, log=None):
        self.symbols = symbols
        self.simulate = simulate
        self.log = log or (lambda *a: None)
        self.client = httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True)
        self.mention_counts: dict[str, int] = {s: 0 for s in symbols}
        self.mention_baseline: dict[str, float] = {}
        self.trending: list[str] = []
        self.fear_greed: int | None = None
        self.fear_greed_label = ""
        self.posts: list[dict] = []
        self.headlines: list[Headline] = []
        self._seen_links: set[str] = set()
        self.healthy = {"reddit": False, "news": False}

    # ----- hype -----
    async def poll_hype(self) -> None:
        if self.simulate:
            self._sim_hype()
            return
        counts = {s: 0 for s in self.symbols}
        posts = []
        ok = False
        for sub in SUBREDDITS:
            try:
                r = await self.client.get(f"https://www.reddit.com/r/{sub}/hot.json", params={"limit": 50})
                for child in r.json()["data"]["children"]:
                    d = child["data"]
                    text = f"{d.get('title', '')} {d.get('selftext', '')[:500]}"
                    hit = mentions(text, self.symbols)
                    weight = 1 + min(d.get("score", 0), 5000) / 1000  # popular posts count more
                    for s in hit:
                        counts[s] += weight
                    if hit:
                        posts.append({"sub": sub, "title": d.get("title", ""), "score": d.get("score", 0), "symbols": hit})
                ok = True
            except Exception as e:
                self.log("Hype Scout", "warn", f"Reddit r/{sub} failed: {e}")
        self.healthy["reddit"] = ok
        if ok:
            self._update_counts(counts)
            self.posts = sorted(posts, key=lambda p: -p["score"])[:30]
        try:
            r = await self.client.get("https://api.coingecko.com/api/v3/search/trending")
            self.trending = [c["item"]["symbol"].upper() for c in r.json().get("coins", [])]
        except Exception as e:
            self.log("Hype Scout", "warn", f"CoinGecko trending failed: {e}")
        try:
            r = await self.client.get("https://api.alternative.me/fng/")
            d = r.json()["data"][0]
            self.fear_greed, self.fear_greed_label = int(d["value"]), d["value_classification"]
        except Exception as e:
            self.log("Hype Scout", "warn", f"Fear & Greed index failed: {e}")

    def _update_counts(self, counts: dict[str, float]) -> None:
        for s, c in counts.items():
            base = self.mention_baseline.get(s)
            self.mention_baseline[s] = c if base is None else base * 0.8 + c * 0.2
        self.mention_counts = {s: round(c, 1) for s, c in counts.items()}

    def hype_velocity(self, symbol: str) -> float:
        """How far current buzz is above its own recent average. 0 = normal, 1 = double."""
        base = self.mention_baseline.get(symbol, 0)
        now = self.mention_counts.get(symbol, 0)
        return (now - base) / (base + 2)

    # ----- news -----
    async def poll_news(self) -> list[Headline]:
        if self.simulate:
            return self._sim_news()
        fresh: list[Headline] = []
        ok = False
        for source, url in RSS_FEEDS.items():
            try:
                r = await self.client.get(url)
                root = ET.fromstring(r.content)
                for item in root.iter("item"):
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or title).strip()
                    if not title or link in self._seen_links:
                        continue
                    self._seen_links.add(link)
                    fresh.append(Headline(source, title, link, time.time(), mentions(title, self.symbols)))
                ok = True
            except Exception as e:
                self.log("News Hunter", "warn", f"{source} feed failed: {e}")
        self.healthy["news"] = ok
        self.headlines = (fresh + self.headlines)[:200]
        return fresh

    # ----- simulation -----
    def _sim_hype(self) -> None:
        counts = {s: max(0.0, random.gauss(5, 3)) * (6 if random.random() < 0.05 else 1) for s in self.symbols}
        self._update_counts(counts)
        self.trending = random.sample(self.symbols, 3)
        self.fear_greed = random.randint(20, 80)
        self.fear_greed_label = "Greed" if self.fear_greed > 55 else "Fear" if self.fear_greed < 45 else "Neutral"
        self.healthy["reddit"] = True

    def _sim_news(self) -> list[Headline]:
        s = random.choice(self.symbols)
        mood = random.choice(["surges after ETF rumor", "slides as regulators circle", "partnership announced", "whales accumulate", "outage hits network"])
        h = Headline("Simulated", f"{s} {mood}", f"sim://{time.time()}", time.time(), [s])
        self.headlines = ([h] + self.headlines)[:200]
        self.healthy["news"] = True
        return [h]
