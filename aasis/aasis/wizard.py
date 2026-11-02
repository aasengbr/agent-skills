"""Assistente de configuração: `python -m aasis.wizard`.

Pergunta as chaves com a digitação oculta, grava o `.env` no formato certo (sem espaços nem comentário na linha),
descobre o seu chat_id do Telegram e roda o diagnóstico online. Nada é impresso nem enviado a ninguém além da API
do Telegram (para validar o token). Pode ser repetido: o que já está definido é mantido, a menos que você peça para trocar.
"""
import getpass
import os
import re
import sys
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9_\-:./]+$")  # sem espaço, aspas nem '#': cobre sk-ant-..., 123:ABC e https://...


def is_set(v: str | None) -> bool:
    return bool(v) and "..." not in v  # o modelo env.example traz 'sk-ant-...' como exemplo


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)=(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Atualiza (ou acrescenta) CHAVE=valor preservando o resto do arquivo; troca também linhas comentadas '# CHAVE='."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for key, val in updates.items():
        pat = re.compile(rf"^\s*#?\s*{re.escape(key)}=.*$")
        idx = next((i for i, l in enumerate(lines) if re.match(rf"^\s*{re.escape(key)}=", l)), None)
        if idx is None:
            idx = next((i for i, l in enumerate(lines) if pat.match(l)), None)
        if idx is None:
            lines.append(f"{key}={val}")
        else:
            lines[idx] = f"{key}={val}"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if os.name == "posix":
        path.chmod(0o600)


def ask_secret_value(label: str, ask_secret, out, validate=None, tries: int = 3) -> str:
    """Pergunta um valor sem mostrá-lo. Devolve '' se o usuário deixar em branco."""
    for _ in range(tries):
        v = ask_secret(f"{label} (digitação oculta; Enter para pular): ").strip()
        if not v:
            return ""
        if not SAFE.match(v):
            out("  Valor inválido: use só letras, números e - _ : . / (sem espaços, aspas nem #). Cole de novo.")
            continue
        if validate:
            err = validate(v)
            if err:
                out(f"  {err}")
                continue
        return v
    return ""


def main(argv=None, ask=input, ask_secret=getpass.getpass, http=None, env_path: Path | None = None, out=print) -> int:
    from .config import ROOT
    from .doctor import find_chat_ids, print_report, run_checks
    from .telegram import Telegram, TelegramError

    env_path = env_path or ROOT / ".env"
    example = ROOT / "env.example"
    if not env_path.exists():
        env_path.write_text(example.read_text(encoding="utf-8") if example.exists() else "", encoding="utf-8")
        if os.name == "posix":
            env_path.chmod(0o600)
    cur = read_env(env_path)
    out("Assistente de configuração do AASIS.\n"
        "Ao colar uma chave ou token, NADA aparece na tela (é proposital, por segurança): cole (botão direito ou Ctrl+V) e aperte Enter.\n")

    # 1) Chave da Anthropic (variável de ambiente do sistema também vale: ela tem prioridade sobre o .env)
    if not is_set(cur.get("ANTHROPIC_API_KEY")) and is_set(os.environ.get("ANTHROPIC_API_KEY")):
        out("ANTHROPIC_API_KEY já está definida no ambiente do sistema. Mantida (não será gravada no .env).")
    elif is_set(cur.get("ANTHROPIC_API_KEY")) and ask("ANTHROPIC_API_KEY já está definida. Trocar? (s/N) ").strip().lower() not in ("s", "sim"):
        out("  Mantida.")
    else:
        key = ask_secret_value("Chave da Anthropic (começa com sk-ant-)", ask_secret, out,
                               lambda v: None if v.startswith("sk-ant-") else "A chave da Anthropic começa com 'sk-ant-'. Confira e cole de novo.")
        if key:
            write_env(env_path, {"ANTHROPIC_API_KEY": key})
            cur["ANTHROPIC_API_KEY"] = key
            out("  Gravada.")
        elif not is_set(cur.get("ANTHROPIC_API_KEY")):
            out("  Sem chave da Anthropic o AASIS não consegue responder. Rode o assistente de novo quando tiver a chave.")

    # 2) Telegram
    http_obj = http
    if not is_set(cur.get("TELEGRAM_BOT_TOKEN")) and is_set(os.environ.get("TELEGRAM_BOT_TOKEN")):
        cur["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_BOT_TOKEN"]
        out("TELEGRAM_BOT_TOKEN já está definido no ambiente do sistema. Mantido.")
    elif is_set(cur.get("TELEGRAM_BOT_TOKEN")) and ask("TELEGRAM_BOT_TOKEN já está definido. Trocar? (s/N) ").strip().lower() not in ("s", "sim"):
        out("  Token mantido.")
    elif ask("\nConfigurar o Telegram agora (alertas de lembrete e remédio no celular)? (S/n) ").strip().lower() not in ("n", "nao", "não"):
        def check_token(v):
            try:
                Telegram(v, "0", http=http_obj)._call("getMe")
                return None
            except TelegramError as e:
                return f"O Telegram não aceitou este token ({e}). Confira no @BotFather e cole de novo."

        tok = ask_secret_value("Token do bot (do @BotFather)", ask_secret, out, check_token)
        if tok:
            write_env(env_path, {"TELEGRAM_BOT_TOKEN": tok})
            cur["TELEGRAM_BOT_TOKEN"] = tok
            out("  Token aceito e gravado.")

    token = cur.get("TELEGRAM_BOT_TOKEN", "")
    if is_set(token) and not is_set(cur.get("TELEGRAM_CHAT_ID")):
        for attempt in range(5):
            try:
                found = find_chat_ids(token, http=http_obj)
            except TelegramError as e:
                out(f"  Não consegui ler o Telegram ({e}). Confira se o AASIS não está rodando e tente de novo.")
                break
            if found:
                if len(found) == 1:
                    chosen = found[0]
                else:
                    for i, c in enumerate(found, 1):
                        out(f"  {i}) {c['name'] or 'sem nome'}" + (f" (@{c['username']})" if c["username"] else ""))
                    n = ask("Qual é você? Digite o número: ").strip()
                    chosen = found[int(n) - 1] if n.isdigit() and 1 <= int(n) <= len(found) else None
                    if not chosen:
                        out("  Número inválido.")
                        continue
                write_env(env_path, {"TELEGRAM_CHAT_ID": str(chosen["id"])})
                cur["TELEGRAM_CHAT_ID"] = str(chosen["id"])
                out(f"  Conversa encontrada: {chosen['name'] or 'sem nome'}. chat_id gravado.")
                break
            out("  Ainda não vi nenhuma mensagem. No Telegram, abra o seu bot e toque em Iniciar (ou envie 'oi').")
            if ask("  Depois de enviar, pressione Enter para tentar de novo (ou digite 'p' para pular): ").strip().lower() == "p":
                break

    # 3) Diagnóstico
    out("\nDiagnóstico (envia 1 mensagem de teste ao Telegram, se configurado):\n")
    from .config import load_config
    cfg = load_config()
    res = run_checks(cfg, env={**cur, **os.environ}, online=True, serve=False, http=http_obj, env_files=[env_path])
    code = print_report(res, out)
    if code == 0:
        out("\nPronto. Para iniciar o AASIS:  python -m aasis.main --text --hud")
    else:
        out("\nHá FALHAS acima. Corrija e rode o assistente de novo (ele mantém o que já está certo).")
    return code


if __name__ == "__main__":
    sys.exit(main())
