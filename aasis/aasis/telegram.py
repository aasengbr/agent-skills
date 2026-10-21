"""Canal Telegram (bot, long polling): alertas confiáveis no celular e conversa com o AASIS.

Segurança:
- Só o chat privado do dono (TELEGRAM_CHAT_ID) é atendido. Qualquer outro é ignorado; o único retorno é o id do chat
  em resposta a /start, para o dono saber o que configurar (no máximo 1 resposta por hora por chat).
- O token do bot vai na URL da API: nenhuma exceção ou log deste módulo o repete.
- Confirmações de ações usam um código de uso único e só valem para o dono.
- Mensagens de bots do Telegram NÃO têm criptografia ponta a ponta: o Telegram vê o conteúdo. Para dados de saúde
  existe `telegram.detail: minimal` (veja docs/telegram-meds.md).
"""
import os
import secrets
import threading
import time
from pathlib import Path

import requests

API = "https://api.telegram.org"
MAX_MSG = 4000          # limite do Telegram é 4096
MAX_IN = 2000           # texto recebido além disso é cortado
CONFIRM_TIMEOUT = 120


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: int | str, http=None, on_text=None, on_action=None,
                 state_file: Path | None = None, api: str = API, poll_timeout: int = 25):
        if not token or not str(chat_id).lstrip("-").isdigit():
            raise ValueError("Telegram exige token e chat_id numérico.")
        self.token, self.chat_id = token, int(chat_id)
        self.http = http or requests.Session()
        self.poll_http = http or requests.Session()  # sessão própria: o long polling não trava os envios
        self.on_text, self.on_action = on_text, on_action
        self.state_file, self.api, self.poll_timeout = state_file, api, poll_timeout
        self.offset = self._load_offset()
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._unknown_replied: dict[int, float] = {}
        self._stop = threading.Event()
        self.last_ok: float = 0.0
        self.errors = 0
        self.last_error = ""

    # ---------- API ----------
    def _call(self, method: str, http=None, timeout: float = 15, **params) -> dict | list:
        try:
            r = (http or self.http).post(f"{self.api}/bot{self.token}/{method}", json=params, timeout=timeout)
            data = r.json()
        except Exception as e:  # a mensagem de requests traz a URL (com o token): só o tipo sai daqui
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if not isinstance(data, dict) or not data.get("ok"):
            code = data.get("error_code") if isinstance(data, dict) else r.status_code
            desc = str(data.get("description", ""))[:120] if isinstance(data, dict) else ""
            raise TelegramError(f"{method}: {code} {desc}".strip())
        return data["result"]

    @staticmethod
    def _markup(buttons) -> dict | None:
        if not buttons:
            return None
        return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None, chat_id: int | None = None) -> int:
        """Envia ao dono (ou a `chat_id`). Levanta TelegramError se falhar. `buttons`: linhas de (rótulo, dado)."""
        text = text.strip() or "(vazio)"
        chunks = [text[i:i + MAX_MSG] for i in range(0, len(text), MAX_MSG)]
        mid = 0
        for i, c in enumerate(chunks):
            params = {"chat_id": chat_id or self.chat_id, "text": c, "disable_web_page_preview": True}
            if buttons and i == len(chunks) - 1:
                params["reply_markup"] = self._markup(buttons)
            mid = self._call("sendMessage", **params)["message_id"]
        self.last_ok = time.time()
        return mid

    def confirm(self, action: str, timeout: float = CONFIRM_TIMEOUT) -> bool:
        """Pede Sim/Não ao dono e espera. Sem resposta no prazo = não."""
        tok = secrets.token_hex(6)
        slot = {"event": threading.Event(), "answer": False}
        with self._lock:
            self._pending[tok] = slot
        try:
            mid = self.send(f"🔐 Confirmar ação?\n{action[:1500]}", [[("✅ Sim", f"c:y:{tok}"), ("❌ Não", f"c:n:{tok}")]])
        except TelegramError:
            with self._lock:
                self._pending.pop(tok, None)
            return False
        ok = slot["event"].wait(timeout) and slot["answer"]
        with self._lock:
            self._pending.pop(tok, None)
        if not slot["event"].is_set():  # expirou: tira os botões para não sobrar um "Sim" esquecido
            self._edit(mid, f"⌛ Expirou sem resposta (negado):\n{action[:1500]}")
        return ok

    def _edit(self, message_id: int, text: str) -> None:
        try:
            self._call("editMessageText", chat_id=self.chat_id, message_id=message_id, text=text[:MAX_MSG])
        except TelegramError:
            pass  # a edição é cosmética

    # ---------- recepção ----------
    def _load_offset(self) -> int:
        try:
            return int(self.state_file.read_text()) if self.state_file else 0
        except (OSError, ValueError):
            return 0

    def _save_offset(self) -> None:
        if not self.state_file:
            return
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(str(self.offset))
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def run_once(self, timeout: int | None = None) -> int:
        """Um ciclo de getUpdates. Retorna quantas atualizações processou."""
        t = self.poll_timeout if timeout is None else timeout
        ups = self._get_updates(t)
        for u in ups:
            self.offset = max(self.offset, int(u["update_id"]) + 1)
            try:
                self._handle(u)
            except Exception:  # uma atualização ruim não pode travar a fila
                self.errors += 1
                self.last_error = "handler"
            self._save_offset()
        self.last_ok = time.time()
        return len(ups)

    def _get_updates(self, t: int) -> list:
        # "timeout" é parâmetro do Telegram (espera longa) e também do requests: aqui vão separados
        try:
            r = self.poll_http.post(f"{self.api}/bot{self.token}/getUpdates", timeout=t + 10,
                                    json={"offset": self.offset, "timeout": t, "allowed_updates": ["message", "callback_query"]})
            data = r.json()
        except Exception as e:
            raise TelegramError(f"getUpdates: {type(e).__name__}") from None
        if not isinstance(data, dict) or not data.get("ok"):
            code = data.get("error_code") if isinstance(data, dict) else r.status_code
            raise TelegramError(f"getUpdates: {code}")
        return data["result"]

    def _handle(self, u: dict) -> None:
        if "callback_query" in u:
            return self._handle_callback(u["callback_query"])
        m = u.get("message") or {}
        chat, frm = m.get("chat") or {}, m.get("from") or {}
        if chat.get("type") != "private" or chat.get("id") != self.chat_id or frm.get("id") != self.chat_id:
            return self._unknown(chat, m.get("text") or "")
        text = (m.get("text") or "").strip()
        if not text:
            self._safe_send("Por enquanto só entendo mensagens de texto.")
            return
        if text.split()[0].lower() in ("/start", "/ajuda", "/help"):
            self._safe_send("AASIS online. Escreva normalmente: lembretes, agenda, remédios ('tomei o losartana'), etc.")
            return
        if self.on_text:
            threading.Thread(target=self._text_worker, args=(text[:MAX_IN],), daemon=True).start()

    def _text_worker(self, text: str) -> None:
        try:
            reply = self.on_text(text)
        except Exception as e:
            reply = f"Falha ao processar ({type(e).__name__})."
        if reply:
            self._safe_send(reply)

    def _safe_send(self, text: str) -> None:
        try:
            self.send(text)
        except TelegramError:
            self.errors += 1
            self.last_error = "send"

    def _unknown(self, chat: dict, text: str) -> None:
        """Chat que não é o dono: ignora; só /start recebe o id (para configurar), limitado a 1 por hora."""
        cid = chat.get("id")
        if chat.get("type") != "private" or not isinstance(cid, int) or not text.strip().lower().startswith("/start"):
            return
        now = time.time()
        if now - self._unknown_replied.get(cid, 0) < 3600:
            return
        self._unknown_replied[cid] = now
        try:
            self.send(f"Seu chat_id é {cid}. Se você é o dono do AASIS, coloque-o em TELEGRAM_CHAT_ID.", chat_id=cid)
        except TelegramError:
            pass

    def _handle_callback(self, cq: dict) -> None:
        msg = cq.get("message") or {}
        if (cq.get("from") or {}).get("id") != self.chat_id or (msg.get("chat") or {}).get("id") != self.chat_id:
            return
        data = str(cq.get("data", ""))
        try:
            self._call("answerCallbackQuery", callback_query_id=cq.get("id"))
        except TelegramError:
            pass
        if data.startswith("c:"):
            _, ans, tok = (data.split(":", 2) + ["", ""])[:3]
            with self._lock:
                slot = self._pending.get(tok)
            if slot and not slot["event"].is_set():
                slot["answer"] = ans == "y"
                slot["event"].set()
                self._edit(msg.get("message_id", 0), ("✅ Confirmado" if ans == "y" else "❌ Negado") + ":\n"
                           + str(msg.get("text", ""))[:1500].split("\n", 1)[-1])
            return
        if self.on_action:
            try:
                result = self.on_action(data)
            except Exception as e:
                result = f"Falha ({type(e).__name__})."
            if result:
                self._edit(msg.get("message_id", 0), f"{msg.get('text', '')}\n→ {result}")

    # ---------- ciclo de vida ----------
    def start(self) -> None:
        def loop():
            fails = 0
            while not self._stop.is_set():
                try:
                    self.run_once()
                    fails = 0
                except TelegramError as e:
                    fails += 1
                    self.errors += 1
                    self.last_error = str(e)[:80]
                    # 409: outra instância usa o mesmo bot (getUpdates); espera mais para não brigar com ela
                    self._stop.wait(30 if "409" in str(e) else min(60, 2 ** min(fails, 6)))
                except Exception:
                    fails += 1
                    self._stop.wait(min(60, 2 ** min(fails, 6)))

        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"last_ok": self.last_ok, "errors": self.errors, "last_error": self.last_error}


def from_env(cfg: dict, data_dir: Path, **kw) -> "Telegram | None":
    """Cria o canal se TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID estiverem definidos (e telegram.enabled != false)."""
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if cfg.get("telegram", {}).get("enabled", True) is False or not token or not chat:
        return None
    # TELEGRAM_API_BASE existe só para os testes ponta a ponta (servidor falso); em produção fica o padrão
    return Telegram(token, chat, state_file=data_dir / "telegram_offset", api=os.getenv("TELEGRAM_API_BASE", API), **kw)
