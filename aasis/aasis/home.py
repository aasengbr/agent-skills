"""Casa inteligente via Home Assistant (API REST). Controle por faixas de risco definidas no config."""
import os
import re

import requests

NAME_RE = re.compile(r"^[a-z_]+$")
STRIPPED = {"entity_id", "device_id", "area_id"}  # o alvo é sempre o entity_id validado


def _wrap(text: str) -> str:
    return f"<home untrusted>\n{text}\n</home>"


class Home:
    def __init__(self, cfg: dict, http=None):
        h = cfg.get("home_assistant", {})
        self.url = (h.get("url") or os.getenv("HA_URL") or "").rstrip("/")
        self.token = os.getenv("HA_TOKEN", "")
        self.auto = set(h.get("auto_domains", []))        # executa direto
        self.confirm = set(h.get("confirm_domains", []))  # pede confirmação
        # qualquer outro domínio (lock, alarm_control_panel, ...) é invisível e bloqueado
        self.http = http or requests.Session()

    @property
    def available(self) -> bool:
        return bool(self.url.startswith(("http://", "https://")) and self.token)

    def tier(self, domain: str) -> str:
        return "auto" if domain in self.auto else "confirm" if domain in self.confirm else "blocked"

    def _req(self, method: str, path: str, **kw):
        r = self.http.request(method, f"{self.url}/api{path}", timeout=10,
                              headers={"Authorization": f"Bearer {self.token}"}, **kw)
        r.raise_for_status()
        return r.json()

    # ---------- ferramentas ----------
    def ha_list(self, domain: str = "") -> str:
        lines = []
        for s in self._req("GET", "/states"):
            d = s["entity_id"].split(".")[0]
            if self.tier(d) == "blocked" or (domain and d != domain):
                continue
            name = s.get("attributes", {}).get("friendly_name", "")
            lines.append(f"{s['entity_id']} | {name} | {s['state']}")
        return _wrap("\n".join(lines[:80]) or "Nenhum dispositivo.")

    def ha_state(self, entity_id: str) -> str:
        if self.tier(entity_id.split(".")[0]) == "blocked":
            return "Domínio não permitido."
        s = self._req("GET", f"/states/{entity_id}")
        attrs = {k: v for k, v in s.get("attributes", {}).items() if k in
                 {"friendly_name", "brightness", "temperature", "current_temperature", "hvac_mode", "volume_level"}}
        return _wrap(f"{entity_id}: {s['state']} {attrs}")

    def needs_confirm(self, args: dict) -> bool:
        return self.tier(str(args.get("domain", ""))) != "auto"

    def ha_call(self, domain: str, service: str, entity_id: str, data: dict | None = None) -> str:
        if not (NAME_RE.match(domain) and NAME_RE.match(service)):
            return "Domínio ou serviço inválido."
        if self.tier(domain) == "blocked":
            return f"Domínio '{domain}' bloqueado por segurança."
        if entity_id.split(".")[0] != domain:
            return "O entity_id não pertence ao domínio informado."
        body = {k: v for k, v in (data or {}).items() if k not in STRIPPED}
        body["entity_id"] = entity_id
        self._req("POST", f"/services/{domain}/{service}", json=body)
        return f"Executado: {domain}.{service} em {entity_id}."


SCHEMAS = [
    ("ha_list", "Lista dispositivos da casa (luzes, tomadas, clima...) com estado atual.",
     {"domain": {"type": "string"}}, []),
    ("ha_state", "Estado detalhado de um dispositivo pelo entity_id.",
     {"entity_id": {"type": "string"}}, ["entity_id"]),
    ("ha_call", "Aciona um serviço do Home Assistant, ex.: domain=light, service=turn_on, entity_id=light.sala, "
                "data={'brightness_pct': 50}. Use ha_list antes para descobrir o entity_id.",
     {"domain": {"type": "string"}, "service": {"type": "string"}, "entity_id": {"type": "string"},
      "data": {"type": "object"}}, ["domain", "service", "entity_id"]),
]
