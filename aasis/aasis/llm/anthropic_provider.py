"""Adaptador Anthropic: blocos de sistema com cache, esforço, busca na web, recusa e erros tipados."""
import anthropic

from .base import LLMResponse, ProviderError, ToolCall, Usage

STOP = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use", "max_tokens": "max_tokens",
        "refusal": "refusal", "pause_turn": "pause"}


def to_anthropic(messages: list) -> list:
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            raw = m.get("raw")
            if raw and raw.get("provider") == "anthropic" and raw.get("content"):
                content = raw["content"]  # reenvia exatamente como veio (blocos criptografados da busca, etc.)
            else:
                content = []
                if m["text"]:
                    content.append({"type": "text", "text": m["text"]})
                content += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                            for c in m.get("tool_calls", [])]
                content = content or [{"type": "text", "text": "(sem resposta)"}]  # a API rejeita conteúdo vazio
            out.append({"role": "assistant", "content": content})
        else:  # tool
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
    return out


class AnthropicProvider:
    trust = "trusted"
    kind = "anthropic"

    def __init__(self, name: str, cfg: dict, client=None):
        self.name, self.cfg = name, cfg
        self._client = client
        self.key_env = cfg.get("key_env", "ANTHROPIC_API_KEY")

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    @client.setter
    def client(self, value):
        self._client = value

    def complete(self, model: dict, stable: str, volatile: str, messages: list, tools: list,
                 max_tokens: int) -> LLMResponse:
        # Prefixo estável com cache; tudo que muda (hora, memórias) vem depois do ponto de cache.
        system = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
        if volatile:
            system.append({"type": "text", "text": volatile})
        kw = {"model": model["id"], "max_tokens": max_tokens, "system": system, "messages": to_anthropic(messages)}
        if tools:
            kw["tools"] = tools  # ferramentas do cliente + ferramenta de busca do servidor, já no formato da API
        if model.get("effort"):
            kw["output_config"] = {"effort": model["effort"]}
        try:
            resp = self.client.messages.create(**kw)
        except anthropic.BadRequestError as e:
            low = str(e).lower()
            raise ProviderError("web_search_disabled" if ("web search" in low or "web_search" in low)
                                else "bad_request", str(e)[:200])
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ProviderError("auth", str(e)[:200])
        except anthropic.RateLimitError as e:
            raise ProviderError("rate_limit", str(e)[:200])
        except anthropic.APITimeoutError as e:
            raise ProviderError("timeout", str(e)[:200])
        except anthropic.APIConnectionError as e:
            raise ProviderError("network", str(e)[:200])
        except anthropic.APIStatusError as e:  # 5xx e 529 (sobrecarga)
            raise ProviderError("server", f"{getattr(e, 'status_code', '')} {str(e)[:150]}")
        return self._parse(resp, model)

    def _parse(self, resp, model: dict) -> LLMResponse:
        content = resp.content
        u = resp.usage
        sources = []
        for b in content:
            if getattr(b, "type", "") == "text":
                for c in getattr(b, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url and all(url != s["url"] for s in sources) and len(sources) < 8:
                        sources.append({"url": url, "title": getattr(c, "title", None) or url})
        return LLMResponse(
            text="".join(b.text for b in content if getattr(b, "type", "") == "text"),
            tool_calls=[ToolCall(b.id, b.name, dict(b.input)) for b in content if getattr(b, "type", "") == "tool_use"],
            stop=STOP.get(getattr(resp, "stop_reason", None) or "end_turn", "end"),
            usage=Usage(input=getattr(u, "input_tokens", 0) or 0, output=getattr(u, "output_tokens", 0) or 0,
                        cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                        cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                        searches=int(getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0)),
            sources=sources, raw=content, provider=self.name, model=model["id"])

    def list_models(self) -> list:
        return [m.id for m in self.client.models.list()]
