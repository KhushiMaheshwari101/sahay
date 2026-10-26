"""Sample profile data and versioned rule configuration."""
from __future__ import annotations
import json
from pathlib import Path
try:
    import yaml
except ImportError:
    yaml = None
ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / "data/mock_profiles.json").read_text(encoding="utf-8-sig"))
def load_rules() -> dict:
    path = ROOT / "config/rules.yaml"
    if yaml:
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    raise RuntimeError("PyYAML is required to read the rules configuration. Install requirements.txt")
RULES = load_rules()
