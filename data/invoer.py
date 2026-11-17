"""Import van de instrumentenlijst uit CSV."""
from __future__ import annotations

import pandas as pd
from pydantic import ValidationError

from data.bronnen import lees_csv
from data.modellen import Instrument

GETALVELDEN = [
    "koers_winst", "dividendrendement", "schuld_eigen_vermogen", "ter", "aantal_posities",
    "concentratie", "couponrente", "couponfrequentie", "koers", "ytm", "duration",
]
ALIASSEN = {"name": "naam", "currency": "valuta", "soort": "type", "symbol": "symbool",
            "coupon": "couponrente", "yield": "ytm", "kredietrating": "rating"}
TYPE_ALIASSEN = {"aandelen": "aandeel", "stock": "aandeel", "etfs": "etf", "etf's": "etf",
                 "staatsobligaties": "staatsobligatie", "bedrijfsobligaties": "bedrijfsobligatie"}


def lees_instrumenten_csv(bron) -> tuple[list[Instrument], list[str]]:
    """Leest instrumenten uit CSV. Geeft (geldige instrumenten, foutmeldingen per regel)."""
    df = bron.copy() if isinstance(bron, pd.DataFrame) else lees_csv(bron)
    df.columns = [ALIASSEN.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]
    instrumenten, fouten = [], []
    for nr, rij in enumerate(df.to_dict("records"), start=2):
        gegevens = {}
        for k, v in rij.items():
            if k not in Instrument.model_fields or v is None or pd.isna(v) or str(v).strip() == "":
                continue
            v = str(v).strip()
            if k in GETALVELDEN:
                v = v.replace("%", "").replace(",", ".")
            if k == "type":
                v = TYPE_ALIASSEN.get(v.lower(), v.lower())
            gegevens[k] = v
        try:
            instrumenten.append(Instrument(**gegevens))
        except ValidationError as e:
            redenen = "; ".join(
                f"{'.'.join(str(p) for p in f['loc']) or 'regel'}: {f['msg'].removeprefix('Value error, ')}"
                for f in e.errors()
            )
            fouten.append(f"Regel {nr} ({gegevens.get('isin', 'geen ISIN')}): {redenen}")
    return instrumenten, fouten
