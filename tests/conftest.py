from datetime import date

import numpy as np
import pandas as pd
import pytest

from analyse.instrument import analyseer
from data.demo import _isin
from data.modellen import Instrument
from instellingen import laad_config

PEILDATUM = date(2026, 11, 16)


@pytest.fixture(scope="session")
def config():
    return laad_config()


def maak_historie(mu=0.07, sigma=0.15, jaren=10.5, eind=PEILDATUM, seed=1, bron="test"):
    rng = np.random.default_rng(seed)
    dagen = pd.bdate_range(end=eind, periods=int(jaren * 252))
    stap = rng.normal((mu - sigma**2 / 2) / 252, sigma / np.sqrt(252), len(dagen))
    return pd.DataFrame({"datum": dagen.date, "koers": 100 * np.exp(np.cumsum(stap)), "bron": bron})


def koers_punt(koers, datum=PEILDATUM, bron="test"):
    return pd.DataFrame({"datum": [datum], "koers": [koers], "bron": [bron]})


@pytest.fixture(scope="session")
def analyses(config):
    """Gemengde set: aandelen, ETF's, staats- en bedrijfsobligaties."""
    invoer = [
        (Instrument(isin=_isin(1), naam="Aandeel A", type="aandeel"), maak_historie(0.10, 0.25, seed=1)),
        (Instrument(isin=_isin(2), naam="Aandeel B", type="aandeel", schuld_eigen_vermogen=3.0),
         maak_historie(0.05, 0.18, seed=2)),
        (Instrument(isin=_isin(3), naam="ETF A", type="etf", ter=0.2), maak_historie(0.08, 0.14, seed=3)),
        (Instrument(isin=_isin(4), naam="ETF B", type="etf", ter=0.5), maak_historie(0.07, 0.20, seed=4)),
        (Instrument(isin=_isin(5), naam="ETF zonder TER", type="etf"), maak_historie(0.06, 0.12, seed=5)),
        (Instrument(isin=_isin(6), naam="Staat 2031", type="staatsobligatie", couponrente=2.5,
                    vervaldatum=date(2031, 7, 15), rating="AAA"), koers_punt(99.0)),
        (Instrument(isin=_isin(7), naam="Staat 2040", type="staatsobligatie", couponrente=3.0,
                    vervaldatum=date(2040, 1, 15), rating="AA"), koers_punt(97.0)),
        (Instrument(isin=_isin(8), naam="Bedrijf A", type="bedrijfsobligatie", couponrente=4.0,
                    vervaldatum=date(2030, 3, 1), rating="A"), koers_punt(100.5)),
        (Instrument(isin=_isin(9), naam="Bedrijf BB", type="bedrijfsobligatie", couponrente=7.5,
                    vervaldatum=date(2029, 6, 1), rating="BB"), koers_punt(96.0)),
        (Instrument(isin=_isin(10), naam="Aandeel weinig data", type="aandeel"),
         maak_historie(0.08, 0.2, jaren=0.1, seed=6)),
        (Instrument(isin=_isin(11), naam="Aandeel verouderd", type="aandeel"),
         maak_historie(0.08, 0.2, eind=date(2026, 6, 1), seed=7)),
    ]
    return [analyseer(i, h, config, PEILDATUM) for i, h in invoer]
