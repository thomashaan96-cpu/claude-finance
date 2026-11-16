"""Datamodel: instrumenten en hun (optionele) kenmerken."""
from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

ISIN_PATROON = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# Ratingschaal: hoger getal = betere kredietwaardigheid.
RATINGS = [
    "D", "C", "CC", "CCC-", "CCC", "CCC+", "B-", "B", "B+", "BB-", "BB", "BB+",
    "BBB-", "BBB", "BBB+", "A-", "A", "A+", "AA-", "AA", "AA+", "AAA",
]
RATING_WAARDE = {r: i + 1 for i, r in enumerate(RATINGS)}


class InstrumentType(str, Enum):
    AANDEEL = "aandeel"
    ETF = "etf"
    STAATSOBLIGATIE = "staatsobligatie"
    BEDRIJFSOBLIGATIE = "bedrijfsobligatie"

    @property
    def is_obligatie(self) -> bool:
        return self in (InstrumentType.STAATSOBLIGATIE, InstrumentType.BEDRIJFSOBLIGATIE)

    @property
    def label(self) -> str:
        return {
            "aandeel": "Aandeel",
            "etf": "ETF",
            "staatsobligatie": "Staatsobligatie",
            "bedrijfsobligatie": "Bedrijfsobligatie",
        }[self.value]


BRONNEN = ["csv", "handmatig", "eodhd", "twelvedata", "eodhd_eubond"]


def isin_geldig(isin: str) -> bool:
    """Controleert formaat en controlecijfer (Luhn) van een ISIN."""
    if not isinstance(isin, str) or not ISIN_PATROON.match(isin):
        return False
    cijfers = "".join(str(int(c, 36)) for c in isin)
    totaal = 0
    for i, c in enumerate(reversed(cijfers)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        totaal += n
    return totaal % 10 == 0


def rating_waarde(rating: Optional[str]) -> Optional[int]:
    if not rating:
        return None
    return RATING_WAARDE.get(rating.strip().upper())


def rating_klasse(rating: Optional[str]) -> str:
    """Ratingklasse zonder +/- (bijv. 'BBB+' -> 'BBB'), voor de kredietverliesaanname."""
    if rating_waarde(rating) is None:
        return "onbekend"
    kern = rating.strip().upper().rstrip("+-")
    return "CCC" if kern in ("CC", "C", "D") else kern


class Instrument(BaseModel):
    isin: str
    naam: str
    type: InstrumentType
    valuta: str = "EUR"
    bron: str = "csv"
    symbool: Optional[str] = None  # tickersymbool bij de API-bron, bijv. ASML.AS

    # Aandelen en ETF's (optioneel, handmatig)
    koers_winst: Optional[float] = None
    dividendrendement: Optional[float] = None       # %
    schuld_eigen_vermogen: Optional[float] = None
    ter: Optional[float] = None                     # % per jaar
    aantal_posities: Optional[int] = None           # spreiding ETF
    concentratie: Optional[float] = None            # grootste positie / afhankelijkheid, %

    # Obligaties
    couponrente: Optional[float] = None             # % per jaar
    couponfrequentie: int = 1                       # betalingen per jaar
    vervaldatum: Optional[date] = None
    koers: Optional[float] = None                   # % van nominaal
    ytm: Optional[float] = None                     # %
    rating: Optional[str] = None
    duration: Optional[float] = None                # modified duration in jaren

    @field_validator("isin")
    @classmethod
    def _isin(cls, v: str) -> str:
        v = v.strip().upper()
        if not isin_geldig(v):
            raise ValueError(f"Ongeldig ISIN: {v}")
        return v

    @field_validator("naam")
    @classmethod
    def _naam(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Naam ontbreekt")
        return v.strip()

    @field_validator("valuta")
    @classmethod
    def _valuta(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError(f"Ongeldige valutacode: {v}")
        return v

    @field_validator("bron")
    @classmethod
    def _bron(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in BRONNEN:
            raise ValueError(f"Onbekende bron: {v}. Kies uit {', '.join(BRONNEN)}")
        return v

    @field_validator("rating")
    @classmethod
    def _rating(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip():
            return None
        v = str(v).strip().upper()
        if v not in RATING_WAARDE:
            raise ValueError(f"Onbekende rating: {v}")
        return v

    @field_validator("koers")
    @classmethod
    def _koers(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("Koers moet groter zijn dan nul")
        return v

    @field_validator("couponfrequentie")
    @classmethod
    def _freq(cls, v: int) -> int:
        if v not in (1, 2, 4, 12):
            raise ValueError("Couponfrequentie moet 1, 2, 4 of 12 zijn")
        return v


class Koers(BaseModel):
    isin: str
    datum: date
    koers: float = Field(gt=0)
    bron: str
