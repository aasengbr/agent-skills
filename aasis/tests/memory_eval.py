"""Mede a memória: recuperação de detalhes (retrieval) e o portão de aprendizado (gate). Use: python tests/memory_eval.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_eval_data import HELDOUT, MEMORIES, QUERIES, UTTERANCES  # noqa: E402

from aasis.brain import worth_learning  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.memstore import norm  # noqa: E402


def build(tmp):
    mem = Memory(Path(tmp) / "e.db")
    for kind, content, slot in MEMORIES:
        mem.mem.save(kind, content, slot=slot, level=0)   # nível 0: avalia só a recuperação, sem filtro de privacidade
    return mem


def distractors(n, seed=7):
    """Memórias de OUTRAS pessoas e assuntos, com o mesmo vocabulário das perguntas (mora, vence, toma, trabalha...)."""
    import random
    r = random.Random(seed)
    nomes = ["João", "Ana", "Carlos", "Beatriz", "Rafael", "Luiza", "Paulo", "Camila", "Diego", "Fernanda", "Marcos", "Helena"]
    cidades = ["Recife", "Olinda", "Natal", "Salvador", "Fortaleza", "Curitiba", "Belém", "Manaus", "Goiânia", "Vitória"]
    profs = ["advogado", "professor", "médico", "arquiteto", "contador", "designer", "motorista", "enfermeiro"]
    coisas = ["aluguel", "seguro", "condomínio", "IPTU", "internet", "plano de saúde", "carro", "passaporte"]
    modelos = [lambda: f"{r.choice(nomes)} mora em {r.choice(cidades)} e trabalha como {r.choice(profs)}",
               lambda: f"O {r.choice(coisas)} de {r.choice(nomes)} vence dia {r.randint(1, 28)}",
               lambda: f"{r.choice(nomes)} toma remédio às {r.randint(6, 22)}h e gosta de café",
               lambda: f"{r.choice(nomes)} estacionou o carro em {r.choice(cidades)}, vaga {r.randint(1, 400)}",
               lambda: f"Aniversário de {r.choice(nomes)} em {r.randint(1, 28)} de {r.choice(['março', 'maio', 'julho', 'outubro'])}",
               lambda: f"{r.choice(nomes)} torce para o time {r.choice(cidades)} e viaja de avião todo ano",
               lambda: f"Consulta de {r.choice(nomes)} com o {r.choice(profs)} em {r.randint(1, 28)} de novembro"]
    out = []
    for _ in range(n):
        txt = r.choice(modelos)()
        who = next((x for x in nomes if x in txt), "eu")   # quem grava rotularia "sobre quem": aqui, a pessoa citada
        out.append(("fact", txt, None, who))
    return out


def build(tmp, extra=0):
    mem = Memory(Path(tmp) / "e.db")
    for kind, content, slot, *who in MEMORIES + distractors(extra):
        mem.mem.save(kind, content, slot=slot, level=0, about=who[0] if who else "eu")   # nível 0: sem filtro de privacidade
    return mem


def retrieval(mem, queries=None):
    queries = [(q, w) for q, w in (queries or QUERIES)]
    r3 = r8 = ctx = 0
    misses = []
    for q, want in queries:
        got = [m["content"] for m in mem.mem.search(q, limit=8)]
        h8 = any(want.lower() in c.lower() for c in got)
        h3 = any(want.lower() in c.lower() for c in got[:3])
        c_hit = want.lower() in mem.mem.context(q, 3).lower()
        r3, r8, ctx = r3 + h3, r8 + h8, ctx + c_hit
        if not c_hit:
            misses.append((q, want, got[:2]))
    n = len(queries)
    return {"recall@3": r3 / n, "recall@8": r8 / n, "no_contexto": ctx / n, "n": n}, misses


def gate(learn_gate=None):
    """Quantas frases duradouras o portão deixa passar (recall) e quantas inúteis ele deixa passar (falso positivo)."""
    ok = learn_gate or worth_learning
    tp = sum(1 for t, want in UTTERANCES if want and ok(t))
    fp = sum(1 for t, want in UTTERANCES if not want and ok(t))
    pos = sum(1 for _, w in UTTERANCES if w)
    return {"recall_do_portao": tp / pos, "falsos_positivos": fp, "negativos": len(UTTERANCES) - pos}, [
        t for t, w in UTTERANCES if w and not ok(t)]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        mem = build(tmp)
        m, misses = retrieval(mem)
        print(f"RECUPERAÇÃO ({m['n']} perguntas, {len(MEMORIES)} memórias):")
        print("  " + " | ".join(f"{k} {v:.0%}" for k, v in m.items() if k != "n"))
        for q, want, got in misses:
            print(f"  ERRO: {q!r} esperava {want!r}; veio {[g[:40] for g in got]}")
        held = [(q, w) for q, w, _ in HELDOUT]
        kinds = {q: k for q, _, k in HELDOUT}
        mh, missh = retrieval(mem, held)
        print(f"\nCONJUNTO SEPARADO ({mh['n']} perguntas novas, escritas depois do ajuste):")
        print("  " + " | ".join(f"{k} {v:.0%}" for k, v in mh.items() if k != "n"))
        for kind in ("lexical", "semantico"):
            sub = [(q, w) for q, w, k in HELDOUT if k == kind]
            ms, _ = retrieval(mem, sub)
            print(f"    {kind:9s}: no contexto {ms['no_contexto']:.0%} ({int(round(ms['no_contexto'] * ms['n']))}/{ms['n']})")
        for q, want, got in missh:
            print(f"  ERRO [{kinds[q]}]: {q!r} esperava {want!r}")
        for extra in (200, 600):
            with tempfile.TemporaryDirectory() as tmp2:
                big = build(tmp2, extra)
                total = len(big.mem.list(limit=2000))
                d, _ = retrieval(big)
                h, mh2 = retrieval(big, held)
                chars = len(big.mem.context("onde eu moro", 3))
                print(f"\nMEMÓRIA GRANDE ({total} itens; contexto {chars} caracteres; modo busca):")
                print(f"  conjunto original: recall@8 {d['recall@8']:.0%}, no contexto {d['no_contexto']:.0%}")
                print(f"  conjunto separado: recall@8 {h['recall@8']:.0%}, no contexto {h['no_contexto']:.0%}")
        g, lost = gate()
        print(f"\nPORTÃO DE APRENDIZADO: acha {g['recall_do_portao']:.0%} das frases duradouras; "
              f"deixa passar {g['falsos_positivos']}/{g['negativos']} frases inúteis")
        for t in lost:
            print(f"  PERDIDA: {t!r}")
