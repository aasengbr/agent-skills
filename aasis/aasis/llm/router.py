"""Orquestrador de IAs: escolhe o modelo por tarefa, respeita privacidade, falha para o próximo e limita gasto."""
import os
import time
from collections import deque

from .anthropic_provider import AnthropicProvider
from .base import ProviderError
from .openai_provider import OpenAICompatProvider
from .privacy import DEFAULT_MAX_LEVEL

TASKS = ("chat", "deep", "memory", "quick")
BREAKER_FAILS, BREAKER_WINDOW, BREAKER_COOLDOWN = 3, 300, 300


class NoProvider(Exception):
    """Nenhum modelo elegível. `reason` resume por quê (privacidade, orçamento, indisponível...)."""

    def __init__(self, reason: str, detail: list):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def default_llm_config(cfg: dict) -> dict:
    """Sem seção `llm`: só Anthropic, com o modelo de assistant.model (comportamento anterior)."""
    return {"providers": {"anthropic": {"type": "anthropic", "trust": "trusted"}},
            "models": {"default": {"provider": "anthropic", "id": cfg["assistant"]["model"], "tools": True,
                                   "vision": True}},
            "routes": {t: ["default"] for t in TASKS}}


def build_providers(llm: dict, http=None) -> dict:
    out = {}
    for name, p in llm["providers"].items():
        out[name] = AnthropicProvider(name, p) if p.get("type", "openai") == "anthropic" else \
            OpenAICompatProvider(name, p, http=http)
    return out


