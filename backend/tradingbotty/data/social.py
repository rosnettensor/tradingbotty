"""Free crypto news from RSS feeds, for the News Hunter and the Guardian."""
from __future__ import annotations

import html
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
FEED_HEADERS = {**BROWSER_UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
                "Accept-Language": "en-US,en;q=0.8"}
ATOM = "{http://www.w3.org/2005/Atom}"
HTTP_WHY = {400: "the site answered 'bad request'", 401: "the site wants a login", 403: "the site blocks servers like ours",
            404: "the address no longer exists", 410: "the feed was removed", 429: "too many requests, the site slows us down",
            451: "blocked in this region"}


class FeedError(Exception):
    pass


def _strip(text: str) -> str:
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def parse_items(content: bytes) -> list[dict]:
    """RSS or Atom items as {title, link, date}. Tolerant: falls back to a plain text scan when the XML is broken
    (stray bytes before the header, HTML entities like &nbsp;, which many WordPress feeds have)."""
    body = content.lstrip(b"\xef\xbb\xbf \t\r\n")
    try:
        root = ET.fromstring(body)
        if root.tag.lower().endswith("html"):
            raise FeedError("not an RSS feed (the site sent a web page)")
        out = [{"title": (i.findtext("title") or "").strip(), "link": (i.findtext("link") or "").strip(),
                "date": i.findtext("pubDate")} for i in root.iter("item")]
        for e in root.iter(ATOM + "entry"):
            link = e.find(ATOM + "link")
            out.append({"title": (e.findtext(ATOM + "title") or "").strip(),
                        "link": (link.get("href") if link is not None else "") or "",
                        "date": e.findtext(ATOM + "published") or e.findtext(ATOM + "updated")})
        return out
    except ET.ParseError:
        text = body.decode("utf-8", "replace")
        if "<item" not in text and "<entry" not in text:
            raise FeedError("not an RSS feed (the site sent a web page)")
        out = []
        for block in re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", text, re.S):
            t = re.search(r"<title[^>]*>(.*?)</title>", block, re.S)
            l = re.search(r"<link[^>]*>(.*?)</link>", block, re.S) or re.search(r"<link[^>]*href=\"([^\"]+)\"", block)
            d = re.search(r"<(?:pubDate|published|updated)>(.*?)</", block, re.S)
            out.append({"title": _strip(t.group(1)) if t else "", "link": _strip(l.group(1)) if l else "",
                        "date": d.group(1).strip() if d else None})
        return out


def parse_any_date(value: str | None) -> float | None:
    ts = parse_date(value)
    if ts is None and value:
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return ts


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

    async def fetch_items(self, url: str) -> list[dict]:
        """Read one feed. Raises FeedError with a plain reason. Some sites refuse a browser disguise but accept an
        honest bot name (or the other way round), so a refused request is retried once with the other one."""
        last = None
        for headers in (FEED_HEADERS, {**UA, "Accept": FEED_HEADERS["Accept"]}):
            try:
                r = await self.client.get(url, headers=headers)
            except httpx.TimeoutException:
                last = FeedError("the site did not answer in 15 seconds")
                continue
            except httpx.HTTPError as e:
                last = FeedError(f"could not connect ({e.__class__.__name__})")
                continue
            if r.status_code >= 400:
                last = FeedError(f"HTTP {r.status_code}: {HTTP_WHY.get(r.status_code, 'the site refused')}")
                continue
            return parse_items(r.content)
        raise last

    async def check_feed(self, url: str) -> str | None:
        """None if the URL is a readable RSS or Atom feed, else a reason."""
        try:
            if not [i for i in await self.fetch_items(url) if i["title"]]:
                return "no news items found at that address"
            return None
        except FeedError as e:
            return str(e)
        except Exception as e:
            return f"not a readable RSS feed ({e.__class__.__name__})"

    # ----- news -----
    async def poll_news(self) -> list[Headline]:
        if self.simulate:
            return self._sim_news()
        fresh: list[Headline] = []
        ok = False
        now = time.time()
        for source, url in list(self.feeds.items()):
            st = self.source_status.get(source) or {}
            if st.get("pause_until", 0) > now:
                continue  # a feed that keeps failing is only retried every few hours
            try:
                items = await self.fetch_items(url)
                self._mark(source, bool(items), len(items), None if items else "no items in feed")
                if st.get("fails"):
                    self.log("News Hunter", "info", f"{source} feed works again.")
                for item in items:
                    title = item["title"]
                    link = item["link"] or title
                    key = re.sub(r"\W+", " ", title.lower()).strip()
                    if not title or link in self._seen_links or key in self._seen_links:
                        continue
                    self._seen_links.update((link, key))
                    published = parse_any_date(item["date"]) or time.time()
                    if time.time() - published > MAX_NEWS_AGE:
                        continue  # old news is already priced in
                    syms = list(dict.fromkeys(self.symbols + self.extra_symbols))
                    fresh.append(Headline(source, title, link, published, mentions(title, syms)))
                ok = True
            except Exception as e:
                fails = st.get("fails", 0) + 1
                why = str(e) if isinstance(e, FeedError) else f"unreadable ({e.__class__.__name__})"
                self._mark(source, False, 0, why)
                self.source_status[source]["fails"] = fails
                if fails >= 3:  # three misses in a row: rest it for 6 hours instead of failing every few minutes
                    self.source_status[source]["pause_until"] = now + 6 * 3600
                if fails in (1, 3):
                    rest = " Paused for 6 hours, then tried again." if fails == 3 else ""
                    self.log("News Hunter", "warn", f"{source} feed failed: {why}.{rest}")
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
