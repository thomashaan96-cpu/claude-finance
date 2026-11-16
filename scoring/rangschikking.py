"""Rangschikking: filters vooraf, daarna een gewogen som van genormaliseerde kenmerken.

score = 100 * som(w_i * n_i) / som(w_i)

n_i is het kenmerk, min-max genormaliseerd binnen de gerangschikte groep (0 = slechtste,
1 = beste). Kenmerken die voor een instrument niet van toepassing zijn (kredietrating bij
aandelen en ETF's) vallen weg; het gewicht wordt naar rato over de overige verdeeld.
Een kenmerk dat wel van toepassing is maar ontbreekt, telt als 0,5 en wordt gemeld.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from analyse.instrument import InstrumentAnalyse
from data.modellen import InstrumentType, rating_waarde
from data.validatie import FOUT


@dataclass(frozen=True)
class Kenmerk:
    label: str
    hoger_is_beter: bool


KENMERKEN: dict[str, Kenmerk] = {
    "verwacht_rendement": Kenmerk("Verwacht rendement (neutraal scenario)", True),
    "verlies_slecht": Kenmerk("Uitkomst in het slechte scenario", True),
    "volatiliteit": Kenmerk("Volatiliteit", False),
    "kosten": Kenmerk("Kosten", False),
    "kredietrating": Kenmerk("Kredietrating", True),
    "liquiditeit": Kenmerk("Liquiditeit (recente koersen)", True),
    "actualiteit": Kenmerk("Actualiteit (leeftijd laatste koers)", False),
}
VERPLICHT = ("verwacht_rendement", "verlies_slecht", "volatiliteit", "liquiditeit", "actualiteit")

CATEGORIEEN = {
    "alle": None,
    "aandelen": InstrumentType.AANDEEL,
    "etf's": InstrumentType.ETF,
    "staatsobligaties": InstrumentType.STAATSOBLIGATIE,
    "bedrijfsobligaties": InstrumentType.BEDRIJFSOBLIGATIE,
}


@dataclass(frozen=True)
class FilterUitkomst:
    naam: str
    gehaald: bool
    toelichting: str
    van_toepassing: bool = True


@dataclass(frozen=True)
class Factor:
    naam: str
    waarde: Optional[float]      # ruwe waarde van het kenmerk
    genormaliseerd: float        # 0..1, hoger = beter
    gewicht: float               # effectief gewicht na herverdeling (som = 1)
    ontbreekt: bool = False

    @property
    def bijdrage(self) -> float:
        """Bijdrage aan de score in punten (0..100)."""
        return 100 * self.gewicht * self.genormaliseerd


@dataclass
class Regel:
    analyse: InstrumentAnalyse
    kenmerken: dict[str, Optional[float]]
    filters: list[FilterUitkomst]
    factoren: dict[str, Factor] = field(default_factory=dict)
    score: Optional[float] = None
    rang: Optional[int] = None

    @property
    def gerangschikt(self) -> bool:
        return self.rang is not None

    @property
    def redenen_uitsluiting(self) -> list[str]:
        return [f"{f.naam}: {f.toelichting}" for f in self.filters if not f.gehaald]


@dataclass
class Rangschikking:
    categorie: str
    profiel: str
    horizon: int
    gewichten: dict[str, float]
    regels: list[Regel]                      # gerangschikt, beste eerst
    uitgesloten: list[Regel]                 # filter niet gehaald, met reden
    medianen: dict[InstrumentType, dict[str, Optional[float]]]        # ruwe waarden per categorie
    medianen_norm: dict[InstrumentType, dict[str, Optional[float]]]   # genormaliseerd per categorie


def kenmerken_van(a: InstrumentAnalyse, horizon: int) -> dict[str, Optional[float]]:
    """Ruwe kenmerken voor de score. None = ontbreekt of niet van toepassing."""
    s = a.scenarios.get(horizon)
    return {
        "verwacht_rendement": s.rendement_neutraal if s else None,
        "verlies_slecht": s.totaal_slecht if s else None,
        "volatiliteit": a.volatiliteit,
        "kosten": a.kosten,
        "kredietrating": rating_waarde(a.instrument.rating) if a.instrument.type.is_obligatie else None,
        "liquiditeit": float(a.koersen_recent),
        "actualiteit": float(a.leeftijd_dagen) if a.leeftijd_dagen is not None else None,
    }


def van_toepassing(naam: str, a: InstrumentAnalyse) -> bool:
    return naam != "kredietrating" or a.instrument.type.is_obligatie


def pas_filters_toe(a: InstrumentAnalyse, kenmerken: dict[str, Optional[float]], config: dict) -> list[FilterUitkomst]:
    f = config["filters"]
    soort = a.instrument.type.value
    uit: list[FilterUitkomst] = []

    # 1. Datakwaliteit
    fouten = [m.tekst for m in a.meldingen if m.niveau == FOUT]
    minimum = f["min_koersen_historie"][soort]
    ontbreekt = [KENMERKEN[k].label for k in VERPLICHT if kenmerken[k] is None]
    if fouten:
        uit.append(FilterUitkomst("Datakwaliteit", False, " ".join(fouten)))
    elif a.aantal_koersen < minimum:
        uit.append(FilterUitkomst("Datakwaliteit", False,
                                  f"{a.aantal_koersen} koersen in de historie, minimaal {minimum} vereist."))
    elif a.historie_jaren < f["min_historie_jaren"][soort]:
        uit.append(FilterUitkomst(
            "Datakwaliteit", False,
            f"{a.historie_jaren:.1f} jaar koershistorie, minimaal {f['min_historie_jaren'][soort]} jaar vereist."
            .replace(".", ",", 1)))
    elif ontbreekt:
        uit.append(FilterUitkomst("Datakwaliteit", False,
                                  "Niet te berekenen: " + ", ".join(ontbreekt).lower() + "."))
    else:
        uit.append(FilterUitkomst("Datakwaliteit", True,
                                  f"{a.aantal_koersen} koersen in de historie (minimaal {minimum}), geen fouten."))

    # 2. Liquiditeit
    minimum = f["min_koersen_recent"][soort]
    venster = f["liquiditeit_venster_dagen"]
    uit.append(FilterUitkomst(
        "Liquiditeit", a.koersen_recent >= minimum,
        f"{a.koersen_recent} koersen in de laatste {venster} dagen (minimaal {minimum})."))

    # 3. Minimale rating (alleen bedrijfsobligaties)
    grens = f["min_rating_bedrijfsobligatie"]
    if a.instrument.type == InstrumentType.BEDRIJFSOBLIGATIE:
        waarde = rating_waarde(a.instrument.rating)
        if waarde is None:
            uit.append(FilterUitkomst("Minimale rating", False, f"Rating ontbreekt (minimaal {grens} vereist)."))
        else:
            uit.append(FilterUitkomst("Minimale rating", waarde >= rating_waarde(grens),
                                      f"Rating {a.instrument.rating} (minimaal {grens})."))
    else:
        uit.append(FilterUitkomst("Minimale rating", True, "Niet van toepassing (geen bedrijfsobligatie).", False))
    return uit


def _normaliseer(waarde: float, laag: float, hoog: float, hoger_is_beter: bool) -> float:
    if hoog - laag < 1e-12:
        return 0.5
    n = (waarde - laag) / (hoog - laag)
    return n if hoger_is_beter else 1 - n


def _mediaan(waarden: list[Optional[float]]) -> Optional[float]:
    aanwezig = [w for w in waarden if w is not None]
    return float(median(aanwezig)) if aanwezig else None


def maak_rangschikking(analyses: list[InstrumentAnalyse], config: dict, categorie: str = "alle",
                       profiel: str = "neutraal", horizon: int = 5) -> Rangschikking:
    if categorie not in CATEGORIEEN:
        raise ValueError(f"Onbekende categorie: {categorie}")
    if profiel not in config["gewichten"]:
        raise ValueError(f"Onbekend risicoprofiel: {profiel}")
    if horizon not in config["algemeen"]["horizons"]:
        raise ValueError(f"Onbekende horizon: {horizon}")
    gewichten = config["gewichten"][profiel]
    if set(gewichten) != set(KENMERKEN):
        raise ValueError("De gewichten in config.yaml komen niet overeen met de kenmerken van de score.")

    soort = CATEGORIEEN[categorie]
    selectie = [a for a in analyses if soort is None or a.instrument.type == soort]
    selectie.sort(key=lambda a: a.instrument.isin)  # volgorde van invoer mag de uitkomst niet beïnvloeden

    regels, uitgesloten = [], []
    for a in selectie:
        k = kenmerken_van(a, horizon)
        regel = Regel(a, k, pas_filters_toe(a, k, config))
        (regels if all(f.gehaald for f in regel.filters) else uitgesloten).append(regel)

    # Normalisatie binnen de gerangschikte groep
    bereik = {}
    for naam in KENMERKEN:
        waarden = [r.kenmerken[naam] for r in regels if r.kenmerken[naam] is not None]
        bereik[naam] = (min(waarden), max(waarden)) if waarden else None

    for r in regels:
        actief = [n for n in KENMERKEN if van_toepassing(n, r.analyse)]
        totaal = sum(gewichten[n] for n in actief)
        for naam in actief:
            waarde = r.kenmerken[naam]
            if waarde is None or bereik[naam] is None:
                n, ontbreekt = 0.5, True
            else:
                n, ontbreekt = _normaliseer(waarde, *bereik[naam], KENMERKEN[naam].hoger_is_beter), False
            r.factoren[naam] = Factor(naam, waarde, n, gewichten[naam] / totaal, ontbreekt)
        r.score = bereken_score(r.factoren)

    regels.sort(key=lambda r: (-round(r.score, 9), r.analyse.instrument.isin))
    for nr, r in enumerate(regels, start=1):
        r.rang = nr

    medianen, medianen_norm = {}, {}
    for t in InstrumentType:
        groep = [r for r in regels if r.analyse.instrument.type == t]
        if not groep:
            continue
        medianen[t] = {n: _mediaan([r.kenmerken[n] for r in groep]) for n in KENMERKEN}
        medianen_norm[t] = {
            n: _mediaan([r.factoren[n].genormaliseerd for r in groep if n in r.factoren]) for n in KENMERKEN
        }
    return Rangschikking(categorie, profiel, horizon, dict(gewichten), regels, uitgesloten, medianen, medianen_norm)


def bereken_score(factoren: dict[str, Factor]) -> float:
    """Score 0..100: som van de bijdragen van alle factoren."""
    return sum(factoren[n].bijdrage for n in KENMERKEN if n in factoren)
