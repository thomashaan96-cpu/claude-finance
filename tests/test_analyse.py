from datetime import date

import numpy as np
import pandas as pd
import pytest

from analyse import obligaties, rendement, risico, scenarios
from analyse.instrument import analyseer
from data.demo import _isin
from data.modellen import Instrument
from tests.conftest import PEILDATUM, koers_punt, maak_historie


def _vaste_groei(jaren, groei=0.08):
    dagen = pd.date_range(end=PEILDATUM, periods=int(jaren * 365.25) + 1, freq="D")
    t = np.arange(len(dagen)) / 365.25
    return pd.DataFrame({"datum": dagen.date, "koers": 100 * (1 + groei) ** t})


def test_cagr_bij_vaste_groei():
    h = _vaste_groei(10.1)
    for jaren in (1, 3, 5, 10):
        assert rendement.cagr(h, jaren) == pytest.approx(8.0, abs=0.05)


def test_cagr_none_bij_te_korte_historie():
    h = _vaste_groei(2.5)
    assert rendement.cagr(h, 1) is not None
    assert rendement.cagr(h, 3) is None
    assert rendement.cagr(h, 10) is None


def test_volatiliteit_benadert_invoer():
    h = maak_historie(mu=0.07, sigma=0.20, jaren=10.5)
    assert risico.volatiliteit(h) == pytest.approx(20.0, abs=1.5)


def test_max_drawdown():
    h = pd.DataFrame({"datum": pd.date_range("2025-01-01", periods=5).date, "koers": [100, 120, 60, 90, 130]})
    assert risico.max_drawdown(h) == pytest.approx(-50.0)


def test_sharpe_daalt_met_hogere_risicovrije_rente():
    h = maak_historie()
    assert risico.sharpe(h, 0.0) > risico.sharpe(h, 4.0)


def test_te_weinig_data_geeft_none():
    h = maak_historie(jaren=0.05)
    assert risico.volatiliteit(h) is None
    assert risico.sharpe(h, 2.0) is None


# --- obligaties ---

def test_prijs_op_pari_als_coupon_gelijk_aan_yield():
    assert obligaties.prijs(4.0, 4.0, 10.0) == pytest.approx(100.0)


def test_ytm_is_inverse_van_prijs():
    p = obligaties.prijs(3.0, 4.2, 7.0, freq=2)
    assert obligaties.ytm_uit_koers(p, 3.0, 7.0, freq=2) == pytest.approx(4.2, abs=1e-6)


def test_duration_nulcoupon():
    # Modified duration van een nulcoupon = looptijd / (1 + y)
    assert obligaties.modified_duration(0.0, 5.0, 10.0) == pytest.approx(10 / 1.05)


def test_prijsverandering_richting_en_duration():
    plus = obligaties.prijsverandering(3.0, 3.0, 10.0, +1.0)
    minus = obligaties.prijsverandering(3.0, 3.0, 10.0, -1.0)
    d = obligaties.modified_duration(3.0, 3.0, 10.0)
    assert plus < 0 < minus
    assert abs(minus) > abs(plus)                 # convexiteit
    assert plus == pytest.approx(-d, abs=0.6)     # eerste-orde benadering


def test_obligatie_analyse_berekent_ytm_en_duration(config):
    i = Instrument(isin=_isin(1), naam="Staat", type="staatsobligatie", couponrente=2.5,
                   vervaldatum=date(2031, 11, 16), rating="AAA")
    a = analyseer(i, koers_punt(100.0), config, PEILDATUM)
    assert a.obligatie.ytm == pytest.approx(2.5, abs=0.01)
    assert a.obligatie.ytm_herkomst == "berekend"
    assert 4.0 < a.obligatie.duration < 5.0
    assert a.volatiliteit_herkomst.startswith("duration")


def test_opgegeven_ytm_en_duration_hebben_voorrang(config):
    i = Instrument(isin=_isin(1), naam="Staat", type="staatsobligatie", couponrente=2.5,
                   vervaldatum=date(2031, 11, 16), ytm=3.3, duration=4.4)
    a = analyseer(i, koers_punt(100.0), config, PEILDATUM)
    assert (a.obligatie.ytm, a.obligatie.duration) == (3.3, 4.4)


# --- scenario's ---

def test_scenario_historisch_volgorde_en_neutraal():
    s = scenarios.scenario_historisch(m=np.log(1.07), s=0.15, horizon=5)
    assert s.eind_slecht < s.eind_neutraal < s.eind_goed
    assert s.eind_neutraal == pytest.approx(10_000 * 1.07**5)
    assert s.rendement_neutraal == pytest.approx(7.0)
    assert s.bandbreedte == pytest.approx(s.eind_goed - s.eind_slecht)


def test_scenario_obligatie_neutraal_is_yield():
    s = scenarios.scenario_obligatie(ytm=3.0, duration=5.0, horizon=3)
    assert s.eind_neutraal == pytest.approx(10_000 * 1.03**3)
    assert s.eind_slecht <= s.eind_neutraal <= s.eind_goed


def test_scenario_obligatie_korte_horizon_rentestijging_is_slecht():
    s = scenarios.scenario_obligatie(ytm=3.0, duration=8.0, horizon=1)
    assert s.eind_slecht == pytest.approx(10_000 * (1 - 0.08) * 1.04)
    assert s.eind_goed == pytest.approx(10_000 * (1 + 0.08) * 1.02)


def test_kredietverlies_verlaagt_alleen_slecht_scenario():
    zonder = scenarios.scenario_obligatie(4.0, 5.0, 5)
    met = scenarios.scenario_obligatie(4.0, 5.0, 5, kredietverlies_slecht=2.0)
    assert met.eind_slecht < zonder.eind_slecht
    assert met.eind_neutraal == zonder.eind_neutraal and met.eind_goed == zonder.eind_goed


def test_analyse_levert_scenario_per_horizon(analyses, config):
    for a in analyses[:9]:
        assert set(a.scenarios) == set(config["algemeen"]["horizons"])


def test_signalering_hoge_schuld(analyses):
    assert any("Hoge schuld" in s for s in analyses[1].signalen)
    assert not analyses[0].signalen
