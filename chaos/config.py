"""Config loading and shared paths."""

from pathlib import Path

import yaml

CHAOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = CHAOS_DIR.parent
DATA_DIR = CHAOS_DIR / "data"
CONFIG_PATH = CHAOS_DIR / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def allowed_links(cfg: dict) -> list[dict]:
    """Links that may be targeted; every one has a Toxiproxy proxy."""
    return list(cfg["limits"]["allowed_links"])


def allowed_link_names(cfg: dict) -> list[str]:
    return [link["name"] for link in allowed_links(cfg)]
