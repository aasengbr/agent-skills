"""Comandos diretos do Telegram para remédios, SEM IA: /remedio, /remedios, /tomei, /estoque, /remover, /ajuda.

Por que existem: o dado de saúde não precisa passar por nenhum modelo de linguagem para ser cadastrado. Tudo aqui é
determinístico (nada é enviado a provedores de IA), funciona mesmo sem chave de IA, e o cadastro pede confirmação
mostrando exatamente o que foi entendido, para que um erro de digitação em dose não passe despercebido.
"""
import datetime as dt

from .meds import DAYS, _norm, fmt_stock

HELP = """Comandos de remédio (não usam IA; os dados ficam só no seu AASIS):

/remedio Nome Dose HH:MM[,HH:MM] [opções]
  ex.: /remedio Losartana 50mg 08:00,20:00 estoque=30
  Use _ no lugar de espaço: /remedio Vitamina_D 1000UI 09:00
  Opções: estoque=30  dias=seg,qua,sex  inicio=2026-12-01  fim=2026-12-31  por_dose=2
/remedios            lista os remédios e as doses de hoje
/tomei Nome          registra a dose tomada (também aceita o número)
/estoque Nome 12     atualiza o estoque
/remover Nome        para os avisos (o histórico fica)

Dose e horários são guardados exatamente como você escrever. O AASIS não sugere dose nem horário."""

OPTION_KEYS = {"estoque": "stock", "dias": "days", "inicio": "start", "fim": "end", "por_dose": "per_dose", "pordose": "per_dose"}
USAGE_ADD = "Uso: /remedio Nome Dose HH:MM[,HH:MM] [estoque=30] [dias=seg,qua] [inicio=AAAA-MM-DD] [fim=AAAA-MM-DD] [por_dose=1]\nEx.: /remedio Losartana 50mg 08:00,20:00 estoque=30"


def _num(text: str, what: str) -> float:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        raise ValueError(f"{what}: '{text}' não é um número.") from None


def _days_text(days: list[int]) -> str:
    return "todos os dias" if not days else ",".join(DAYS[d] for d in days)


def _when(due: dt.datetime, now: dt.datetime) -> str:
    d, today = due.date(), now.date()
    hh = f"{due:%H:%M}"
    if d == today:
        return f"hoje às {hh}"
    if d == today + dt.timedelta(days=1):
        return f"amanhã às {hh}"
    return f"{DAYS[d.weekday()]} {d:%d/%m} às {hh}"


def next_dose(meds, med: dict, now: dt.datetime) -> dt.datetime | None:
    """Próximo horário de aviso (no fuso do usuário), respeitando dias da semana e período; None se já acabou."""
    first = now.astimezone(meds.tz).date()
    for off in range(0, 15):
        d = first + dt.timedelta(days=off)
        iso = d.isoformat()
        if (med["start"] and iso < med["start"]) or (med["end"] and iso > med["end"]):
            continue
        if med["days"] and d.weekday() not in med["days"]:
            continue
        for t in med["times"]:
            due = meds._due_utc(d, t)
            if due > now:
                return due.astimezone(meds.tz)
    return None


def _parse_add(args: list[str]) -> dict:
    if len(args) < 3:
        raise ValueError(USAGE_ADD)
    name, dose = args[0].replace("_", " "), args[1].replace("_", " ")
    times: list[str] = []
    opts: dict[str, str] = {}
    last = None
    for tok in args[2:]:
        if "=" in tok:
            k, _, v = tok.partition("=")
            key = OPTION_KEYS.get(_norm(k).replace("-", "_"))
            if not key or not v:
                raise ValueError(f"Opção inválida: '{tok}'.\n{USAGE_ADD}")
            opts[key], last = v, key
        elif last:                      # "dias=seg, qua" (vírgula seguida de espaço) continua a opção anterior
            opts[last] += "," + tok
        else:
            times.append(tok)           # "08:00, 20:00" ou "08:00 20:00": tudo antes da 1ª opção são horários
    return {"name": name, "dose": dose, "times": ",".join(t.strip(",") for t in times if t.strip(",")), **opts}


