"""Risico: volatiliteit, maximaal verlies en Sharpe ratio."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

MIN_RENDEMENTEN = 20


def periodes_per_jaar(historie: pd.DataFrame) -> Optional[float]:
    """Gemiddeld aantal koersen per jaar, afgeleid uit de data (dag-, week- of maandkoersen)."""
    if historie is None or len(historie) < 2:
        return None
    dagen = (historie["datum"].iloc[-1] - historie["datum"].iloc[0]).days
    if dagen <= 0:
        return None
    return (len(historie) - 1) / (dagen / 365.25)


def log_rendementen(historie: pd.DataFrame) -> np.ndarray:
    koers = historie["koers"].to_numpy(dtype=float)
    if len(koers) < 2 or (koers <= 0).any():
        return np.array([])
    return np.diff(np.log(koers))


def jaarstatistiek(historie: pd.DataFrame) -> Optional[tuple[float, float]]:
    """(gemiddeld log-jaarrendement, jaarvolatiliteit) als fracties. None bij te weinig data."""
    r = log_rendementen(historie)
    ppj = periodes_per_jaar(historie)
    if len(r) < MIN_RENDEMENTEN or ppj is None:
        return None
    return float(r.mean() * ppj), float(r.std(ddof=1) * np.sqrt(ppj))


def volatiliteit(historie: pd.DataFrame) -> Optional[float]:
    """Jaarlijkse volatiliteit in %."""
    s = jaarstatistiek(historie)
    return s[1] * 100 if s else None


def max_drawdown(historie: pd.DataFrame) -> Optional[float]:
    """Maximaal verlies van top naar dal in % (negatief getal)."""
    if historie is None or len(historie) < 2:
        return None
    koers = historie["koers"].to_numpy(dtype=float)
    top = np.maximum.accumulate(koers)
    return float((koers / top - 1).min() * 100)


def sharpe(historie: pd.DataFrame, risicovrije_rente: float) -> Optional[float]:
    """Sharpe ratio: (gemiddeld jaarrendement - risicovrije rente) / volatiliteit."""
    s = jaarstatistiek(historie)
    if s is None or s[1] == 0:
        return None
    jaarrendement = np.exp(s[0]) - 1
    return float((jaarrendement - risicovrije_rente / 100) / s[1])
