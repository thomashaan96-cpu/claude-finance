"""Tests voor Eulerpool, wisselkoersen/valutaomrekening en het ophalen van kengetallen."""
from datetime import date

import pandas as pd
import pytest

from analyse.instrument import analyseer
from analyse.valuta import naar_euro
from data import bronnen
from data.demo import _isin
from data.dienst import benodigde_valuta, ververs_instrument, ververs_wisselkoersen, vul_kengetallen_aan
from data.modellen import Instrument
from storage.db import Opslag
from tests.conftest import PEILDATUM, maak_historie


# --- Eulerpool ---

@pytest.mark.parametrize("antwoord", [
    [{"date": "2026-11-13", "close": 99.5}, {"date": "2026-11-12", "close": 99.1}],
    {"data": [{"datum": "2026-11-12", "price": 99.1}, {"datum": "2026-11-13", "price": 99.5}]},
    {"prices": [{"timestamp": 1794960000000, "value": 99.1}, {"timestamp": 1795046400000, "value": 99.5}]},
])
def test_eulerpool_leest_verschillende_formaten(antwoord):
    df = bronnen.EulerpoolBron.lees_json(antwoord)
    assert df["koers"].tolist() == [99.1, 99.5]
    assert df["datum"].is_monotonic_increasing


def test_eulerpool_onbekend_formaat_geeft_bronfout():
    with pytest.raises(bronnen.BronFout):
        bronnen.EulerpoolBron.lees_json({"fout": "x"})
    with pytest.raises(bronnen.BronFout):
        bronnen.EulerpoolBron.lees_json([{"dag": "2026-11-13", "x": 1}])


def test_eulerpool_adres_uit_config(monkeypatch, config):
    monkeypatch.setenv("EULERPOOL_API_KEY", "geheim")
    aangeroepen = {}

    class Sessie:
        def get(self, url, params, timeout):
            aangeroepen.update(url=url, params=params)

            class Antwoord:
                status_code = 200

                def json(self):
                    return [{"date": "2026-11-13", "close": 98.0}]
            return Antwoord()

    bron = bronnen.EulerpoolBron(config["eulerpool"], sessie=Sessie())
    df = bron.historie("XS1234567890", date(2026, 1, 1), date(2026, 11, 13))
    assert aangeroepen["url"].startswith(config["eulerpool"]["basis_url"])
    assert "XS1234567890" in aangeroepen["url"] and "2026-01-01" in aangeroepen["url"]
    assert aangeroepen["params"] == {"token": "geheim"}
    assert df["koers"].tolist() == [98.0]


def test_eulerpool_via_fabriek_en_dienst(config, monkeypatch):
    monkeypatch.delenv("EULERPOOL_API_KEY", raising=False)
    assert isinstance(bronnen.maak_bron("eulerpool", config=config), bronnen.EulerpoolBron)
    opslag = Opslag()
    i = Instrument(isin=_isin(1), naam="B", type="bedrijfsobligatie", bron="eulerpool")
    assert "EULERPOOL_API_KEY" in ververs_instrument(i, opslag, config)


# --- wisselkoersen ---

def test_ecb_wisselkoersen_csv():
    tekst = "KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.USD.EUR.SP00.A,2026-11-12,1.08\nEXR.D.USD.EUR.SP00.A,2026-11-13,1.09\n"
    df = bronnen.ECBWisselkoersBron.lees_csv(tekst)
    assert df["koers"].tolist() == [1.08, 1.09] and df["datum"].iloc[-1] == date(2026, 11, 13)


def test_wisselkoersen_csv_import():
    csv = pd.DataFrame({"Datum": ["2026-11-12", "2026-11-13", "x"], "Valuta": ["usd", "usd", "usd"],
                        "Koers": ["1,08", "1,09", "1"]})
    df = bronnen.lees_wisselkoersen_csv(csv)
    assert df["valuta"].unique().tolist() == ["USD"] and df["koers"].tolist() == [1.08, 1.09]


def test_naar_euro_deelt_door_wisselkoers_en_vult_gaten():
    h = pd.DataFrame({"datum": [date(2026, 11, d) for d in (10, 11, 12)], "koers": [110.0, 121.0, 120.0]})
    fx = pd.DataFrame({"datum": [date(2026, 11, 10), date(2026, 11, 11)], "koers": [1.10, 1.10]})
    eur, dekking = naar_euro(h, fx)
    assert eur["koers"].round(6).tolist() == [100.0, 110.0, round(120 / 1.10, 6)]  # 12-11 gebruikt koers 11-11
    assert dekking == 1.0
    leeg, dekking = naar_euro(h, pd.DataFrame(columns=["datum", "koers"]))
    assert leeg.empty and dekking == 0.0


