"""Rendement: CAGR over meerdere perioden."""
from __future__ import annotations

from typing import Optional

import pandas as pd

TOLERANTIE_DAGEN = 10


def cagr(historie: pd.DataFrame, jaren: int) -> Optional[float]:
    """Samengesteld jaarrendement in % over de laatste `jaren` jaar.

    Geeft None als de historie de periode niet (vrijwel) volledig dekt.
    """
    if historie is None or len(historie) < 2:
        return None
    eind_datum = historie["datum"].iloc[-1]
    start_doel = (pd.Timestamp(eind_datum) - pd.DateOffset(years=jaren)).date()
    if (historie["datum"].iloc[0] - start_doel).days > TOLERANTIE_DAGEN:
        return None
    # Eerste koers op of na de startdatum; als er een gat zit, de laatste ervoor.
    voor = historie[historie["datum"] <= start_doel]
    start = voor.iloc[-1] if not voor.empty else historie.iloc[0]
    eind = historie.iloc[-1]
    if start["koers"] <= 0 or eind["koers"] <= 0:
        return None
    return ((eind["koers"] / start["koers"]) ** (1 / jaren) - 1) * 100


def cagr_overzicht(historie: pd.DataFrame, horizons=(1, 3, 5, 10)) -> dict[int, Optional[float]]:
    return {j: cagr(historie, j) for j in horizons}
