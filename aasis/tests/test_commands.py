import datetime as dt
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from aasis import commands  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.meds import Meds  # noqa: E402
from test_telegram import FakeHttp, make as make_tg, msg, wait_for  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")
UTC = dt.timezone.utc


def at(day, hh, mm=0, month=10):
    return dt.datetime(2026, month, day, hh, mm, tzinfo=SP).astimezone(UTC)


class Env:
    def __init__(self, tmp, answer=True):
        self.mem = Memory(Path(tmp) / "m.db", tz=SP)
        self.meds = Meds(self.mem, SP)
        self.asked, self.answer, self.audit_log = [], answer, []

    def confirm(self, text):
        self.asked.append(text)
        return self.answer

    def run(self, text, now=None):
        return commands.handle(text, self.meds, self.confirm, audit=lambda n, s: self.audit_log.append((n, s)),
                               now=now or at(21, 10))     # quarta-feira, 10h


def test_add_happy_path_confirms_with_exact_preview_then_saves():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        out = e.run("/remedio Losartana 50mg 08:00,20:00 estoque=30")
        assert "✅ Cadastrado" in out and "Primeiro aviso: hoje às 20:00" in out
        assert len(e.asked) == 1
        for piece in ("Losartana", "50mg", "08:00, 20:00", "todos os dias", "Estoque: 30"):
            assert piece in e.asked[0]
        (m,) = e.meds.active_meds()
        assert (m["name"], m["dose"], m["times"], m["stock"], m["per_dose"]) == ("Losartana", "50mg", ["08:00", "20:00"], 30, 1)


def test_add_denied_saves_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp, answer=False)
        assert "Cancelado" in e.run("/remedio Losartana 50mg 08:00")
        assert e.meds.active_meds() == [] and e.audit_log == [("cmd_remedio", "negado")]


def test_underscore_means_space_and_accented_option_names_work():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio Vitamina_D 1000_UI 09:00 início=2026-12-01 fim=2026-12-31 por_dose=0,5 dias=seg,qua")
        (m,) = e.meds.active_meds()
        assert (m["name"], m["dose"], m["start"], m["end"], m["per_dose"], m["days"]) == (
            "Vitamina D", "1000 UI", "2026-12-01", "2026-12-31", 0.5, [0, 2])


def test_spaces_after_commas_are_tolerated():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio X 1cp 08:00, 20:00 dias=seg, qua estoque=10")
        (m,) = e.meds.active_meds()
        assert m["times"] == ["08:00", "20:00"] and m["days"] == [0, 2] and m["stock"] == 10


@pytest.mark.parametrize("cmd,expect", [
    ("/remedio X 1cp 25:00", "Horário inválido"),
    ("/remedio X 1cp 08:00 inicio=amanhã", "Data inválida"),
    ("/remedio X 1cp 08:00 fim=2020-01-01 inicio=2021-01-01", "data final"),
    ("/remedio X 1cp 08:00 estoque=muito", "não é um número"),
    ("/remedio X 1cp 08:00 estoque=-3", "negativo"),
    ("/remedio X 1cp 08:00 por_dose=99", "entre 0 e 20"),
    ("/remedio X 1cp 08:00 cor=azul", "Opção inválida"),
    ("/remedio X 1cp 08:00 estoque=", "Opção inválida"),
    ("/remedio X 1cp 08:00 dias=segredo", "Dia inválido"),
    ("/remedio X 1cp 08:00 dias=seg,xyz", "Dia inválido"),
    ("/remedio X 1cp", "Uso: /remedio"),
    ("/remedio X", "Uso: /remedio"),
])
def test_invalid_input_is_explained_and_never_asks_confirmation(cmd, expect):
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        out = e.run(cmd)
        assert expect in out
        assert e.asked == [] and e.meds.active_meds() == []                # nada para confirmar, nada gravado


def test_bare_remedio_shows_usage():
    with tempfile.TemporaryDirectory() as tmp:
        assert "Uso: /remedio" in Env(tmp).run("/remedio")


def test_first_alert_text_respects_time_weekday_and_period():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        assert "hoje às 20:00" in e.run("/remedio A 1 08:00,20:00", now=at(21, 10))
        assert "amanhã às 08:00" in e.run("/remedio B 1 08:00,20:00", now=at(21, 21))
        assert "seg 26/10 às 08:00" in e.run("/remedio C 1 08:00 dias=seg", now=at(21, 10))
        assert "Sem próximo horário" in e.run("/remedio D 1 08:00 fim=2026-10-01", now=at(21, 10))


def test_list_shows_meds_and_todays_doses():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        assert "Nenhum remédio" in e.run("/remedios")
        e.run("/remedio Losartana 50mg 08:00,20:00 estoque=30", now=at(21, 6))
        e.meds.materialize(at(21, 8, 30))
        out = e.run("/remedios", now=at(21, 8, 30))
        assert "Losartana 50mg" in out and "Hoje:" in out and "08:00 Losartana — pendente" in out


def test_tomei_registers_once_and_decrements_stock():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio Losartana 50mg 08:00 estoque=10", now=at(21, 6))
        e.meds.materialize(at(21, 8, 5))
        out = e.run("/tomei losartana", now=at(21, 8, 10))
        assert "tomada às 08:10" in out and "Restam 9" in out
        assert "Nenhuma dose em aberto" in e.run("/tomei Losartana", now=at(21, 8, 11))   # segunda vez: nada a registrar
        assert e.meds.active_meds()[0]["stock"] == 9


