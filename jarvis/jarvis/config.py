import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))
    if os.getenv("JARVIS_MODEL"):
        cfg["assistant"]["model"] = os.environ["JARVIS_MODEL"]
    ws = Path(os.path.expanduser(cfg["safety"]["workspace"])).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    cfg["safety"]["workspace"] = str(ws)
    return cfg
