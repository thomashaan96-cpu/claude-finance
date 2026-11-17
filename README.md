# Beleggingsdashboard

Lokaal Streamlit-dashboard om aandelen, ETF's, staatsobligaties en bedrijfsobligaties te analyseren
en te rangschikken als koopkandidaat. Het dashboard voert geen orders uit en heeft geen koppeling
met een broker.

> **Dit is geen financieel advies.** De rangschikking ordent instrumenten op basis van historie en
> aannames. Elke uitkomst is een schatting, geen garantie.

## Installatie en starten

Vereist: Python 3.12 of hoger.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Tests draaien: `python -m pytest`

Zonder eigen data kun je op het tabblad **Instrumenten** onder *Demodata laden* twaalf fictieve
instrumenten toevoegen.

## Gebruik

| Tabblad | Functie |
|---|---|
| Instrumenten | Instrumentenlijst beheren (CSV of handmatig), koersen importeren, validatiemeldingen |
| Analyse | Rendement, risico, obligatiekenmerken, liquiditeit en scenario's per instrument |
| Rangschikking | Knop **Rangschikking maken** met categorie, risicoprofiel en horizon; per rij een sectie *Waarom?* |

### Invoer

- **Instrumentenlijst (CSV):** kolommen `isin, naam, type, valuta, bron`; optioneel `symbool, koers_winst,
  dividendrendement, schuld_eigen_vermogen, ter, aantal_posities, concentratie, couponrente,
  couponfrequentie, vervaldatum, koers, ytm, rating, duration`. Voorbeeld: `voorbeelden/instrumenten.csv`.
- **Koersen (CSV):** kolommen `datum, koers` en eventueel `isin`. Voorbeeld: `voorbeelden/koersen.csv`.
- Komma of puntkomma als scheidingsteken; decimale komma's zijn toegestaan.
- Obligatiekoersen staan in % van nominaal. Een handmatig ingevulde obligatiekoers geldt als koers van vandaag.

### Databronnen

Alle bronnen implementeren de interface `PrijsBron` (`koers`, `historie`, `rente`) in `data/bronnen.py`.

| Bron | Levert | Sleutel |
|---|---|---|
| `ecb` | Rentecurve eurozone (ECB Data Portal) | geen |
| `eodhd` | Aandelen en ETF's | omgevingsvariabele `EODHD_API_KEY` |
| `twelvedata` | Aandelen en ETF's | omgevingsvariabele `TWELVEDATA_API_KEY` |
| `eodhd_eubond` | Obligaties (EODHD EUBOND) | `EODHD_API_KEY` |
| `eulerpool` | Obligaties (Eulerpool); adressen instelbaar in `config.yaml` | `EULERPOOL_API_KEY` |
| `csv` / `handmatig` | Alles, via import | geen |

Daarnaast:

- **Wisselkoersen:** ECB-referentiekoersen (gratis), via de knop *Wisselkoersen ophalen (ECB)* of als CSV
  (`datum, valuta, koers` = eenheden valuta per euro; voorbeeld `voorbeelden/wisselkoersen.csv`).
- **Kengetallen:** koers/winst, dividendrendement, schuld/eigen vermogen, TER, aantal posities en grootste
  positie via EODHD fundamentals (tabblad Instrumenten). Alleen lege velden worden aangevuld.

Koersen worden lokaal in SQLite bewaard (`storage/dashboard.db`). Ze worden alleen opgehaald via de
knop **Koersen verversen** of automatisch wanneer de laatste poging ouder is dan 7 dagen
(`verversen_na_dagen` in `config.yaml`).

## Rekenmethode

Alle aannames en gewichten staan in `config.yaml`.

**Scenario's (inleg €10.000, horizon 1, 3, 5 en 10 jaar)**

- *Aandelen en ETF's:* neutraal is het historisch gemiddelde rendement. Goed en slecht liggen één
  standaardafwijking erboven en eronder, gemeten over de horizon:
  `eindbedrag = inleg × exp(m·h ± s·√h)`.
- *Obligaties:* neutraal is een gelijkblijvende rente (rendement = yield). Goed en slecht volgen uit
  een directe renteverandering van ±1 procentpunt: `eindbedrag = inleg × (1 − D·Δ) × (1 + y + Δ)^h`.
  Bij bedrijfsobligaties gaat in het slechte scenario een jaarlijks kredietverlies per ratingklasse af.

**Score**

`score = 100 × Σ(gewicht × genormaliseerd kenmerk)`. Elk kenmerk wordt min-max geschaald binnen de
gerangschikte groep (0 = slechtste, 1 = beste). Kredietrating telt alleen bij obligaties; bij andere
instrumenten wordt dat gewicht naar rato verdeeld. Een ontbrekend kenmerk telt als 0,5 en wordt gemeld.

| Kenmerk | Defensief | Neutraal | Offensief |
|---|---|---|---|
| Verwacht rendement (neutraal scenario) | 0,15 | 0,30 | 0,50 |
| Uitkomst in het slechte scenario | 0,25 | 0,20 | 0,10 |
| Volatiliteit | 0,20 | 0,15 | 0,10 |
| Kosten | 0,10 | 0,10 | 0,10 |
| Kredietrating | 0,15 | 0,10 | 0,05 |
| Liquiditeit | 0,10 | 0,10 | 0,10 |
| Actualiteit | 0,05 | 0,05 | 0,05 |

**Filters vóór de score** (niet gehaald = niet gerangschikt, wel getoond met reden)

- Datakwaliteit: geen validatiefouten, minimaal 60 koersen en 3 jaar historie (aandelen en ETF's).
- Liquiditeit: minimaal 10 koersen in de laatste 30 dagen (obligaties: 1).
- Minimale rating BBB- voor bedrijfsobligaties.

**Motivatie ("Waarom?")** toont de drie factoren met de grootste invloed op de score ten opzichte van
de mediaan van de eigen categorie: `invloed = 100 × gewicht × (genormaliseerd − mediaan)`.

## Beperkingen

- Aandelen en ETF's in vreemde valuta worden omgerekend naar euro (valutarisico zit in rendement, risico en
  scenario's). Obligaties in vreemde valuta worden niet omgerekend; dat wordt gemeld.
- De API-koppelingen (EODHD, Twelve Data, Eulerpool, ECB) zijn getest met voorbeeldantwoorden, niet live.
  Controleer bij Eulerpool de adressen in `config.yaml` tegen de documentatie van je abonnement.
- Obligaties worden gerekend zonder opgelopen rente; de liquiditeitsmaat is het aantal koersen, niet de verhandelde omzet.
- Volatiliteit van obligaties zonder koershistorie is een schatting (duration × rentevolatiliteit).
- Kredietratings van obligaties worden handmatig ingevoerd.

## Structuur

```
app.py            interface (tabbladen Instrumenten, Analyse, Rangschikking)
config.yaml       aannames, filters en gewichten per profiel
instellingen.py   laadt en controleert config.yaml
data/             datamodel, databronnen (PrijsBron), validatie, CSV-import, verversen, demodata
storage/          SQLite-opslag en cache
analyse/          rendement, risico, obligaties, scenario's
scoring/          rangschikking en motivatie
tests/            unit tests
voorbeelden/      voorbeeld-CSV's
```
