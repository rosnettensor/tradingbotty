"""'Ask the bot': the chat answers from the bot's own state, and says plainly when there is no AI."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeFusion, _engine  # noqa: E402


def test_chat_without_ai_key_gives_raw_facts(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.llm.client = None                       # no ANTHROPIC_API_KEY, whatever the local .env says
    r = asyncio.run(e.chat("Wie geht's dem Konto?", []))
    assert r["ok"] is False
    assert "ANTHROPIC_API_KEY" in r["answer"] and "Modus: live" in r["answer"]
    r = asyncio.run(e.chat("How is the account?", []))
    assert not r["ok"] and "Mode: live" in r["answer"]


def test_chat_with_ai_sends_context_and_trimmed_history(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    for i in range(200):
        e.db.log("Daily Brain", "info", f"step {i} " + "x" * 300)
    seen = {}

    async def create(**kw):
        seen.update(kw)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=50), stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="Alles ruhig.")])

    e.llm.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    history = [{"role": "assistant", "content": "hi"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(20)]
    r = asyncio.run(e.chat("Was macht der Fast-Topf?", history))
    assert r["ok"] and r["answer"] == "Alles ruhig." and r["cost"] > 0
    msgs = seen["messages"]
    assert msgs[0]["role"] == "user" and msgs[-1] == {"role": "user", "content": "Was macht der Fast-Topf?"}
    assert len(msgs) <= 9 and all(a["role"] != b["role"] for a, b in zip(msgs, msgs[1:]))
    assert "output_config" not in seen
    ctx = seen["system"].split("Context (JSON):\n", 1)[1]
    assert len(ctx) <= 6000
    data = json.loads(ctx)
    assert data["mode"] == "live" and data["agents"] and "fast_pot" in data and data["log"]
    assert e.db.query("SELECT COUNT(*) n FROM llm_calls WHERE agent='Chat'")[0]["n"] == 1


def test_chat_endpoint_rejects_empty_message():
    from fastapi import HTTPException

    from tradingbotty.server import ChatIn, chat

    for bad in ("", "   ", "x" * 2001):
        with pytest.raises(HTTPException) as err:
            asyncio.run(chat(ChatIn(message=bad)))
        assert err.value.status_code == 400
