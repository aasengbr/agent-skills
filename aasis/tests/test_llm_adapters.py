import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "x")

import anthropic  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402

try:
    import httpx2 as httpx  # noqa: E402
except ImportError:
    import httpx  # noqa: E402

from aasis.llm.anthropic_provider import AnthropicProvider, to_anthropic  # noqa: E402
from aasis.llm.base import LLMResponse, ProviderError, ToolCall, assistant_msg, tool_msg, user_msg  # noqa: E402
from aasis.llm.openai_provider import OpenAICompatProvider, to_openai, to_openai_tools  # noqa: E402

IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}}
HIST = [user_msg("oi"),
        assistant_msg(LLMResponse(text="vou ver", tool_calls=[ToolCall("c1", "news", {"topic": "x"})], provider="a")),
        tool_msg([{"id": "c1", "name": "news", "content": "manchete"}])]
TOOLS = [{"name": "news", "description": "d", "input_schema": {"type": "object", "properties": {}}},
         {"type": "web_search_20250305", "name": "web_search"}]


# ---------- OpenAI-compatível ----------
class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        return self._data


class Http:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.exc:
            raise self.exc
        return self.resp


def oa(http, **cfg):
    return OpenAICompatProvider("p", {"base_url": "http://h/v1/", "key_env": "P_KEY", **cfg}, http=http)


def ok_data(**msg):
    return {"choices": [{"message": {"content": "olá", **msg}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3}}


def test_to_openai_conversion_and_tool_filtering():
    out = to_openai(HIST, vision=False)
    assert out[0] == {"role": "user", "content": "oi"}
    assert out[1]["tool_calls"][0]["function"] == {"name": "news", "arguments": '{"topic": "x"}'}
    assert out[2] == {"role": "tool", "tool_call_id": "c1", "content": "manchete"}
    specs = to_openai_tools(TOOLS)
    assert [s["function"]["name"] for s in specs] == ["news"]           # ferramenta do servidor Anthropic fica de fora


def test_images_become_user_message_only_with_vision():
    h = [tool_msg([{"id": "c", "name": "screen_look", "content": [{"type": "text", "text": "tela"}, IMG]}])]
    with_v = to_openai(h, vision=True)
    assert with_v[0]["content"] == "tela" and with_v[1]["role"] == "user"
    assert with_v[1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,AAA"
    no_v = to_openai(h, vision=False)
    assert len(no_v) == 1 and "imagem omitida" in no_v[0]["content"]


def test_openai_complete_request_and_parsing(monkeypatch):
    monkeypatch.setenv("P_KEY", "segredo")
    http = Http(Resp(data=ok_data(tool_calls=[{"id": "t9", "type": "function",
                                               "function": {"name": "news", "arguments": '{"topic": "selic"}'}}])))
    p = oa(http, headers={"X-Title": "AASIS"})
    r = p.complete({"id": "vendor/m", "tools": True}, "ST", "VO", HIST, TOOLS, 77)
    method, url, kw = http.calls[0]
    assert (method, url) == ("POST", "http://h/v1/chat/completions")
    body = kw["json"]
    assert body["model"] == "vendor/m" and body["max_tokens"] == 77 and body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "ST\n\nVO"} and len(body["tools"]) == 1
    assert kw["headers"]["Authorization"] == "Bearer segredo" and kw["headers"]["X-Title"] == "AASIS"
    assert r.tool_calls[0].args == {"topic": "selic"} and r.stop == "tool_use"
    assert r.usage.input == 11 and r.usage.output == 3 and r.provider == "p"


def test_openai_without_tool_support_sends_no_tools_and_dict_args():
    http = Http(Resp(data=ok_data()))
    r = oa(http).complete({"id": "m", "tools": False}, "S", "", HIST, TOOLS, 10)
    assert "tools" not in http.calls[0][2]["json"] and r.text == "olá" and r.stop == "end"
    http = Http(Resp(data=ok_data(tool_calls=[{"id": "1", "function": {"name": "news", "arguments": {"a": 1}}}])))
    assert oa(http).complete({"id": "m"}, "S", "", HIST, TOOLS, 10).tool_calls[0].args == {"a": 1}


@pytest.mark.parametrize("finish,stop", [("length", "max_tokens"), ("content_filter", "refusal"), ("stop", "end")])
def test_openai_finish_reasons(finish, stop):
    d = ok_data()
    d["choices"][0]["finish_reason"] = finish
    assert oa(Http(Resp(data=d))).complete({"id": "m"}, "S", "", HIST, [], 10).stop == stop


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "server"),
                                         (503, "server"), (400, "bad_request"), (404, "bad_request")])
def test_openai_http_errors_are_typed(status, kind):
    with pytest.raises(ProviderError) as e:
        oa(Http(Resp(status, text="x"))).complete({"id": "m"}, "S", "", HIST, [], 10)
    assert e.value.kind == kind