class Orchestrator:
    def __init__(self, cfg: dict, memory, providers: dict | None = None, http=None, clock=time.time, emit=None):
        self.cfg, self.memory, self.clock = cfg, memory, clock
        self.emit = emit or (lambda *a, **k: None)
        self.llm = cfg.get("llm") or default_llm_config(cfg)
        self.providers = providers if providers is not None else build_providers(self.llm, http)
        self.models = self.llm.get("models", {})
        self.routes = self.llm.get("routes", {})
        self.max_level = {**DEFAULT_MAX_LEVEL, **self.llm.get("privacy", {}).get("max_level", {})}
        self._fails: dict[str, list] = {}
        self._open_until: dict[str, float] = {}
        self._calls: dict[str, deque] = {}
        self._last: dict[str, dict] = {}

    # ---------- disponibilidade ----------
    def model_id(self, alias: str) -> str:
        m = self.models[alias]
        return m.get("id") or os.getenv(m.get("id_env", ""), "")

    def _provider_cfg(self, name: str) -> dict:
        return self.llm["providers"].get(name, {})

    def available(self, alias: str) -> tuple:
        m = self.models.get(alias)
        if not m or m.get("provider") not in self.providers:
            return False, "provedor desconhecido"
        pc = self._provider_cfg(m["provider"])
        if pc.get("enabled", True) is False:
            return False, "desativado"
        if not self.model_id(alias):
            return False, f"defina o ID do modelo ({m.get('id_env', 'id')})"
        prov = self.providers[m["provider"]]
        if prov.kind == "openai":
            if not prov.base:
                return False, "sem endereço (base_url)"
            if prov.key_env and not prov.key:
                return False, f"sem chave ({prov.key_env})"
        return True, ""

    def trust_of(self, alias: str) -> str:
        return self.providers[self.models[alias]["provider"]].trust

    def _budget_cap(self, provider: str) -> int:
        pc = self._provider_cfg(provider)
        if "daily_tokens" in pc:
            return int(pc["daily_tokens"])
        return int(self.cfg.get("budget", {}).get("daily_tokens", 0)) if pc.get("type") == "anthropic" else 0

    def _skip_reason(self, alias: str, level: int, use_tools: bool, vision: bool) -> str:
        ok, why = self.available(alias)
        if not ok:
            return why
        m, now = self.models[alias], self.clock()
        if self.max_level.get(self.trust_of(alias), 0) < level:
            return "privacidade: este provedor não pode ver dados desse nível"
        if use_tools and not m.get("tools", True):
            return "sem suporte a ferramentas"
        if vision and not m.get("vision"):
            return "sem suporte a imagens"
        if self._open_until.get(alias, 0) > now:
            return "pausado após falhas seguidas"
        prov = m["provider"]
        rpm = int(self._provider_cfg(prov).get("rpm", 0))
        if rpm:
            q = self._calls.setdefault(prov, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= rpm:
                return f"limite de {rpm} requisições/min atingido"
        cap = self._budget_cap(prov)
        if cap and self.memory.usage_today(prov) >= cap:
            return "orçamento diário do provedor esgotado"
        return ""

    def candidates(self, task: str, level: int = 0, use_tools: bool = True, vision: bool = False):
        eligible, skipped = [], []
        for alias in self.routes.get(task) or self.routes.get("chat", []):
            why = self._skip_reason(alias, level, use_tools, vision) if alias in self.models else "modelo desconhecido"
            (skipped if why else eligible).append((alias, why) if why else alias)
        return eligible, skipped

    # ---------- chamada ----------
    def complete(self, task: str, stable: str, volatile_for, messages: list, tools_for, level: int,
                 max_tokens: int, use_tools: bool = True, vision: bool = False):
        """volatile_for(max_level) -> texto; tools_for(max_level) -> ferramentas. Ambos filtrados pela confiança
        do provedor escolhido, para que um modelo externo nunca receba memória privada nem ferramentas privadas."""
        eligible, skipped = self.candidates(task, level, use_tools, vision)
        errors = []
        for alias in eligible:
            m = self.models[alias]
            prov = self.providers[m["provider"]]
            maxl = self.max_level.get(prov.trust, 0)
            spec = {**m, "id": self.model_id(alias)}
            t0 = self.clock()
            try:
                self._calls.setdefault(m["provider"], deque()).append(t0)
                resp = prov.complete(spec, stable, volatile_for(maxl), messages, tools_for(maxl) if use_tools else [],
                                     max_tokens)
            except ProviderError as e:
                if e.kind == "web_search_disabled":
                    raise  # o chamador desliga a busca e repete
                self._fail(alias, e)
                errors.append((alias, e.kind))
                continue
            self._ok(alias, (self.clock() - t0) * 1000)
            self.memory.add_usage(resp.usage.total, m["provider"])
            self.memory.add_searches(resp.usage.searches)
            resp.provider = m["provider"]
            self.emit("model", alias=alias, provider=m["provider"], id=spec["id"])
            return resp
        raise NoProvider(self._summary(eligible, skipped, errors), skipped + errors)

    @staticmethod
    def _summary(eligible, skipped, errors) -> str:
        if errors:
            return "todos os modelos disponíveis falharam: " + ", ".join(f"{a} ({k})" for a, k in errors)
        if any("privacidade" in w for _, w in skipped):
            return "nenhum modelo autorizado para dados desse nível está disponível"
        if any("orçamento" in w for _, w in skipped):
            return "orçamento diário esgotado"
        return "nenhum modelo disponível: " + "; ".join(f"{a}: {w}" for a, w in skipped) if skipped else "sem modelos"

    # ---------- saúde dos provedores ----------
    def _fail(self, alias: str, err: ProviderError) -> None:
        now = self.clock()
        fails = [t for t in self._fails.get(alias, []) if now - t < BREAKER_WINDOW] + [now]
        self._fails[alias] = fails
        self._last[alias] = {"error": err.kind, "at": now}
        if err.kind == "auth":  # chave errada não melhora sozinha
            self._open_until[alias] = now + 3600
        elif len(fails) >= BREAKER_FAILS:
            self._open_until[alias] = now + BREAKER_COOLDOWN

    def _ok(self, alias: str, latency_ms: float) -> None:
        self._fails.pop(alias, None)
        self._open_until.pop(alias, None)
        self._last[alias] = {"latency_ms": round(latency_ms), "at": self.clock()}

    # ---------- diagnóstico (HUD) ----------
    def status(self) -> list:
        now, out = self.clock(), []
        for alias, m in self.models.items():
            ok, why = self.available(alias)
            prov = m.get("provider")
            out.append({"alias": alias, "provider": prov, "id": self.model_id(alias) if prov in self.providers else "",
                        "trust": self.trust_of(alias) if prov in self.providers else "",
                        "available": ok, "reason": why,
                        "paused": max(0, round(self._open_until.get(alias, 0) - now)),
                        "last": self._last.get(alias, {}), "tokens_today": self.memory.usage_today(prov)})
        return out

    def test(self, alias: str) -> dict:
        ok, why = self.available(alias)
        if not ok:
            return {"ok": False, "error": why}
        m = self.models[alias]
        prov, spec, t0 = self.providers[m["provider"]], {**m, "id": self.model_id(alias)}, self.clock()
        try:
            from .base import user_msg
            resp = prov.complete({**spec, "tools": False}, "Responda apenas: ok", "", [user_msg("ping")], [], 16)
        except ProviderError as e:
            self._fail(alias, e)
            return {"ok": False, "error": e.kind, "detail": str(e)[:160]}
        self._ok(alias, (self.clock() - t0) * 1000)
        self.memory.add_usage(resp.usage.total, m["provider"])
        return {"ok": True, "latency_ms": round((self.clock() - t0) * 1000), "text": resp.text[:40]}

    def list_models(self, provider: str) -> list:
        return self.providers[provider].list_models()