def test_tomei_by_number_exact_name_wins_ambiguity_and_errors():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio Losartana 50mg 08:00", now=at(21, 6))
        e.run("/remedio Losartana_HCT 50mg 08:00", now=at(21, 6))
        e.meds.materialize(at(21, 8, 5))
        assert "tomada" in e.run("/tomei 2", now=at(21, 8, 10)) and "HCT" in e.run("/remedios", now=at(21, 8, 10))
        assert "tomada" in e.run("/tomei Losartana", now=at(21, 8, 11))                    # nome exato vence "Losartana HCT"
        assert "Mais de um" in e.run("/tomei Los", now=at(21, 8, 12))
        assert "Não há remédio" in e.run("/tomei Xyz", now=at(21, 8, 12))
        assert "Não há remédio ativo com o número 99" in e.run("/tomei 99")
        assert "Uso: /tomei" in e.run("/tomei")


def test_estoque_updates_and_reports_days_left():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio Losartana_HCT 50mg 08:00,20:00")
        out = e.run("/estoque Losartana HCT 12")
        assert "Estoque de Losartana HCT: 12" in out and "6 dia(s)" in out
        assert e.meds.active_meds()[0]["stock"] == 12
        assert "não é um número" in e.run("/estoque Losartana HCT muito")
        assert "Não há remédio" in e.run("/estoque Zzz 5")
        assert "Uso: /estoque" in e.run("/estoque Losartana")
        assert e.meds.active_meds()[0]["stock"] == 12


def test_remover_confirms_then_stops_alerts_but_keeps_history():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio Losartana 50mg 08:00", now=at(21, 6))
        e.meds.materialize(at(21, 8, 5))
        e.asked.clear()
        e.answer = False
        assert "Cancelado" in e.run("/remover Losartana") and len(e.meds.active_meds()) == 1
        assert "Losartana" in e.asked[0]                                                  # a confirmação mostra o remédio
        e.answer = True
        assert "Removido" in e.run("/remover Losartana")
        assert e.meds.active_meds() == [] and e.meds.due_doses(at(21, 8, 6)) == []
        assert e.meds.db.execute("SELECT COUNT(*) FROM doses").fetchone()[0] == 1
        assert "Não há remédio" in e.run("/remover Losartana")


def test_help_unknown_command_and_bot_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        h = e.run("/ajuda")
        for c in ("/remedio", "/remedios", "/tomei", "/estoque", "/remover", "não usam IA", "não sugere dose"):
            assert c in h
        assert "desconhecido" in e.run("/foobar")
        assert "✅ Cadastrado" in e.run("/Remedio@meu_bot Losartana 50mg 08:00")


def test_audit_records_command_and_result_but_never_content():
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        e.run("/remedio Clonazepam 2mg 22:00 estoque=30")
        e.run("/estoque Clonazepam 5")
        e.run("/tomei Clonazepam")
        e.run("/remover Clonazepam")
        assert [n for n, _ in e.audit_log] == ["cmd_remedio", "cmd_estoque", "cmd_tomei", "cmd_remover"]
        assert "Clonazepam" not in str(e.audit_log) and "2mg" not in str(e.audit_log)


# ---------- roteamento no Telegram: comando nunca vai para a IA ----------
def test_telegram_routes_slash_commands_to_on_command_and_text_to_on_text():
    http, texts, cmds = FakeHttp(), [], []
    tg = make_tg(http, on_text=lambda t: texts.append(t) or "ia", on_command=lambda t: cmds.append(t) or "cmd")
    http.updates = [msg("/remedio Losartana 50mg 08:00", update_id=1), msg("que horas são?", update_id=2),
                    msg("/Tomei@meu_bot losartana", update_id=3)]
    tg.run_once(0)
    assert wait_for(lambda: len(http.sent()) == 3)
    assert cmds == ["/remedio Losartana 50mg 08:00", "/Tomei@meu_bot losartana"] and texts == ["que horas são?"]
    assert sorted(m["text"] for m in http.sent()) == ["cmd", "cmd", "ia"]


def test_telegram_start_is_a_greeting_and_unknown_senders_cannot_run_commands():
    http, cmds = FakeHttp(), []
    tg = make_tg(http, on_command=lambda t: cmds.append(t) or "x")
    http.updates = [msg("/start", update_id=1), msg("/remedio A 1 08:00", uid=999, update_id=2),
                    msg("/remover 1", uid=999, update_id=3)]
    tg.run_once(0)
    time.sleep(0.2)
    assert cmds == []                                                                    # estranho não executa comando
    sent = http.sent()
    assert any("/ajuda" in m["text"] and m["chat_id"] == 777 for m in sent)
    assert not [m for m in sent if m["chat_id"] == 999 and "remedio" in m["text"].lower()]


def test_telegram_without_command_handler_keeps_help_greeting_and_sends_slash_to_text():
    http, texts = FakeHttp(), []
    tg = make_tg(http, on_text=lambda t: texts.append(t) or None)
    http.updates = [msg("/ajuda", update_id=1), msg("/qualquer coisa", update_id=2)]
    tg.run_once(0)
    assert wait_for(lambda: texts)
    assert texts == ["/qualquer coisa"] and "/ajuda" in http.sent()[0]["text"]


def test_telegram_command_handler_failure_is_reported_without_details():
    http = FakeHttp()

    def boom(_):
        raise RuntimeError("segredo interno: Clonazepam")

    tg = make_tg(http, on_command=boom)
    http.updates = [msg("/remedio A 1 08:00")]
    tg.run_once(0)
    assert wait_for(lambda: http.sent())
    assert "RuntimeError" in http.sent()[0]["text"] and "Clonazepam" not in http.sent()[0]["text"]
