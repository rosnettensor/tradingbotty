"""Free hype and news sources: Reddit, CoinGecko trending, Crypto Fear & Greed, and news RSS feeds.

X (Twitter) is left out on purpose for now: reading posts costs about 0.005 USD each.
"""
from __future__ import annotations

import random
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field

import httpx

UA = {"User-Agent": "TradingBotty/0.1 (hobby research bot)"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/128.0 Safari/537.36"}
ATOM = "{http://www.w3.org/2005/Atom}"


def parse_atom(content: bytes) -> list[tuple[str, str, int]]:
    """Reddit RSS (Atom) entries as (title, text, score). RSS has no scores, so every post counts as 0."""
    root = ET.fromstring(content)
    out = []
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        body = re.sub(r"<[^>]+>", " ", entry.findtext(f"{ATOM}content") or "")
        out.append((title, body, 0))
    if not out:
        raise ValueError("empty feed")
    return out

SUBREDDITS = ["CryptoCurrency", "CryptoMarkets", "wallstreetbets", "stocks", "investing"]
RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "Decrypt": "https://decrypt.co/feed",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
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


MAX_NEWS_AGE = 12 * 3600


def parse_date(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


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
        # editable from the dashboard
        self.subreddits: list[str] = list(SUBREDDITS)
        self.feeds: dict[str, str] = dict(RSS_FEEDS)
        # per-source health for the dashboard: name -> {ok, items, error, ts}
        self.source_status: dict[str, dict] = {}

    # ----- hype -----
    async def poll_hype(self) -> None:
        if self.simulate:
            self._sim_hype()
            return
        counts = {s: 0 for s in self.symbols}
        posts = []
        ok = False
        for sub in list(self.subreddits):
            try:
                got = await self._reddit_posts(sub)
                self._mark(f"r/{sub}", True, len(got))
                for title, body, score in got:
                    hit = mentions(f"{title} {body[:500]}", self.symbols)
                    weight = 1 + min(score, 5000) / 1000  # popular posts count more
                    for s in hit:
                        counts[s] += weight
                    if hit:
                        posts.append({"sub": sub, "title": title, "score": score, "symbols": hit})
                ok = True
            except Exception as e:
                self._mark(f"r/{sub}", False, 0, e)
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

    async def _reddit_posts(self, sub: str) -> list[tuple[str, str, int]]:
        """Hot posts as (title, text, score). Reddit often blocks the JSON API for scripts, so fall back to its RSS feed."""
        try:
            r = await self.client.get(f"https://old.reddit.com/r/{sub}/hot.json", params={"limit": 50}, headers=BROWSER_UA)
            children = r.json()["data"]["children"]
            return [(c["data"].get("title", ""), c["data"].get("selftext", ""), int(c["data"].get("score", 0))) for c in children]
        except Exception:
            pass
        r = await self.client.get(f"https://www.reddit.com/r/{sub}/hot/.rss", params={"limit": 50}, headers=BROWSER_UA)
        r.raise_for_status()
        return parse_atom(r.content)

    def _mark(self, name: str, ok: bool, items: int, error=None) -> None:
        self.source_status[name] = {"ok": ok, "items": items, "error": str(error)[:120] if error else None,
                                    "ts": time.time()}

    async def check_feed(self, url: str) -> str | None:
        """None if the URL is a readable RSS feed, else a reason."""
        try:
            r = await self.client.get(url, headers=BROWSER_UA)
            if not list(ET.fromstring(r.content).iter("item")):
                return "no news items found at that address"
            return None
        except Exception as e:
            return f"not a readable RSS feed ({e.__class__.__name__})"

    async def check_subreddit(self, sub: str) -> str | None:
        try:
            await self._reddit_posts(sub)
            return None
        except Exception:
            return f"r/{sub} couldn't be read"

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
        for source, url in list(self.feeds.items()):
            try:
                r = await self.client.get(url, headers=BROWSER_UA)
                root = ET.fromstring(r.content)
                items = list(root.iter("item"))
                self._mark(source, bool(items), len(items), None if items else "no items in feed")
                for item in items:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or title).strip()
                    key = re.sub(r"\W+", " ", title.lower()).strip()
                    if not title or link in self._seen_links or key in self._seen_links:
                        continue
                    self._seen_links.update((link, key))
                    published = parse_date(item.findtext("pubDate")) or time.time()
                    if time.time() - published > MAX_NEWS_AGE:
                        continue  # old news is already priced in
                    fresh.append(Headline(source, title, link, published, mentions(title, self.symbols)))
                ok = True
            except Exception as e:
                self._mark(source, False, 0, e)
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
        for sub in self.subreddits:
            self._mark(f"r/{sub}", True, 50)
        self.posts = [{"sub": random.choice(self.subreddits), "title": f"Is {s} about to move? (simulated post)",
                       "score": random.randint(5, 3000), "symbols": [s]} for s in random.sample(self.symbols, 6)]

    def _sim_news(self) -> list[Headline]:
        s = random.choice(self.symbols)
        mood = random.choice(["surges after ETF rumor", "slides as regulators circle", "partnership announced", "whales accumulate", "outage hits network"])
        h = Headline("Simulated", f"{s} {mood}", f"sim://{time.time()}", time.time(), [s])
        self.headlines = ([h] + self.headlines)[:200]
        self.healthy["news"] = True
        for name in self.feeds:
            self._mark(name, True, 20)
        return [h]
