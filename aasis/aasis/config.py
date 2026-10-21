import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Onde ficam banco, token e workspace. Na nuvem aponte para um disco persistente (AASIS_DATA_DIR)."""
    d = Path(os.getenv("AASIS_DATA_DIR") or ROOT)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config(path: Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    path = path or Path(os.getenv("AASIS_CONFIG") or ROOT / "config.yaml")  # config própria sem editar o repositório
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if os.getenv("AASIS_MODEL"):
        cfg["assistant"]["model"] = os.environ["AASIS_MODEL"]
    configured = os.getenv("AASIS_DATA_DIR")
    ws = Path(os.path.expanduser(cfg["safety"]["workspace"])) if not configured else data_dir() / "workspace"
    ws = ws.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    cfg["safety"]["workspace"] = str(ws)
    return cfg
