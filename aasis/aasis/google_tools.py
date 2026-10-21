"""Gmail (leitura + rascunho) e Google Agenda. OAuth local; nada é enviado a terceiros além do Google."""
import base64
import datetime as dt
import json
import os
import secrets
import time
from email.message import EmailMessage
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # usado só para criar rascunhos; o código nunca envia
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",  # escrita só em arquivos criados pelo AASIS
]


def load_creds(token: Path):
    """Credenciais salvas, renovadas se preciso. Nunca abre navegador. None se não houver/inválidas."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.has_scopes(SCOPES):  # token antigo sem os escopos atuais: reautoriza
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds if creds.valid else None


def authorize(credentials: Path, token: Path):
    """Fluxo OAuth local (abre o navegador na 1ª vez; só para uso no PC) e devolve as credenciais."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = load_creds(token)
    if not creds:
        creds = InstalledAppFlow.from_client_secrets_file(str(credentials), SCOPES).run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


class WebAuth:
    """Login Google pelo navegador (nuvem/celular). Credencial Web em GOOGLE_CLIENT_JSON ou credentials.json."""

    TTL = 600

    def __init__(self, credentials: Path, token: Path):
        self.credentials, self.token = credentials, token
        self._flows: dict[str, tuple[float, object]] = {}

    def _config(self) -> dict | None:
        raw = os.getenv("GOOGLE_CLIENT_JSON")
        if raw:
            return json.loads(raw)
        return json.loads(self.credentials.read_text(encoding="utf-8")) if self.credentials.exists() else None

    @property
    def configured(self) -> bool:
        return self._config() is not None

    @property
    def connected(self) -> bool:
        try:
            return load_creds(self.token) is not None
        except Exception:
            return False

    def start(self, redirect_uri: str) -> str:
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(self._config(), SCOPES, redirect_uri=redirect_uri)
        state = secrets.token_urlsafe(24)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
        now = time.time()
        self._flows = {k: v for k, v in self._flows.items() if now - v[0] < self.TTL}
        self._flows[state] = (now, flow)
        return url

    def finish(self, state: str, code: str) -> bool:
        """Uso único: o state só existe se um usuário autenticado iniciou o fluxo."""
        item = self._flows.pop(state, None)
        if not item or time.time() - item[0] > self.TTL:
            return False
        item[1].fetch_token(code=code)
        self.token.write_text(item[1].credentials.to_json(), encoding="utf-8")
        return True


