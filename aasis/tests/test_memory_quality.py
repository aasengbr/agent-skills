import sqlite3
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aasis.brain import worth_learning  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.memstore import LongTermMemory  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "t.db", tz=SP)


def test_verb_forms_and_synonym_families_are_found(mem):
    mem.mem.save("fact", "Mora na Rua das Flores, 120", level=0)
    mem.mem.save("fact", "Torce para o Sport Recife", level=0)
    mem.mem.save("fact", "Toma Losartana às 8h", level=0)
    assert mem.mem.search("onde eu moro")[0]["content"].startswith("Mora")            # moro -> mora
    assert mem.mem.search("para que time eu torço")[0]["content"].startswith("Torce")  # torço -> torce, time
    assert mem.mem.search("qual o endereço")[0]["content"].startswith("Mora")          # endereço -> mora/rua
    assert mem.mem.search("que remédio eu tomo")[0]["content"].startswith("Toma")      # remédio -> toma


def test_keywords_chosen_at_write_time_make_semantic_recall_possible(mem):
    a = mem.mem.save("fact", "Dra. Ana, revisão a cada 6 meses", keywords="dentista dentes sorriso odontologia", level=0)
    mem.mem.save("fact", "Dr. Paulo, consulta anual", level=0)
    assert [m["id"] for m in mem.mem.search("quem cuida dos meus dentes")] == [a]      # sem keywords: nada
    assert "sorriso" not in mem.mem.context("qualquer", 3)                              # tags não poluem o prompt
    b = mem.mem.save("fact", "Texto qualquer sobre carros", keywords="senha é 1234", level=0)   # segredo nas keywords: ignorado
    assert mem.mem.search("1234") == [] and mem.mem.get(b)
    v2 = mem.mem.update(a, "Dra. Ana, revisão a cada 4 meses")                         # a nova versão herda as palavras-chave
    assert [m["id"] for m in mem.mem.search("dentes")] == [v2]


def test_subject_awareness_first_person_third_person_and_relations():
    q = LongTermMemory._query_subject
    assert q("onde eu moro")[0] and q("quanto pago no meu aluguel")[0] and q("qual meu telefone")[0]
    assert not q("como se chama meu filho")[0] and not q("quando é o aniversário da minha mãe")[0]
    assert not q("quando é o aniversário da Maria")[0] and "maria" in q("quando é o aniversário da Maria")[1]


def test_search_prefers_the_users_own_memories_or_the_named_person(mem):
    mem.mem.save("fact", "Mora em Olinda e trabalha como professor", about="Ana", level=0)
    mem.mem.save("fact", "Mora em Boa Viagem", about="eu", level=0)
    mem.mem.save("fact", "Aniversário em 12 de março", about="Maria", level=0)
    mem.mem.save("fact", "Aniversário em 3 de maio", about="Ana", level=0)
    assert mem.mem.search("onde eu moro")[0]["content"] == "Mora em Boa Viagem"
    assert mem.mem.search("onde a Ana mora")[0]["about"] == "Ana"
    assert mem.mem.search("aniversário da Maria")[0]["content"].startswith("Aniversário em 12")
    assert mem.mem.search("aniversário da Ana")[0]["content"].startswith("Aniversário em 3")


def test_candidate_pool_is_large_enough_for_crowded_vocabulary(mem):
    """Regressão: o corte em 60 candidatos antes de reordenar escondia a memória certa."""
    for i in range(300):
        mem.mem.save("fact", f"Pessoa{i} mora em Cidade{i % 7}", about=f"Pessoa{i}", level=0)
    mem.mem.save("fact", "Mora na Rua das Flores, apartamento 502", about="eu", level=0)
    assert mem.mem.search("onde eu moro", limit=3)[0]["content"].startswith("Mora na Rua das Flores")


def test_full_context_for_small_memory_respects_privacy_status_and_history(mem):
    mem.mem.save("profile", "Alergia a dipirona")                                       # sensível (2)
    mem.mem.save("fact", "Gosta de jazz", level=0)
    mem.mem.save("state", "Carro no nível 2", slot="car_location", level=1)
    mem.mem.save("state", "Carro no nível 3", slot="car_location", level=1)
    mem.mem.save("fact", "Pendente de aprovação", status="pending", level=0)
    c0, c1, c2 = (mem.mem.context("qualquer coisa", lv) for lv in (0, 1, 2))
    assert "TUDO o que você sabe" in c2 and "dipirona" in c2 and "nível 3" in c2 and "jazz" in c2
    assert "nível 2" not in c2 and "Pendente" not in c2                                  # histórico e pendentes fora
    assert "dipirona" not in c1 and "nível 3" in c1 and "dipirona" not in c0 and "nível 3" not in c0 and "jazz" in c0
    big = mem.mem.context("x", 2, full_chars=50)                                          # acima do orçamento: modo busca
    assert "TUDO" not in big and "dipirona" in big                                        # perfil continua sempre presente


def test_old_databases_are_migrated_with_new_columns(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, slot TEXT, content TEXT NOT NULL, norm TEXT,"
               " data TEXT, level INTEGER DEFAULT 1, source TEXT DEFAULT 'user', importance INTEGER DEFAULT 3,"
               " status TEXT DEFAULT 'active', valid_from TEXT, valid_to TEXT, supersedes INTEGER, last_used TEXT,"
               " uses INTEGER DEFAULT 0)")
    db.execute("INSERT INTO mem(kind, content, norm, level, valid_from) VALUES ('fact', 'Gosta de jazz', 'gosta de jazz', 1, "
               "'2026-01-01T00:00:00Z')")
    db.commit()
    db.close()
    m = Memory(tmp_path / "old.db", tz=SP)
    row = m.mem.get(1)
    assert row["about"] == "eu" and "jazz" in m.mem.context("jazz", 3)
    new = m.mem.save("fact", "Moro em Recife", keywords="cidade casa", about="eu")
    assert m.mem.search("cidade")[0]["id"] == new


@pytest.mark.parametrize("text,ok", [
    ("O carro está na vaga 214", True), ("O aluguel vence todo dia 10", True), ("Vou me mudar para São Paulo em janeiro", True),
    ("A consulta é em 12 de novembro às 15h", True), ("Minha esposa se chama Maria", True),
    ("Onde estacionei o carro?", False), ("Qual a previsão do tempo?", False), ("Abra a calculadora", False),
    ("Me mostre meus e-mails", False), ("Obrigado!", False), ("ok", False), ("Lembre que gosto de café", False),
    ("Como está o dólar hoje", False), ("Que horas são em Lisboa", False), ("Quanto custa um voo para Lisboa", False)])
def test_learning_gate(text, ok):
    assert worth_learning(text) is ok
