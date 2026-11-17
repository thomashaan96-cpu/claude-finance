"""Volledige analyse van één instrument: bundelt rendement, risico, obligatiekenmerken,
liquiditeit, scenario's en signaleringen."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from analyse import obligaties, rendement, risico, scenarios, valuta
from data.modellen import Instrument, InstrumentType, rating_klasse
from data.validatie import WAARSCHUWING, Melding, valideer


@dataclass
class InstrumentAnalyse:
    instrument: Instrument
    peildatum: date
    meldingen: list[Melding] = field(default_factory=list)
    signalen: list[str] = field(default_factory=list)

    # Koers en liquiditeit
    laatste_koers: Optional[float] = None
    laatste_datum: Optional[date] = None
    koersbron: Optional[str] = None
    rekenvaluta: str = "EUR"              # valuta waarin rendement en risico zijn berekend
    leeftijd_dagen: Optional[int] = None
    aantal_koersen: int = 0
    koersen_recent: int = 0
    historie_jaren: float = 0.0

    # Rendement en risico
    cagr: dict[int, Optional[float]] = field(default_factory=dict)
    volatiliteit: Optional[float] = None
    volatiliteit_herkomst: str = "ontbreekt"
    max_drawdown: Optional[float] = None
    sharpe: Optional[float] = None

    obligatie: Optional[obligaties.ObligatieAnalyse] = None
    scenarios: dict[int, scenarios.Scenario] = field(default_factory=dict)

    @property
    def kosten(self) -> Optional[float]:
        """Lopende kosten in % per jaar. Alleen ETF's hebben een TER; overige instrumenten 0."""
        if self.instrument.type == InstrumentType.ETF:
            return self.instrument.ter
        return 0.0


