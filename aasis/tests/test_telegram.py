import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import requests  # noqa: E402

from aasis import telegram as tgm  # noqa: E402
from aasis.telegram import Telegram, TelegramError  # noqa: E402

TOKEN = "123456:SECRET-TOKEN-VALUE"
OWNER = 777


class Resp:
    def __init__(self, body):
        self._b, self.status_code = body, 200

    def json(self):
        return self._b


class FakeHttp:
    """API do Telegram em memória: registra chamadas e entrega as atualizações enfileiradas."""

    def __init__(self):
        self.calls, self.updates, self.fail, self.next_id = [], [], None, 100
        self.reply = None

    def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))
        if self.fail:
            raise requests.ConnectionError(f"falha em {url}")  # a mensagem real do requests traz a URL com o token
        if self.reply is not None:
            return Resp(self.reply)
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return Resp({"ok": True, "result": ups})
        if method == "sendMessage":
            self.next_id += 1
            return Resp({"ok": True, "result": {"message_id": self.next_id}})
        return Resp({"ok": True, "result": True})

    def sent(self, method="sendMessage"):
        return [p for m, p in self.calls if m == method]


def msg(text, uid=OWNER, chat_type="private", chat_id=None, update_id=1):
    return {"update_id": update_id, "message": {"message_id": 1, "text": text, "from": {"id": uid},
                                               "chat": {"id": chat_id if chat_id is not None else uid, "type": chat_type}}}


def cb(data, uid=OWNER, update_id=1, text="💊 Hora", mid=55):
    return {"update_id": update_id, "callback_query": {"id": "cq1", "from": {"id": uid}, "data": data,
                                                       "message": {"message_id": mid, "text": text, "chat": {"id": OWNER}}}}


def make(http=None, **kw):
    return Telegram(TOKEN, OWNER, http=http or FakeHttp(), **kw)


