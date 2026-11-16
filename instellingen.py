"""Laadt config.yaml (aannames en gewichten)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BASIS = Path(__file__).resolve().parent
CONFIG_PAD = BASIS / "config.yaml"


def laad_config(pad: Path | str = CONFIG_PAD) -> dict[str, Any]:
    with open(pad, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    controleer_gewichten(config["gewichten"])
    return config


def controleer_gewichten(gewichten: dict[str, dict[str, float]]) -> None:
    for profiel, w in gewichten.items():
        totaal = sum(w.values())
        if abs(totaal - 1.0) > 1e-9:
            raise ValueError(f"Gewichten van profiel '{profiel}' tellen op tot {totaal}, niet tot 1.")
        if any(v < 0 for v in w.values()):
            raise ValueError(f"Profiel '{profiel}' bevat een negatief gewicht.")
