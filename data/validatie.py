"""Validatie van instrumenten en koersdata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from data.modellen import Instrument, isin_geldig

FOUT = "fout"
WAARSCHUWING = "waarschuwing"


@dataclass(frozen=True)
class Melding:
    niveau: str
    tekst: str


def valideer(instrument: Instrument, historie: pd.DataFrame, peildatum: date, config: dict) -> list[Melding]:
    """Controleert ontbrekende data, koersen <= 0, ISIN-formaat en verouderde koersen."""
    m: list[Melding] = []
    v = config["validatie"]

    if not isin_geldig(instrument.isin):
        m.append(Melding(FOUT, "Ongeldig ISIN-formaat."))

    if historie is None or historie.empty:
        m.append(Melding(FOUT, "Geen koersdata beschikbaar."))
    else:
        if (historie["koers"] <= 0).any():
            m.append(Melding(FOUT, "Koershistorie bevat koersen van nul of lager."))
        leeftijd = (peildatum - historie["datum"].max()).days
        if leeftijd > v["max_leeftijd_koers_dagen"]:
            m.append(Melding(WAARSCHUWING, f"Verouderde koers: laatste koers is {leeftijd} dagen oud."))

    if instrument.type.is_obligatie:
        if instrument.vervaldatum is None:
            m.append(Melding(FOUT, "Vervaldatum ontbreekt."))
        elif instrument.vervaldatum <= peildatum:
            m.append(Melding(FOUT, "Obligatie is al afgelost (vervaldatum verstreken)."))
        if instrument.couponrente is None and instrument.ytm is None:
            m.append(Melding(FOUT, "Couponrente en yield ontbreken beide."))
        elif instrument.couponrente is None:
            m.append(Melding(WAARSCHUWING, "Couponrente ontbreekt; duration kan niet worden berekend."))
        if instrument.rating is None:
            m.append(Melding(WAARSCHUWING, "Kredietrating ontbreekt."))
    elif instrument.type.value == "etf" and instrument.ter is None:
        m.append(Melding(WAARSCHUWING, "Kostenratio (TER) ontbreekt."))
    return m


def heeft_fout(meldingen: list[Melding]) -> bool:
    return any(x.niveau == FOUT for x in meldingen)
