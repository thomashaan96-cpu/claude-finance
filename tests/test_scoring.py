import random

import pytest

from data.modellen import InstrumentType
from instellingen import controleer_gewichten
from scoring.motivatie import alle_factoren, maak_motivatie
from scoring.rangschikking import KENMERKEN, bereken_score, kenmerken_van, maak_rangschikking

PROFIELEN = ["defensief", "neutraal", "offensief"]


@pytest.mark.parametrize("profiel", PROFIELEN)
def test_gewichten_tellen_op_tot_1(config, profiel):
    gewichten = config["gewichten"][profiel]
    assert sum(gewichten.values()) == pytest.approx(1.0)
    assert set(gewichten) == set(KENMERKEN)
    assert all(w >= 0 for w in gewichten.values())


def test_ongeldige_gewichten_worden_geweigerd():
    with pytest.raises(ValueError):
        controleer_gewichten({"x": {"a": 0.5, "b": 0.6}})


@pytest.mark.parametrize("profiel", PROFIELEN)
@pytest.mark.parametrize("horizon", [1, 3, 5, 10])
def test_zelfde_invoer_geeft_zelfde_rang(analyses, config, profiel, horizon):
    eerste = maak_rangschikking(analyses, config, "alle", profiel, horizon)
    geschud = list(analyses)
    random.Random(7).shuffle(geschud)
    tweede = maak_rangschikking(geschud, config, "alle", profiel, horizon)
    rang = lambda r: [(x.rang, x.analyse.instrument.isin, x.score) for x in r.regels]
    assert rang(eerste) == rang(tweede)
    assert [x.rang for x in eerste.regels] == list(range(1, len(eerste.regels) + 1))


def test_score_is_gewogen_som_tussen_0_en_100(analyses, config):
    r = maak_rangschikking(analyses, config, "alle", "neutraal", 5)
    for regel in r.regels:
        assert sum(f.gewicht for f in regel.factoren.values()) == pytest.approx(1.0)
        assert regel.score == pytest.approx(sum(100 * f.gewicht * f.genormaliseerd for f in regel.factoren.values()))
        assert 0 <= regel.score <= 100
        assert all(0 <= f.genormaliseerd <= 1 for f in regel.factoren.values())
    scores = [x.score for x in r.regels]
    assert scores == sorted(scores, reverse=True)


def test_rating_niet_van_toepassing_op_aandelen_en_etfs(analyses, config):
    r = maak_rangschikking(analyses, config, "alle", "neutraal", 5)
    for regel in r.regels:
        heeft_rating = "kredietrating" in regel.factoren
        assert heeft_rating == regel.analyse.instrument.type.is_obligatie


def test_filters_sluiten_uit_met_reden(analyses, config):
    r = maak_rangschikking(analyses, config, "alle", "neutraal", 5)
    uit = {x.analyse.instrument.naam: x for x in r.uitgesloten}
    assert set(uit) == {"Bedrijf BB", "Aandeel weinig data", "Aandeel verouderd"}
    assert "Minimale rating" in uit["Bedrijf BB"].redenen_uitsluiting[0]
    assert any("Datakwaliteit" in t for t in uit["Aandeel weinig data"].redenen_uitsluiting)
    assert any("Liquiditeit" in t for t in uit["Aandeel verouderd"].redenen_uitsluiting)
    assert all(x.rang is None and x.score is None for x in r.uitgesloten)
    assert len(r.regels) + len(r.uitgesloten) == len(analyses)


def test_categorie_filtert_op_type(analyses, config):
    r = maak_rangschikking(analyses, config, "etf's", "neutraal", 5)
    assert {x.analyse.instrument.type for x in r.regels} == {InstrumentType.ETF}
    assert len(r.regels) == 3


def test_profiel_verandert_de_volgorde(analyses, config):
    defensief = maak_rangschikking(analyses, config, "alle", "defensief", 5)
    offensief = maak_rangschikking(analyses, config, "alle", "offensief", 5)
    assert [x.analyse.instrument.isin for x in defensief.regels] != [x.analyse.instrument.isin for x in offensief.regels]
    # Offensief zet het hoogste verwachte rendement bovenaan vergeleken met defensief
    rendement = lambda r: r.regels[0].kenmerken["verwacht_rendement"]
    assert rendement(offensief) >= rendement(defensief)


