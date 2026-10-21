import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aasis.google_tools import Google  # noqa: E402
from aasis.home import Home  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.tools import Tools  # noqa: E402
from aasis.vision import Screen  # noqa: E402

STATES = [
    {"entity_id": "light.sala", "state": "off", "attributes": {"friendly_name": "Luz da sala"}},
    {"entity_id": "climate.quarto", "state": "cool", "attributes": {"friendly_name": "Ar"}},
    {"entity_id": "lock.porta", "state": "locked", "attributes": {"friendly_name": "Porta"}},
]


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Http:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return Resp(STATES if url.endswith("/states") else [])


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "tok")
    cfg = {"safety": {"workspace": tempfile.mkdtemp(), "apps": {}, "confirm_tools": []},
           "screen": {"enabled": False},
           "home_assistant": {"url": "http://ha:8123", "auto_domains": ["light"], "confirm_domains": ["climate"]}}
    http = Http()
    t = Tools(cfg, Memory(Path(cfg["safety"]["workspace"]) / "t.db"), google=Google(cfg, Path("/nonexistent")),
              screen=Screen(cfg), home=Home(cfg, http=http))
    return t, http


def test_list_hides_blocked_domains(env):
    t, http = env
    out = t.run("ha_list", {}, lambda _: True)
    assert "light.sala" in out and "climate.quarto" in out and "lock.porta" not in out
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer tok"


def test_blocked_and_mismatch_and_invalid(env):
    t, http = env
    assert "bloqueado" in t.run("ha_call", {"domain": "lock", "service": "unlock", "entity_id": "lock.porta"}, lambda _: True)
    assert "não pertence" in t.run("ha_call", {"domain": "light", "service": "turn_on", "entity_id": "climate.quarto"}, lambda _: True)
    assert "inválido" in t.run("ha_call", {"domain": "light", "service": "../x", "entity_id": "light.sala"}, lambda _: True)
    assert "não permitido" in t.run("ha_state", {"entity_id": "lock.porta"}, lambda _: True)
    assert not any(c[0] == "POST" for c in http.calls)


def test_auto_runs_without_confirmation_and_strips_targets(env):
    t, http = env

    def never(_):
        raise AssertionError("não deveria pedir confirmação")

    out = t.run("ha_call", {"domain": "light", "service": "turn_on", "entity_id": "light.sala",
                            "data": {"brightness_pct": 40, "entity_id": "all", "area_id": "casa"}}, never)
    assert "Executado" in out
    method, url, kw = http.calls[-1]
    assert method == "POST" and url.endswith("/services/light/turn_on")
    assert kw["json"] == {"brightness_pct": 40, "entity_id": "light.sala"}


def test_confirm_tier_requires_confirmation(env):
    t, http = env
    args = {"domain": "climate", "service": "set_temperature", "entity_id": "climate.quarto", "data": {"temperature": 22}}
    assert "negada" in t.run("ha_call", args, lambda _: False)
    assert not any(c[0] == "POST" for c in http.calls)
    assert "Executado" in t.run("ha_call", args, lambda _: True)


def test_hidden_without_token(monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    cfg = {"safety": {"workspace": tempfile.mkdtemp(), "apps": {}, "confirm_tools": []},
           "screen": {"enabled": False}, "home_assistant": {"url": "http://ha:8123"}}
    t = Tools(cfg, Memory(Path(cfg["safety"]["workspace"]) / "t.db"), google=Google(cfg, Path("/nonexistent")),
              screen=Screen(cfg), home=Home(cfg))
    assert "ha_call" not in {s["name"] for s in t.schemas()}
