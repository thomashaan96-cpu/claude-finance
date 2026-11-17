"""Rooktest van de interface met Streamlit AppTest (zonder browser)."""
import pytest
from streamlit.testing.v1 import AppTest

import instellingen
from data import demo
from storage.db import Opslag


@pytest.fixture
def app(tmp_path, monkeypatch):
    config = instellingen.laad_config()
    config["algemeen"]["database"] = str(tmp_path / "test.db")
    monkeypatch.setattr(instellingen, "laad_config", lambda *a, **k: config)
    demo.laad_demo(Opslag(config["algemeen"]["database"]))
    import streamlit as st
    st.cache_resource.clear()
    st.cache_data.clear()
    return AppTest.from_file(str(instellingen.BASIS / "app.py"), default_timeout=60)


def test_app_start_en_maakt_rangschikking(app):
    app.run()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Instrumenten", "Analyse", "Rangschikking"]
    assert any("geen financieel advies" in c.value for c in app.caption)

    knop = next(b for b in app.button if b.label == "Rangschikking maken")
    knop.click().run()
    assert not app.exception
    waarom = [e.label for e in app.expander if e.label.startswith("Waarom?")]
    assert len(waarom) == 12                                   # elk instrument heeft een motivatie
    assert sum("niet gerangschikt" in w for w in waarom) == 2  # high yield (rating) en jong aandeel (historie)
    assert waarom[0].startswith("Waarom? · #1 ")
