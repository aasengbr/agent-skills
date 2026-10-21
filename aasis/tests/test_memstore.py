import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aasis.memory import Memory  # noqa: E402
from aasis.memstore import LongTermMemory, looks_secret, norm  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "t.db", tz=SP)


def test_allergy_survives_hundreds_of_newer_facts(mem):
    """O defeito antigo: só os 30 fatos mais recentes entravam no prompt, e a alergia sumia."""
    mem.mem.save("profile", "Tem alergia grave a dipirona", importance=5)
    for i in range(200):
        mem.mem.save("fact", f"Fato qualquer número {i}")
    ctx = mem.mem.context("qual o horário da reunião?", 3)
    assert "alergia grave a dipirona" in ctx and "Perfil (sempre vale)" in ctx
    assert len(ctx) <= 2400


def test_state_supersedes_and_keeps_history(mem):
    a = mem.mem.save("state", "Carro no nível 2, vaga 214", slot="car_location")
    b = mem.mem.save("state", "Carro no nível 3, vaga 301", slot="car_location")
    assert mem.mem.state("car_location")["id"] == b and mem.mem.get(a)["status"] == "active"
    ctx = mem.mem.context("onde estacionei o carro?", 3)
    assert "nível 3" in ctx and "nível 2" not in ctx                          # só o valor atual vai ao modelo
    assert [h["id"] for h in mem.mem.history("car_location")] == [b, a]       # histórico preservado
    assert mem.mem.save("state", "Carro no nível 3, vaga 301", slot="car_location") == b    # repetido não duplica


def test_search_ignores_accents_uses_prefix_and_synonyms(mem):
    mem.mem.save("state", "Estacionei o veículo no Shopping Center", slot="car_location")
    mem.mem.save("fact", "Maria é minha esposa")
    mem.mem.save("fact", "Tomo Losartana às 8h", level=2)
    assert mem.mem.search("onde ESTACIONEI?")[0]["slot"] == "car_location"
    assert mem.mem.search("meu carro")[0]["slot"] == "car_location"            # sinônimo carro -> veículo/estacion
    assert mem.mem.search("quem é minha mulher")[0]["content"].startswith("Maria")
    assert mem.mem.search("remédio")[0]["content"].startswith("Tomo Losartana")
    assert mem.mem.search("assunto inexistente zzz") == [] and mem.mem.search("de a o") == []


def test_privacy_level_filters_context_and_search(mem):
    mem.mem.save("fact", "Gosta de café sem açúcar", level=0)
    mem.mem.save("fact", "Mora na rua das Flores", level=1)
    mem.mem.save("fact", "Tem diabetes tipo 2", level=2)
    mem.mem.save("fact", "Segredo familiar", level=3)
    for lvl, expected in ((0, {"café"}), (1, {"café", "Flores"}), (2, {"café", "Flores", "diabetes"}),
                          (3, {"café", "Flores", "diabetes", "Segredo"})):
        ctx = mem.mem.context("café rua diabetes segredo", lvl)
        for word in ("café", "Flores", "diabetes", "Segredo"):
            assert (word in ctx) == (word in expected), (lvl, word)
    assert [r["content"] for r in mem.mem.search("diabetes", max_level=1)] == []


def test_default_levels_health_is_sensitive_preference_is_normal(mem):
    assert mem.mem.get(mem.mem.save("fact", "Tomo remédio para pressão"))["level"] == 2
    assert mem.mem.get(mem.mem.save("fact", "Gosta de jazz"))["level"] == 1
    assert mem.mem.get(mem.mem.save("preference", "Prefere respostas curtas"))["level"] == 0


@pytest.mark.parametrize("text,why", [
    ("minha senha é abc123", "senhas"), ("password: hunter2", "senhas"), ("CVV: 123 do cartão", "senhas"),
    ("cartão 4111 1111 1111 1111", "cartão"), ("cartao 4111-1111-1111-1111 vence 12/30", "cartão")])
def test_secrets_are_refused(mem, text, why):
    with pytest.raises(ValueError) as e:
        mem.mem.save("fact", text)
    assert why in str(e.value)
    assert looks_secret(text)


def test_normal_numbers_are_not_mistaken_for_cards(mem):
    for ok in ("Apartamento 1201 bloco 3", "CPF 123.456.789-09", "Telefone 11 91234-5678", "Vaga 214 nível 2"):
        assert not looks_secret(ok)
        mem.mem.save("fact", ok)


