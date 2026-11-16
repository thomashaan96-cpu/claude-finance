"""Fictieve demodata om het dashboard zonder API-sleutel te proberen.

Alle instrumenten en koersen zijn verzonnen (landcode XX bestaat niet).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from data.modellen import Instrument
from storage.db import Opslag


def _isin(nummer: int) -> str:
    basis = f"XX{nummer:09d}"
    cijfers = "".join(str(int(c, 36)) for c in basis)
    totaal = 0
    for i, c in enumerate(reversed(cijfers)):
        n = int(c)
        if i % 2 == 0:  # positie naast het controlecijfer wordt verdubbeld
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        totaal += n
    return basis + str((10 - totaal % 10) % 10)


# (naam, type, jaarrendement, volatiliteit, jaren historie, extra velden)
DEMO = [
    ("Demo Aandeel Groei", "aandeel", 0.11, 0.28, 10.5, dict(koers_winst=31, dividendrendement=0.6, schuld_eigen_vermogen=0.4)),
    ("Demo Aandeel Dividend", "aandeel", 0.06, 0.16, 10.5, dict(koers_winst=13, dividendrendement=4.2, schuld_eigen_vermogen=2.6)),
    ("Demo Aandeel Jong", "aandeel", 0.15, 0.40, 2.0, dict(koers_winst=55, concentratie=45)),
    ("Demo ETF Wereld", "etf", 0.08, 0.15, 10.5, dict(ter=0.20, aantal_posities=1500, concentratie=5)),
    ("Demo ETF Technologie", "etf", 0.13, 0.24, 6.0, dict(ter=0.35, aantal_posities=40, concentratie=18)),
    ("Demo ETF Obligaties", "etf", 0.02, 0.05, 10.5, dict(ter=0.09, aantal_posities=900)),
    ("Demo Staatsobligatie 2031", "staatsobligatie", 0, 0, 0,
     dict(couponrente=2.5, vervaldatum=date(2031, 7, 15), koers=99.2, rating="AAA")),
    ("Demo Staatsobligatie 2040", "staatsobligatie", 0, 0, 0,
     dict(couponrente=3.0, vervaldatum=date(2040, 1, 15), koers=97.5, rating="AA")),
    ("Demo Bedrijfsobligatie A", "bedrijfsobligatie", 0, 0, 0,
     dict(couponrente=3.75, vervaldatum=date(2030, 3, 1), koers=100.8, rating="A-")),
    ("Demo Bedrijfsobligatie BBB", "bedrijfsobligatie", 0, 0, 0,
     dict(couponrente=4.5, vervaldatum=date(2033, 9, 1), koers=98.0, rating="BBB")),
    ("Demo Bedrijfsobligatie High Yield", "bedrijfsobligatie", 0, 0, 0,
     dict(couponrente=7.5, vervaldatum=date(2029, 6, 1), koers=96.0, rating="BB-")),
]


def laad_demo(opslag: Opslag, vandaag: date | None = None) -> int:
    vandaag = vandaag or date.today()
    rng = np.random.default_rng(42)
    for nr, (naam, soort, mu, sigma, jaren, extra) in enumerate(DEMO, start=1):
        instrument = Instrument(isin=_isin(nr), naam=naam, type=soort, valuta="EUR", bron="csv", **extra)
        opslag.bewaar_instrument(instrument)
        if jaren:
            dagen = pd.bdate_range(end=vandaag, periods=int(jaren * 252))
            stap = rng.normal(0, sigma / np.sqrt(252), len(dagen))
            stap += np.log(1 + mu) / 252 - stap.mean()   # gerealiseerd jaarrendement = mu
            koersen = 100 * np.exp(np.cumsum(stap))
            df = pd.DataFrame({"datum": dagen.date, "koers": koersen.round(2)})
        else:  # obligatie: alleen de actuele koers
            df = pd.DataFrame({"datum": [vandaag - timedelta(days=1)], "koers": [extra["koers"]]})
        opslag.bewaar_koersen(instrument.isin, df, "demo")
    return len(DEMO)
