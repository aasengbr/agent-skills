import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="script bash: só faz sentido em Linux")

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "vps" / "post-install.sh"
SECRET_KEY, SECRET_CODE = "sk-ant-MUITO-SECRETA", "uma frase longa e secreta 12345"


def run(tmp, **env):
    e = {**os.environ, "AASIS_DRY_RUN": "1", "AASIS_APP_DIR": str(Path(tmp) / "app"), **env}
    for k in ("AASIS_DOMAIN", "ANTHROPIC_API_KEY", "AASIS_HUD_CODE"):
        if k not in env:
            e.pop(k, None)
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=e, timeout=30)
    return r, Path(tmp) / "app" / "aasis" / "deploy" / "vps" / ".env"


def test_syntax_and_executable():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert os.access(SCRIPT, os.X_OK)


def test_full_flow_order_firewall_before_enable_and_no_secret_leak():
    with tempfile.TemporaryDirectory() as tmp:
        r, envf = run(tmp, AASIS_DOMAIN="aasis.exemplo.com", ANTHROPIC_API_KEY=SECRET_KEY, AASIS_HUD_CODE=SECRET_CODE)
        assert r.returncode == 0, r.stderr
        out = [l for l in r.stdout.splitlines() if l.startswith("+ ")]
        idx = lambda s: next(i for i, l in enumerate(out) if s in l)  # noqa: E731
        assert idx("ufw allow 22/tcp") < idx("ufw --force enable")                    # nunca tranca o SSH
        assert idx("ufw allow 443/tcp") < idx("ufw --force enable") and idx("ufw default deny incoming") < idx("ufw --force enable")
        assert idx("git clone") < idx("docker compose up") and idx("apt-get update") < idx("git clone")
        assert SECRET_KEY not in r.stdout + r.stderr and SECRET_CODE not in r.stdout + r.stderr   # segredo nunca impresso
        assert oct(os.stat(envf).st_mode & 0o777) == "0o600"
        body = envf.read_text(encoding="utf-8")
        assert f"ANTHROPIC_API_KEY={SECRET_KEY}" in body and "AASIS_DOMAIN=aasis.exemplo.com" in body
        assert "https://aasis.exemplo.com" in r.stdout


def test_without_secrets_it_stops_before_starting_and_tells_what_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        r, envf = run(tmp)
        assert r.returncode == 0 and "docker compose up" not in "\n".join(l for l in r.stdout.splitlines() if l.startswith("+ "))
        assert "AASIS_DOMAIN" in r.stdout and "ANTHROPIC_API_KEY" in r.stdout and "AASIS_HUD_CODE" in r.stdout


def test_short_access_code_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = run(tmp, AASIS_DOMAIN="a.b.com", ANTHROPIC_API_KEY="k", AASIS_HUD_CODE="curto123")
        assert "20+caracteres" in r.stdout and "docker compose up" not in "\n".join(
            l for l in r.stdout.splitlines() if l.startswith("+ "))


def test_existing_env_is_never_overwritten_and_missing_key_does_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        envf = Path(tmp) / "app" / "aasis" / "deploy" / "vps" / ".env"
        envf.parent.mkdir(parents=True)
        envf.write_text("AASIS_DOMAIN=meu.dominio.com\n", encoding="utf-8")                     # faltam duas chaves
        r, _ = run(tmp, AASIS_DOMAIN="outro.com", ANTHROPIC_API_KEY="novo", AASIS_HUD_CODE=SECRET_CODE)
        assert r.returncode == 0 and "já existe" in r.stdout
        assert envf.read_text(encoding="utf-8") == "AASIS_DOMAIN=meu.dominio.com\n"            # intacto
        assert "ANTHROPIC_API_KEY" in r.stdout and "AASIS_HUD_CODE" in r.stdout                # aponta o que falta


def test_non_root_without_dry_run_is_refused():
    if os.getuid() == 0:
        return  # o teste só faz sentido fora de root
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env={**os.environ, "AASIS_DRY_RUN": "0"})
    assert r.returncode == 1 and "root" in r.stdout


def test_partial_config_never_prints_the_secrets_it_did_receive():
    with tempfile.TemporaryDirectory() as tmp:
        r, envf = run(tmp, ANTHROPIC_API_KEY=SECRET_KEY, AASIS_HUD_CODE=SECRET_CODE)          # falta só o domínio
        assert "AASIS_DOMAIN" in r.stdout and "ANTHROPIC_API_KEY" not in r.stdout.split("Falta preencher")[1]
        assert SECRET_KEY not in r.stdout + r.stderr and SECRET_CODE not in r.stdout + r.stderr