def test_openai_network_errors_and_malformed_bodies():
    for exc, kind in ((requests.Timeout("t"), "timeout"), (requests.ConnectionError("c"), "network")):
        with pytest.raises(ProviderError) as e:
            oa(Http(exc=exc)).complete({"id": "m"}, "S", "", HIST, [], 10)
        assert e.value.kind == kind
    for bad in ({}, {"choices": []}, {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "n", "arguments": "{nao-json"}}]}}]}):
        with pytest.raises(ProviderError) as e:
            oa(Http(Resp(data=bad))).complete({"id": "m"}, "S", "", HIST, [], 10)
        assert e.value.kind == "bad_response"


def test_openai_list_models_and_no_key_header(monkeypatch):
    monkeypatch.delenv("P_KEY", raising=False)
    http = Http(Resp(data={"data": [{"id": "b"}, {"id": "a"}]}))
    p = oa(http)
    assert p.list_models() == ["a", "b"] and "Authorization" not in http.calls[0][2]["headers"]


# ---------- Anthropic ----------
def test_to_anthropic_replays_raw_and_converts_foreign_messages():
    raw_blocks = [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}, {"type": "text", "text": "r"}]
    h = [user_msg("q"), assistant_msg(LLMResponse(text="r", raw=raw_blocks, provider="anthropic")), HIST[1], HIST[2],
         assistant_msg(LLMResponse(text="", provider="deepseek"))]
    out = to_anthropic(h)
    assert out[1]["content"] is raw_blocks                                  # reenviado exatamente (busca criptografada)
    assert out[2]["content"] == [{"type": "text", "text": "vou ver"},
                                 {"type": "tool_use", "id": "c1", "name": "news", "input": {"topic": "x"}}]
    assert out[3]["content"][0] == {"type": "tool_result", "tool_use_id": "c1", "content": "manchete"}
    assert out[4]["content"] == [{"type": "text", "text": "(sem resposta)"}]    # a API rejeita conteúdo vazio


class Client:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.kw, self.messages = resp, exc, None, self

    def create(self, **kw):
        self.kw = kw
        if self.exc:
            raise self.exc
        return self.resp


def a_resp(content, stop="end_turn", **usage):
    return NS(content=content, stop_reason=stop, usage=NS(input_tokens=5, output_tokens=2, **usage))


def test_anthropic_request_shape_cache_effort_and_parse():
    cites = [NS(url="https://a", title="A"), NS(url="https://a", title="A2"), NS(url="https://b", title=None)]
    resp = a_resp([NS(type="text", text="oi", citations=cites), NS(type="tool_use", id="t1", name="news", input={"x": 1})],
                  stop="tool_use", cache_creation_input_tokens=100, cache_read_input_tokens=50,
                  server_tool_use=NS(web_search_requests=2))
    c = Client(resp)
    p = AnthropicProvider("anthropic", {}, client=c)
    r = p.complete({"id": "claude-x", "effort": "low"}, "STABLE", "VOLATILE", [user_msg("q")], TOOLS, 99)
    kw = c.kw
    assert kw["system"][0] == {"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}}
    assert kw["system"][1] == {"type": "text", "text": "VOLATILE"} and "cache_control" not in kw["system"][1]
    assert kw["output_config"] == {"effort": "low"} and kw["tools"] == TOOLS and kw["max_tokens"] == 99
    assert r.stop == "tool_use" and r.tool_calls[0].args == {"x": 1} and r.usage.searches == 2
    assert r.usage.total == 5 + 2 + 100 + 50 and [s["url"] for s in r.sources] == ["https://a", "https://b"]
    assert r.raw == resp.content


def test_anthropic_omits_optional_params_and_maps_stops():
    c = Client(a_resp([NS(type="text", text="x")], stop="refusal"))
    r = AnthropicProvider("anthropic", {}, client=c).complete({"id": "m"}, "S", "", [user_msg("q")], [], 9)
    assert "tools" not in c.kw and "output_config" not in c.kw and len(c.kw["system"]) == 1 and r.stop == "refusal"
    assert AnthropicProvider("a", {}, client=Client(a_resp([], stop="pause_turn"))).complete(
        {"id": "m"}, "S", "", [user_msg("q")], [], 9).stop == "pause"


def _resp(code):
    return httpx.Response(code, request=httpx.Request("POST", "http://x"))


@pytest.mark.parametrize("exc,kind", [
    (anthropic.RateLimitError("r", response=_resp(429), body=None), "rate_limit"),
    (anthropic.AuthenticationError("a", response=_resp(401), body=None), "auth"),
    (anthropic.PermissionDeniedError("a", response=_resp(403), body=None), "auth"),
    (anthropic.BadRequestError("web search is not enabled", response=_resp(400), body=None), "web_search_disabled"),
    (anthropic.BadRequestError("outro", response=_resp(400), body=None), "bad_request"),
    (anthropic.InternalServerError("s", response=_resp(500), body=None), "server"),
    (anthropic.APITimeoutError(httpx.Request("POST", "http://x")), "timeout"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")), "network"),
])
def test_anthropic_errors_are_typed(exc, kind):
    with pytest.raises(ProviderError) as e:
        AnthropicProvider("anthropic", {}, client=Client(exc=exc)).complete({"id": "m"}, "S", "", [user_msg("q")], [], 9)
    assert e.value.kind == kind