def test_omrekening_verandert_rendement(config):
    """Dollarkoers stijgt 10%, maar de dollar daalt 10% t.o.v. de euro: in euro ongeveer 0%."""
    dagen = pd.bdate_range(end=PEILDATUM, periods=300).date
    koers = pd.Series(range(len(dagen))) / (len(dagen) - 1)
    h = pd.DataFrame({"datum": dagen, "koers": 100 * 1.10 ** koers, "bron": "test"})
    fx = pd.DataFrame({"datum": dagen, "koers": 1.0 * 1.10 ** koers})
    i = Instrument(isin=_isin(1), naam="VS", type="aandeel", valuta="USD")
    in_usd = analyseer(i, h, config, PEILDATUM)
    in_eur = analyseer(i, h, config, PEILDATUM, wisselkoersen=fx)
    assert in_usd.rekenvaluta == "USD" and in_eur.rekenvaluta == "EUR"
    assert in_usd.cagr[1] > 7 and abs(in_eur.cagr[1]) < 0.5
    assert any("Geen wisselkoersen" in m.tekst for m in in_usd.meldingen)
    assert not any("wisselkoers" in m.tekst.lower() for m in in_eur.meldingen)


def test_ververs_wisselkoersen(config):
    class NepFX:
        naam = "nep"

        def wisselkoersen(self, valuta, start, eind):
            if valuta == "JPY":
                raise bronnen.BronFout("HTTP 404")
            return pd.DataFrame({"datum": [date(2026, 11, 13)], "koers": [1.09]})

    opslag = Opslag()
    for nr, v in enumerate(["USD", "EUR", "JPY"], start=1):
        opslag.bewaar_instrument(Instrument(isin=_isin(nr), naam=v, type="aandeel", valuta=v))
    opslag.bewaar_instrument(Instrument(isin=_isin(9), naam="O", type="staatsobligatie", valuta="GBP"))
    assert benodigde_valuta(opslag) == ["JPY", "USD"]
    uit = ververs_wisselkoersen(opslag, config, NepFX())
    assert uit["USD"].startswith("ok") and uit["JPY"].startswith("mislukt")
    assert opslag.wisselkoersen("usd")["koers"].tolist() == [1.09]


# --- kengetallen ---

FUNDAMENTALS_AANDEEL = {
    "Highlights": {"PERatio": "22.5", "DividendYield": 0.0185},
    "Financials": {"Balance_Sheet": {"quarterly": {
        "2026-06-30": {"shortLongTermDebtTotal": "5000", "totalStockholderEquity": "4000"},
        "2026-09-30": {"shortLongTermDebtTotal": "6000", "totalStockholderEquity": "4000"},
    }}},
}
FUNDAMENTALS_ETF = {
    "Highlights": {},
    "ETF_Data": {"NetExpenseRatio": "0.00200", "Holdings_Count": 1450, "Yield": "1.60",
                 "Top_10_Holdings": {"A": {"Assets_%": 4.8}, "B": {"Assets_%": 3.9}}},
}


def test_lees_fundamentals():
    assert bronnen.lees_fundamentals(FUNDAMENTALS_AANDEEL) == {
        "koers_winst": 22.5, "dividendrendement": 1.85, "schuld_eigen_vermogen": 1.5}  # meest recente kwartaal
    assert bronnen.lees_fundamentals(FUNDAMENTALS_ETF) == {
        "ter": 0.2, "aantal_posities": 1450, "concentratie": 4.8, "dividendrendement": 1.6}
    assert bronnen.lees_fundamentals({}) == {} and bronnen.lees_fundamentals(None) == {}


def test_kengetallen_aanvullen_laat_handmatige_waarden_staan():
    opslag = Opslag()
    i = Instrument(isin=_isin(1), naam="ETF", type="etf", ter=0.07)
    opslag.bewaar_instrument(i)
    status = vul_kengetallen_aan(i, opslag, ophalen=lambda isin, symbool: bronnen.lees_fundamentals(FUNDAMENTALS_ETF))
    nieuw = opslag.instrument(i.isin)
    assert nieuw.ter == 0.07                                  # handmatig blijft staan
    assert nieuw.aantal_posities == 1450 and nieuw.concentratie == 4.8
    assert status.startswith("aangevuld") and "ter" not in status


def test_kengetallen_fout_en_obligatie():
    opslag = Opslag()

    def kapot(isin, symbool):
        raise bronnen.BronFout("Geen API-sleutel")

    i = Instrument(isin=_isin(1), naam="A", type="aandeel")
    assert vul_kengetallen_aan(i, opslag, ophalen=kapot).startswith("mislukt")
    o = Instrument(isin=_isin(2), naam="O", type="staatsobligatie")
    assert vul_kengetallen_aan(o, opslag).startswith("overgeslagen")
