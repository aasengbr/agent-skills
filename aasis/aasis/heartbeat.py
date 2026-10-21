"""Monitoramento externo (dead-man's switch): avisa um serviço como healthchecks.io que o AASIS está vivo.

Se o servidor cair, ninguém dentro dele consegue avisar. Por isso o aviso vem de fora: o serviço espera um "ping" a
cada poucos minutos e alerta o usuário (e-mail/Telegram dele) quando os pings param. Com saúde ruim (agendador parado
ou Telegram sem responder) o ping vai para `<url>/fail`, que o serviço trata como falha imediata.
"""
import threading
import time
import urllib.parse

import requests


def start_heartbeat(url: str, healthy, interval: float = 300, http=None) -> threading.Thread | None:
    if urllib.parse.urlsplit(url).scheme != "https":
        return None  # só https: a URL carrega um identificador secreto
    http = http or requests.Session()

    def loop():
        while True:
            ping_once(url, healthy, http)
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def ping_once(url: str, healthy, http) -> bool:
    try:
        ok = bool(healthy())
    except Exception:
        ok = False
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        http.get(target, timeout=10)
    except Exception:
        return False  # sem rede: o próprio silêncio dispara o alerta do serviço
    return ok
