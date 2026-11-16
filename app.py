"""Beleggingsdashboard: analyse en rangschikking van koopkandidaten.

Starten: streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pydantic import ValidationError

from analyse.instrument import InstrumentAnalyse, analyseer
from analyse.scenarios import LABEL
from data import demo, dienst
from data.bronnen import BronFout, lees_koersen_csv
from data.invoer import lees_instrumenten_csv
from data.modellen import BRONNEN, RATINGS, Instrument, InstrumentType
from data.validatie import FOUT
from instellingen import BASIS, laad_config
from scoring.motivatie import formatteer, maak_motivatie
from scoring.rangschikking import CATEGORIEEN, KENMERKEN, Rangschikking, Regel, maak_rangschikking
from storage.db import Opslag

DISCLAIMER = ("Dit is geen financieel advies. De rangschikking ordent instrumenten op basis van "
              "historie en aannames. Het dashboard voert geen orders uit.")

st.set_page_config(page_title="Beleggingsdashboard", page_icon="📊", layout="wide")


# ---------------------------------------------------------------- hulpfuncties
@st.cache_resource
def _opslag() -> Opslag:
    return Opslag(BASIS / laad_config()["algemeen"]["database"])


@st.cache_data
def _config() -> dict:
    return laad_config()


def nl(x: float | None, decimalen: int = 1, eenheid: str = "") -> str:
    """Getal in Nederlandse notatie; streepje als de waarde ontbreekt."""
    if x is None or pd.isna(x):
        return "–"
    tekst = f"{x:,.{decimalen}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return tekst + eenheid


def euro(x: float | None) -> str:
    return "–" if x is None else "€ " + nl(x, 0)


def datum_nl(d: date | None) -> str:
    return d.strftime("%d-%m-%Y") if d else "–"


def serie_kleur() -> str:
    try:
        donker = st.context.theme.type == "dark"
    except Exception:
        donker = False
    return "#3987e5" if donker else "#2a78d6"


def analyseer_alles(rf: float) -> list[InstrumentAnalyse]:
    opslag, config = _opslag(), _config()
    return [analyseer(i, opslag.historie(i.isin), config, date.today(), rf) for i in opslag.instrumenten()]


def bewaar(instrument: Instrument) -> None:
    """Slaat het instrument op; een handmatig ingevoerde obligatiekoers geldt als koers van vandaag."""
    opslag = _opslag()
    opslag.bewaar_instrument(instrument)
    if instrument.koers:
        opslag.bewaar_koersen(instrument.isin, pd.DataFrame({"datum": [date.today()], "koers": [instrument.koers]}),
                              "handmatig")


def wis_rangschikking() -> None:
    st.session_state.pop("rangschikking", None)


# ---------------------------------------------------------------- zijbalk
def zijbalk() -> float:
    opslag, config = _opslag(), _config()
    with st.sidebar:
        st.header("Instellingen")
        rf = st.number_input("Risicovrije rente (% per jaar)", min_value=-2.0, max_value=15.0,
                             value=float(config["algemeen"]["risicovrije_rente"]), step=0.1,
                             help="Gebruikt voor de Sharpe ratio.", on_change=wis_rangschikking)
        ecb = opslag.rente(1)
        if ecb:
            st.caption(f"ECB 1-jaarsrente: {nl(ecb[0], 2)}% (opgehaald {ecb[2][:10]}).")

        st.header("Data")
        st.caption(f"Koersen staan lokaal in SQLite. Verversen gebeurt op verzoek, of automatisch als de "
                   f"laatste poging ouder is dan {config['algemeen']['verversen_na_dagen']} dagen.")
        if st.button("Koersen verversen", width="stretch"):
            with st.spinner("Koersen ophalen…"):
                st.session_state["ververs_resultaat"] = dienst.ververs_alles(opslag, config)
            wis_rangschikking()
        if st.button("Rentecurve ophalen (ECB)", width="stretch"):
            with st.spinner("Rentes ophalen…"):
                st.session_state["rente_resultaat"] = dienst.ververs_rentes(opslag)
            st.rerun()
        if "rente_resultaat" in st.session_state:
            st.caption(f"Rentecurve: {st.session_state['rente_resultaat']}")
        resultaat = st.session_state.get("ververs_resultaat")
        if resultaat is not None:
            if not resultaat:
                st.caption("Geen instrumenten met een API-bron om te verversen.")
            for isin, status in resultaat.items():
                st.caption(f"{isin}: {status}")

        st.divider()
        st.warning(DISCLAIMER)
    return rf


def wekelijkse_verversing() -> None:
    """Eén keer per sessie: ververs instrumenten waarvan de laatste poging ouder is dan de termijn."""
    if st.session_state.get("wekelijks_gecontroleerd"):
        return
    st.session_state["wekelijks_gecontroleerd"] = True
    resultaat = dienst.ververs_alles(_opslag(), _config(), alleen_verouderd=True)
    if resultaat:
        st.session_state["ververs_resultaat"] = resultaat


# ---------------------------------------------------------------- tab Instrumenten
def tab_instrumenten(analyses: list[InstrumentAnalyse]) -> None:
    opslag = _opslag()
    st.subheader("Instrumentenlijst")
    if not analyses:
        st.info("Nog geen instrumenten. Voeg ze hieronder toe via CSV of handmatig, of laad de demodata.")
    else:
        st.dataframe(pd.DataFrame([{
            "ISIN": a.instrument.isin,
            "Naam": a.instrument.naam,
            "Type": a.instrument.type.label,
            "Valuta": a.instrument.valuta,
            "Bron": a.instrument.bron,
            "Laatste koers": a.laatste_koers,
            "Koersdatum": a.laatste_datum,
            "Aantal koersen": a.aantal_koersen,
            "Status": ("Fout" if any(m.niveau == FOUT for m in a.meldingen)
                       else "Waarschuwing" if a.meldingen else "In orde"),
        } for a in analyses]), hide_index=True, width="stretch",
            column_config={"Laatste koers": st.column_config.NumberColumn(format="%.2f"),
                           "Koersdatum": st.column_config.DateColumn(format="DD-MM-YYYY")})

        problemen = [(a, m) for a in analyses for m in a.meldingen]
        if problemen:
            with st.expander(f"Validatie: {len(problemen)} melding(en)"):
                for a, m in problemen:
                    (st.error if m.niveau == FOUT else st.warning)(f"**{a.instrument.naam}** ({a.instrument.isin}): {m.tekst}")

    st.subheader("Toevoegen en importeren")
    with st.expander("Instrument handmatig toevoegen of wijzigen"):
        formulier_instrument()

    with st.expander("Instrumentenlijst importeren (CSV)"):
        st.caption("Kolommen: isin, naam, type (aandeel / etf / staatsobligatie / bedrijfsobligatie), valuta, bron. "
                   "Optioneel: symbool, koers_winst, dividendrendement, schuld_eigen_vermogen, ter, aantal_posities, "
                   "concentratie, couponrente, couponfrequentie, vervaldatum, koers, ytm, rating, duration. "
                   "Zie voorbeelden/instrumenten.csv.")
        bestand = st.file_uploader("CSV met instrumenten", type="csv", key="csv_instrumenten")
        if bestand and st.button("Importeren", key="knop_instrumenten"):
            try:
                goed, fouten = lees_instrumenten_csv(bestand)
            except Exception as e:  # onleesbaar bestand
                goed, fouten = [], [f"Bestand kon niet worden gelezen: {e}"]
            for i in goed:
                bewaar(i)
            wis_rangschikking()
            st.session_state["melding"] = (f"{len(goed)} instrument(en) geïmporteerd.", fouten)
            st.rerun()

    with st.expander("Koersen importeren (CSV)"):
        st.caption("Kolommen: datum, koers en eventueel isin. Zonder kolom isin gelden de koersen voor het "
                   "gekozen instrument. Obligatiekoersen in % van nominaal. Zie voorbeelden/koersen.csv.")
        opties = {f"{a.instrument.naam} ({a.instrument.isin})": a.instrument.isin for a in analyses}
        keuze = st.selectbox("Instrument (als het bestand geen kolom isin heeft)", list(opties) or ["–"])
        bestand = st.file_uploader("CSV met koersen", type="csv", key="csv_koersen")
        if bestand and st.button("Importeren", key="knop_koersen"):
            try:
                df = lees_koersen_csv(bestand, isin=opties.get(keuze))
                bekend = {i.isin for i in opslag.instrumenten()}
                geweigerd = int((df["koers"] <= 0).sum())
                totaal, onbekend = 0, []
                for isin, groep in df.groupby("isin"):
                    if isin not in bekend:
                        onbekend.append(isin)
                        continue
                    totaal += opslag.bewaar_koersen(isin, groep, "csv")
                fouten = [f"Onbekend ISIN overgeslagen: {x}" for x in onbekend]
                if geweigerd:
                    fouten.append(f"{geweigerd} koers(en) van nul of lager geweigerd.")
                st.session_state["melding"] = (f"{totaal} koers(en) opgeslagen.", fouten)
            except BronFout as e:
                st.session_state["melding"] = ("Niets geïmporteerd.", [str(e)])
            wis_rangschikking()
            st.rerun()

    with st.expander("Instrument verwijderen"):
        opties = {f"{a.instrument.naam} ({a.instrument.isin})": a.instrument.isin for a in analyses}
        keuze = st.selectbox("Instrument", list(opties) or ["–"], key="verwijder_keuze")
        if st.button("Verwijderen (inclusief koersen)", disabled=not opties):
            opslag.verwijder_instrument(opties[keuze])
            wis_rangschikking()
            st.rerun()

    with st.expander("Demodata laden"):
        st.caption("Voegt 11 fictieve instrumenten met verzonnen koersen toe (ISIN's beginnen met XX). "
                   "Alleen bedoeld om het dashboard te verkennen.")
        if st.button("Demodata laden"):
            n = demo.laad_demo(opslag)
            wis_rangschikking()
            st.session_state["melding"] = (f"{n} demo-instrumenten geladen.", [])
            st.rerun()

    if "melding" in st.session_state:
        tekst, fouten = st.session_state.pop("melding")
        st.success(tekst)
        for f in fouten:
            st.error(f)


def formulier_instrument() -> None:
    soort = st.selectbox("Type", list(InstrumentType), format_func=lambda t: t.label, key="nieuw_type")
    with st.form("nieuw_instrument", clear_on_submit=False):
        k1, k2, k3, k4 = st.columns(4)
        isin = k1.text_input("ISIN")
        naam = k2.text_input("Naam")
        valuta = k3.text_input("Valuta", "EUR")
        bron = k4.selectbox("Bron", BRONNEN)
        symbool = st.text_input("Symbool bij de bron (optioneel, bijv. ASML.AS)")
        velden: dict = {}
        leeg = dict(value=None, placeholder="optioneel")
        if soort.is_obligatie:
            k1, k2, k3, k4 = st.columns(4)
            velden["couponrente"] = k1.number_input("Couponrente (%)", min_value=0.0, step=0.125, **leeg)
            velden["vervaldatum"] = k2.date_input("Vervaldatum", value=None, format="DD-MM-YYYY",
                                                  max_value=date(2126, 12, 31))
            velden["koers"] = k3.number_input("Koers (% van nominaal)", min_value=0.0, step=0.1, **leeg)
            velden["couponfrequentie"] = k4.selectbox("Couponbetalingen per jaar", [1, 2, 4, 12])
            k1, k2, k3 = st.columns(3)
            velden["ytm"] = k1.number_input("Yield to maturity (%)", step=0.01,
                                            value=None, placeholder="leeg = berekenen")
            velden["duration"] = k2.number_input("Modified duration (jaren)", min_value=0.0, step=0.1,
                                                 value=None, placeholder="leeg = berekenen")
            velden["rating"] = k3.selectbox("Kredietrating", [None] + RATINGS[::-1],
                                            format_func=lambda r: r or "onbekend")
        else:
            k1, k2, k3 = st.columns(3)
            velden["koers_winst"] = k1.number_input("Koers/winst", step=0.1, **leeg)
            velden["dividendrendement"] = k2.number_input("Dividendrendement (%)", min_value=0.0, step=0.1, **leeg)
            if soort == InstrumentType.ETF:
                velden["ter"] = k3.number_input("Kostenratio TER (%)", min_value=0.0, step=0.01, **leeg)
                k1, k2 = st.columns(2)
                velden["aantal_posities"] = k1.number_input("Aantal posities (spreiding)", min_value=1, step=1, **leeg)
                velden["concentratie"] = k2.number_input("Grootste positie (%)", min_value=0.0, max_value=100.0, **leeg)
            else:
                velden["schuld_eigen_vermogen"] = k3.number_input("Schuld/eigen vermogen", min_value=0.0, step=0.1, **leeg)
                velden["concentratie"] = st.number_input(
                    "Concentratie (%): aandeel van grootste klant, product of regio in de omzet",
                    min_value=0.0, max_value=100.0, **leeg)
        if st.form_submit_button("Opslaan"):
            try:
                gegevens = {k: v for k, v in velden.items() if v is not None}
                bewaar(Instrument(isin=isin, naam=naam, type=soort, valuta=valuta, bron=bron,
                                  symbool=symbool.strip() or None, **gegevens))
                wis_rangschikking()
                st.session_state["melding"] = (f"{naam} opgeslagen.", [])
                st.rerun()
            except ValidationError as e:
                for fout in e.errors():
                    st.error(fout["msg"].removeprefix("Value error, "))


# ---------------------------------------------------------------- tab Analyse
def tab_analyse(analyses: list[InstrumentAnalyse], rf: float) -> None:
    if not analyses:
        st.info("Voeg eerst instrumenten toe op het tabblad Instrumenten.")
        return
    opties = {f"{a.instrument.naam} · {a.instrument.type.label} · {a.instrument.isin}": a for a in analyses}
    a = opties[st.selectbox("Instrument", list(opties))]
    i = a.instrument

    for m in a.meldingen:
        (st.error if m.niveau == FOUT else st.warning)(m.tekst)
    for s in a.signalen:
        st.warning(f"Signaal: {s}")

    st.subheader("Rendement en risico")
    k = st.columns(4)
    for kolom, jaren in zip(k, _config()["algemeen"]["horizons"]):
        kolom.metric(f"CAGR {jaren} jaar", nl(a.cagr.get(jaren), 1, "%"))
    k = st.columns(4)
    k[0].metric("Volatiliteit (per jaar)", nl(a.volatiliteit, 1, "%"),
                help=f"Herkomst: {a.volatiliteit_herkomst}.")
    k[1].metric("Maximaal verlies", nl(a.max_drawdown, 1, "%"), help="Grootste daling van top naar dal in de historie.")
    k[2].metric("Sharpe ratio", nl(a.sharpe, 2), help=f"Bij een risicovrije rente van {nl(rf, 1)}%.")
    k[3].metric("Historie", nl(a.historie_jaren, 1, " jaar"))
    if all(v is None for v in a.cagr.values()):
        st.caption("CAGR is niet te berekenen: de koershistorie is korter dan 1 jaar.")

    if i.type.is_obligatie:
        st.subheader("Obligatiekenmerken")
        o = a.obligatie
        if o is None:
            st.info("Obligatiekenmerken zijn niet te berekenen (vervaldatum ontbreekt of is verstreken).")
        else:
            k = st.columns(4)
            k[0].metric("Yield to maturity", nl(o.ytm, 2, "%"), help=f"Herkomst: {o.ytm_herkomst}.")
            k[1].metric("Modified duration", nl(o.duration, 2, " jaar"), help=f"Herkomst: {o.duration_herkomst}.")
            k[2].metric("Koers bij rente +1%", nl(o.prijs_bij_plus_1, 1, "%"))
            k[3].metric("Koers bij rente −1%", nl(o.prijs_bij_min_1, 1, "%"))
            k = st.columns(4)
            k[0].metric("Kredietrating", o.rating or "onbekend")
            k[1].metric("Couponrente", nl(i.couponrente, 2, "%"))
            k[2].metric("Resterende looptijd", nl(o.looptijd, 1, " jaar"))
            k[3].metric("Vervaldatum", datum_nl(i.vervaldatum))
    elif i.type == InstrumentType.ETF:
        st.subheader("Kosten en spreiding")
        k = st.columns(4)
        k[0].metric("Kostenratio (TER)", nl(i.ter, 2, "%"))
        k[1].metric("Aantal posities", nl(i.aantal_posities, 0))
        k[2].metric("Grootste positie", nl(i.concentratie, 1, "%"))
        k[3].metric("Dividendrendement", nl(i.dividendrendement, 1, "%"))
    else:
        st.subheader("Kengetallen")
        k = st.columns(4)
        k[0].metric("Koers/winst", nl(i.koers_winst, 1))
        k[1].metric("Dividendrendement", nl(i.dividendrendement, 1, "%"))
        k[2].metric("Schuld/eigen vermogen", nl(i.schuld_eigen_vermogen, 2))
        k[3].metric("Concentratie", nl(i.concentratie, 1, "%"))

    st.subheader("Liquiditeit en actualiteit")
    venster = _config()["filters"]["liquiditeit_venster_dagen"]
    k = st.columns(4)
    k[0].metric("Laatste koers", nl(a.laatste_koers, 2, f" {'%' if i.type.is_obligatie else i.valuta}"))
    k[1].metric("Koersdatum", datum_nl(a.laatste_datum))
    k[2].metric("Leeftijd laatste koers", nl(a.leeftijd_dagen, 0, " dagen"))
    k[3].metric(f"Koersen laatste {venster} dagen", nl(a.koersen_recent, 0))
    st.caption(f"Bron van de laatste koers: {a.koersbron or 'onbekend'}.")

    historie = _opslag().historie(i.isin)
    if len(historie) >= 2:
        st.subheader("Koersverloop")
        eenheid = "% van nominaal" if i.type.is_obligatie else i.valuta
        fig = go.Figure(go.Scatter(
            x=historie["datum"], y=historie["koers"], mode="lines", name=i.naam,
            line=dict(color=serie_kleur(), width=2),
            hovertemplate="%{x|%d-%m-%Y}<br>%{y:,.2f} " + eenheid + "<extra></extra>"))
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                          yaxis_title=f"Koers ({eenheid})", hovermode="x unified", separators=",.")
        st.plotly_chart(fig, width="stretch", theme="streamlit")

    st.subheader(f"Scenario's bij een inleg van {euro(_config()['algemeen']['startbedrag'])}")
    if not a.scenarios:
        st.info("Scenario's zijn niet te berekenen: er is te weinig data.")
        return
    if i.type.is_obligatie:
        st.caption(f"Neutraal: de rente blijft gelijk. Goed en slecht: de rente verandert direct met "
                   f"±{nl(_config()['scenario']['renteschok'], 1)} procentpunt; effect via duration en couponrente.")
    else:
        st.caption("Neutraal: historisch gemiddeld rendement. Goed en slecht: één standaardafwijking "
                   "erboven en eronder, gemeten over de horizon.")
    scen = list(a.scenarios.values())
    st.dataframe(pd.DataFrame([{
        "Horizon": f"{s.horizon} jaar",
        "Slecht": euro(s.eind_slecht),
        "Neutraal": euro(s.eind_neutraal),
        "Goed": euro(s.eind_goed),
        "Bandbreedte": euro(s.bandbreedte),
        "Rendement slecht": nl(s.rendement_slecht, 1, "% p.j."),
        "Rendement neutraal": nl(s.rendement_neutraal, 1, "% p.j."),
        "Rendement goed": nl(s.rendement_goed, 1, "% p.j."),
    } for s in scen]), hide_index=True, width="stretch")

    kleur = serie_kleur()
    x = [f"{s.horizon} jaar" for s in scen]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=[s.bandbreedte for s in scen], base=[s.eind_slecht for s in scen], width=0.35,
        name="Bandbreedte slecht – goed", marker=dict(color=kleur, opacity=0.3, line_width=0),
        customdata=[[euro(s.eind_slecht), euro(s.eind_goed)] for s in scen],
        hovertemplate="Slecht: %{customdata[0]}<br>Goed: %{customdata[1]}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=[s.eind_neutraal for s in scen], mode="markers", name="Neutraal",
        marker=dict(color=kleur, size=11, line=dict(color="rgba(0,0,0,0)", width=0)),
        customdata=[euro(s.eind_neutraal) for s in scen],
        hovertemplate="Neutraal: %{customdata}<extra></extra>"))
    fig.add_hline(y=scen[0].start, line_dash="dot", line_width=1, line_color="gray",
                  annotation_text="Inleg", annotation_position="bottom left")
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Eindbedrag (€)",
                      separators=",.", legend=dict(orientation="h", y=1.1), xaxis_title="Horizon")
    fig.update_yaxes(tickformat=",.0f")
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    st.caption(f"**{LABEL}**")


# ---------------------------------------------------------------- tab Rangschikking
def tab_rangschikking(rf: float) -> None:
    config = _config()
    with st.form("rangschikking_formulier"):
        k1, k2, k3 = st.columns(3)
        categorie = k1.selectbox("Categorie", list(CATEGORIEEN), format_func=str.capitalize)
        profiel = k2.selectbox("Risicoprofiel", list(config["gewichten"]), index=1, format_func=str.capitalize)
        horizon = k3.selectbox("Horizon", config["algemeen"]["horizons"], index=2, format_func=lambda h: f"{h} jaar")
        if st.form_submit_button("Rangschikking maken", type="primary"):
            st.session_state["rangschikking"] = (
                maak_rangschikking(analyseer_alles(rf), config, categorie, profiel, horizon), datetime.now())

    if "rangschikking" not in st.session_state:
        st.info("Kies categorie, risicoprofiel en horizon en klik op **Rangschikking maken**.")
        return
    r, moment = st.session_state["rangschikking"]
    st.caption(f"Rangschikking van {moment:%d-%m-%Y %H:%M} · categorie: {r.categorie} · profiel: {r.profiel} · "
               f"horizon: {r.horizon} jaar. {LABEL}")

    with st.expander("Scoreformule en gewichten"):
        st.markdown("Score = 100 × som(gewicht × genormaliseerd kenmerk). Elk kenmerk is geschaald van 0 "
                    "(slechtste in deze rangschikking) tot 1 (beste). Kredietrating telt alleen bij obligaties; "
                    "bij andere instrumenten wordt dat gewicht naar rato verdeeld over de overige kenmerken.")
        st.dataframe(pd.DataFrame([{"Kenmerk": KENMERKEN[n].label, "Gewicht": f"{w:.0%}",
                                    "Beter bij": "hoger" if KENMERKEN[n].hoger_is_beter else "lager"}
                                   for n, w in r.gewichten.items()]), hide_index=True)

    if not r.regels:
        st.warning("Geen enkel instrument haalt alle filters.")
    else:
        k1, k2, k3 = st.columns([2, 2, 1])
        zoek = k1.text_input("Filter op naam of ISIN").strip().lower()
        soorten = sorted({x.analyse.instrument.type.label for x in r.regels})
        gekozen = k2.multiselect("Categorie", soorten, default=soorten)
        min_score = k3.number_input("Minimale score", 0, 100, 0, step=5)
        zichtbaar = [x for x in r.regels
                     if x.analyse.instrument.type.label in gekozen and x.score >= min_score
                     and (zoek in x.analyse.instrument.naam.lower() or zoek in x.analyse.instrument.isin.lower())]

        st.dataframe(pd.DataFrame([_tabelrij(x) for x in zichtbaar]), hide_index=True, width="stretch",
                     column_config={
                         "Score": st.column_config.ProgressColumn(format="%.1f", min_value=0, max_value=100),
                         "Verwacht rendement (% p.j.)": st.column_config.NumberColumn(format="%.1f"),
                         "Slecht scenario (%)": st.column_config.NumberColumn(format="%.1f"),
                         "Volatiliteit (%)": st.column_config.NumberColumn(format="%.1f"),
                         "Kosten (%)": st.column_config.NumberColumn(format="%.2f"),
                     })
        st.caption("Klik op een kolomkop om te sorteren. 'Slecht scenario' is de totale waardeverandering "
                   "over de horizon in het slechte scenario.")

        st.subheader("Motivatie per instrument")
        for x in zichtbaar:
            i = x.analyse.instrument
            with st.expander(f"Waarom? · #{x.rang} {i.naam} · score {nl(x.score, 1)}"):
                toon_motivatie(x, r)

    if r.uitgesloten:
        st.subheader("Niet gerangschikt")
        st.caption("Deze instrumenten halen een of meer filters niet en hebben daarom geen score.")
        st.dataframe(pd.DataFrame([{
            "Naam": x.analyse.instrument.naam, "ISIN": x.analyse.instrument.isin,
            "Categorie": x.analyse.instrument.type.label, "Reden": " | ".join(x.redenen_uitsluiting),
        } for x in r.uitgesloten]), hide_index=True, width="stretch")
        for x in r.uitgesloten:
            with st.expander(f"Waarom? · {x.analyse.instrument.naam} · niet gerangschikt"):
                toon_motivatie(x, r)


def _tabelrij(x: Regel) -> dict:
    i, k = x.analyse.instrument, x.kenmerken
    return {
        "Rang": x.rang, "Naam": i.naam, "ISIN": i.isin, "Categorie": i.type.label, "Score": x.score,
        "Verwacht rendement (% p.j.)": k["verwacht_rendement"],
        "Slecht scenario (%)": k["verlies_slecht"],
        "Volatiliteit (%)": k["volatiliteit"],
        "Kosten (%)": k["kosten"],
        "Rating": i.rating if i.type.is_obligatie else None,
        "Recente koersen": int(k["liquiditeit"]),
        "Leeftijd koers (dagen)": int(k["actualiteit"]) if k["actualiteit"] is not None else None,
    }


def toon_motivatie(regel: Regel, r: Rangschikking) -> None:
    m = maak_motivatie(regel, r)
    soort = regel.analyse.instrument.type.label
    soort = soort if soort == "ETF" else soort.lower()
    if m.factoren:
        st.markdown(f"**De drie factoren die de score het sterkst bepalen** (vergeleken met de mediaan van de "
                    f"categorie {soort} in deze rangschikking):")
        st.dataframe(pd.DataFrame([{
            "Factor": f.label,
            "Waarde": formatteer(f.naam, f.waarde),
            "Mediaan categorie": formatteer(f.naam, f.mediaan),
            "Gewicht": f"{f.gewicht:.0%}",
            "Bijdrage aan score": nl(f.bijdrage, 1),
            "Effect t.o.v. mediaan": ("neutraal (waarde ontbreekt)" if f.ontbreekt else
                                      "gelijk" if abs(f.invloed) < 0.05 else
                                      f"{'▲ +' if f.invloed > 0 else '▼ −'}{nl(abs(f.invloed), 1)} punten"),
        } for f in m.factoren]), hide_index=True, width="stretch")
    st.markdown("**Filters**")
    for f in m.filters:
        if not f.van_toepassing:
            st.markdown(f"➖ {f.naam}: {f.toelichting}")
        else:
            st.markdown(f"{'✅ Gehaald' if f.gehaald else '❌ Niet gehaald'} · {f.naam}: {f.toelichting}")
    st.markdown(f"**Laatste koers:** {datum_nl(m.laatste_koersdatum)} · **Bron:** {m.bron or 'onbekend'}")
    for w in m.waarschuwingen:
        st.warning(w)


# ---------------------------------------------------------------- opbouw
def main() -> None:
    st.title("Beleggingsdashboard")
    st.caption(DISCLAIMER)
    wekelijkse_verversing()
    rf = zijbalk()
    analyses = analyseer_alles(rf)
    t1, t2, t3 = st.tabs(["Instrumenten", "Analyse", "Rangschikking"])
    with t1:
        tab_instrumenten(analyses)
    with t2:
        tab_analyse(analyses, rf)
    with t3:
        tab_rangschikking(rf)


main()
