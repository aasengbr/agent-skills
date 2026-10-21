import socket
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aasis import world  # noqa: E402
from aasis.world import World, check_public, html_to_text  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")


class Resp:
    def __init__(self, content=b"", status=200, headers=None, data=None):
        self.content, self.status_code, self.headers, self._data = content, status, headers or {}, data
        self.text = content.decode() if isinstance(content, bytes) else content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data

    def iter_content(self, n):
        yield self.content


class Http:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        for key, resp in self.routes.items():
            if key in url:
                return resp(url) if callable(resp) else resp
        raise AssertionError(f"rota inesperada: {url}")


def public(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0))])


# ---------- anti-SSRF ----------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "ftp://example.com/", "file:///etc/passwd", "http://example.com:8080/", "javascript:alert(1)", "http:///x",
])
def test_blocks_internal_and_odd_urls(url):
    with pytest.raises(ValueError):
        check_public(url)


def test_hostname_resolving_to_private_is_blocked(monkeypatch):
    public(monkeypatch, "10.1.2.3")
    with pytest.raises(ValueError):
        check_public("https://evil.example.com/")


def test_redirect_to_internal_is_blocked(monkeypatch):
    public(monkeypatch)
    http = Http({"example.com": Resp(status=302, headers={"Location": "http://169.254.169.254/x"})})
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("169.254.169.254" if "169" in host else "93.184.216.34", 0))])
    with pytest.raises(ValueError):
        world.safe_get(http, "https://example.com/")
    assert len(http.calls) == 1 and http.calls[0][1]["allow_redirects"] is False


def test_web_fetch_extracts_text_and_wraps(monkeypatch):
    public(monkeypatch)
    page = b"<html><head><title>x</title><script>EVIL()</script></head><body><h1>Ol\xc3\xa1</h1><style>p{}</style><p>mundo</p></body></html>"
    w = World(Http({"example.com": Resp(page, headers={"Content-Type": "text/html"})}), SP)
    out = w.web_fetch("https://example.com/")
    assert "<web_page untrusted>" in out and "Olá mundo" in out and "EVIL" not in out and "p{}" not in out
    w = World(Http({"example.com": Resp(b"%PDF", headers={"Content-Type": "application/pdf"})}), SP)
    assert "não suportado" in w.web_fetch("https://example.com/a.pdf")


def test_web_fetch_size_cap(monkeypatch):
    public(monkeypatch)
    big = Resp(b"a" * (world.MAX_BYTES * 3), headers={"Content-Type": "text/plain"})
    _, data = world.safe_get(Http({"example.com": big}), "https://example.com/")
    assert len(data) == world.MAX_BYTES


def test_html_to_text():
    assert html_to_text("<div>a<script>x</script> <b>b</b></div>") == "a b"


# ---------- fontes (formatos simulados; NÃO validam os serviços reais) ----------
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Selic sobe</title><link>https://n.example/1</link><pubDate>Tue, 21 Oct 2026 10:00:00 GMT</pubDate><source url="x">Valor</source></item>
<item><title>Dolar cai</title><link>https://n.example/2</link><pubDate>Tue, 21 Oct 2026 11:00:00 GMT</pubDate><source url="y">Folha</source></item>
</channel></rss>"""


def test_news_parse_limit_and_xxe_rejection():
    w = World(Http({"news.google.com": Resp(RSS)}), SP)
    out = w.news("selic", max_items=1)
    assert "<news untrusted>" in out and "Selic sobe | Valor" in out and "Dolar" not in out
    evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    with pytest.raises(ValueError):
        World(Http({"news.google.com": Resp(evil)}), SP).news("x")


def test_fx_rates_parse_and_validation():
    data = {"USDBRL": {"code": "USD", "codein": "BRL", "bid": "5.39", "ask": "5.40", "pctChange": "0.1",
                       "create_date": "2026-10-21 10:00:00"}}
    http = Http({"awesomeapi": Resp(data=data)})
    out = World(http, SP).fx_rates("usd-brl")
    assert "USD/BRL: compra 5.39 | venda 5.40" in out and http.calls[0][0].endswith("/last/USD-BRL")
    for bad in ("USD-BRL;rm", "../x", "", "USDBRL"):
        with pytest.raises(ValueError):
            World(http, SP).fx_rates(bad)


def test_stock_quote_and_not_found():
    ok = "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-10-21,22:00:00,1,3,0.5,2,100\n"
    nf = "Symbol,Date,Time,Open,High,Low,Close,Volume\nXXX,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
    assert "fechamento 2 em 2026-10-21" in World(Http({"stooq": Resp(ok)}), SP).stock_quote("aapl.us")
    assert "não encontrado" in World(Http({"stooq": Resp(nf)}), SP).stock_quote("xxx")
    with pytest.raises(ValueError):
        World(Http({}), SP).stock_quote("a b;c")


def test_bcb_partial_failure():
    def route(url):
        if "sgs.433" in url:
            return Resp(status=500)
        return Resp(data=[{"data": "21/10/2026", "valor": "10.50"}])
    out = World(Http({"bcdata.sgs": route}), SP).bcb_indicators()
    assert "Meta Selic (% a.a.): 10.50 (ref. 21/10/2026)" in out and "IPCA no mês (%): indisponível" in out


def test_weather_and_city_not_found():
    geo = {"results": [{"name": "Lisboa", "latitude": 38.7, "longitude": -9.1, "admin1": "Lisboa", "country": "Portugal"}]}
    fc = {"daily": {"time": ["2026-10-22"], "temperature_2m_max": [22], "temperature_2m_min": [15],
                    "precipitation_probability_max": [30]}}
    http = Http({"geocoding": Resp(data=geo), "forecast": Resp(data=fc)})
    out = World(http, SP).weather("Lisboa", days=99)
    assert "Lisboa" in out and "15°C a 22°C, chuva 30%" in out
    assert http.calls[1][1]["params"]["forecast_days"] == 7                       # limitado a 7
    assert "não encontrada" in World(Http({"geocoding": Resp(data={})}), SP).weather("Xyzzy")


def test_world_time():
    out = World(Http({}), SP).world_time("Europe/Lisbon")
    assert out.startswith("Europe/Lisbon:") and "h em relação ao seu fuso" in out
    with pytest.raises(ValueError):
        World(Http({}), SP).world_time("Mars/Olympus")
