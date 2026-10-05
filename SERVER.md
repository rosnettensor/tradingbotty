# TradingBotty auf einem Server (Render)

Damit läuft der Bot rund um die Uhr, auch wenn der Mac zu ist. Kosten bei Render: Starter etwa 7 USD pro Monat plus 1 GB Disk für etwa 0.25 USD.

**Wichtig:** Es darf immer nur **eine** Kopie handeln. Sonst kauft und verkauft der Bot alles doppelt. Darum gibt es den Umzug mit `move-to-server.sh`: Danach bleibt der Mac auf STANDBY und lässt sich erst wieder auf LIVE schalten, wenn du in Controls "trade on this computer again" drückst.

## 1. Server anlegen (einmal, etwa 10 Minuten)

1. Auf render.com einloggen (gleicher Account wie botanica).
2. **New → Blueprint** und das Repo `rosnettensor/tradingbotty` wählen. Render liest `render.yaml` und schlägt einen Web Service "tradingbotty" in Frankfurt mit 1 GB Disk vor.
3. Render fragt nach den geheimen Werten. Sie bleiben bei Render und landen nie auf GitHub:
   - `TB_PASSWORD`: ein langes Passwort für das Dashboard, das du dir merkst
   - `BITPANDA_FUSION_API_KEY`: derselbe Fusion-Key wie im `.env` auf dem Mac (nur Read + Trade)
   - `ANTHROPIC_API_KEY`: optional
   - `WHATSAPP_PHONE` und `WHATSAPP_APIKEY`: optional
4. **Apply** drücken und warten, bis "Live" steht. Die Adresse sieht so aus: `https://tradingbotty-xxxx.onrender.com`.
5. Die Adresse öffnen. Der Browser fragt nach einem Login: Benutzername beliebig, Passwort ist `TB_PASSWORD`. Der Server läuft jetzt auf STANDBY und handelt nicht.

## 2. Umziehen (Mac → Server)

Der Mac-Bot muss dabei laufen. Im Terminal:

```
cd ~/tradingbotty && ./move-to-server.sh https://tradingbotty-xxxx.onrender.com
```

Das Skript fragt verdeckt nach dem Passwort. Dann:

1. Der Mac geht auf STANDBY und gibt seine Datenbank ab: Coins des Bots, Fast-Pot, Geschichte und Einstellungen.
2. Der Server übernimmt die Datenbank und startet neu, das dauert etwa eine Minute.
3. Danach öffnest du die Server-Adresse, prüfst das Cockpit und schaltest oben auf **LIVE** (REAL MONEY eintippen).
4. Den Mac-Bot stoppst du mit Ctrl+C.

## 3. Updates

`autoDeploy` ist aus, damit nie mitten in der Nacht ein Update startet. Bei einem Update im Render-Dashboard **Manual Deploy → Deploy latest commit** drücken. Modus, Coins und Einstellungen bleiben erhalten, weil die Datenbank auf der Disk liegt.

## Zurück auf den Mac

1. Den Server auf STANDBY schalten.
2. Auf dem Mac in Controls **"trade on this computer again"** drücken.
3. Den neuesten Stand holst du mit `curl -u bot:PASSWORT https://…/api/move/export -o data/tradingbotty.db`, während der Mac-Bot gestoppt ist.
