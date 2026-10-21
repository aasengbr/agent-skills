"""Ferramentas que o JARVIS pode acionar. Tudo é restrito por allowlist/workspace."""
import datetime as dt
import subprocess
from pathlib import Path

import psutil
import requests

from .config import ROOT
from .google_tools import SCHEMAS as GOOGLE_SCHEMAS
from .google_tools import Google
from .memory import Memory


class Tools:
    def __init__(self, cfg: dict, memory: Memory, google: Google | None = None):
        self.cfg = cfg
        self.memory = memory
        self.workspace = Path(cfg["safety"]["workspace"])
        self.apps = {k.lower(): v for k, v in cfg["safety"]["apps"].items()}
        self.confirm_tools = set(cfg["safety"]["confirm_tools"])
        google = google or Google(cfg, ROOT)
        self.google = google if google.available else None

    # ---------- segurança ----------
    def _safe_path(self, rel: str) -> Path:
        p = (self.workspace / rel).resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise PermissionError("Caminho fora do workspace permitido.")
        return p

    # ---------- implementações ----------
    def get_time(self) -> str:
        return dt.datetime.now().strftime("%A, %d/%m/%Y %H:%M")

    def system_status(self) -> str:
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        mem = psutil.virtual_memory()
        out = f"CPU {psutil.cpu_percent(interval=0.5)}%, RAM {mem.percent}%"
        if bat:
            out += f", bateria {bat.percent:.0f}%"
        return out

    def remember(self, text: str) -> str:
        return f"Memorizado (id {self.memory.remember(text)})."

    def recall(self, query: str = "") -> str:
        facts = self.memory.recall(query)
        return "\n".join(facts) or "Nada encontrado."

    def read_file(self, path: str) -> str:
        return self._safe_path(path).read_text(encoding="utf-8")[:8000]

    def write_file(self, path: str, content: str) -> str:
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Arquivo salvo: {p.name}"

    def list_files(self, path: str = ".") -> str:
        p = self._safe_path(path)
        return "\n".join(sorted(x.name for x in p.iterdir())) or "(vazio)"

    def open_app(self, name: str) -> str:
        cmd = self.apps.get(name.lower())
        if not cmd:
            return f"App '{name}' não está na allowlist: {', '.join(self.apps)}"
        subprocess.Popen(cmd, shell=True)  # comando vem só do config.yaml
        return f"Abrindo {name}."

    def web_fetch(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return "URL inválida."
        r = requests.get(url, timeout=15, headers={"User-Agent": "JARVIS/0.1"})
        r.raise_for_status()
        return r.text[:6000]

    # ---------- registro ----------
    def schemas(self) -> list[dict]:
        def tool(name, desc, props=None, required=None):
            return {
                "name": name,
                "description": desc,
                "input_schema": {
                    "type": "object",
                    "properties": props or {},
                    "required": required or [],
                },
            }

        s = {"type": "string"}
        base = [
            tool("get_time", "Data e hora atuais."),
            tool("system_status", "Uso de CPU, RAM e bateria do PC."),
            tool("remember", "Guarda um fato na memória de longo prazo.", {"text": s}, ["text"]),
            tool("recall", "Busca fatos memorizados.", {"query": s}),
            tool("read_file", "Lê arquivo do workspace.", {"path": s}, ["path"]),
            tool("write_file", "Grava arquivo no workspace.", {"path": s, "content": s}, ["path", "content"]),
            tool("list_files", "Lista arquivos do workspace.", {"path": s}),
            tool("open_app", "Abre um aplicativo permitido.", {"name": s}, ["name"]),
            tool("web_fetch", "Baixa o texto de uma URL http(s).", {"url": s}, ["url"]),
        ]
        if self.google:
            for name, desc, props, req in GOOGLE_SCHEMAS:
                base.append(tool(name, desc, {k: {"type": v} for k, v in props.items()}, req))
        return base

    def run(self, name: str, args: dict, confirm) -> str:
        if name not in {t["name"] for t in self.schemas()}:
            return f"Ferramenta desconhecida: {name}"
        fn = getattr(self, name, None) or getattr(self.google, name)
        if name in self.confirm_tools and not confirm(f"{name} {args}"):
            return "Ação negada pelo usuário."
        try:
            return str(fn(**args))
        except Exception as e:  # devolve o erro ao modelo em vez de quebrar o loop
            return f"Erro em {name}: {e}"
