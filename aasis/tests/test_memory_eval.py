"""Regressão da qualidade da memória (limiares com folga, para pegar queda e não oscilação)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory_eval as E  # noqa: E402
from memory_eval_data import HELDOUT  # noqa: E402

HELD = [(q, w) for q, w, _ in HELDOUT]


def test_small_memory_shows_everything_so_nothing_is_lost():
    with tempfile.TemporaryDirectory() as tmp:
        mem = E.build(tmp)
        d, _ = E.retrieval(mem)
        h, _ = E.retrieval(mem, HELD)
        assert d["no_contexto"] == 1.0 and h["no_contexto"] == 1.0         # inclusive as perguntas de sentido


def test_large_memory_search_mode_keeps_quality_with_600_lookalike_distractors():
    with tempfile.TemporaryDirectory() as tmp:
        mem = E.build(tmp, 600)
        assert len(mem.mem.context("onde eu moro", 3)) < 2500               # prova que está no modo busca
        d, misses = E.retrieval(mem)
        h, _ = E.retrieval(mem, HELD)
        assert d["no_contexto"] >= 0.95, misses                              # hoje: 100%
        assert h["no_contexto"] >= 0.80                                      # hoje: 87% (o resto é lacuna de sentido)
        lex = [(q, w) for q, w, k in HELDOUT if k == "lexical"]
        assert E.retrieval(mem, lex)[0]["no_contexto"] >= 0.95               # perguntas por palavra: hoje 100%


def test_learning_gate_catches_durable_statements_and_skips_commands():
    g, lost = E.gate()
    assert g["recall_do_portao"] >= 0.9 and g["falsos_positivos"] <= 3, lost
