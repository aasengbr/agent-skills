"""Adaptador genérico para APIs compatíveis com OpenAI (/chat/completions): DeepSeek, Kimi, GLM, NVIDIA NIM,
OpenRouter, Ollama, LM Studio, llama.cpp. IDs de modelo e endereços vêm da configuração, nunca do código."""
import json
import os

import requests

from .base import LLMResponse, ProviderError, ToolCall, Usage

FINISH = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
          "content_filter": "refusal"}


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if b.get("type") == "text")


def _images_of(content) -> list:
    if isinstance(content, str):
        return []
    return [b["source"] for b in content if b.get("type") == "image" and b.get("source", {}).get("type") == "base64"]


def to_openai(messages: list, vision: bool) -> list:
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m["text"] or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                                     for c in m["tool_calls"]]
            elif not m["text"]:
                msg["content"] = "(sem resposta)"
            out.append(msg)
        else:
            pending_images = []
            for r in m["results"]:
                text = _text_of(r["content"])
                imgs = _images_of(r["content"])
                if imgs and not vision:
                    text += " [imagem omitida: este modelo não lê imagens]"
                out.append({"role": "tool", "tool_call_id": r["id"], "content": text or "(vazio)"})
                if imgs and vision:
                    pending_images += imgs
            if pending_images:  # tool messages não carregam imagem: vai em uma mensagem de usuário logo depois
                out.append({"role": "user", "content": [{"type": "text", "text": "Imagem retornada pela ferramenta:"}] + [
                    {"type": "image_url", "image_url": {"url": f"data:{i['media_type']};base64,{i['data']}"}}
                    for i in pending_images]})
    return out


def to_openai_tools(tools: list) -> list:
    """Só ferramentas do cliente. Ferramentas do servidor da Anthropic (busca) não existem aqui."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}}
            for t in tools if "input_schema" in t]


class OpenAICompatProvider:
    kind = "openai"

    def __init__(self, name: str, cfg: dict, http=None):
        self.name, self.cfg = name, cfg
        self.base = (cfg.get("base_url") or os.getenv(cfg.get("base_url_env", ""), "")).rstrip("/")
        self.key_env = cfg.get("key_env", "")
        self.trust = cfg.get("trust", "external")
        self.http = http or requests.Session()
        self.timeout = float(cfg.get("timeout", 60))

    @property
    def key(self) -> str:
        return os.getenv(self.key_env, "") if self.key_env else ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        h.update(self.cfg.get("headers", {}))  # ex.: OpenRouter aceita X-Title e HTTP-Referer
        return h

    def _call(self, method: str, path: str, **kw):
        try:
            r = self.http.request(method, f"{self.base}{path}", headers=self._headers(), timeout=self.timeout, **kw)
        except requests.Timeout as e:
            raise ProviderError("timeout", str(e)[:150])
        except requests.ConnectionError as e:
            raise ProviderError("network", str(e)[:150])
        code = r.status_code
        if code >= 400:
            kind = ("auth" if code in (401, 403) else "rate_limit" if code == 429 else
                    "server" if code >= 500 else "bad_request")
            raise ProviderError(kind, f"HTTP {code} {getattr(r, 'text', '')[:150]}")
        return r

    def complete(self, model: dict, stable: str, volatile: str, messages: list, tools: list,
                 max_tokens: int) -> LLMResponse:
        body = {"model": model["id"], "max_tokens": max_tokens, "stream": False,
                "messages": [{"role": "system", "content": stable + ("\n\n" + volatile if volatile else "")}]
                + to_openai(messages, bool(model.get("vision")))}
        specs = to_openai_tools(tools) if model.get("tools", True) else []
        if specs:
            body["tools"] = specs
        data = self._call("POST", "/chat/completions", json=body).json()
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            calls = []
            for tc in msg.get("tool_calls") or []:
                args = tc["function"].get("arguments") or "{}"
                calls.append(ToolCall(tc["id"], tc["function"]["name"], args if isinstance(args, dict) else json.loads(args)))
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ProviderError("bad_response", f"resposta fora do formato esperado: {type(e).__name__}")
        u = data.get("usage") or {}
        return LLMResponse(
            text=msg.get("content") or "", tool_calls=calls,
            stop=("tool_use" if calls else FINISH.get(choice.get("finish_reason") or "stop", "end")),
            usage=Usage(input=u.get("prompt_tokens", 0) or 0, output=u.get("completion_tokens", 0) or 0),
            provider=self.name, model=model["id"])

    def list_models(self) -> list:
        data = self._call("GET", "/models").json()
        return sorted(m["id"] for m in data.get("data", []))
