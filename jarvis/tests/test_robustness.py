import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "x")

import pytest  # noqa: E402

from jarvis.brain import Brain  # noqa: E402
from jarvis.google_tools import Google  # noqa: E402
from jarvis.hud import Hud  # noqa: E402
from jarvis.memory import Memory  # noqa: E402
from jarvis.tools import Tools  # noqa: E402
from jarvis.vision import Screen  # noqa: E402


def text(t):
    return NS(type="text", text=t)


def tool_use(i, name="get_time"):
    return NS(type="tool_use", id=f"t{i}", name=name, input={})


class FakeClient:
    def __init__(self, script, tokens=10):
        self.script, self.tokens, self.calls = list(script), tokens, 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        step = self.script.pop(0) if self.script else [text("ok")]
        if isinstance(step, Exception):
            raise step
        return NS(content=step, usage=NS(input_tokens=self.tokens, output_tokens=0))


def make(tmp, script, tokens=10, **a):
    cfg = {"assistant": {"name": "J", "user_title": "senhor", "language": "pt-BR", "model": "m", "max_tokens": 10,
                         "max_tool_rounds": 4, **a},
           "safety": {"workspace": tmp, "apps": {}, "confirm_tools": []}, "screen": {"enabled": False},
           "budget": {"daily_tokens": a.pop("cap", 0)}}
    mem = Memory(Path(tmp) / "t.db")
    tools = Tools(cfg, mem, google=Google(cfg, Path(tmp)), screen=Screen(cfg))
    b = Brain(cfg, tools, mem, lambda _: True)
    b.client = FakeClient(script, tokens)
    return b, mem, cfg


def test_memory_usable_from_other_threads():
    with tempfile.TemporaryDirectory() as tmp:
        m = Memory(Path(tmp) / "t.db")
        err = []

        def work():
            try:
                m.remember("x")
                m.log_action("t", {}, "ok")
                m.add_usage(5)
            except Exception as e:  # noqa: BLE001
                err.append(e)

        ts = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not err and m.usage_today() == 40 and len(m.recent_actions()) == 8


def test_history_trim_keeps_pairs_intact():
    with tempfile.TemporaryDirectory() as tmp:
        script = []
        for i in range(10):  # cada pergunta usa uma ferramenta e depois responde
            script += [[tool_use(i)], [text("r")]]
        b, _, _ = make(tmp, script, max_history_turns=3)
        for i in range(10):
            b.ask(f"p{i}")
        users = [m for m in b.history if m["role"] == "user" and isinstance(m["content"], str)]
        assert len(users) == 3 and b.history[0]["role"] == "user" and isinstance(b.history[0]["content"], str)
        for i, m in enumerate(b.history):  # todo tool_result tem seu tool_use logo antes
            if m["role"] == "user" and isinstance(m["content"], list):
                ids = {x.id for x in b.history[i - 1]["content"] if getattr(x, "type", "") == "tool_use"}
                assert {r["tool_use_id"] for r in m["content"]} <= ids


def test_daily_budget_blocks_before_and_during():
    with tempfile.TemporaryDirectory() as tmp:
        b, mem, cfg = make(tmp, [[text("a")]], tokens=150)
        cfg["budget"]["daily_tokens"] = 100
        assert b.ask("oi") == "a" and mem.usage_today() == 150
        calls = b.client.calls
        assert "Limite diário" in b.ask("outra") and b.client.calls == calls   # não chama a API


def test_budget_hit_mid_loop_keeps_history_valid():
    with tempfile.TemporaryDirectory() as tmp:
        b, mem, cfg = make(tmp, [[tool_use(1)], [text("nunca")]], tokens=150)
        cfg["budget"]["daily_tokens"] = 100
        assert "Limite diário" in b.ask("oi")
        assert b.history[-1]["role"] == "assistant" and b.client.calls == 1


def test_api_failure_rolls_back_history():
    with tempfile.TemporaryDirectory() as tmp:
        b, _, _ = make(tmp, [[text("a")], [tool_use(1)], RuntimeError("rede")])
        b.ask("um")
        before = list(b.history)
        with pytest.raises(RuntimeError):
            b.ask("dois")
        assert b.history == before          # sem tool_use órfão nem pergunta pendurada


def test_audit_logs_status_without_results():
    with tempfile.TemporaryDirectory() as tmp:
        b, mem, cfg = make(tmp, [])
        t = b.tools
        t.run("get_time", {}, lambda _: True)
        t.run("nao_existe", {}, lambda _: True)
        t.run("read_file", {"path": "../../etc/passwd"}, lambda _: True)
        t.confirm_tools = {"write_file"}
        t.run("write_file", {"path": "a", "content": "SEGREDO"}, lambda _: False)
        rows = {r["tool"]: r for r in mem.recent_actions()}
        assert rows["get_time"]["status"] == "ok" and rows["get_time"]["note"] == ""
        assert rows["nao_existe"]["status"] == "desconhecida"
        assert rows["read_file"]["status"] == "erro"
        assert rows["write_file"]["status"] == "negado"
        assert "hora" not in json.dumps(rows["get_time"])      # resultado não é guardado
        for i in range(5):
            mem.log_action("x", {}, "ok", keep=3)
        assert len(mem.recent_actions(100)) == 3               # retenção: mantém só os 3 últimos


def test_hud_audit_endpoint_and_usage_event():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Memory(Path(tmp) / "t.db")
        mem.log_action("gmail_draft", {"to": "a@b.c"}, "negado", "Ação negada")
        hud = Hud(0, lambda t: None, audit_fn=mem.recent_actions, usage_fn=lambda: {"used": 5, "limit": 10})
        hud.start()
        try:
            base = f"http://127.0.0.1:{hud.port}"
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/audit?token=errado", timeout=5)
            rows = json.load(urllib.request.urlopen(f"{base}/audit?token={hud.token}", timeout=5))
            assert rows[0]["tool"] == "gmail_draft" and rows[0]["status"] == "negado"
            r = urllib.request.urlopen(f"{base}/events?token={hud.token}", timeout=5)
            seen = []
            for line in r:
                if line.startswith(b"data:"):
                    seen.append(json.loads(line[5:])["type"])
                if "usage" in seen:
                    break
            assert "usage" in seen
        finally:
            hud.stop()
