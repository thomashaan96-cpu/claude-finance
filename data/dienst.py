"""Verversen van data via de bronnen, met SQLite als cache.

Data wordt alleen opgehaald op verzoek of als de laatste poging ouder is dan de
ingestelde termijn (standaard 7 dagen) - nooit bij elke klik.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from data.bronnen import BronFout, ECBBron, PrijsBron, maak_bron
from data.modellen import Instrument
from storage.db import Opslag

API_BRONNEN = {"eodhd", "twelvedata", "eodhd_eubond", "eulerpool"}
RENTE_SLEUTEL = "rente:ecb"


def ververs_instrument(instrument: Instrument, opslag: Opslag, config: dict,
                       bron: Optional[PrijsBron] = None, vandaag: Optional[date] = None) -> str:
    """Haalt de koershistorie op en slaat die op. Geeft een statusregel terug."""
    if bron is None and instrument.bron not in API_BRONNEN:
        return "overgeslagen (bron is CSV of handmatig)"
    vandaag = vandaag or date.today()
    start = vandaag - timedelta(days=int(config["algemeen"]["historie_jaren"] * 365.25))
    try:
        symbolen = {instrument.isin: instrument.symbool} if instrument.symbool else None
        bron = bron or maak_bron(instrument.bron, symbolen, config)
        df = bron.historie(instrument.isin, start, vandaag)
        n = opslag.bewaar_koersen(instrument.isin, df, bron.naam)
        status = f"ok: {n} koersen" if n else "geen koersen ontvangen"
    except BronFout as e:
        status = f"mislukt: {e}"
    opslag.noteer_verversing(instrument.isin, status)
    return status


def ververs_alles(opslag: Opslag, config: dict, alleen_verouderd: bool = False,
                  bron_fabriek: Optional[Callable[[Instrument], PrijsBron]] = None) -> dict[str, str]:
    """Ververst alle instrumenten met een API-bron. Met alleen_verouderd geldt de weektermijn."""
    resultaat = {}
    termijn = config["algemeen"]["verversen_na_dagen"]
    for instrument in opslag.instrumenten():
        if instrument.bron not in API_BRONNEN and bron_fabriek is None:
            continue
        if alleen_verouderd and not opslag.moet_verversen(instrument.isin, termijn):
            continue
        bron = bron_fabriek(instrument) if bron_fabriek else None
        resultaat[instrument.isin] = ververs_instrument(instrument, opslag, config, bron)
    return resultaat


def ververs_rentes(opslag: Opslag, bron: Optional[PrijsBron] = None,
                   looptijden: tuple[float, ...] = (1, 2, 3, 5, 7, 10, 20, 30)) -> str:
    """Haalt de rentecurve op (standaard bij de ECB) en slaat die op."""
    bron = bron or ECBBron()
    try:
        for looptijd in looptijden:
            opslag.bewaar_rente(looptijd, bron.rente(looptijd), bron.naam)
        status = f"ok: {len(looptijden)} looptijden"
    except BronFout as e:
        status = f"mislukt: {e}"
    opslag.noteer_verversing(RENTE_SLEUTEL, status)
    return status


def benodigde_valuta(opslag: Opslag) -> list[str]:
    """Vreemde valuta van aandelen en ETF's in de instrumentenlijst."""
    return sorted({i.valuta for i in opslag.instrumenten() if i.valuta != "EUR" and not i.type.is_obligatie})


def ververs_wisselkoersen(opslag: Opslag, config: dict, bron=None, valuta: Optional[list[str]] = None,
                          vandaag: Optional[date] = None) -> dict[str, str]:
    """Haalt ECB-wisselkoersen op voor alle benodigde valuta."""
    from data.bronnen import ECBWisselkoersBron

    bron = bron or ECBWisselkoersBron()
    vandaag = vandaag or date.today()
    start = vandaag - timedelta(days=int(config["algemeen"]["historie_jaren"] * 365.25))
    resultaat = {}
    for v in valuta if valuta is not None else benodigde_valuta(opslag):
        try:
            n = opslag.bewaar_wisselkoersen(v, bron.wisselkoersen(v, start, vandaag), bron.naam)
            resultaat[v] = f"ok: {n} koersen"
        except BronFout as e:
            resultaat[v] = f"mislukt: {e}"
        opslag.noteer_verversing(f"valuta:{v}", resultaat[v])
    return resultaat


def vul_kengetallen_aan(instrument: Instrument, opslag: Opslag, ophalen=None, overschrijven: bool = False) -> str:
    """Haalt kengetallen op (standaard EODHD fundamentals) en vult ontbrekende velden aan.

    Handmatig ingevulde waarden blijven staan, tenzij `overschrijven` waar is.
    """
    from data.bronnen import haal_kengetallen_eodhd

    if instrument.type.is_obligatie:
        return "overgeslagen (obligatie)"
    try:
        gevonden = (ophalen or haal_kengetallen_eodhd)(instrument.isin, instrument.symbool)
    except BronFout as e:
        return f"mislukt: {e}"
    if instrument.type.value == "aandeel":
        gevonden = {k: v for k, v in gevonden.items() if k not in ("ter", "aantal_posities")}
    nieuw = {k: v for k, v in gevonden.items() if overschrijven or getattr(instrument, k) is None}
    if not nieuw:
        return "geen nieuwe kengetallen"
    opslag.bewaar_instrument(instrument.model_copy(update=nieuw))
    return "aangevuld: " + ", ".join(sorted(nieuw))
