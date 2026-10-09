"""Orders from the chat bar: "kaufe ADA für 30 CHF im Fast Pot", "verkaufe RLC", "verkaufe alles im Fast Pot".

Read by plain rules, never by the AI, so a chat answer can't turn into an order by itself. Each order is first
shown as a proposal with the Risk Officer's checks; it goes out only after you tap "Ausführen" (a one-time token,
valid for 2 minutes), and then through the same checks as the bot's own orders.

What the chat can do:
- buy a coin for an amount into the fast pot (from the pot's free cash); the pot's rule then looks after it like
  its own coin: sells at +10%, -10% or after 3 days;
- sell a coin of the fast pot or of the daily brain, or everything in the fast pot.
Your own coins and buys for the daily brain stay in the Bitpanda app and with the brain's rules.
"""
from __future__ import annotations

import re
import secrets
import time

from . import fastlab, stance

TTL = 120  # seconds a proposal stays valid
PRICE = "https://data-api.binance.vision/api/v3/ticker/price"

_SELL = re.compile(r"\b(verkauf\w*|sell\w*|raus|abstossen)\b")
_BUY = re.compile(r"\b(kauf\w*|buy\w*)\b")
_ASK = re.compile(r"^\s*(soll|sollte|sollen|würdest|würde|wäre|ist es|lohnt|warum|wieso|wann|was|wie|welche\w*|"
                  r"should|would|why|when|what|how|which|is it|can you explain)\b")
_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:chf|fr\b|fr\.|franken|stutz|sfr|\.-)"
                     r"|(?:für|for|mit|with|um)\s+(\d+(?:[.,]\d+)?)\b")
_ALL = re.compile(r"\b(alles|alle|all|everything)\b")
_FAST = re.compile(r"fast|topf|pot\b")
_BRAIN = re.compile(r"brain|hirn")
_STOP = {"CHF", "EUR", "USD", "FR", "FAST", "POT", "FASTPOT", "TOPF", "BRAIN", "FUR", "FOR", "IM", "IN", "ALL",
         "ALLES", "ALLE", "DIE", "DER", "DAS", "DEN", "UND", "MIT", "MY", "MEIN", "MEINE", "ETWAS", "ONE", "AND",
         "THE", "SOME", "BITTE", "PLEASE", "JETZT", "NOW", "VON", "AUS", "ES", "EIN", "EINE", "UM", "AT", "TO"}


def parse(text: str, symbols: set[str]) -> dict | None:
    """The order in a chat message, or None if it isn't one (questions about buying are questions, not orders)."""
    t = " ".join(text.lower().split())
    if not t or t.endswith("?") or _ASK.search(t):
        return None
    side = "SELL" if _SELL.search(t) else "BUY" if _BUY.search(t) else None
    if not side:
        return None
    m = _AMOUNT.search(t)
    amount = float((m.group(1) or m.group(2)).replace(",", ".")) if m else None
    words = {w.upper() for w in re.findall(r"[a-z0-9]{2,12}", t)}
    coins = sorted((words & {s.upper() for s in symbols}) - _STOP)
    everything = bool(_ALL.search(t)) and side == "SELL" and not coins
    if not coins and amount is None and not everything:
        return None
    book = "fast" if _FAST.search(t) else "brain" if _BRAIN.search(t) else None
    return {"side": side, "coins": coins, "amount": amount, "book": book, "all": everything}


