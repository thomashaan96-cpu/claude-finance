"""Obligaties: koers, yield to maturity, duration en rentegevoeligheid.

Vereenvoudiging: gerekend wordt zonder opgelopen rente (de opgegeven koers geldt als
contante waarde van alle resterende betalingen).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from data.modellen import Instrument


def looptijd_jaren(vervaldatum: date, peildatum: date) -> float:
    return (vervaldatum - peildatum).days / 365.25


def _kasstromen(coupon: float, looptijd: float, freq: int) -> list[tuple[float, float]]:
    """(tijdstip in jaren, bedrag) per 100 nominaal."""
    n = max(1, math.ceil(looptijd * freq - 1e-9))
    stromen = [(looptijd - k / freq, coupon / freq) for k in range(n)]
    stromen = [(t, b) for t, b in stromen if t > 0]
    stromen[0] = (stromen[0][0], stromen[0][1] + 100.0)  # aflossing op vervaldatum
    return stromen


def prijs(coupon: float, ytm: float, looptijd: float, freq: int = 1) -> float:
    """Koers (% van nominaal) bij een yield in %."""
    y = ytm / 100
    return sum(b / (1 + y / freq) ** (t * freq) for t, b in _kasstromen(coupon, looptijd, freq))


def ytm_uit_koers(koers: float, coupon: float, looptijd: float, freq: int = 1) -> float:
    """Yield to maturity in %, via bisectie."""
    laag, hoog = -50.0, 200.0
    for _ in range(100):
        mid = (laag + hoog) / 2
        if prijs(coupon, mid, looptijd, freq) > koers:
            laag = mid
        else:
            hoog = mid
    return (laag + hoog) / 2


def modified_duration(coupon: float, ytm: float, looptijd: float, freq: int = 1) -> float:
    y = ytm / 100
    stromen = _kasstromen(coupon, looptijd, freq)
    cw = [(t, b / (1 + y / freq) ** (t * freq)) for t, b in stromen]
    totaal = sum(w for _, w in cw)
    macaulay = sum(t * w for t, w in cw) / totaal
    return macaulay / (1 + y / freq)


def prijsverandering(coupon: float, ytm: float, looptijd: float, delta: float, freq: int = 1) -> float:
    """Koersverandering in % bij een renteverandering van `delta` procentpunt (volledige herwaardering)."""
    return (prijs(coupon, ytm + delta, looptijd, freq) / prijs(coupon, ytm, looptijd, freq) - 1) * 100


@dataclass(frozen=True)
class ObligatieAnalyse:
    looptijd: float                      # jaren tot vervaldatum
    ytm: Optional[float]                 # %
    ytm_herkomst: str                    # "opgegeven" / "berekend" / "ontbreekt"
    duration: Optional[float]            # modified duration, jaren
    duration_herkomst: str
    prijs_bij_plus_1: Optional[float]    # % koersverandering bij +1% rente
    prijs_bij_min_1: Optional[float]     # % koersverandering bij -1% rente
    rating: Optional[str]


def analyseer_obligatie(instrument: Instrument, koers: Optional[float], peildatum: date) -> Optional[ObligatieAnalyse]:
    """Yield, duration en rentegevoeligheid. `koers` is de laatste koers in % van nominaal."""
    if instrument.vervaldatum is None or instrument.vervaldatum <= peildatum:
        return None
    T = looptijd_jaren(instrument.vervaldatum, peildatum)
    coupon, freq = instrument.couponrente, instrument.couponfrequentie

    if instrument.ytm is not None:
        ytm, ytm_h = instrument.ytm, "opgegeven"
    elif koers and coupon is not None:
        ytm, ytm_h = ytm_uit_koers(koers, coupon, T, freq), "berekend"
    else:
        ytm, ytm_h = None, "ontbreekt"

    if instrument.duration is not None:
        duration, dur_h = instrument.duration, "opgegeven"
    elif ytm is not None and coupon is not None:
        duration, dur_h = modified_duration(coupon, ytm, T, freq), "berekend"
    else:
        duration, dur_h = None, "ontbreekt"

    plus = minus = None
    if ytm is not None and coupon is not None:
        plus = prijsverandering(coupon, ytm, T, 1.0, freq)
        minus = prijsverandering(coupon, ytm, T, -1.0, freq)
    elif duration is not None:  # benadering via duration
        plus, minus = -duration, duration

    return ObligatieAnalyse(T, ytm, ytm_h, duration, dur_h, plus, minus, instrument.rating)
