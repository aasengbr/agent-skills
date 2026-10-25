import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
import yaml  # noqa: E402

from aasis import wizard  # noqa: E402
from test_telegram import FakeHttp, Resp, msg  # noqa: E402

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
KEY = "sk-ant-SEGREDO-da-chave-0123456789"
TOKEN = "123456789:AAH-token_secreto_0123456789abcdef"


class TgHttp(FakeHttp):
    """API do Telegram falsa que só aceita TOKEN (qualquer outro recebe 401) e pode revelar a mensagem aos poucos."""

    def __init__(self):
        super().__init__()
        self.later = []         # atualizações que "chegam" depois da 1ª leitura vazia
        self.reads = 0

    def post(self, url, json=None, timeout=None):
        if f"/bot{TOKEN}/" not in url:
            return Resp({"ok": False, "error_code": 401, "description": "Unauthorized"})
        if url.endswith("/getUpdates"):
            # como a API real: as mensagens ficam disponíveis até o offset avançar (o assistente nunca o avança)
            self.reads += 1
            if self.reads >= 2 and self.later:
                self.updates, self.later = self.updates + self.later, []
            self.calls.append(("getUpdates", json))
            return Resp({"ok": True, "result": [u for u in self.updates if u["update_id"] >= (json or {}).get("offset", 0)]})
        return super().post(url, json=json, timeout=timeout)


class Script:
    """Respostas combinadas: `answers` para ask() e `secrets` para ask_secret(); registra tudo que foi mostrado."""

    def __init__(self, answers=(), secrets=()):
        self.answers, self.secrets, self.shown = list(answers), list(secrets), []

    def ask(self, prompt=""):
        self.shown.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def ask_secret(self, prompt=""):
        self.shown.append(prompt)
        assert self.secrets, f"pediu um segredo inesperado: {prompt}"
        return self.secrets.pop(0)

    def out(self, text=""):
        self.shown.append(str(text))

    def text(self):
        return "\n".join(self.shown)


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    import aasis.config as config
    monkeypatch.setattr(config, "load_config", lambda *a, **k: CFG)       # sem criar pastas no HOME
    for k in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "HEALTHCHECK_URL", "AASIS_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)


def run(tmp, script, http=None):
    return wizard.main(ask=script.ask, ask_secret=script.ask_secret, http=http, env_path=Path(tmp) / ".env", out=script.out)


def private_update(uid=777, name="Ana", update_id=1):
    u = msg("oi", uid=uid, update_id=update_id)
    u["message"]["chat"].update({"first_name": name, "username": ""})
    return u


def test_fresh_setup_writes_clean_env_and_never_shows_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        http = TgHttp()
        http.updates = [private_update()]
        s = Script(secrets=[KEY, TOKEN])
        assert run(tmp, s, http) == 0
        env = (Path(tmp) / ".env").read_text(encoding="utf-8")
        assert f"\nANTHROPIC_API_KEY={KEY}\n" in "\n" + env and f"\nTELEGRAM_BOT_TOKEN={TOKEN}\n" in "\n" + env
        assert "\nTELEGRAM_CHAT_ID=777\n" in "\n" + env
        assert not any(l.startswith(("ANTHROPIC_API_KEY=", "TELEGRAM_")) and (" " in l or "#" in l) for l in env.splitlines())
        assert "# AASIS_MODEL" in env                                         # o resto do modelo foi preservado
        for secret in (KEY, TOKEN):
            assert secret not in s.text()                                     # nada de segredo na tela
        assert http.sent()[-1]["text"].startswith("✅ Teste do AASIS doctor")   # o diagnóstico mandou a mensagem de teste
        assert "python -m aasis.main --text --hud" in s.text()


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")
def test_env_file_is_private():
    with tempfile.TemporaryDirectory() as tmp:
        run(tmp, Script(answers=["n"], secrets=[KEY]), TgHttp())
        assert oct((Path(tmp) / ".env").stat().st_mode & 0o777) == "0o600"


def test_rerun_keeps_everything_and_changes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        http = TgHttp()
        http.updates = [private_update()]
        run(tmp, Script(secrets=[KEY, TOKEN]), http)
        before = (Path(tmp) / ".env").read_bytes()
        s = Script(answers=["", ""])                                          # "Trocar?" -> Enter (não) nas duas
        assert run(tmp, s, TgHttp()) == 0
        assert (Path(tmp) / ".env").read_bytes() == before
        assert "Mantida" in s.text() and "Token mantido" in s.text()
        assert not s.secrets and "ask_secret" not in s.text()


def test_replace_existing_key_when_asked():
    with tempfile.TemporaryDirectory() as tmp:
        run(tmp, Script(answers=["n"], secrets=[KEY]), TgHttp())
        new = "sk-ant-NOVA-chave-9876543210"
        assert run(tmp, Script(answers=["s", "n"], secrets=[new]), TgHttp()) == 0
        env = (Path(tmp) / ".env").read_text(encoding="utf-8")
        assert f"ANTHROPIC_API_KEY={new}" in env and KEY not in env and env.count("ANTHROPIC_API_KEY=") == 1