class ChatOrders:
    def __init__(self, engine):
        self.e = engine
        self.pending: dict[str, dict] = {}

    def symbols(self) -> set[str]:
        e = self.e
        pairs = getattr(e.live or e._viewer, "pairs", None) or {}
        known = set(getattr(e, "fusion_coins", None) or ()) | set(e.prices.quotes)
        return set(pairs) | known | set(e.db.get("fast_qty", {})) | set(e.db.get("live_qty", {}))

    async def usd_price(self, sym: str) -> float | None:
        """The coin's price on Binance in USDT: the fast pot's rule reads Binance 4-hour candles, so a manual buy's
        entry price must be in the same money. None if Binance doesn't list the coin."""
        if self.e.settings.simulate:
            return None
        try:
            r = await self.e.prices.client.get(PRICE, params={"symbol": f"{sym}USDT"})
            return float(r.json()["price"])
        except Exception:
            return None

    async def understand(self, message: str) -> dict | None:
        """A proposal (with a token when it can go out) or an explanation why not; None if it isn't an order."""
        e = self.e
        st = stance.parse(message)
        if st and not parse(message, self.symbols()):
            return self._course(st)
        p = parse(message, self.symbols())
        if not p:
            return None
        cur = e.live.currency if e.live else (e.wallet or {}).get("currency", "CHF")
        fast_qty, brain_qty = e.db.get("fast_qty", {}), e.db.get("live_qty", {})

        def no(why: str) -> dict:
            return {"ok": False, "answer": why}

        if p["side"] == "SELL" and p["all"]:
            if p["book"] == "brain":
                return no("Alles vom Daily Brain verkaufe ich per Chat nicht auf einmal. Nenne mir den Coin, "
                          "z.B. \"verkaufe NEAR vom Brain\".")
            if not fast_qty:
                return no("Der Fast Pot hält gerade keine Coins, es gibt nichts zu verkaufen.")
            order = {"side": "SELL", "book": "fast", "symbol": None, "all": True}
            text = f"**Alles im Fast Pot verkaufen:** {', '.join(sorted(fast_qty))} zum aktuellen Kurs."
            return self._propose(order, text, [])
        if len(p["coins"]) > 1:
            return no(f"Ich habe mehrere Coins erkannt ({', '.join(p['coins'])}). Bitte eine Order pro Nachricht.")
        if not p["coins"]:
            return no(("Welchen Coin? Zum Beispiel: \"kaufe ADA für 30 CHF im Fast Pot\"." if p["side"] == "BUY"
                       else "Welchen Coin? Zum Beispiel: \"verkaufe RLC\" oder \"verkaufe alles im Fast Pot\"."))
        sym = p["coins"][0]

        if p["side"] == "SELL":
            books = [b for b in ("fast", "brain") if sym in (fast_qty if b == "fast" else brain_qty)]
            if p["book"]:
                books = [b for b in books if b == p["book"]]
            if not books:
                where = {"fast": "im Fast Pot", "brain": "beim Daily Brain"}.get(p["book"], "beim Bot")
                return no(f"{sym} liegt nicht {where}. Deine eigenen Coins verkaufst du in der Bitpanda-App; "
                          "der Bot verkauft nur, was er selbst gekauft hat.")
            if len(books) > 1:
                return no(f"{sym} liegt im Fast Pot und beim Daily Brain. Sag \"verkaufe {sym} im Fast Pot\" "
                          f"oder \"verkaufe {sym} vom Brain\".")
            book = books[0]
            qty = (fast_qty if book == "fast" else brain_qty)[sym]
            cost = e.db.get(e._book_keys(book)[1], {}).get(sym, 0.0)
            who = "Fast Pot" if book == "fast" else "Daily Brain"
            notes = []
            if book == "brain":
                notes.append("Der Daily Brain kauft ihn frühestens nach der nächsten Tageskerze wieder, und nur, "
                             "wenn sein Signal dann noch steht.")
            text = (f"**{sym} verkaufen ({who}):** {qty:.6g} Stück, gekauft für {cost:.2f} {cur}, zum aktuellen Kurs. "
                    "Fusion-Gebühr etwa 0.25%.")
            return self._propose({"side": "SELL", "book": book, "symbol": sym, "all": False}, text, notes)

        # BUY: only into the fast pot
        if p["book"] == "brain":
            return no("Der Daily Brain kauft nur nach seinen eigenen Regeln. Per Chat kaufe ich in den Fast Pot: "
                      f"\"kaufe {sym} für 30 {cur} im Fast Pot\".")
        if p["amount"] is None:
            return no(f"Für wie viel? Zum Beispiel: \"kaufe {sym} für 30 {cur} im Fast Pot\".")
        fp = e.fast
        c = fp.cfg()
        if not c["on"]:
            return no("Der Fast Pot ist aus. Schalte ihn in Research → Fast Trader Lab ein; dann kann ich darin "
                      "kaufen, und seine Regel passt danach auf den Coin auf.")
        amount = round(p["amount"], 2)
        pairs = getattr(e.live or e._viewer, "pairs", None) or {}
        if pairs and sym not in pairs:
            return no(f"{sym} kann man auf Bitpanda Fusion nicht handeln.")
        min_amt = max(e._min_order(sym), 1)
        if amount < min_amt:
            return no(f"{amount:g} {cur} ist zu wenig: Fusion nimmt für {sym} erst Orders ab {min_amt:g} {cur}. "
                      f"Versuch es mit \"kaufe {sym} für {max(25, round(min_amt + 0.5)):g} {cur} im Fast Pot\".")
        free = round(fp.pot_size(c) - sum(e.db.get("fast_cost", {}).values()), 2)
        if amount > free:
            return no(f"Im Fast Pot sind nur {free:.2f} {cur} frei (Topf {fp.pot_size(c):.2f} {cur}). "
                      "Kauf weniger oder vergrössere den Topf im Fast Trader Lab.")
        if sym in e.db.get("live_qty", {}):
            return no(f"{sym} hält schon der Daily Brain. Der Fast Pot kauft keine Coins doppelt.")
        if sym in fast_qty:
            return no(f"{sym} liegt schon im Fast Pot. Nachkaufen per Chat geht nicht; verkaufe zuerst oder nimm einen "
                      "anderen Coin.")
        if not e.settings.simulate and await self.usd_price(sym) is None:
            return no(f"Die Regel des Fast Pots liest Binance-Kerzen, und {sym} ist dort nicht gelistet. Sie könnte "
                      "den Coin nicht überwachen, darum kaufe ich ihn nicht in den Fast Pot.")
        notes = []
        g = e.guard().get(sym)
        if g:
            notes.append(f"⚠ Der Guardian sperrt {sym} gerade ({g.get('reason', 'Warnung')}). Der Bot selbst würde ihn "
                         "nicht kaufen.")
        strat = fastlab.by_name(c["strategy"])
        rule = (f"Danach passt die Regel des Fast Pots darauf auf: Verkauf bei +{round(strat.tp * 100)}%, "
                f"-{round(strat.stop * 100)}% oder nach {strat.max_hold * 4 // 24} Tagen."
                if strat is not None and hasattr(strat, "tp") and hasattr(strat, "max_hold") else
                "Danach entscheidet die Regel des Fast Pots alle 4 Stunden, wann er verkauft.")
        text = (f"**{sym} kaufen für {amount:.2f} {cur}** aus dem Fast Pot ({free:.2f} {cur} frei). "
                f"Fusion-Gebühr etwa 0.25%. {rule}")
        return self._propose({"side": "BUY", "book": "fast", "symbol": sym, "amount": amount, "all": False}, text, notes)

    def _course(self, p: dict) -> dict:
        """A course to confirm: what changes, until when. Allowed on standby too (it only changes settings)."""
        e = self.e
        info = stance.STANCES[p["stance"]]
        cur = e.stance.get()
        if p["stance"] == cur["key"] == "normal":
            return {"ok": False, "answer": "Der Bot läuft schon auf Normal."}
        until = stance.until_for(p)
        when = f"bis {stance.swiss(until)}, dann wieder Normal" if until else "ab sofort"
        text = f"**Kurs {info['name']}** ({when}). {info['what']}"
        notes = ["Die Signale bleiben gleich: welche Coins gekauft und verkauft werden, entscheiden weiter die "
                 "getesteten Regeln."] if p["stance"] != "normal" else []
        if cur["key"] != "normal":
            notes.append(f"Ersetzt den aktuellen Kurs {cur['name']}.")
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["until"] > now}
        token = secrets.token_urlsafe(12)
        order = {"kind": "stance", "side": "STANCE", "stance": p["stance"], "ends": until}
        self.pending[token] = {"order": order, "until": now + TTL}
        answer = "\n\n".join([text, *notes, "Tippe auf **Kurs setzen**, um ihn zu übernehmen (gilt 2 Minuten)."])
        return {"ok": True, "answer": answer, "order": {**order, "token": token, "until": now + TTL}}

    def _propose(self, order: dict, text: str, notes: list[str]) -> dict:
        e = self.e
        blocker = None
        if e.mode != "live" or not e.live:
            blocker = "Der Bot ist auf STANDBY: echte Orders gehen nur im LIVE-Modus raus."
        elif e.kill_switch:
            blocker = "Der Kill-Switch ist an: es gehen keine Orders raus."
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["until"] > now}
        if blocker:
            return {"ok": False, "answer": text + "\n\n" + blocker}
        token = secrets.token_urlsafe(12)
        self.pending[token] = {"order": order, "until": now + TTL}
        answer = "\n\n".join([text, *notes, "Echtes Geld: tippe auf **Ausführen**, um die Order zu senden "
                                            "(gilt 2 Minuten)."])
        return {"ok": True, "answer": answer, "order": {**order, "token": token, "until": now + TTL}}

    async def confirm(self, token: str) -> dict:
        """Send a proposed order. Only with a valid, unused token; re-checks mode and kill switch."""
        e = self.e
        p = self.pending.pop(token or "", None)
        if not p or p["until"] < time.time():
            return {"ok": False, "answer": "Dieser Vorschlag ist abgelaufen oder schon benutzt. Schreib die Order "
                                           "nochmal, dann rechne ich neu."}
        o = p["order"]
        if o.get("kind") == "stance":
            c = e.stance.set(o["stance"], o.get("ends"), by="you (chat)")
            return {"ok": True, "answer": (f"✅ Kurs {c['name']} gilt bis {stance.swiss(c['until'])}."
                                           if c.get("until") else "✅ Zurück auf Normal.")}
        if e.mode != "live" or not e.live or e.kill_switch:
            return {"ok": False, "answer": "Nicht gesendet: der Bot ist nicht LIVE oder der Kill-Switch ist an."}
        if e.__dict__.get("_fast_trading"):
            return {"ok": False, "answer": "Der Fast Pot entscheidet gerade selbst. Versuch es in einer Minute nochmal."}
        cur = e.live.currency
        e._fast_trading = True  # the 4-hour decision waits while your order runs
        try:
            if o["side"] == "SELL" and o.get("all"):
                res = await e.fast.close_all()
                return {"ok": True, "answer": "Fast Pot: " + ("; ".join(res["done"]) or "nichts verkauft") + "."}
            if o["side"] == "SELL":
                who = "fast pot" if o["book"] == "fast" else "daily brain"
                ex = await e._live_sell(o["symbol"], f"{who}: sold by you (chat)", book=o["book"])
                if not ex:
                    return {"ok": False, "answer": f"{o['symbol']} wurde nicht verkauft (Fusion hat abgelehnt oder "
                                                   "der Betrag liegt unter dem Minimum). Details im Feed."}
                got = float(ex.get("notional", 0) or 0)
                extra = ""
                if o["book"] == "brain":  # the brain mustn't buy it straight back on the same daily candle
                    e.db.set("brain_sold_by_you", {**(e.db.get("brain_sold_by_you") or {}), o["symbol"]: time.time()})
                if o["book"] == "fast":
                    pnl = e.fast.booked_sell(o["symbol"], ex)
                    extra = f", Ergebnis {pnl:+.2f} {cur} nach Gebühren"
                return {"ok": True, "answer": f"✅ {o['symbol']} verkauft für {got:.2f} {cur}{extra}."}
            sym = o["symbol"]
            try:
                amount, got = await e._live_buy(sym, o["amount"], "fast pot: bought by you (chat)", book="fast")
            except Exception as ex:
                return {"ok": False, "answer": f"Nicht gekauft: {str(ex)[:160]}"}
            entry = await self.usd_price(sym)
            if entry is None:  # simulate, or Binance unreachable: the newest 4-hour close the rule itself would use
                cd = await e.fast._candles([sym])
                entry = next((x for x in reversed(cd.c.get(sym) or []) if x), None) or (amount / got if got else 1.0)
            c = e.fast.cfg()
            bar = (time.time() // fastlab.BAR - 1) * fastlab.BAR
            c["pos"][sym] = {"entry": entry, "bar": bar, "ts": time.time(), "cost": amount,
                             "qty": got, "manual": True}
            e.fast.save(c)
            e._log("Live Desk", "live", f"LIVE BUY {sym}: {amount:.2f} {cur} filled (fast pot: bought by you in the chat).")
            return {"ok": True, "answer": f"✅ {sym} gekauft für {amount:.2f} {cur} ({got:.6g} Stück). Liegt jetzt im "
                                          "Fast Pot; seine Regel entscheidet, wann er wieder verkauft."}
        finally:
            e._fast_trading = False