def wait_for(cond, t=2.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_requires_token_and_numeric_chat():
    for token, chat in (("", 1), (TOKEN, "abc"), (TOKEN, "")):
        with pytest.raises(ValueError):
            Telegram(token, chat)
    assert Telegram(TOKEN, "-100123").chat_id == -100123


def test_send_with_buttons_and_chunking():
    http = FakeHttp()
    tg = make(http)
    tg.send("oi", [[("✅ Tomei", "d:t:1")], [("Adiar", "d:z:1"), ("Pular", "d:s:1")]])
    p = http.sent()[0]
    assert p["chat_id"] == OWNER and p["text"] == "oi"
    assert p["reply_markup"]["inline_keyboard"][1][1] == {"text": "Pular", "callback_data": "d:s:1"}
    http.calls.clear()
    tg.send("x" * 9000, [[("OK", "r:ack:1")]])
    sent = http.sent()
    assert len(sent) == 3 and all(len(s["text"]) <= 4000 for s in sent)
    assert "reply_markup" not in sent[0] and "reply_markup" in sent[2]    # botões só na última parte


def test_errors_never_leak_the_token():
    http = FakeHttp()
    tg = make(http)
    http.fail = True
    with pytest.raises(TelegramError) as e:
        tg.send("oi")
    assert TOKEN not in str(e.value) and TOKEN not in repr(e.value) and "ConnectionError" in str(e.value)
    http.fail, http.reply = None, {"ok": False, "error_code": 401, "description": "Unauthorized"}
    with pytest.raises(TelegramError) as e:
        tg.send("oi")
    assert "401" in str(e.value) and TOKEN not in str(e.value)
    with pytest.raises(TelegramError) as e:
        tg.run_once(timeout=0)
    assert TOKEN not in str(e.value)


def test_strangers_and_groups_are_ignored_completely():
    http, got = FakeHttp(), []
    tg = make(http, on_text=got.append, on_action=got.append)
    http.updates = [msg("oi", uid=999, update_id=1),                                   # outro usuário
                    msg("oi", uid=OWNER, chat_type="group", chat_id=-5, update_id=2),  # grupo, mesmo dono
                    cb("d:t:1", uid=999, update_id=3),                                 # botão de outro usuário
                    msg("apague tudo", uid=999, update_id=4)]
    assert tg.run_once(0) == 4
    time.sleep(0.1)
    assert got == [] and http.sent() == [] and http.sent("editMessageText") == []


def test_group_chat_is_ignored_even_with_owner_ids():
    """Defesa em profundidade: mesmo com id e remetente do dono, só conversa privada vale."""
    http, got = FakeHttp(), []
    tg = make(http, on_text=got.append, on_action=got.append)
    http.updates = [msg("oi", uid=OWNER, chat_type="group", chat_id=OWNER, update_id=1),
                    msg("oi", uid=OWNER, chat_type="supergroup", chat_id=OWNER, update_id=2)]
    tg.run_once(0)
    time.sleep(0.1)
    assert got == [] and http.sent() == []


def test_double_tap_cannot_flip_a_decided_confirmation():
    """Sim seguido de Não (toque duplo) antes de quem espera ler a resposta: vale a primeira."""
    http = FakeHttp()
    tg = make(http)
    slot = {"event": threading.Event(), "answer": False}
    tg._pending["tok"] = slot
    http.updates = [cb("c:y:tok", update_id=1), cb("c:n:tok", update_id=2)]
    tg.run_once(0)
    assert slot["event"].is_set() and slot["answer"] is True
    http.updates = [cb("c:n:tok", update_id=3)]
    tg.run_once(0)
    assert slot["answer"] is True


def test_unknown_start_gets_chat_id_once_per_hour():
    http = FakeHttp()
    tg = make(http)
    http.updates = [msg("/start", uid=999, update_id=1), msg("/start", uid=999, update_id=2)]
    tg.run_once(0)
    sent = http.sent()
    assert len(sent) == 1 and sent[0]["chat_id"] == 999 and "999" in sent[0]["text"]
    assert "TELEGRAM_CHAT_ID" in sent[0]["text"] and TOKEN not in sent[0]["text"]


def test_owner_text_goes_to_brain_and_reply_returns():
    http, seen = FakeHttp(), []
    tg = make(http, on_text=lambda t: seen.append(t) or "resposta")
    http.updates = [msg("  que horas são?  ")]
    tg.run_once(0)
    assert wait_for(lambda: http.sent())
    assert seen == ["que horas são?"] and http.sent()[0]["text"] == "resposta"


def test_long_input_is_truncated_and_non_text_gets_polite_reply():
    http, seen = FakeHttp(), []
    tg = make(http, on_text=lambda t: seen.append(len(t)) or None)
    http.updates = [msg("a" * 5000, update_id=1)]
    tg.run_once(0)
    assert wait_for(lambda: seen) and seen == [tgm.MAX_IN]
    http.updates = [{"update_id": 2, "message": {"from": {"id": OWNER}, "chat": {"id": OWNER, "type": "private"},
                                                 "photo": [{}]}}]
    tg.run_once(0)
    assert "texto" in http.sent()[-1]["text"]


def test_on_text_failure_is_reported_not_swallowed():
    http = FakeHttp()

    def boom(_):
        raise RuntimeError("segredo interno")

    tg = make(http, on_text=boom)
    http.updates = [msg("oi")]
    tg.run_once(0)
    assert wait_for(lambda: http.sent())
    assert "RuntimeError" in http.sent()[0]["text"] and "segredo" not in http.sent()[0]["text"]


def test_button_press_runs_action_answers_and_edits_message():
    http, acts = FakeHttp(), []
    tg = make(http, on_action=lambda d: acts.append(d) or "Losartana: tomada às 08:03.")
    http.updates = [cb("d:t:5")]
    tg.run_once(0)
    assert acts == ["d:t:5"]
    assert http.sent("answerCallbackQuery")[0]["callback_query_id"] == "cq1"      # tira o "carregando" do botão
    e = http.sent("editMessageText")[0]
    assert e["message_id"] == 55 and "tomada às 08:03" in e["text"] and "reply_markup" not in e   # sem botões


def _press(tg, http, data_prefix, delay=0.0):
    """Espera o pedido de confirmação aparecer e responde com o código dele."""
    assert wait_for(lambda: http.sent())
    markup = http.sent()[-1]["reply_markup"]["inline_keyboard"][0]
    data = next(b["callback_data"] for b in markup if b["callback_data"].startswith(data_prefix))
    http.updates = [cb(data, mid=http.next_id)]
    tg.run_once(0)
    return data


@pytest.mark.parametrize("button,expected", [("c:y", True), ("c:n", False)])
def test_confirm_yes_no(button, expected):
    http = FakeHttp()
    tg = make(http)
    out = []
    t = threading.Thread(target=lambda: out.append(tg.confirm("write_file x", timeout=5)))
    t.start()
    _press(tg, http, button)
    t.join(3)
    assert out == [expected]
    assert tg._pending == {}


def test_confirm_times_out_as_no_and_removes_buttons():
    http = FakeHttp()
    tg = make(http)
    assert tg.confirm("apagar tudo", timeout=0.1) is False
    e = http.sent("editMessageText")[0]
    assert "Expirou" in e["text"] and "apagar tudo" in e["text"] and tg._pending == {}


def test_confirm_code_is_single_use_and_forged_codes_do_nothing():
    http = FakeHttp()
    tg = make(http)
    out = []
    t = threading.Thread(target=lambda: out.append(tg.confirm("acao", timeout=5)))
    t.start()
    assert wait_for(lambda: http.sent())
    http.updates = [cb("c:y:forjado", update_id=1)]
    tg.run_once(0)
    assert t.is_alive() and out == []                                   # código errado não confirma
    data = _press(tg, http, "c:n")                                      # nega de verdade
    t.join(3)
    assert out == [False]
    http.updates = [cb(data.replace("c:n", "c:y"), update_id=9)]        # reaproveitar o código depois: nada acontece
    tg.run_once(0)
    assert out == [False]


def test_confirm_is_denied_when_telegram_is_down():
    http = FakeHttp()
    tg = make(http)
    http.fail = True
    assert tg.confirm("acao", timeout=0.1) is False and tg._pending == {}


def test_offset_persisted_across_restarts_so_commands_never_replay():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "off"
        http, seen = FakeHttp(), []
        tg = make(http, on_text=lambda t: seen.append(t), state_file=f)
        http.updates = [msg("lembre de X", update_id=41), msg("outra", update_id=42)]
        tg.run_once(0)
        assert f.read_text() == "43"
        tg2 = make(http, state_file=f)                                  # reinício do servidor
        assert tg2.offset == 43
        tg2.run_once(0)
        assert http.sent("getUpdates")[-1]["offset"] == 43


def test_a_bad_update_does_not_block_the_queue():
    http, seen = FakeHttp(), []
    tg = make(http, on_text=lambda t: seen.append(t))
    http.updates = [{"update_id": 1, "message": "lixo"}, {"update_id": 2}, msg("ok", update_id=3)]
    assert tg.run_once(0) == 3
    assert wait_for(lambda: seen) and seen == ["ok"] and tg.offset == 4


def test_status_tracks_failures_without_secrets():
    http = FakeHttp()
    tg = make(http)
    tg.run_once(0)
    assert tg.last_ok > 0
    http.fail = True
    with pytest.raises(TelegramError):
        tg.run_once(0)
    assert TOKEN not in str(tg.status())


def test_from_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            monkeypatch.delenv(k, raising=False)
        assert tgm.from_env({}, Path(tmp)) is None
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        assert tgm.from_env({}, Path(tmp)) is None                      # falta o chat_id
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
        assert tgm.from_env({}, Path(tmp)).chat_id == 777
        assert tgm.from_env({"telegram": {"enabled": False}}, Path(tmp)) is None