def _wrap(kind: str, text: str) -> str:
    """Conteúdo de terceiros é dado, não instrução (defesa contra prompt injection)."""
    return f"<{kind} untrusted>\n{text}\n</{kind}>"


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload: dict) -> str:
    """Primeiro text/plain encontrado (percorre multipart)."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "===").decode("utf-8", "replace")
    for part in payload.get("parts", []):
        text = _body_text(part)
        if text:
            return text
    return ""


class Google:
    def __init__(self, cfg: dict, root: Path, services: dict | None = None):
        g = cfg.get("google", {})
        self.cloud = bool(cfg.get("runtime", {}).get("cloud"))  # na nuvem nunca abre navegador no servidor
        self.tz = g.get("timezone", "America/Sao_Paulo")
        self.credentials = root / g.get("credentials_file", "credentials.json")
        self.token = root / g.get("token_file", "token.json")
        self._services = services or {}

    @property
    def available(self) -> bool:
        if self._services:
            return True
        return self.token.exists() if self.cloud else (self.credentials.exists() or self.token.exists())

    def _svc(self, name: str, version: str):
        if name not in self._services:
            from googleapiclient.discovery import build

            if self.cloud:
                creds = load_creds(self.token)
                if not creds:
                    raise RuntimeError("Google não autorizado: use o botão 'Conectar Google' no HUD.")
            else:
                creds = authorize(self.credentials, self.token)
            self._services[name] = build(name, version, credentials=creds)
        return self._services[name]

    # ---------- Gmail ----------
    def gmail_search(self, query: str = "is:unread", max_results: int = 5) -> str:
        gm = self._svc("gmail", "v1").users().messages()
        ids = gm.list(userId="me", q=query, maxResults=min(int(max_results), 10)).execute().get("messages", [])
        lines = []
        for m in ids:
            msg = gm.get(userId="me", id=m["id"], format="metadata",
                         metadataHeaders=["From", "Subject", "Date"]).execute()
            p = msg["payload"]
            lines.append(f"id={m['id']} | {_header(p, 'From')} | {_header(p, 'Subject')} | {_header(p, 'Date')}")
        return _wrap("email_list", "\n".join(lines) or "Nenhuma mensagem.")

    def gmail_read(self, message_id: str) -> str:
        msg = self._svc("gmail", "v1").users().messages().get(userId="me", id=message_id, format="full").execute()
        p = msg["payload"]
        body = (_body_text(p) or msg.get("snippet", ""))[:4000]
        return _wrap("email", f"De: {_header(p, 'From')}\nAssunto: {_header(p, 'Subject')}\n\n{body}")

    def gmail_draft(self, to: str, subject: str, body: str) -> str:
        m = EmailMessage()
        m["To"], m["Subject"] = to, subject
        m.set_content(body)
        raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
        d = self._svc("gmail", "v1").users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return f"Rascunho criado (id {d['id']}). Não foi enviado; revise no Gmail."

    # ---------- Agenda ----------
    def calendar_list(self, days: int = 1) -> str:
        now = dt.datetime.now(dt.timezone.utc)
        ev = self._svc("calendar", "v3").events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=(now + dt.timedelta(days=min(int(days), 30))).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute().get("items", [])
        lines = [
            f"id={e['id']} | {e['start'].get('dateTime', e['start'].get('date'))} | {e.get('summary', '(sem título)')}"
            for e in ev
        ]
        return _wrap("calendar", "\n".join(lines) or "Agenda livre no período.")

    def calendar_create(self, summary: str, start: str, end: str, description: str = "") -> str:
        """start/end em ISO 8601 sem fuso (ex.: 2026-10-22T14:00:00); o fuso vem do config."""
        dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)  # valida o formato
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start, "timeZone": self.tz},
            "end": {"dateTime": end, "timeZone": self.tz},
        }
        e = self._svc("calendar", "v3").events().insert(calendarId="primary", body=body).execute()
        return f"Evento criado (id {e['id']}): {summary}, {start}"

    # ---------- Drive ----------
    EXPORT = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    def drive_search(self, query: str, max_results: int = 8) -> str:
        q = query.replace("\\", "\\\\").replace("'", "\\'")
        files = self._svc("drive", "v3").files().list(
            q=f"(name contains '{q}' or fullText contains '{q}') and trashed = false",
            pageSize=min(int(max_results), 20),
            fields="files(id,name,mimeType,modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute().get("files", [])
        lines = [f"id={f['id']} | {f['name']} | {f['mimeType']} | {f['modifiedTime']}" for f in files]
        return _wrap("drive_list", "\n".join(lines) or "Nada encontrado.")

    def drive_read(self, file_id: str) -> str:
        files = self._svc("drive", "v3").files()
        meta = files.get(fileId=file_id, fields="name,mimeType").execute()
        mime = meta["mimeType"]
        if mime in self.EXPORT:
            data = files.export(fileId=file_id, mimeType=self.EXPORT[mime]).execute()
        elif mime.startswith("text/") or mime == "application/json":
            data = files.get_media(fileId=file_id).execute()
        else:
            return f"Tipo não suportado para leitura: {mime}"
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        return _wrap("drive_file", f"Nome: {meta['name']}\n\n{text[:6000]}")

    def drive_create_doc(self, name: str, content: str) -> str:
        from googleapiclient.http import MediaIoBaseUpload
        import io

        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
        f = self._svc("drive", "v3").files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.document"},
            media_body=media,
            fields="id,webViewLink",
        ).execute()
        return f"Documento criado (id {f['id']}): {name}"


SCHEMAS = [
    ("drive_search", "Busca arquivos no Google Drive por nome ou conteúdo.",
     {"query": "string", "max_results": "integer"}, ["query"]),
    ("drive_read", "Lê o texto de um arquivo do Drive (Docs, Planilhas, Apresentações, texto) pelo id.",
     {"file_id": "string"}, ["file_id"]),
    ("drive_create_doc", "Cria um Google Doc novo com o texto dado.",
     {"name": "string", "content": "string"}, ["name", "content"]),
    ("gmail_search", "Busca e-mails (sintaxe do Gmail, ex.: is:unread, from:x). Retorna remetente, assunto e id.",
     {"query": "string", "max_results": "integer"}, []),
    ("gmail_read", "Lê o corpo de um e-mail pelo id.", {"message_id": "string"}, ["message_id"]),
    ("gmail_draft", "Cria RASCUNHO de e-mail (nunca envia).",
     {"to": "string", "subject": "string", "body": "string"}, ["to", "subject", "body"]),
    ("calendar_list", "Lista eventos dos próximos N dias.", {"days": "integer"}, []),
    ("calendar_create", "Cria evento. start/end em ISO 8601 sem fuso, ex.: 2026-10-22T14:00:00.",
     {"summary": "string", "start": "string", "end": "string", "description": "string"},
     ["summary", "start", "end"]),
]


if __name__ == "__main__":  # python -m aasis.google_tools  -> autoriza a conta Google (1ª vez)
    from .config import ROOT, load_config

    g = Google(load_config(), ROOT)
    authorize(g.credentials, g.token)
    print("Autorizado. token.json salvo.")