def test_hoger_gewicht_is_monotoon(analyses, config):
    """Een instrument dat op één kenmerk het beste is, scoort hoger als dat kenmerk zwaarder weegt."""
    r = maak_rangschikking(analyses, config, "aandelen", "offensief", 5)
    beste = max(r.regels, key=lambda x: x.kenmerken["verwacht_rendement"])
    assert beste.factoren["verwacht_rendement"].genormaliseerd == 1.0


# --- motivatie ---

@pytest.mark.parametrize("profiel", PROFIELEN)
def test_elke_factor_in_motivatie_komt_uit_de_berekening(analyses, config, profiel):
    r = maak_rangschikking(analyses, config, "alle", profiel, 5)
    for regel in r.regels:
        m = maak_motivatie(regel, r)
        assert len(m.factoren) == 3
        ruwe = kenmerken_van(regel.analyse, r.horizon)
        for f in m.factoren:
            assert f.naam in KENMERKEN and f.naam in r.gewichten
            berekend = regel.factoren[f.naam]            # dezelfde factor als in de score
            assert f.waarde == berekend.waarde == ruwe[f.naam]
            assert f.gewicht == berekend.gewicht
            assert f.bijdrage == pytest.approx(berekend.bijdrage)
            assert f.mediaan == r.medianen[regel.analyse.instrument.type][f.naam]
            assert f.label in f.tekst


def test_motivatie_factoren_tellen_op_tot_de_score(analyses, config):
    r = maak_rangschikking(analyses, config, "alle", "neutraal", 5)
    for regel in r.regels:
        alle = alle_factoren(regel, r)
        assert {f.naam for f in alle} == set(regel.factoren)
        assert sum(f.bijdrage for f in alle) == pytest.approx(regel.score)
        assert regel.score == pytest.approx(bereken_score(regel.factoren))
        invloed = [abs(f.invloed) for f in alle]
        assert invloed == sorted(invloed, reverse=True)       # top 3 = sterkste factoren


def test_motivatie_bevat_filters_datum_en_bron(analyses, config):
    r = maak_rangschikking(analyses, config, "alle", "neutraal", 5)
    for regel in r.regels + r.uitgesloten:
        m = maak_motivatie(regel, r)
        assert [f.naam for f in m.filters] == ["Datakwaliteit", "Liquiditeit", "Minimale rating"]
        assert m.laatste_koersdatum == regel.analyse.laatste_datum and m.bron == "test"
    assert all(maak_motivatie(x, r).factoren == [] for x in r.uitgesloten)


def test_motivatie_waarschuwt_bij_beperkte_of_verouderde_data(analyses, config):
    r = maak_rangschikking(analyses, config, "alle", "neutraal", 5)
    per_naam = {x.analyse.instrument.naam: maak_motivatie(x, r) for x in r.regels + r.uitgesloten}
    assert any("Verouderde koers" in w for w in per_naam["Aandeel verouderd"].waarschuwingen)
    assert any("TER" in w or "Kosten" in w for w in per_naam["ETF zonder TER"].waarschuwingen)
    assert any("Volatiliteit is geschat" in w for w in per_naam["Staat 2031"].waarschuwingen)
    assert any("Hoge schuld" in w for w in per_naam["Aandeel B"].waarschuwingen)


def test_korte_historie_haalt_datakwaliteit_niet(config):
    from analyse.instrument import analyseer
    from data.demo import _isin
    from data.modellen import Instrument
    from tests.conftest import PEILDATUM, maak_historie
    a = analyseer(Instrument(isin=_isin(20), naam="Jong", type="aandeel"), maak_historie(jaren=2), config, PEILDATUM)
    r = maak_rangschikking([a], config, "alle", "neutraal", 5)
    assert not r.regels and "jaar koershistorie" in r.uitgesloten[0].redenen_uitsluiting[0]