def _preview(v: dict) -> str:
    lines = ["Cadastrar este remédio?", f"Nome: {v['name']}", f"Dose: {v['dose']}", f"Horários: {', '.join(v['times'])}",
             f"Dias: {_days_text(v['days'])}"]
    if v["start"] or v["end"]:
        lines.append(f"Período: {v['start'] or '…'} a {v['end'] or '…'}")
    if v["stock"] is not None:
        lines.append(f"Estoque: {fmt_stock(v['stock'])} (usa {fmt_stock(v['per_dose'])} por dose)")
    return "\n".join(lines)


def handle(text: str, meds, confirm, audit=None, now: dt.datetime | None = None) -> str:
    """Executa um comando de texto (começa com '/'). Devolve a resposta. `confirm(texto) -> bool` pergunta Sim/Não."""
    now = now or dt.datetime.now(dt.timezone.utc)
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0].lstrip("/")
    args = parts[1:]

    def done(status: str, reply: str) -> str:
        if audit:
            audit(f"cmd_{cmd}", status)       # nome do comando e resultado; nunca o conteúdo
        return reply

    try:
        if cmd in ("ajuda", "help"):
            return HELP
        if cmd == "remedio":
            if not args:
                return USAGE_ADD + "\n\n/ajuda mostra todos os comandos."
            raw = _parse_add(args)
            v = meds.validate(raw["name"], raw["dose"], raw["times"], raw.get("days", ""), raw.get("start", ""),
                              raw.get("end", ""), _num(raw["stock"], "Estoque") if "stock" in raw else None,
                              _num(raw["per_dose"], "Por dose") if "per_dose" in raw else 1)
            if not confirm(_preview(v)):
                return done("negado", "Cancelado: nada foi cadastrado.")
            mid = meds.add(v["name"], v["dose"], ",".join(v["times"]), ",".join(DAYS[d] for d in v["days"]),
                           v["start"] or "", v["end"] or "", v["stock"], v["per_dose"], now=now)
            med = meds.get(mid)
            nxt = next_dose(meds, med, now)
            return done("ok", f"✅ Cadastrado: {meds.describe(med)}\n" +
                        (f"Primeiro aviso: {_when(nxt, now.astimezone(meds.tz))}." if nxt else "Sem próximo horário (período encerrado)."))
        if cmd == "remedios":
            lines = [meds.describe(m) for m in meds.active_meds()] or ["Nenhum remédio cadastrado."]
            today = meds.today(now)
            if today:
                lines.append("\nHoje:")
                lines += [f"{dt.datetime.strptime(d['due_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).astimezone(meds.tz):%H:%M} "
                          f"{d['name']} — {meds._state_pt(d['state'])}" for d in today]
            return done("ok", "\n".join(lines))
        if cmd == "tomei":
            if not args:
                return "Uso: /tomei Nome (ou o número do remédio). Veja /remedios."
            med, err = meds.resolve(" ".join(args))
            return done("erro" if err else "ok", err or meds.taken_for(med, now))
        if cmd == "estoque":
            if len(args) < 2:
                return "Uso: /estoque Nome 12 (quantas unidades você tem agora)."
            qty = _num(args[-1], "Estoque")
            med, err = meds.resolve(" ".join(args[:-1]))
            if err:
                return done("erro", err)
            meds.set_stock(med["id"], qty)
            left = meds.days_left(meds.get(med["id"]))
            return done("ok", f"Estoque de {med['name']}: {fmt_stock(qty)}" + (f" (dura cerca de {left:.0f} dia(s))." if left not in (None, float('inf')) else "."))
        if cmd == "remover":
            if not args:
                return "Uso: /remover Nome (ou o número). Veja /remedios."
            med, err = meds.resolve(" ".join(args))
            if err:
                return done("erro", err)
            if not confirm(f"Remover este remédio e parar os avisos?\n{meds.describe(med)}"):
                return done("negado", "Cancelado: o remédio continua cadastrado.")
            meds.remove(med["id"])
            return done("ok", f"Removido: {med['name']} (o histórico de doses foi mantido).")
    except ValueError as e:
        return done("erro", f"⚠️ {e}")
    return "Comando desconhecido. Envie /ajuda."


COMMANDS = {"ajuda", "help", "remedio", "remedios", "tomei", "estoque", "remover"}
