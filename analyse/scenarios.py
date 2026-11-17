"""Scenario's goed / neutraal / slecht per horizon.

Aandelen en ETF's: neutraal = historisch gemiddeld (samengesteld) rendement. Goed en slecht
liggen één standaardafwijking erboven en eronder, gemeten over de hele horizon:
    eindwaarde = start * exp(m*h +/- s*sqrt(h))
met m het gemiddelde log-jaarrendement en s de jaarvolatiliteit.

Obligaties: neutraal = rente blijft gelijk (rendement = yield). Goed en slecht volgen uit een
directe renteverandering van +/- de renteschok: de koers verandert met -duration * schok,
daarna rendeert de belegging tegen de nieuwe yield (herbelegging van coupons en aflossing):
    eindwaarde = start * (1 - D*schok) * (1 + y + schok)^h
Het gunstigste van beide richtingen is "goed", het ongunstigste "slecht". Bij bedrijfsobligaties
gaat er in het slechte scenario een jaarlijks kredietverlies af (per ratingklasse, zie config).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

LABEL = "Schatting op basis van historie en aannames. Geen garantie."


@dataclass(frozen=True)
class Scenario:
    horizon: int
    start: float
    eind_goed: float
    eind_neutraal: float
    eind_slecht: float

    def _jaar(self, eind: float) -> float:
        if eind <= 0:
            return -100.0
        return ((eind / self.start) ** (1 / self.horizon) - 1) * 100

    @property
    def rendement_goed(self) -> float:
        return self._jaar(self.eind_goed)

    @property
    def rendement_neutraal(self) -> float:
        """Verwacht jaarrendement in % in het neutrale scenario."""
        return self._jaar(self.eind_neutraal)

    @property
    def rendement_slecht(self) -> float:
        return self._jaar(self.eind_slecht)

    @property
    def totaal_slecht(self) -> float:
        """Totale waardeverandering in % over de horizon in het slechte scenario."""
        return (self.eind_slecht / self.start - 1) * 100

    @property
    def bandbreedte(self) -> float:
        return self.eind_goed - self.eind_slecht


def scenario_historisch(m: float, s: float, horizon: int, start: float = 10_000) -> Scenario:
    """m en s: gemiddeld log-jaarrendement en jaarvolatiliteit (fracties)."""
    spreiding = s * math.sqrt(horizon)
    return Scenario(
        horizon=horizon,
        start=start,
        eind_goed=start * math.exp(m * horizon + spreiding),
        eind_neutraal=start * math.exp(m * horizon),
        eind_slecht=start * math.exp(m * horizon - spreiding),
    )


def scenario_obligatie(ytm: float, duration: float, horizon: int, renteschok: float = 1.0,
                       kredietverlies_slecht: float = 0.0, start: float = 10_000) -> Scenario:
    """ytm, renteschok en kredietverlies in %; duration in jaren."""
    y, d = ytm / 100, renteschok / 100

    def eind(delta: float) -> float:
        koersfactor = max(0.0, 1 - duration * delta)
        return start * koersfactor * max(0.0, 1 + y + delta) ** horizon

    neutraal = eind(0.0)
    omhoog, omlaag = eind(d), eind(-d)
    slecht = min(omhoog, omlaag, neutraal) * max(0.0, 1 - kredietverlies_slecht / 100) ** horizon
    return Scenario(horizon, start, max(omhoog, omlaag, neutraal), neutraal, slecht)
