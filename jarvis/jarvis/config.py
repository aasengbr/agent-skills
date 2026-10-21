import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Onde ficam banco, token e workspace. Na nuvem aponte para um disco persistente (JARVIS_DATA_DIR)."""
    d = Path(os.getenv("JARVIS_DATA_DIR") or ROOT)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config(path: Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))
    if os.getenv("JARVIS_MODEL"):
        cfg["assistant"]["model"] = os.environ["JARVIS_MODEL"]
    configured = os.getenv("JARVIS_DATA_DIR")
    ws = Path(os.path.expanduser(cfg["safety"]["workspace"])) if not configured else data_dir() / "workspace"
    ws = ws.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    cfg["safety"]["workspace"] = str(ws)
    return cfg
