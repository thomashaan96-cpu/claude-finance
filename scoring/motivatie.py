"""Motivatie per instrument ("Waarom?"), volledig afgeleid uit de scoreberekening.

De invloed van een factor is het verschil in punten ten opzichte van de mediaan van de
eigen categorie:  invloed = 100 * gewicht * (genormaliseerd - mediaan genormaliseerd).
De drie factoren met de grootste absolute invloed worden getoond.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from data.modellen import RATINGS
from data.validatie import WAARSCHUWING
from scoring.rangschikking import KENMERKEN, FilterUitkomst, Rangschikking, Regel


@dataclass(frozen=True)
class FactorUitleg:
    naam: str
    label: str
    waarde: Optional[float]
    mediaan: Optional[float]
    gewicht: float
    bijdrage: float      # punten in de score
    invloed: float       # punten t.o.v. de mediaan van de categorie
    ontbreekt: bool

    @property
    def tekst(self) -> str:
        w = formatteer(self.naam, self.waarde)
        m = formatteer(self.naam, self.mediaan)
        if self.ontbreekt:
            effect = "waarde ontbreekt, telt neutraal mee"
        elif abs(self.invloed) < 0.05:
            effect = "gelijk aan de mediaan"
        else:
            richting = "verhoogt" if self.invloed > 0 else "verlaagt"
            effect = f"{richting} de score met {abs(self.invloed):.1f} punten t.o.v. de mediaan"
        return f"{self.label}: {w} (mediaan categorie: {m}; gewicht {self.gewicht:.0%}) - {effect}."


@dataclass(frozen=True)
class Motivatie:
    isin: str
    gerangschikt: bool
    score: Optional[float]
    factoren: list[FactorUitleg]          # top 3, sterkste eerst
    filters: list[FilterUitkomst]
    laatste_koersdatum: Optional[date]
    bron: Optional[str]
    waarschuwingen: list[str]


def formatteer(naam: str, waarde: Optional[float]) -> str:
    if waarde is None:
        return "onbekend"
    if naam == "kredietrating":
        return RATINGS[int(round(waarde)) - 1]
    if naam == "liquiditeit":
        return f"{waarde:.0f} koersen"
    if naam == "actualiteit":
        return f"{waarde:.0f} dagen"
    if naam == "verwacht_rendement":
        return f"{waarde:+.1f}% per jaar".replace(".", ",")
    if naam == "verlies_slecht":
        return f"{waarde:+.1f}% over de horizon".replace(".", ",")
    return f"{waarde:.2f}%".replace(".", ",")


def alle_factoren(regel: Regel, rangschikking: Rangschikking) -> list[FactorUitleg]:
    """Uitleg voor elke factor in de score, gesorteerd op absolute invloed."""
    soort = regel.analyse.instrument.type
    med = rangschikking.medianen.get(soort, {})
    med_norm = rangschikking.medianen_norm.get(soort, {})
    uit = []
    for naam, f in regel.factoren.items():
        m_norm = med_norm.get(naam)
        invloed = 100 * f.gewicht * (f.genormaliseerd - m_norm) if m_norm is not None else 0.0
        uit.append(FactorUitleg(naam, KENMERKEN[naam].label, f.waarde, med.get(naam),
                                f.gewicht, f.bijdrage, invloed, f.ontbreekt))
    uit.sort(key=lambda u: (-abs(u.invloed), -u.gewicht, u.naam))
    return uit


def maak_motivatie(regel: Regel, rangschikking: Rangschikking, aantal: int = 3) -> Motivatie:
    a = regel.analyse
    waarschuwingen = [m.tekst for m in a.meldingen if m.niveau == WAARSCHUWING]
    waarschuwingen += a.signalen
    for f in regel.factoren.values():
        if f.ontbreekt:
            waarschuwingen.append(f"{KENMERKEN[f.naam].label} ontbreekt en telt neutraal (0,5) mee in de score.")
    if a.volatiliteit_herkomst.startswith("duration"):
        waarschuwingen.append("Volatiliteit is geschat uit duration (geen of te weinig koershistorie).")
    return Motivatie(
        isin=a.instrument.isin,
        gerangschikt=regel.gerangschikt,
        score=regel.score,
        factoren=alle_factoren(regel, rangschikking)[:aantal] if regel.gerangschikt else [],
        filters=regel.filters,
        laatste_koersdatum=a.laatste_datum,
        bron=a.koersbron,
        waarschuwingen=waarschuwingen,
    )
