"""Event bus: everything the dashboard shows flows through here to all open browser tabs."""
from __future__ import annotations

import asyncio
import json
from typing import Any


class Bus:
    def __init__(self):
        self.clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def publish(self, kind: str, payload: Any) -> None:
        msg = json.dumps({"kind": kind, "data": payload}, default=str)
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # a slow tab drops old messages instead of slowing the bot down
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass
