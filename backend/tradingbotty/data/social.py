"""Free crypto news from RSS feeds, for the News Hunter and the Guardian."""
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

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "Decrypt": "https://decrypt.co/feed",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "The Block": "https://www.theblock.co/rss.xml",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "CryptoPotato": "https://cryptopotato.com/feed/",
    "The Defiant": "https://thedefiant.io/api/feed",
}
# added after the first release: merged into a saved feed list once, so existing installs get them too
NEW_FEEDS_2026_10 = ["The Block", "Bitcoin Magazine", "CryptoSlate", "CryptoPotato", "The Defiant"]

ALIASES = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "eth", "ether"], "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"], "DOGE": ["doge", "dogecoin"], "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"], "LINK": ["chainlink", "link"], "DOT": ["polkadot", "dot"],
    "PEPE": ["pepe"], "LTC": ["litecoin", "ltc"], "BCH": ["bitcoin cash", "bch"], "NEAR": ["near protocol", "near"],
    "UNI": ["uniswap", "uni"], "AAVE": ["aave"], "ATOM": ["cosmos hub", "atom"], "XLM": ["stellar", "xlm"],
    "SUI": ["sui"], "FET": ["fetch.ai", "fet", "artificial superintelligence alliance"], "TRX": ["tron", "trx"],
    "HBAR": ["hedera", "hbar"], "ARB": ["arbitrum", "arb"], "ONDO": ["ondo"],
}
# short tickers that are also common words only count in upper case ($DOT, DOT)
CASE_SENSITIVE = {"dot", "link", "sol", "ada", "coin", "eth", "near", "uni", "atom", "sui", "fet", "arb", "ondo", "trx"}


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
        self.extra_symbols: list[str] = []  # coins the daily brain may trade, watched or not
        self.simulate = simulate
        self.log = log or (lambda *a: None)
        self.client = httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True)
        self.headlines: list[Headline] = []
        self._seen_links: set[str] = set()
        self.healthy = {"news": False}
        # editable from the dashboard
        self.feeds: dict[str, str] = dict(RSS_FEEDS)
        # per-source health for the dashboard: name -> {ok, items, error, ts}
        self.source_status: dict[str, dict] = {}

    # ----- hype -----
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
                    syms = list(dict.fromkeys(self.symbols + self.extra_symbols))
                    fresh.append(Headline(source, title, link, published, mentions(title, syms)))
                ok = True
            except Exception as e:
                self._mark(source, False, 0, e)
                self.log("News Hunter", "warn", f"{source} feed failed: {e}")
        self.healthy["news"] = ok
        self.headlines = (fresh + self.headlines)[:200]
        return fresh

    # ----- simulation -----
    def _sim_news(self) -> list[Headline]:
        s = random.choice(self.symbols)
        mood = random.choice(["surges after ETF rumor", "slides as regulators circle", "partnership announced", "whales accumulate", "outage hits network"])
        h = Headline("Simulated", f"{s} {mood}", f"sim://{time.time()}", time.time(), [s])
        self.headlines = ([h] + self.headlines)[:200]
        self.healthy["news"] = True
        for name in self.feeds:
            self._mark(name, True, 20)
        return [h]
