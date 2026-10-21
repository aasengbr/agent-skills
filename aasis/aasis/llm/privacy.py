"""Quem pode ver o quê. O nível do pedido decide quais provedores e ferramentas podem ser usados.

0 normal    : pergunta sem dado pessoal (notícias, câmbio, clima, conhecimento geral)
1 privado   : e-mail, agenda, Drive, casa, tarefas, memórias pessoais
2 sensível  : saúde e finanças
3 secreto   : nunca sai da máquina local
A classificação do texto é por palavras-chave e **errada em ambos os sentidos**; por isso o histórico da conversa
também conta: depois de um dado privado, a janela inteira fica privada até ele sair do histórico.
"""
import re
import unicodedata

NORMAL, PRIVATE, SENSITIVE, SECRET = 0, 1, 2, 3

# Ferramentas que só lidam com informação pública. Qualquer outra é privada por padrão (conservador).
PUBLIC_TOOLS = {"get_time", "news", "fx_rates", "stock_quote", "bcb_indicators", "weather", "world_time",
                "web_fetch", "web_search"}
SENSITIVE_TOOLS = {"remember", "recall", "memory_search", "memory_save", "memory_update", "memory_forget",
                   "memory_list", "daily_brief"}


def tool_level(name: str) -> int:
    if name in PUBLIC_TOOLS:
        return NORMAL
    return SENSITIVE if name in SENSITIVE_TOOLS else PRIVATE


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


SENSITIVE_RE = re.compile(
    r"\b(remedio|medicamento|medicacao|dose|comprimido|receita medica|saude|doenca|alergi\w*|pressao arterial|diabet\w*|"
    r"consulta medica|medico|exame|cirurgia|diagnostic\w*|sintoma\w*|saldo|conta bancaria|banco|cartao|fatura|"
    r"salario|divida|emprestimo|investimento|cpf|senha|pix)\b")
PRIVATE_RE = re.compile(
    r"\b(e-?mails?|agenda|reuniao|compromisso|lembrete\w*|lembre|lembra|tarefa\w*|drive|planilha|documento|"
    r"memoria|esqueca|voce sabe|casa|luz|luzes|ar condicionado|estacion\w+|carro|resumo do dia|minha rotina)\b")


def classify(text: str) -> int:
    t = _norm(text)
    if SENSITIVE_RE.search(t):
        return SENSITIVE
    return PRIVATE if PRIVATE_RE.search(t) else NORMAL


def history_level(history: list) -> int:
    return max((m.get("level", 0) for m in history), default=0)


# Quanto de cada nível cada tipo de provedor pode ver (configurável em llm.privacy.max_level).
DEFAULT_MAX_LEVEL = {"local": SECRET, "trusted": SENSITIVE, "external": NORMAL}
