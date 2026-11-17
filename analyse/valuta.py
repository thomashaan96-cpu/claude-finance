"""Omrekening van koersen in vreemde valuta naar euro met ECB-referentiekoersen."""
from __future__ import annotations

import pandas as pd

MAX_GAT_DAGEN = 7  # een koersdag zonder wisselkoers gebruikt de laatste koers van hooguit 7 dagen eerder


def naar_euro(historie: pd.DataFrame, wisselkoersen: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Rekent koersen om naar euro: koers_eur = koers / (valuta per euro).

    Geeft (omgerekende historie, dekking 0..1). Koersdagen zonder bruikbare wisselkoers vervallen.
    """
    if historie is None or historie.empty:
        return historie, 1.0
    if wisselkoersen is None or wisselkoersen.empty:
        return historie.iloc[0:0], 0.0
    links = historie.assign(_t=pd.to_datetime(historie["datum"])).sort_values("_t")
    rechts = (wisselkoersen[["datum", "koers"]].rename(columns={"koers": "_fx"})
              .assign(_t=lambda d: pd.to_datetime(d["datum"])).drop(columns="datum").sort_values("_t"))
    samen = pd.merge_asof(links, rechts, on="_t", direction="backward",
                          tolerance=pd.Timedelta(days=MAX_GAT_DAGEN))
    dekking = float(samen["_fx"].notna().mean())
    samen = samen.dropna(subset=["_fx"])
    samen["koers"] = samen["koers"] / samen["_fx"]
    return samen.drop(columns=["_t", "_fx"]).reset_index(drop=True), dekking
