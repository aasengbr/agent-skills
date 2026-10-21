"""Informação do mundo: notícias, câmbio, ações, indicadores do BCB, clima, fuso. Fontes gratuitas, sem chave.

Tudo que vem de fora chega ao modelo marcado como não confiável. Páginas arbitrárias (web_fetch) passam por
uma checagem anti-SSRF: o servidor na nuvem nunca deve ser usado para acessar rede interna ou metadados.
"""
import csv
import datetime as dt
import io
import ipaddress
import re
import socket
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .google_tools import _wrap

UA = {"User-Agent": "AASIS/0.1"}
MAX_BYTES = 300_000


# ---------- acesso seguro a URLs arbitrárias ----------
def check_public(url: str) -> None:
    """Levanta ValueError se a URL não for http(s) público nas portas 80/443."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Só URLs http(s) são permitidas.")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("Porta não permitida.")
    for info in socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("Endereço interno/privado bloqueado.")


def safe_get(http, url: str, timeout: float = 15, max_redirects: int = 3):
    """GET com validação a cada redirecionamento e limite de tamanho. Retorna (content_type, bytes)."""
    for _ in range(max_redirects + 1):
        check_public(url)
        r = http.get(url, headers=UA, timeout=timeout, allow_redirects=False, stream=True)
        if 300 <= r.status_code < 400 and r.headers.get("Location"):
            url = urllib.parse.urljoin(url, r.headers["Location"])
            continue
        r.raise_for_status()
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) >= MAX_BYTES:
                break
        return r.headers.get("Content-Type", ""), data[:MAX_BYTES]
    raise ValueError("Redirecionamentos demais.")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts, self.depth = [], 0

    def handle_starttag(self, tag, attrs):
        self.depth += tag in self.SKIP

    def handle_endtag(self, tag):
        self.depth -= (tag in self.SKIP) and self.depth > 0

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


class World:
    def __init__(self, http, user_tz: ZoneInfo):
        self.http, self.tz = http, user_tz

    def _json(self, url: str, **params):
        r = self.http.get(url, params=params or None, headers=UA, timeout=15)
        r.raise_for_status()
        return r.json()

    # ---------- páginas ----------
    def web_fetch(self, url: str) -> str:
        ctype, data = safe_get(self.http, url)
        text = data.decode("utf-8", "replace")
        if "html" in ctype.lower() or text.lstrip().lower().startswith(("<!doctype", "<html")):
            text = html_to_text(text)
        elif not ctype.lower().startswith(("text/", "application/json", "application/xml")):
            return f"Tipo de conteúdo não suportado: {ctype or 'desconhecido'}"
        return _wrap("web_page", text[:6000])

    # ---------- notícias (Google News RSS) ----------
    def news(self, topic: str, country: str = "BR", max_items: int = 6) -> str:
        lang = "pt-BR" if country.upper() == "BR" else "en"
        q = urllib.parse.quote(topic[:120])
        url = (f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={country.upper()[:2]}"
               f"&ceid={country.upper()[:2]}:{lang.split('-')[0]}")
        r = self.http.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        raw = r.content[:MAX_BYTES]
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():  # bloqueia XXE e "billion laughs"
            raise ValueError("Feed rejeitado (DTD/entidades não são aceitos).")
        items = ET.fromstring(raw).findall("./channel/item")[: max(1, min(int(max_items), 10))]
        lines = []
        for it in items:
            src = it.find("source")
            lines.append(f"{(it.findtext('title') or '').strip()} | {(src.text if src is not None else '')} | "
                         f"{(it.findtext('pubDate') or '').strip()} | {(it.findtext('link') or '').strip()}")
        return _wrap("news", "\n".join(lines) or "Nenhuma notícia encontrada.")

    # ---------- câmbio e cripto (AwesomeAPI) ----------
    PAIR = re.compile(r"^[A-Z]{3,5}-[A-Z]{3,5}$")

    def fx_rates(self, pairs: str = "USD-BRL,EUR-BRL,BTC-BRL") -> str:
        wanted = [p.strip().upper() for p in pairs.split(",") if p.strip()][:8]
        if not wanted or not all(self.PAIR.match(p) for p in wanted):
            raise ValueError("Use pares como USD-BRL,EUR-BRL,BTC-BRL.")
        data = self._json(f"https://economia.awesomeapi.com.br/json/last/{','.join(wanted)}")
        lines = [f"{v['code']}/{v['codein']}: compra {v['bid']} | venda {v['ask']} | var. {v['pctChange']}% | "
                 f"{v['create_date']}" for v in data.values()]
        return _wrap("market_data", "\n".join(lines) or "Sem dados.")

    # ---------- ações (Stooq) ----------
    SYMBOL = re.compile(r"^[A-Za-z0-9.^_-]{1,15}$")

    def stock_quote(self, symbol: str) -> str:
        if not self.SYMBOL.match(symbol):
            raise ValueError("Símbolo inválido.")
        r = self.http.get("https://stooq.com/q/l/", params={"s": symbol.lower(), "f": "sd2t2ohlcv", "h": "", "e": "csv"},
                          headers=UA, timeout=15)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows or rows[0].get("Close") in (None, "N/D"):
            return "Símbolo não encontrado (EUA usam sufixo .us, ex.: aapl.us). Para a B3 use a busca na web."
        q = rows[0]
        return _wrap("market_data", f"{q['Symbol']}: fechamento {q['Close']} em {q['Date']} {q['Time']} | "
                     f"abertura {q['Open']} máx {q['High']} mín {q['Low']} vol {q['Volume']} (pode estar atrasado)")

    # ---------- indicadores do Banco Central (SGS) ----------
    BCB = (("Meta Selic (% a.a.)", 432), ("IPCA no mês (%)", 433), ("Dólar comercial, venda (R$)", 1))

    def bcb_indicators(self) -> str:
        lines = []
        for label, code in self.BCB:
            try:
                row = self._json(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/1", formato="json")[0]
                lines.append(f"{label}: {row['valor']} (ref. {row['data']})")
            except Exception as e:  # uma série fora do ar não derruba as outras
                lines.append(f"{label}: indisponível ({type(e).__name__})")
        return _wrap("market_data", "\n".join(lines))

    # ---------- clima (Open-Meteo) ----------
    def weather(self, city: str, days: int = 3) -> str:
        geo = self._json("https://geocoding-api.open-meteo.com/v1/search", name=city[:80], count=1, language="pt")
        res = (geo.get("results") or [None])[0]
        if not res:
            return f"Cidade não encontrada: {city}"
        n = max(1, min(int(days), 7))
        f = self._json("https://api.open-meteo.com/v1/forecast", latitude=res["latitude"], longitude=res["longitude"],
                       daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                       timezone="auto", forecast_days=n)["daily"]
        lines = [f"{d}: {lo}°C a {hi}°C, chuva {p}%" for d, hi, lo, p in zip(
            f["time"], f["temperature_2m_max"], f["temperature_2m_min"], f["precipitation_probability_max"])]
        where = f"{res['name']}, {res.get('admin1', '')}, {res.get('country', '')}".replace(", ,", ",")
        return _wrap("weather", f"{where}\n" + "\n".join(lines))

    # ---------- fuso horário (local, sem rede) ----------
    def world_time(self, timezone: str) -> str:
        try:
            z = ZoneInfo(timezone)
        except Exception:
            raise ValueError("Fuso inválido. Use o nome IANA, ex.: Europe/Lisbon, Asia/Tokyo.")
        now = dt.datetime.now(z)
        diff = (now.utcoffset() - dt.datetime.now(self.tz).utcoffset()).total_seconds() / 3600
        return f"{timezone}: {now:%d/%m/%Y %H:%M} (UTC{now:%z}); {diff:+g} h em relação ao seu fuso."
