import os
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
import yaml  # noqa: E402

from aasis.doctor import FAIL, OK, WARN, run_checks  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.meds import Meds  # noqa: E402
from test_telegram import FakeHttp  # noqa: E402

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
TOKEN = "123456789:AAH-secret_token_value_0123456789abcdef"
KEY = "sk-ant-SEGREDO-123"
CODE = "frase-longa-de-acesso-1234567"
HB = "https://hc-ping.com/uuid-secreto-9999"
GOOD = {"ANTHROPIC_API_KEY": KEY, "AASIS_HUD_CODE": CODE, "AASIS_DOMAIN": "aasis.exemplo.com",
        "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "777", "HEALTHCHECK_URL": HB}


def levels(res):
    return {n: l for l, n, _ in res}


def check(env, tmp, **kw):
    return run_checks(CFG, env=env, data=Path(tmp), serve=kw.pop("serve", True), **kw)


class Get(FakeHttp):
    def __init__(self, ok=True):
        super().__init__()
        self.urls, self.ok = [], ok

    def get(self, url, timeout=None):
        self.urls.append(url)
        if not self.ok:
            raise OSError("x")


def test_good_setup_has_no_failures_offline():
    with tempfile.TemporaryDirectory() as tmp:
        res = check(GOOD, tmp)
        assert not [r for r in res if r[0] == FAIL], res
        assert levels(res)["Telegram: online"] == WARN                    # offline: avisa que não testou


def test_missing_requirements_fail_in_cloud_mode():
    with tempfile.TemporaryDirectory() as tmp:
        lv = levels(check({}, tmp))
        assert lv["Provedores de IA com chave"] == FAIL and lv["Código de acesso (AASIS_HUD_CODE)"] == FAIL
        assert lv["Domínio permitido"] == FAIL and lv["Telegram"] == WARN
        assert levels(check({**GOOD, "AASIS_HUD_CODE": "curto"}, tmp))["Código de acesso (AASIS_HUD_CODE)"] == FAIL
        assert levels(check({**GOOD, "AASIS_HUD_CODE": "curto"}, tmp, serve=False)).get("Código de acesso (AASIS_HUD_CODE)") is None


def test_only_external_provider_cannot_serve_sensitive_data():
    with tempfile.TemporaryDirectory() as tmp:
        res = check({**GOOD, "ANTHROPIC_API_KEY": "", "DEEPSEEK_API_KEY": "k"}, tmp)
        assert levels(res)["Provedor para dados sensíveis"] == FAIL
        assert levels(check(GOOD, tmp))["Provedor para dados sensíveis"] == OK


def test_telegram_config_validation():
    with tempfile.TemporaryDirectory() as tmp:
        only_token = {k: v for k, v in GOOD.items() if k != "TELEGRAM_CHAT_ID"}
        assert levels(check(only_token, tmp))["Telegram"] == FAIL
        only_chat = {k: v for k, v in GOOD.items() if k != "TELEGRAM_BOT_TOKEN"}
        assert levels(check(only_chat, tmp))["Telegram"] == FAIL
        assert levels(check({**GOOD, "TELEGRAM_CHAT_ID": "abc"}, tmp))["Telegram"] == FAIL
        assert levels(check({**GOOD, "TELEGRAM_BOT_TOKEN": "curto:x"}, tmp))["Telegram: formato do token"] == WARN
        assert levels(check(GOOD, tmp))["Telegram: formato do token"] == OK


def test_online_telegram_sends_one_test_message_and_never_leaks():
    with tempfile.TemporaryDirectory() as tmp:
        http = Get()
        http.reply = None
        res = check(GOOD, tmp, online=True, http=http)
        lv = levels(res)
        assert lv["Telegram: token aceito"] == OK and lv["Telegram: mensagem entregue ao seu chat"] == OK
        assert len(http.sent()) == 1 and http.sent()[0]["chat_id"] == 777
        assert http.urls == [HB]                                             # e o monitor recebeu 1 ping
        assert lv["Monitor externo: ping"] == OK


def test_online_failures_are_reported_with_hint_and_no_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        http = Get(ok=False)
        http.reply = {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        res = check(GOOD, tmp, online=True, http=http)
        lv = levels(res)
        assert lv["Telegram: online"] == FAIL and lv["Monitor externo: ping"] == FAIL
        text = " ".join(f"{n} {d}" for _, n, d in res)
        assert "/start" in text                                              # dica para o erro mais comum
        for secret in (TOKEN, KEY, CODE, HB, "uuid-secreto"):
            assert secret not in text, secret


def test_no_secret_ever_printed_even_when_everything_is_ok():
    with tempfile.TemporaryDirectory() as tmp:
        http = Get()
        text = " ".join(f"{n} {d}" for _, n, d in check(GOOD, tmp, online=True, http=http))
        for secret in (TOKEN, KEY, CODE, HB):
            assert secret not in text


def test_monitor_must_be_https_and_is_recommended():
    with tempfile.TemporaryDirectory() as tmp:
        assert levels(check({**GOOD, "HEALTHCHECK_URL": "http://x/y"}, tmp))["Monitor externo (HEALTHCHECK_URL)"] == FAIL
        no_hb = {k: v for k, v in GOOD.items() if k != "HEALTHCHECK_URL"}
        assert levels(check(no_hb, tmp))["Monitor externo (HEALTHCHECK_URL)"] == WARN


def test_corrupt_database_is_a_failure():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "aasis.db").write_bytes(b"isto nao e um banco sqlite " * 50)       # nem abre
        assert levels(check(GOOD, tmp))["Banco de dados"] == FAIL


def test_database_that_opens_but_is_inconsistent_is_a_failure():
    """Corrupção que o SQLite só revela no integrity_check (índice sem a linha), sem lançar exceção ao abrir."""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "aasis.db"
        c = sqlite3.connect(p)
        c.execute("create table t(a integer primary key, b text)")
        c.execute("create index ib on t(b)")
        c.executemany("insert into t(b) values (?)", [(f"valor-{i}-" * 5,) for i in range(2000)])
        c.commit()
        c.close()
        raw = bytearray(p.read_bytes())
        idx = 2 * 4096 + 4056                                   # página 3 do arquivo (índice)
        raw[idx:idx + 8] = b"\x00\xff\x00\xff\x00\xff\x00\xff"
        p.write_bytes(raw)
        c = sqlite3.connect(p)
        assert c.execute("PRAGMA integrity_check").fetchone()[0] != "ok"                 # a premissa do teste
        c.close()
        res = check(GOOD, tmp)
        assert levels(res)["Banco de dados"] == FAIL and "integrity_check" in dict((n, d) for _, n, d in res)["Banco de dados"]


def test_meds_without_telegram_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Memory(Path(tmp) / "aasis.db", tz=ZoneInfo("America/Sao_Paulo"))
        Meds(mem, ZoneInfo("America/Sao_Paulo")).add("Losartana", "50 mg", "08:00")
        mem.db.close()
        no_tg = {k: v for k, v in GOOD.items() if not k.startswith("TELEGRAM")}
        assert levels(check(no_tg, tmp))["Remédios"] == WARN
        assert levels(check(GOOD, tmp))["Remédios"] == OK


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")
def test_world_readable_env_file_warns():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / ".env"
        f.write_text("X=1")
        f.chmod(0o644)
        assert levels(check(GOOD, tmp, env_files=[f]))["Permissão de .env"] == WARN
        f.chmod(0o600)
        assert "Permissão de .env" not in levels(check(GOOD, tmp, env_files=[f]))


def test_unwritable_data_dir_fails():
    assert levels(run_checks(CFG, env=GOOD, data=Path("/proc/nao-existe/x"), serve=False))["Pasta de dados gravável"] == FAIL
