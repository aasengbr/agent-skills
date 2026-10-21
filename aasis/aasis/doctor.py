"""Diagnóstico de implantação: `python -m aasis.doctor [--online] [--serve]`.

Confere configuração, segredos, banco, Telegram, provedores de IA e monitor ANTES de você confiar no sistema.
Sem --online só lê o ambiente (nada sai da máquina). Com --online fala com o Telegram (getMe e uma mensagem de
teste) e com o monitor externo. Nunca imprime chaves, token nem URL secreta.
Saída: OK / AVISO / FALHA por item; código de saída 1 se houver FALHA.
"""
import argparse
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

OK, WARN, FAIL = "OK", "AVISO", "FALHA"
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def run_checks(cfg: dict, env=None, online: bool = False, serve: bool = False, http=None, data: Path | None = None,
               env_files: list[Path] | None = None) -> list[tuple[str, str, str]]:
    env = os.environ if env is None else env
    out: list[tuple[str, str, str]] = []

    def add(level, name, detail=""):
        out.append((level, name, detail))

    if sys.version_info < (3, 10):
        add(FAIL, "Python", f"{sys.version_info.major}.{sys.version_info.minor}: é preciso 3.10 ou mais novo")
    else:
        add(OK, "Python", f"{sys.version_info.major}.{sys.version_info.minor}")

    # ---- dados e banco
    data = data or Path(env.get("AASIS_DATA_DIR") or Path(__file__).resolve().parent.parent)
    try:
        data.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data):
            pass
        add(OK, "Pasta de dados gravável", str(data))
    except OSError as e:
        add(FAIL, "Pasta de dados gravável", f"{data}: {type(e).__name__}")
    db = data / "aasis.db"
    if db.exists():
        try:
            c = sqlite3.connect(db, timeout=3)
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
            c.close()
            add(OK if ok == "ok" else FAIL, "Banco de dados", "íntegro" if ok == "ok" else f"integrity_check: {ok[:60]}")
        except sqlite3.Error as e:
            add(FAIL, "Banco de dados", type(e).__name__)
    else:
        add(OK, "Banco de dados", "ainda não existe (será criado no primeiro uso)")
    if serve and not (data / "backups").exists():
        add(WARN, "Backups", "a pasta ainda não existe; o primeiro backup sai em até 24 h depois de subir")
    try:
        ZoneInfo(cfg["assistant"].get("timezone", "America/Sao_Paulo"))
        add(OK, "Fuso horário", cfg["assistant"].get("timezone", "America/Sao_Paulo"))
    except Exception:
        add(FAIL, "Fuso horário", "inválido em assistant.timezone (use nome IANA, ex.: America/Sao_Paulo)")

    # ---- acesso ao HUD
    if serve:
        code = env.get("AASIS_HUD_CODE", "")
        add(OK if len(code) >= 20 else FAIL, "Código de acesso (AASIS_HUD_CODE)",
            f"{len(code)} caracteres" + ("" if len(code) >= 20 else "; modo nuvem exige 20 ou mais"))
        hosts = env.get("AASIS_ALLOWED_HOSTS") or env.get("AASIS_DOMAIN")
        add(OK if hosts else FAIL, "Domínio permitido", "definido" if hosts else "defina AASIS_DOMAIN / AASIS_ALLOWED_HOSTS")

    # ---- provedores de IA
    providers, usable, trusted_ok = cfg.get("llm", {}).get("providers", {}), [], False
    for name, p in providers.items():
        key_env = p.get("key_env", "ANTHROPIC_API_KEY" if p.get("type") == "anthropic" else "")
        if key_env and env.get(key_env):
            usable.append(name)
            trusted_ok |= p.get("trust") in ("trusted", "local")
    if usable:
        add(OK, "Provedores de IA com chave", ", ".join(usable))
    else:
        add(FAIL, "Provedores de IA com chave", "nenhuma chave definida (ex.: ANTHROPIC_API_KEY)")
    if usable and not trusted_ok:
        add(FAIL, "Provedor para dados sensíveis",
            "só há provedores externos: pedidos de saúde e finanças não teriam para onde ir (defina ANTHROPIC_API_KEY)")
    elif usable:
        add(OK, "Provedor para dados sensíveis", "há provedor confiável/local")
    add(WARN, "Teste real do modelo", "abra o HUD > Modelos > testar (este diagnóstico não gasta tokens)")

    # ---- Telegram
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
    tg = None
    if not token and not chat:
        add(WARN, "Telegram", "não configurado: lembretes e doses só chegam com o HUD aberto (não confiável para remédio)")
    elif not token or not chat:
        add(FAIL, "Telegram", "defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID juntos")
    elif not chat.lstrip("-").isdigit():
        add(FAIL, "Telegram", "TELEGRAM_CHAT_ID deve ser numérico (envie /start ao bot para ver o seu)")
    else:
        add(OK if TOKEN_RE.match(token) else WARN, "Telegram: formato do token",
            "ok" if TOKEN_RE.match(token) else "formato incomum (esperado 123456789:AA...); confira com o BotFather")
        if online:
            from .telegram import Telegram, TelegramError
            tg = Telegram(token, chat, http=http)
            try:
                me = tg._call("getMe")
                add(OK, "Telegram: token aceito", f"bot @{me.get('username', '?') if isinstance(me, dict) else '?'}")
                tg.send("✅ Teste do AASIS doctor: se você leu isto, o canal funciona.")
                add(OK, "Telegram: mensagem entregue ao seu chat", "confira no celular")
            except TelegramError as e:
                hint = " (abra o bot e envie /start primeiro)" if "400" in str(e) or "403" in str(e) else ""
                add(FAIL, "Telegram: online", str(e) + hint)
        else:
            add(WARN, "Telegram: online", "não testado (rode com --online)")

    # ---- monitor externo
    hb = env.get("HEALTHCHECK_URL", "").strip()
    if not hb:
        add(WARN, "Monitor externo (HEALTHCHECK_URL)", "ausente: se o servidor cair, ninguém avisa que não avisou")
    elif urllib.parse.urlsplit(hb).scheme != "https":
        add(FAIL, "Monitor externo (HEALTHCHECK_URL)", "precisa começar com https://")
    elif online:
        from .heartbeat import ping_once
        import requests
        ok = ping_once(hb, lambda: True, http or requests.Session())
        add(OK if ok else FAIL, "Monitor externo: ping", "enviado" if ok else "sem resposta (rede ou URL errada)")
    else:
        add(OK, "Monitor externo (HEALTHCHECK_URL)", "definido (https); ping não testado")

    # ---- remédios
    try:
        if db.exists():
            c = sqlite3.connect(db, timeout=3)
            n = c.execute("SELECT COUNT(*) FROM meds WHERE active = 1").fetchone()[0] if c.execute(
                "SELECT name FROM sqlite_master WHERE name = 'meds'").fetchone() else 0
            c.close()
            if n and not (token and chat):
                add(WARN, "Remédios", f"{n} cadastrado(s) sem Telegram: avisos só com o HUD aberto")
            elif n:
                add(OK, "Remédios", f"{n} cadastrado(s)")
    except sqlite3.Error:
        pass

    # ---- higiene de segredos
    for f in env_files or []:
        if f.exists() and os.name == "posix" and (f.stat().st_mode & 0o077):
            add(WARN, f"Permissão de {f.name}", f"{oct(f.stat().st_mode & 0o777)}: use chmod 600 (contém segredos)")
    if cfg.get("telegram", {}).get("detail", "full") == "full" and token:
        add(WARN, "Privacidade do Telegram", "detail=full: nome e dose vão ao Telegram (sem criptografia ponta a ponta); "
            "use telegram.detail: minimal se isso incomoda")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnóstico do AASIS")
    ap.add_argument("--online", action="store_true", help="testa Telegram e monitor de verdade (envia 1 mensagem de teste)")
    ap.add_argument("--serve", action="store_true", help="aplica as exigências do modo nuvem")
    args = ap.parse_args()
    from .config import ROOT, load_config
    cfg = load_config()
    res = run_checks(cfg, online=args.online, serve=args.serve,
                     env_files=[ROOT / ".env", ROOT / "deploy" / "vps" / ".env"])
    for level, name, detail in res:
        print(f"{level:<6} {name}" + (f" — {detail}" if detail else ""))
    bad = sum(1 for r in res if r[0] == FAIL)
    print(f"\n{sum(1 for r in res if r[0] == OK)} ok, {sum(1 for r in res if r[0] == WARN)} aviso(s), {bad} falha(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
