"""Ferramentas que o AASIS pode acionar. Tudo é restrito por allowlist/workspace."""
import datetime as dt
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import psutil
import requests

from .config import data_dir
from .google_tools import SCHEMAS as GOOGLE_SCHEMAS
from .google_tools import Google, _wrap
from .home import SCHEMAS as HOME_SCHEMAS
from .home import Home
from .memory import Memory
from .vision import Screen


class Tools:
    def __init__(self, cfg: dict, memory: Memory, google: Google | None = None, screen: Screen | None = None,
                 home: Home | None = None):
        self.cfg = cfg
        self.memory = memory
        self.workspace = Path(cfg["safety"]["workspace"])
        self.apps = {k.lower(): v for k, v in cfg["safety"]["apps"].items()}
        self.confirm_tools = set(cfg["safety"]["confirm_tools"])
        self.tz = ZoneInfo(cfg.get("assistant", {}).get("timezone", "America/Sao_Paulo"))
        self.cloud = bool(cfg.get("runtime", {}).get("cloud"))  # na nuvem: sem ferramentas do PC
        self.google = google or Google(cfg, data_dir())  # disponibilidade checada a cada uso (login pelo HUD)
        self.screen = screen or Screen(cfg)
        home = home or Home(cfg)
        self.home = home if home.available else None

    # ---------- segurança ----------
    def _safe_path(self, rel: str) -> Path:
        p = (self.workspace / rel).resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise PermissionError("Caminho fora do workspace permitido.")
        return p

    # ---------- implementações ----------
    WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz)

    def now_str(self) -> str:
        n = self.now()
        return f"{self.WEEKDAYS[n.weekday()]}-feira, {n:%d/%m/%Y %H:%M}" if n.weekday() < 5 else \
            f"{self.WEEKDAYS[n.weekday()]}, {n:%d/%m/%Y %H:%M}"

    def get_time(self) -> str:
        return f"{self.now_str()} ({self.tz.key})"

    # ---------- tarefas ----------
    def task_add(self, text: str, due: str = "") -> str:
        if due:
            dt.date.fromisoformat(due)  # valida AAAA-MM-DD
        return f"Tarefa {self.memory.add_task(text, due)} criada."

    def task_list(self, show_done: bool = False) -> str:
        rows = self.memory.list_tasks(bool(show_done))
        lines = [f"{t['id']} | {'[x] ' if t['done'] else ''}{t['text']}" + (f" | prazo {t['due']}" if t["due"] else "")
                 for t in rows]
        return _wrap("tasks", "\n".join(lines) or "Nenhuma tarefa pendente.")

    def task_done(self, id: int) -> str:
        return "Tarefa concluída." if self.memory.set_task_done(int(id)) else "Tarefa não encontrada."

    def task_remove(self, id: int) -> str:
        return "Tarefa removida." if self.memory.remove_task(int(id)) else "Tarefa não encontrada."

    # ---------- lembretes ----------
    UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

    def reminder_add(self, text: str, when: str = "", in_minutes: int = 0) -> str:
        """when: ISO 8601 no fuso do usuário (ex.: 2026-10-22T09:00:00); ou in_minutes."""
        now = self.now()
        if in_minutes:
            due = now + dt.timedelta(minutes=int(in_minutes))
        elif when:
            due = dt.datetime.fromisoformat(when)
            due = due.replace(tzinfo=self.tz) if due.tzinfo is None else due
        else:
            raise ValueError("Informe 'when' (ISO 8601) ou 'in_minutes'.")
        if due <= now:
            raise ValueError("Esse horário já passou.")
        if len(self.memory.pending_reminders()) >= 100:
            raise ValueError("Limite de 100 lembretes pendentes.")
        rid = self.memory.add_reminder(text, due.astimezone(dt.timezone.utc).strftime(self.UTC_FMT))
        return f"Lembrete {rid} agendado para {due.astimezone(self.tz):%d/%m %H:%M}: {text}"

    def reminder_list(self) -> str:
        lines = [f"{r['id']} | {dt.datetime.strptime(r['due_utc'], self.UTC_FMT).replace(tzinfo=dt.timezone.utc).astimezone(self.tz):%d/%m %H:%M} | {r['text']}"
                 for r in self.memory.pending_reminders()]
        return _wrap("reminders", "\n".join(lines) or "Nenhum lembrete pendente.")

    def reminder_cancel(self, id: int) -> str:
        return "Lembrete cancelado." if self.memory.cancel_reminder(int(id)) else "Lembrete não encontrado."

    # ---------- resumo do dia ----------
    def daily_brief(self) -> str:
        parts = [f"Agora: {self.now_str()}"]
        if self.google.available:
            for label, call in (
                ("Agenda (próximas 24h)", lambda: self.google.calendar_list(1)),
                ("E-mails não lidos recentes", lambda: self.google.gmail_search("is:unread newer_than:2d", 5)),
            ):
                try:
                    parts.append(f"## {label}\n{call()}")
                except Exception as e:  # uma seção com falha não derruba o resumo
                    parts.append(f"## {label}\n(indisponível: {type(e).__name__})")
        else:
            parts.append("## Google\n(não conectado: agenda e e-mail fora do resumo)")
        parts.append("## Tarefas pendentes\n" + self.task_list())
        parts.append("## Lembretes pendentes\n" + self.reminder_list())
        return "\n\n".join(parts)

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

    def screen_look(self) -> list[dict]:
        return self.screen.look()

    def web_fetch(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return "URL inválida."
        r = requests.get(url, timeout=15, headers={"User-Agent": "AASIS/0.1"})
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
        i = {"type": "integer"}
        base = [
            tool("get_time", "Data e hora atuais."),
            tool("system_status", "Uso de CPU, RAM e bateria do PC."),
            tool("remember", "Guarda um fato na memória de longo prazo.", {"text": s}, ["text"]),
            tool("recall", "Busca fatos memorizados.", {"query": s}),
            tool("read_file", "Lê arquivo do workspace.", {"path": s}, ["path"]),
            tool("write_file", "Grava arquivo no workspace.", {"path": s, "content": s}, ["path", "content"]),
            tool("list_files", "Lista arquivos do workspace.", {"path": s}),
            tool("open_app", "Abre um aplicativo permitido.", {"name": s}, ["name"]),
            tool("daily_brief", "Resumo do dia: agenda, e-mails não lidos, tarefas e lembretes."),
            tool("task_add", "Cria tarefa. due opcional AAAA-MM-DD.", {"text": s, "due": s}, ["text"]),
            tool("task_list", "Lista tarefas pendentes.", {"show_done": {"type": "boolean"}}),
            tool("task_done", "Conclui tarefa pelo id.", {"id": i}, ["id"]),
            tool("task_remove", "Remove tarefa pelo id.", {"id": i}, ["id"]),
            tool("reminder_add", "Agenda lembrete. Use in_minutes ou when (ISO 8601 no fuso do usuário, "
                 "ex.: 2026-10-22T09:00:00).", {"text": s, "when": s, "in_minutes": i}, ["text"]),
            tool("reminder_list", "Lista lembretes pendentes."),
            tool("reminder_cancel", "Cancela lembrete pelo id.", {"id": i}, ["id"]),
            tool("web_fetch", "Baixa o texto de uma URL http(s).", {"url": s}, ["url"]),
        ]
        if self.cloud:
            base = [t for t in base if t["name"] not in {"open_app", "system_status"}]
        if self.screen.enabled and not self.cloud:
            base.append(tool("screen_look", "Captura e analisa a tela do usuário (somente visão)."))
        if self.home:
            for name, desc, props, req in HOME_SCHEMAS:
                base.append(tool(name, desc, props, req))
        if self.google.available:
            for name, desc, props, req in GOOGLE_SCHEMAS:
                base.append(tool(name, desc, {k: {"type": v} for k, v in props.items()}, req))
        return base

    def run(self, name: str, args: dict, confirm) -> str | list:
        out, status = self._run(name, args, confirm)
        note = out if status != "ok" and isinstance(out, str) else ""
        self.memory.log_action(name, args, status, note)
        return out

    def _run(self, name: str, args: dict, confirm) -> tuple[str | list, str]:
        if name not in {t["name"] for t in self.schemas()}:
            return f"Ferramenta desconhecida: {name}", "desconhecida"
        fn = getattr(self, name, None) or getattr(self.google, name, None) or getattr(self.home, name)
        needs = name in self.confirm_tools or (name == "ha_call" and self.home.needs_confirm(args))
        if needs and not confirm(f"{name} {args}"):
            return "Ação negada pelo usuário.", "negado"
        try:
            out = fn(**args)
            return (out if isinstance(out, list) else str(out)), "ok"
        except Exception as e:  # devolve o erro ao modelo em vez de quebrar o loop
            return f"Erro em {name}: {e}", "erro"
