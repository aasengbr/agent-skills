import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "x")

import pytest  # noqa: E402

from aasis.brain import Brain  # noqa: E402
from aasis.google_tools import Google  # noqa: E402
from aasis.llm.base import LLMResponse, ProviderError, ToolCall, Usage, user_msg  # noqa: E402
from aasis.llm.privacy import classify, history_level  # noqa: E402
from aasis.llm.router import NoProvider, Orchestrator  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.tools import Tools  # noqa: E402
from aasis.vision import Screen  # noqa: E402


class Prov:
    """Provedor falso: executa um roteiro de respostas/erros e guarda o que recebeu."""
    kind = "fake"

    def __init__(self, name, trust, script=None, tokens=10):
        self.name, self.trust, self.script, self.tokens, self.calls = name, trust, list(script or []), tokens, []

    def complete(self, model, stable, volatile, messages, tools, max_tokens):
        self.calls.append({"model": model["id"], "stable": stable, "volatile": volatile, "tools": [t["name"] for t in tools],
                           "messages": list(messages)})
        step = self.script.pop(0) if self.script else LLMResponse(text=f"ok-{self.name}")
        if isinstance(step, Exception):
            raise step
        step.usage = step.usage if step.usage.total else Usage(input=self.tokens)
        return step


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def build(tmp, providers, models=None, routes=None, clock=None, **extra):
    llm = {"providers": {n: {"type": "openai", **extra.get(n, {})} for n in providers},
           "models": models or {n: {"provider": n, "id": f"{n}-id", "tools": True} for n in providers},
           "routes": routes or {"chat": list(providers)}}
    cfg = {"assistant": {"model": "m"}, "llm": llm, "budget": {}}
    mem = Memory(Path(tmp) / "t.db")
    return Orchestrator(cfg, mem, providers=providers, clock=clock or Clock()), mem


def call(o, level=0, task="chat", **kw):
    return o.complete(task, "STABLE", lambda lv: f"VOL{lv}", [user_msg("oi")],
                      lambda lv: [{"name": f"t{lv}", "input_schema": {}}], level, 100, **kw)


# ---------- privacidade ----------
def test_classification_and_history_level():
    assert classify("como está o dólar?") == 0 and classify("quais meus e-mails?") == 1
    assert classify("me lembre do remédio") == 2 and classify("meu saldo no banco") == 2
    assert history_level([{"level": 0}, {"level": 2}, {"level": 1}]) == 2 and history_level([]) == 0


@pytest.mark.parametrize("text", [
    "Tomo Losartana 50 mg às 8h", "eu tomo Rivotril", "tenho diabetes", "minha pressão está alta", "tomar 2 comprimidos",
    "gotas de 10 ml", "meu boleto vence amanhã", "qual o saldo da poupança", "imposto de renda irpf", "tenho depressão"])
def test_classifier_catches_health_and_finance_without_the_word_remedio(text):
    assert classify(text) == 2


@pytest.mark.parametrize("text", ["como está o dólar", "previsão do tempo em Recife", "quem foi Santos Dumont",
                                  "traduza bom dia para inglês", "explique a Selic"])
def test_classifier_leaves_general_questions_normal(text):
    assert classify(text) == 0


def test_external_never_sees_private_levels_and_gets_filtered_context():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Prov("a", "trusted"), Prov("b", "external")
        o, _ = build(tmp, {"a": a, "b": b})
        a.script = [ProviderError("server")]
        assert call(o, level=0).text == "ok-b"                        # nível 0: pode cair para o externo
        assert b.calls[0]["volatile"] == "VOL0" and b.calls[0]["tools"] == ["t0"]   # contexto e ferramentas filtrados
        a.script = [ProviderError("server")]
        with pytest.raises(NoProvider) as e:                           # nível 1: externo é proibido
            call(o, level=1)
        assert len(b.calls) == 1 and "falharam" in e.value.reason
        assert call(o, level=2).provider == "a" and a.calls[-1]["volatile"] == "VOL2"   # confiável vê tudo permitido


def test_no_trusted_available_gives_privacy_message_not_a_leak():
    with tempfile.TemporaryDirectory() as tmp:
        b = Prov("b", "external")
        o, _ = build(tmp, {"b": b})
        with pytest.raises(NoProvider) as e:
            call(o, level=2)
        assert "autorizado" in e.value.reason and not b.calls