def test_invalid_values_are_rejected_and_asked_again():
    with tempfile.TemporaryDirectory() as tmp:
        s = Script(answers=["n"], secrets=["sk-ant-tem espaço", "sk-errada-123", KEY])
        run(tmp, s, TgHttp())
        text = s.text()
        assert "sem espaços, aspas nem #" in text and "começa com 'sk-ant-'" in text
        assert f"ANTHROPIC_API_KEY={KEY}" in (Path(tmp) / ".env").read_text(encoding="utf-8")
        assert "tem espaço" not in text and "sk-errada" not in text           # nem o valor recusado é repetido


def test_quotes_and_hash_and_newlines_cannot_reach_the_env_file():
    for bad in ('"sk-ant-aspas"', "'sk-ant-x'", "sk-ant-a#b", "sk-ant-a\nOUTRA=1", "sk-ant-a=b"):
        with tempfile.TemporaryDirectory() as tmp:
            run(tmp, Script(answers=["n"], secrets=[bad, bad, bad]), TgHttp())
            env = (Path(tmp) / ".env").read_text(encoding="utf-8")
            assert "OUTRA=1" not in env and "aspas" not in env
            assert not wizard.is_set(wizard.read_env(Path(tmp) / ".env").get("ANTHROPIC_API_KEY"))


def test_rejected_telegram_token_is_not_saved_nor_echoed():
    with tempfile.TemporaryDirectory() as tmp:
        bad = "111111111:AAH-token_errado_0123456789abcdef"
        http = TgHttp()
        http.updates = [private_update()]
        s = Script(secrets=[KEY, bad, TOKEN])
        assert run(tmp, s, http) == 0
        assert "não aceitou este token" in s.text() and "401" in s.text() and bad not in s.text()
        env = (Path(tmp) / ".env").read_text(encoding="utf-8")
        assert bad not in env and TOKEN in env


def test_skipping_telegram_is_fine():
    with tempfile.TemporaryDirectory() as tmp:
        s = Script(answers=["n"], secrets=[KEY])
        assert run(tmp, s, TgHttp()) == 0
        env = wizard.read_env(Path(tmp) / ".env")
        assert not wizard.is_set(env.get("TELEGRAM_BOT_TOKEN")) and "Telegram" in s.text()


def test_waits_for_the_first_message_then_finds_the_chat():
    with tempfile.TemporaryDirectory() as tmp:
        http = TgHttp()
        http.later = [private_update(555, "Bia")]                              # a mensagem só chega depois da 1ª tentativa
        s = Script(secrets=[KEY, TOKEN])
        assert run(tmp, s, http) == 0
        assert "Ainda não vi nenhuma mensagem" in s.text() and "Iniciar" in s.text()
        assert wizard.read_env(Path(tmp) / ".env")["TELEGRAM_CHAT_ID"] == "555"


def test_user_can_skip_chat_discovery():
    with tempfile.TemporaryDirectory() as tmp:
        http = TgHttp()
        s = Script(answers=["", "p"], secrets=[KEY, TOKEN])                    # configurar Telegram? Enter; esperar? 'p'
        run(tmp, s, http)
        assert http.reads == 1                                                 # pulou na hora: não ficou tentando de novo
        assert not wizard.is_set(wizard.read_env(Path(tmp) / ".env").get("TELEGRAM_CHAT_ID"))


def test_several_chats_ask_which_one_is_you_and_reject_bad_numbers():
    with tempfile.TemporaryDirectory() as tmp:
        http = TgHttp()
        http.updates = [private_update(111, "Carlos", 1), private_update(222, "Dora", 2)]
        s = Script(answers=["", "9", "2"], secrets=[KEY, TOKEN])               # Telegram? Enter; número inválido; número 2
        run(tmp, s, http)
        assert "Número inválido" in s.text()
        assert wizard.read_env(Path(tmp) / ".env")["TELEGRAM_CHAT_ID"] == "222"


def test_write_env_updates_commented_and_missing_keys_without_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / ".env"
        p.write_text("# comentário\nA=1\n# B=\nC=3\n", encoding="utf-8")
        wizard.write_env(p, {"B": "2", "A": "9", "D": "4"})
        assert p.read_text(encoding="utf-8").splitlines() == ["# comentário", "A=9", "B=2", "C=3", "D=4"]
        wizard.write_env(p, {"A": "9"})
        assert p.read_text(encoding="utf-8").count("A=") == 1


def test_placeholder_from_env_example_counts_as_not_set():
    assert not wizard.is_set("sk-ant-...") and not wizard.is_set("") and not wizard.is_set(None)
    assert wizard.is_set("sk-ant-real-1234")
