import io
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from data import bronnen
from data.demo import _isin, laad_demo
from data.dienst import ververs_alles, ververs_instrument
from data.invoer import lees_instrumenten_csv
from data.modellen import Instrument, isin_geldig, rating_waarde
from data.validatie import FOUT, WAARSCHUWING, valideer
from storage.db import Opslag
from tests.conftest import PEILDATUM, koers_punt, maak_historie


def test_isin_validatie():
    assert isin_geldig("US0378331005")
    assert isin_geldig("NL0010273215")
    assert not isin_geldig("US0378331006")   # fout controlecijfer
    assert not isin_geldig("0378331005")
    with pytest.raises(ValidationError):
        Instrument(isin="ONGELDIG", naam="x", type="aandeel")


def test_koers_nul_of_lager_geweigerd():
    with pytest.raises(ValidationError):
        Instrument(isin=_isin(1), naam="x", type="staatsobligatie", koers=0)


def test_rating_volgorde():
    assert rating_waarde("AAA") > rating_waarde("BBB-") > rating_waarde("BB+") > rating_waarde("D")
    assert rating_waarde(None) is None


def test_validatie_meldingen(config):
    aandeel = Instrument(isin=_isin(1), naam="A", type="aandeel")
    leeg = pd.DataFrame(columns=["datum", "koers"])
    assert any(m.niveau == FOUT and "Geen koersdata" in m.tekst for m in valideer(aandeel, leeg, PEILDATUM, config))

    oud = maak_historie(eind=date(2026, 6, 1))
    assert any(m.niveau == WAARSCHUWING and "Verouderde" in m.tekst for m in valideer(aandeel, oud, PEILDATUM, config))

    negatief = pd.DataFrame({"datum": [PEILDATUM], "koers": [-1.0]})
    assert any("nul of lager" in m.tekst for m in valideer(aandeel, negatief, PEILDATUM, config))

    obligatie = Instrument(isin=_isin(2), naam="O", type="bedrijfsobligatie")
    teksten = [m.tekst for m in valideer(obligatie, koers_punt(99), PEILDATUM, config)]
    assert any("Vervaldatum" in t for t in teksten) and any("Couponrente" in t for t in teksten)


def test_instrumenten_csv_met_fouten():
    csv = io.StringIO(
        "isin;naam;type;valuta;bron;ter;couponrente;vervaldatum;rating\n"
        f"{_isin(1)};ETF Wereld;ETF;EUR;csv;0,20;;;\n"
        f"{_isin(2)};Staat;staatsobligatie;EUR;csv;;2,5%;2031-07-15;AAA\n"
        "FOUT;Kapot;aandeel;EUR;csv;;;;\n"
    )
    goed, fouten = lees_instrumenten_csv(csv)
    assert [i.naam for i in goed] == ["ETF Wereld", "Staat"]
    assert goed[0].ter == 0.20 and goed[1].couponrente == 2.5 and goed[1].vervaldatum == date(2031, 7, 15)
    assert len(fouten) == 1 and "Regel 4" in fouten[0] and "ISIN" in fouten[0]


def test_koersen_csv_en_csvbron():
    csv = io.StringIO("datum;koers\n2026-11-10;101,5\n2026-11-11;102,0\nkapot;1\n")
    df = bronnen.lees_koersen_csv(csv, isin=_isin(1))
    assert len(df) == 2 and df["koers"].iloc[0] == 101.5
    bron = bronnen.CSVBron(df, rentes=pd.DataFrame({"looptijd": [1, 10], "rente": [2.1, 2.9]}))
    assert bron.koers(_isin(1)) == (date(2026, 11, 11), 102.0)
    assert len(bron.historie(_isin(1), date(2026, 11, 11), date(2026, 11, 30))) == 1
    assert bron.rente(8) == 2.9


def test_api_antwoorden_parsen():
    eod = [{"date": "2026-11-12", "close": 10, "adjusted_close": 9.5}, {"date": "2026-11-13", "close": 11, "adjusted_close": 10.5}]
    assert bronnen.EODHDBron.lees_json(eod)["koers"].tolist() == [9.5, 10.5]
    td = {"values": [{"datetime": "2026-11-13", "close": "11.0"}, {"datetime": "2026-11-12", "close": "10.0"}]}
    assert bronnen.TwelveDataBron.lees_json(td)["koers"].tolist() == [10.0, 11.0]
    with pytest.raises(bronnen.BronFout):
        bronnen.TwelveDataBron.lees_json({"status": "error", "message": "limiet"})
    assert bronnen.ECBBron.lees_csv("KEY,TIME_PERIOD,OBS_VALUE\nx,2026-11-13,2.71\n") == 2.71
    assert bronnen.ECBBron.sleutel(9.6) == "SR_10Y" and bronnen.ECBBron.sleutel(0.3) == "SR_3M"
    assert bronnen.EODHDObligatieBron().symbool("XS1234567890") == "XS1234567890.EUBOND"


def test_bron_zonder_sleutel_geeft_bronfout(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(bronnen.BronFout):
        bronnen.EODHDBron().historie(_isin(1), date(2026, 1, 1), date(2026, 2, 1))


def test_opslag_rondreis():
    opslag = Opslag()
    i = Instrument(isin=_isin(1), naam="A", type="etf", ter=0.2)
    opslag.bewaar_instrument(i)
    assert opslag.instrumenten() == [i]
    df = pd.DataFrame({"datum": [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)], "koers": [10.0, 0.0, 11.0]})
    assert opslag.bewaar_koersen(i.isin, df, "csv") == 2     # koers 0 geweigerd
    opslag.bewaar_koersen(i.isin, df.tail(1).assign(koers=12.0), "csv")
    assert opslag.historie(i.isin)["koers"].tolist() == [10.0, 12.0]   # bijwerken, geen dubbelen
    opslag.verwijder_instrument(i.isin)
    assert opslag.instrumenten() == [] and opslag.historie(i.isin).empty


def test_verversen_alleen_wekelijks(config):
    opslag = Opslag()
    assert opslag.moet_verversen("x")
    nu = datetime(2026, 11, 16, 12)
    opslag.noteer_verversing("x", "ok", nu)
    assert not opslag.moet_verversen("x", 7, nu + timedelta(days=6))
    assert opslag.moet_verversen("x", 7, nu + timedelta(days=7))


def test_ververs_via_bron_en_cache(config):
    class NepBron(bronnen.PrijsBron):
        naam = "nep"
        aanroepen = 0

        def historie(self, isin, start, eind):
            NepBron.aanroepen += 1
            return maak_historie(jaren=1)[["datum", "koers"]]

        def koers(self, isin):
            return self._laatste(isin)

        def rente(self, looptijd):
            return 2.5

    opslag = Opslag()
    i = Instrument(isin=_isin(1), naam="A", type="aandeel", bron="eodhd")
    opslag.bewaar_instrument(i)
    assert ververs_instrument(i, opslag, config, NepBron()).startswith("ok")
    assert len(opslag.historie(i.isin)) == 252
    # Tweede ronde binnen een week: niets ophalen
    assert ververs_alles(opslag, config, alleen_verouderd=True, bron_fabriek=lambda _: NepBron()) == {}
    assert NepBron.aanroepen == 1


def test_demo_laden():
    opslag = Opslag()
    assert laad_demo(opslag, PEILDATUM) == len(opslag.instrumenten()) == 12
    assert opslag.valuta_met_wisselkoersen() == ["USD"]
