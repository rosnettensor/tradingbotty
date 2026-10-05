#!/usr/bin/env bash
# Moves the running bot from this Mac to your Render server, so that only ONE copy ever trades.
#   ./move-to-server.sh https://tradingbotty-xxxx.onrender.com
# The Mac bot must be running. Afterwards it stays on STANDBY; the server starts on STANDBY too and you switch it LIVE there.
set -e
URL="${1%/}"
[ -n "$URL" ] || { echo "Usage: ./move-to-server.sh https://your-server.onrender.com"; exit 1; }
read -s -p "Dashboard password of the server (TB_PASSWORD): " PW; echo
CODE=$(curl -s -o /dev/null -w "%{http_code}" -u "bot:$PW" "$URL/api/state")
case "$CODE" in
  200) ;;
  401) echo "Wrong password (the server's TB_PASSWORD). Nothing changed."; exit 1 ;;
  404) echo "No bot at $URL (404). Copy the address exactly from the top of the Render page. Nothing changed."; exit 1 ;;
  *)   echo "Server not reachable ($CODE). Is it 'Live' on Render? Nothing changed."; exit 1 ;;
esac
LOCAL="${LOCAL:-http://localhost:8000}"
TMP="$(mktemp -d)/tradingbotty.db"
echo "1/2 This Mac goes to STANDBY and hands over its database..."
curl -fsS "$LOCAL/api/move/export?to=$URL" -o "$TMP"
echo "2/2 Sending it to the server ($(du -h "$TMP" | cut -f1))..."
curl -fsS -u "bot:$PW" -H "Content-Type: application/octet-stream" --data-binary "@$TMP" "$URL/api/move/import"
echo
echo "Done. The server restarts with your data in about a minute."
echo "Open $URL, check the Cockpit, then switch it LIVE (type REAL MONEY). Stop the Mac bot with Ctrl+C."
