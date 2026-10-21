"""Tipos neutros de provedor: o histórico não depende de nenhuma API, então dá para trocar de modelo no meio."""
from dataclasses import dataclass, field


class ProviderError(Exception):
    """kind: rate_limit | auth | server | timeout | network | bad_request | bad_response | web_search_disabled"""

    def __init__(self, kind: str, message: str = ""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    searches: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    stop: str = "end"  # end | tool_use | max_tokens | refusal | pause
    usage: Usage = field(default_factory=Usage)
    sources: list = field(default_factory=list)  # [{"url", "title"}] citados pela busca na web
    raw: object = None  # conteúdo original do provedor, reenviado só a ele (blocos criptografados de busca etc.)
    provider: str = ""
    model: str = ""


# ---- mensagens neutras (dicts simples, com o nível de sensibilidade do que carregam) ----
def user_msg(text: str, level: int = 0) -> dict:
    return {"role": "user", "text": text, "level": level}


def assistant_msg(resp: LLMResponse, level: int = 0) -> dict:
    return {"role": "assistant", "text": resp.text, "level": level,
            "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in resp.tool_calls],
            "raw": {"provider": resp.provider, "content": resp.raw} if resp.raw is not None else None}


def tool_msg(results: list, level: int = 0) -> dict:
    """results: [{"id", "name", "content": str | list de blocos texto/imagem}]"""
    return {"role": "tool", "results": results, "level": level}