def analyseer(instrument: Instrument, historie: pd.DataFrame, config: dict,
              peildatum: Optional[date] = None, risicovrije_rente: Optional[float] = None,
              wisselkoersen: Optional[pd.DataFrame] = None) -> InstrumentAnalyse:
    """Analyseert één instrument.

    `wisselkoersen` (datum, koers = valuta per euro) wordt gebruikt om koersen in vreemde valuta
    naar euro om te rekenen, zodat valutarisico in rendement, risico en scenario's zit.
    """
    peildatum = peildatum or date.today()
    alg, val, scen = config["algemeen"], config["validatie"], config["scenario"]
    rf = alg["risicovrije_rente"] if risicovrije_rente is None else risicovrije_rente
    horizons = alg["horizons"]

    if historie is None:
        historie = pd.DataFrame(columns=["datum", "koers", "bron"])
    historie = historie[historie["datum"] <= peildatum].sort_values("datum").reset_index(drop=True)

    a = InstrumentAnalyse(instrument=instrument, peildatum=peildatum)
    a.meldingen = valideer(instrument, historie, peildatum, config)
    geldig = historie[historie["koers"] > 0].reset_index(drop=True)

    # --- valuta ---
    a.rekenvaluta = instrument.valuta
    if instrument.valuta != "EUR":
        if instrument.type.is_obligatie:
            a.meldingen.append(Melding(WAARSCHUWING, f"Obligatie in {instrument.valuta}: yield en scenario's zijn "
                                                     "in die valuta; valutarisico is niet meegerekend."))
        elif not alg.get("omrekenen_naar_euro", True):
            a.meldingen.append(Melding(WAARSCHUWING, f"Rendement in {instrument.valuta}; omrekening naar euro staat "
                                                     "uit in config.yaml. Valutarisico niet meegerekend."))
        elif wisselkoersen is None or wisselkoersen.empty:
            a.meldingen.append(Melding(WAARSCHUWING, f"Geen wisselkoersen voor {instrument.valuta}: rendement en "
                                                     "risico in die valuta, valutarisico niet meegerekend. "
                                                     "Haal wisselkoersen op in de zijbalk."))
        else:
            omgerekend, dekking = valuta.naar_euro(geldig, wisselkoersen)
            if dekking < 0.95:
                a.meldingen.append(Melding(WAARSCHUWING, f"Wisselkoersen dekken {dekking:.0%} van de koershistorie; "
                                                         "koersdagen zonder wisselkoers zijn weggelaten."
                                                         .replace(".", ",", 0)))
            if not omgerekend.empty:
                geldig, a.rekenvaluta = omgerekend, "EUR"

    # --- koers en liquiditeit ---
    a.aantal_koersen = len(geldig)
    if not geldig.empty:
        laatste = geldig.iloc[-1]
        a.laatste_koers = float(laatste["koers"])
        a.laatste_datum = laatste["datum"]
        a.koersbron = laatste["bron"] if "bron" in geldig.columns else instrument.bron
        a.leeftijd_dagen = (peildatum - a.laatste_datum).days
        venster = peildatum - timedelta(days=config["filters"]["liquiditeit_venster_dagen"])
        a.koersen_recent = int((geldig["datum"] > venster).sum())
        a.historie_jaren = (geldig["datum"].iloc[-1] - geldig["datum"].iloc[0]).days / 365.25

    # --- rendement en risico uit historie ---
    a.cagr = rendement.cagr_overzicht(geldig, horizons)
    statistiek = risico.jaarstatistiek(geldig)
    if statistiek:
        a.volatiliteit, a.volatiliteit_herkomst = statistiek[1] * 100, "koershistorie"
        a.sharpe = risico.sharpe(geldig, rf)
    a.max_drawdown = risico.max_drawdown(geldig)

    # --- scenario's ---
    if instrument.type.is_obligatie:
        koers = a.laatste_koers or instrument.koers
        a.obligatie = obligaties.analyseer_obligatie(instrument, koers, peildatum)
        o = a.obligatie
        if o and o.duration is not None and a.volatiliteit is None:
            a.volatiliteit = o.duration * scen["rentevolatiliteit"]
            a.volatiliteit_herkomst = "duration x rentevolatiliteit (aanname)"
        if o and o.ytm is not None and o.duration is not None:
            verlies = 0.0
            if instrument.type == InstrumentType.BEDRIJFSOBLIGATIE:
                verlies = scen["kredietverlies_slecht"][rating_klasse(instrument.rating)]
            a.scenarios = {
                h: scenarios.scenario_obligatie(o.ytm, o.duration, h, scen["renteschok"], verlies, alg["startbedrag"])
                for h in horizons
            }
            for h in horizons:
                if h > o.looptijd:
                    a.meldingen.append(Melding(
                        WAARSCHUWING,
                        f"Looptijd ({o.looptijd:.1f} jaar) is korter dan de horizon van {h} jaar: "
                        "na aflossing is herbelegging tegen dezelfde yield aangenomen."))
                    break
    elif statistiek:
        m, s = statistiek
        a.scenarios = {h: scenarios.scenario_historisch(m, s, h, alg["startbedrag"]) for h in horizons}
        if a.historie_jaren < 5:
            a.meldingen.append(Melding(
                WAARSCHUWING,
                f"Beperkte historie ({a.historie_jaren:.1f} jaar): scenario's zijn minder betrouwbaar."))

    # --- signaleringen ---
    i = instrument
    if i.schuld_eigen_vermogen is not None and i.schuld_eigen_vermogen > val["schuld_ev_hoog"]:
        a.signalen.append(f"Hoge schuld: schuld/eigen vermogen {i.schuld_eigen_vermogen:.2f} "
                          f"(grens {val['schuld_ev_hoog']:.1f}).")
    if i.concentratie is not None and i.concentratie > val["concentratie_hoog"]:
        a.signalen.append(f"Hoge concentratie: grootste positie {i.concentratie:.1f}% "
                          f"(grens {val['concentratie_hoog']:.0f}%).")
    if i.type == InstrumentType.ETF and i.aantal_posities is not None \
            and i.aantal_posities < val["spreiding_min_posities"]:
        a.signalen.append(f"Beperkte spreiding: {i.aantal_posities} posities "
                          f"(grens {val['spreiding_min_posities']}).")
    a.signalen = [t.replace(".", ",").rstrip(",") + "." for t in a.signalen]  # Nederlandse decimaalnotatie
    return a