def test_local_provider_may_see_everything_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        loc = Prov("loc", "local")
        o, _ = build(tmp, {"loc": loc})
        assert call(o, level=3).text == "ok-loc"


# ---------- disponibilidade ----------
def test_model_without_id_or_key_is_skipped_with_reason(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        from aasis.llm.openai_provider import OpenAICompatProvider
        monkeypatch.delenv("OR_MODEL", raising=False)
        monkeypatch.delenv("OR_KEY", raising=False)
        prov = OpenAICompatProvider("or", {"base_url": "http://x/v1", "key_env": "OR_KEY", "trust": "external"})
        o, _ = build(tmp, {"or": prov}, models={"m": {"provider": "or", "id_env": "OR_MODEL"}}, routes={"chat": ["m"]})
        ok, why = o.available("m")
        assert not ok and "OR_MODEL" in why
        monkeypatch.setenv("OR_MODEL", "vendor/model:free")
        assert "OR_KEY" in o.available("m")[1]
        monkeypatch.setenv("OR_KEY", "k")
        assert o.available("m") == (True, "") and o.model_id("m") == "vendor/model:free"


def test_tools_and_vision_requirements_and_unknown_alias():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Prov("a", "trusted"), Prov("b", "trusted")
        models = {"a": {"provider": "a", "id": "a1", "tools": False}, "b": {"provider": "b", "id": "b1", "tools": True,
                                                                           "vision": True}}
        o, _ = build(tmp, {"a": a, "b": b}, models=models, routes={"chat": ["ghost", "a", "b"]})
        assert call(o).provider == "b"                                 # a não tem ferramentas; ghost não existe
        assert call(o, use_tools=False).provider == "a"
        assert call(o, vision=True).provider == "b"


# ---------- falhas, disjuntor, limites ----------
def test_circuit_breaker_pauses_then_recovers_and_auth_pauses_long():
    with tempfile.TemporaryDirectory() as tmp:
        clock = Clock()
        a, b = Prov("a", "trusted"), Prov("b", "trusted")
        o, _ = build(tmp, {"a": a, "b": b}, clock=clock)
        a.script = [ProviderError("server")] * 3
        for _ in range(3):
            assert call(o).provider == "b"                             # cada falha cai para b
        n = len(a.calls)
        assert call(o).provider == "b" and len(a.calls) == n           # a está pausado: nem é tentado
        assert o.status()[0]["paused"] > 0
        clock.t += 301
        assert call(o).provider == "a"                                 # voltou após o período
        a.script = [ProviderError("auth")]
        assert call(o).provider == "b"
        clock.t += 301
        assert call(o).provider == "b"                                 # chave errada: pausa de 1 h


def test_rate_limit_per_minute_for_free_tiers():
    with tempfile.TemporaryDirectory() as tmp:
        clock = Clock()
        a, b = Prov("a", "trusted"), Prov("b", "trusted")
        o, _ = build(tmp, {"a": a, "b": b}, clock=clock, a={"rpm": 2})
        assert [call(o).provider for _ in range(3)] == ["a", "a", "b"]  # 3ª passa para b
        clock.t += 61
        assert call(o).provider == "a"


def test_daily_budget_per_provider_then_falls_back_or_reports():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Prov("a", "trusted", tokens=150), Prov("b", "trusted")
        o, mem = build(tmp, {"a": a, "b": b}, a={"daily_tokens": 100})
        assert call(o).provider == "a" and mem.usage_today("a") == 150
        assert call(o).provider == "b"                                 # orçamento de a esgotado
    with tempfile.TemporaryDirectory() as tmp2:                         # banco separado: sem outro provedor para assumir
        o2, _ = build(tmp2, {"a": Prov("a", "trusted", tokens=150)}, a={"daily_tokens": 100})
        call(o2)
        with pytest.raises(NoProvider) as e:
            call(o2)
        assert "orçamento" in e.value.reason


def test_web_search_disabled_propagates_to_caller():
    with tempfile.TemporaryDirectory() as tmp:
        a = Prov("a", "trusted", [ProviderError("web_search_disabled")])
        o, _ = build(tmp, {"a": a})
        with pytest.raises(ProviderError) as e:
            call(o)
        assert e.value.kind == "web_search_disabled"


def test_status_and_test_ping():
    with tempfile.TemporaryDirectory() as tmp:
        a = Prov("a", "trusted")
        o, mem = build(tmp, {"a": a})
        r = o.test("a")
        assert r["ok"] and mem.usage_today("a") == 10
        a.script = [ProviderError("rate_limit")]
        assert o.test("a") == {"ok": False, "error": "rate_limit", "detail": "rate_limit"}
        assert o.test("nada")["ok"] is False
        st = o.status()[0]
        assert st["alias"] == "a" and st["trust"] == "trusted" and st["last"]["error"] == "rate_limit"


# ---------- cérebro com troca de provedor no meio ----------
def make_brain(tmp, a, b):
    cfg = {"assistant": {"name": "A", "user_title": "senhor", "language": "pt-BR", "model": "m", "max_tokens": 50,
                         "max_tool_rounds": 4},
           "safety": {"workspace": tmp, "apps": {}, "confirm_tools": []}, "screen": {"enabled": False},
           "llm": {"providers": {"a": {"type": "openai"}, "b": {"type": "openai"}},
                   "models": {"a": {"provider": "a", "id": "a1", "tools": True}, "b": {"provider": "b", "id": "b1",
                                                                                         "tools": True}},
                   "routes": {"chat": ["a", "b"], "deep": ["a"]}}}
    mem = Memory(Path(tmp) / "t.db")
    t = Tools(cfg, mem, google=Google(cfg, Path(tmp)), screen=Screen(cfg))
    orch = Orchestrator(cfg, mem, providers={"a": a, "b": b})
    return Brain(cfg, t, mem, lambda _: True, orchestrator=orch)


def test_brain_switches_provider_mid_task_with_neutral_history():
    with tempfile.TemporaryDirectory() as tmp:
        a = Prov("a", "trusted", [LLMResponse(tool_calls=[ToolCall("c1", "world_time", {"timezone": "Europe/Lisbon"})],
                                              stop="tool_use"), ProviderError("server")])
        b = Prov("b", "external", [LLMResponse(text="Em Lisboa são tantas horas.")])
        brain = make_brain(tmp, a, b)
        assert brain.ask("que horas são em Lisboa?") == "Em Lisboa são tantas horas."
        seen = b.calls[0]["messages"]                                  # b recebeu a conversa inteira, em formato neutro
        assert [m["role"] for m in seen] == ["user", "assistant", "tool"] and seen[2]["results"][0]["id"] == "c1"
        public = {"get_time", "news", "fx_rates", "stock_quote", "bcb_indicators", "weather", "world_time", "web_fetch"}
        offered = set(b.calls[0]["tools"])
        assert offered and offered <= public                           # o externo só recebe ferramentas públicas


def test_brain_private_tool_result_locks_out_external_for_the_rest_of_the_window():
    with tempfile.TemporaryDirectory() as tmp:
        a = Prov("a", "trusted", [LLMResponse(tool_calls=[ToolCall("c1", "task_list", {})], stop="tool_use"),
                                  ProviderError("server")])
        b = Prov("b", "external", [LLMResponse(text="VAZOU")])
        brain = make_brain(tmp, a, b)
        out = brain.ask("o que tenho a fazer hoje?")                  # a lista de tarefas é dado privado
        assert "Não consegui falar com nenhum modelo" in out and not b.calls
        assert brain.history[-1]["role"] == "assistant"               # a ferramenta rodou: o turno fica registrado
        out = brain.ask("e como está o dólar?")                       # pergunta pública, mas o histórico é privado
        assert not b.calls                                             # continua sem enviar ao externo


def test_deep_mode_uses_deep_route_and_refusal_is_not_kept():
    with tempfile.TemporaryDirectory() as tmp:
        a = Prov("a", "trusted", [LLMResponse(text="profundo")])
        b = Prov("b", "external")
        brain = make_brain(tmp, a, b)
        assert brain.ask("analise isto", deep=True) == "profundo"
        a.script = [LLMResponse(text="", stop="refusal")]
        out = brain.ask("pedido recusado")
        assert "Não posso ajudar" in out and all(m.get("text") != "pedido recusado" for m in brain.history)