def test_pending_is_invisible_until_approved_and_reject_deletes(mem):
    pid = mem.mem.save("fact", "Toma Rivotril à noite", status="pending", source="inferido", level=2)
    assert mem.mem.search("rivotril") == [] and "Rivotril" not in mem.mem.context("rivotril", 3)
    assert [p["id"] for p in mem.mem.list(status="pending")] == [pid]
    assert mem.mem.approve(pid) and mem.mem.search("rivotril")[0]["id"] == pid
    assert mem.mem.approve(pid) is False
    p2 = mem.mem.save("fact", "Lixo", status="pending")
    assert mem.mem.reject(p2) and mem.mem.reject(p2) is False


def test_update_versions_forget_deletes_whole_chain(mem):
    a = mem.mem.save("fact", "Médico é o Dr. Paulo")
    b = mem.mem.update(a, "Médico é a Dra. Ana")
    assert mem.mem.search("médico")[0]["id"] == b and mem.mem.get(a)["status"] == "active"
    assert mem.mem.forget(b) == 2                                              # apaga a versão atual e a anterior
    assert mem.mem.get(a) is None and mem.mem.get(b) is None and mem.mem.search("médico") == []
    with pytest.raises(ValueError):
        mem.mem.update(999, "x")


def test_validation(mem):
    for kind, content in (("xyz", "a"), ("fact", "   ")):
        with pytest.raises(ValueError):
            mem.mem.save(kind, content)
    with pytest.raises(ValueError):
        mem.mem.save("state", "x", slot="Slot Inválido!")


def test_export_contains_history_and_status(mem):
    mem.mem.save("state", "A", slot="s_um")
    mem.mem.save("state", "B", slot="s_um")
    mem.mem.save("fact", "P", status="pending")
    ex = mem.mem.export()
    assert len(ex) == 3 and {e["status"] for e in ex} == {"active", "pending"} and any(e["valid_to"] for e in ex)


def test_legacy_facts_are_migrated_once(tmp_path):
    db = sqlite3.connect(tmp_path / "t.db")
    db.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.executemany("INSERT INTO facts(text) VALUES (?)", [("Gosta de chá",), ("Tem um gato",)])
    db.commit()
    db.close()
    m = Memory(tmp_path / "t.db", tz=SP)
    assert sorted(r["content"] for r in m.mem.list()) == ["Gosta de chá", "Tem um gato"]
    m.mem.forget(m.mem.search("gato")[0]["id"])
    m2 = Memory(tmp_path / "t.db", tz=SP)                                      # reabrir não ressuscita o apagado
    assert [r["content"] for r in m2.mem.list()] == ["Gosta de chá"]


def test_works_without_fts5(tmp_path, monkeypatch):
    real = sqlite3.Connection.execute

    class NoFts(sqlite3.Connection):
        def execute(self, sql, *a):
            if "fts5" in sql.lower():
                raise sqlite3.OperationalError("no such module: fts5")
            return real(self, sql, *a)

    db = sqlite3.connect(tmp_path / "n.db", factory=NoFts, check_same_thread=False)
    m = LongTermMemory(db, threading.RLock(), SP)
    assert m.fts is False
    m.save("state", "Estacionei no nível 2", slot="car_location")
    assert m.search("estacionei")[0]["slot"] == "car_location"                 # cai para LIKE


def test_legacy_api_and_thread_safety(mem):
    mem.remember("gosta de jazz")
    assert mem.recall("jazz") == ["gosta de jazz"] and mem.forget(mem.mem.search("jazz")[0]["id"])
    err = []

    def work(i):
        try:
            mem.mem.save("fact", f"fato paralelo {i}")
            mem.mem.search("paralelo")
        except Exception as e:  # noqa: BLE001
            err.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not err and len(mem.mem.list(limit=100)) == 12


def test_norm_helper():
    assert norm("Estacionei NÃO-sei! Vaga 214") == "estacionei nao sei vaga 214"


def test_always_included_blocks_also_respect_privacy_level(mem):
    """Perfil, estados e preferências entram sempre no contexto: nenhum pode vazar para quem tem nível menor."""
    mem.mem.save("profile", "Alergia a dipirona", importance=5)                          # padrão: sensível (2)
    mem.mem.save("state", "Carro no nível 2", slot="car_location")                      # padrão: pessoal (1)
    mem.mem.save("preference", "Prefere respostas curtas")                              # normal (0)
    c0, c1, c2 = (mem.mem.context("qualquer coisa", lv) for lv in (0, 1, 2))
    assert "respostas curtas" in c0 and "dipirona" not in c0 and "nível 2" not in c0
    assert "nível 2" in c1 and "dipirona" not in c1
    assert "dipirona" in c2 and "nível 2" in c2
    assert [m["content"] for m in mem.mem.list("profile", max_level=1)] == []
