"""Databronnen achter de interface PrijsBron.

Elke bron levert koersen en/of rentes. Wat een bron niet kan, geeft NietOndersteund.
API-sleutels komen uit omgevingsvariabelen: EODHD_API_KEY en TWELVEDATA_API_KEY.
"""
from __future__ import annotations

import io
import os
from abc import ABC, abstractmethod
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

KOLOMMEN = ["datum", "koers"]
TIMEOUT = 20


class BronFout(Exception):
    """De bron kon de gevraagde data niet leveren."""


class NietOndersteund(BronFout):
    """De bron ondersteunt deze methode niet."""


def _schoon(df: pd.DataFrame) -> pd.DataFrame:
    """Uniform formaat: kolommen datum (date) en koers (float), oplopend, zonder dubbelen."""
    if df is None or df.empty:
        return pd.DataFrame(columns=KOLOMMEN)
    df = df[KOLOMMEN].copy()
    df["datum"] = pd.to_datetime(df["datum"], errors="coerce").dt.date
    df["koers"] = pd.to_numeric(df["koers"], errors="coerce")
    df = df.dropna().drop_duplicates("datum", keep="last").sort_values("datum")
    return df.reset_index(drop=True)


class PrijsBron(ABC):
    naam: str = "bron"

    @abstractmethod
    def koers(self, isin: str) -> tuple[date, float]:
        """Laatste koers: (datum, koers)."""

    @abstractmethod
    def historie(self, isin: str, start: date, eind: date) -> pd.DataFrame:
        """Koershistorie met kolommen datum en koers."""

    @abstractmethod
    def rente(self, looptijd: float) -> float:
        """Rente in % voor een looptijd in jaren."""

    def _laatste(self, isin: str, dagen: int = 30) -> tuple[date, float]:
        vandaag = date.today()
        df = self.historie(isin, vandaag - timedelta(days=dagen), vandaag)
        if df.empty:
            raise BronFout(f"Geen recente koers voor {isin} bij {self.naam}")
        rij = df.iloc[-1]
        return rij["datum"], float(rij["koers"])


def _get(url: str, params: dict, sessie=None) -> requests.Response:
    try:
        r = (sessie or requests).get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise BronFout(f"Verbinding mislukt: {e.__class__.__name__}") from e
    if r.status_code != 200:
        raise BronFout(f"HTTP {r.status_code} van {url.split('/')[2]}")
    return r


class ECBBron(PrijsBron):
    """ECB Data Portal: spotrentes van de eurozone-rentecurve (AAA-staatsobligaties). Gratis."""

    naam = "ecb"
    URL = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{sleutel}"
    LOOPTIJDEN = {0.25: "SR_3M", 0.5: "SR_6M", **{float(j): f"SR_{j}Y" for j in range(1, 31)}}

    def __init__(self, sessie=None):
        self.sessie = sessie

    def koers(self, isin):
        raise NietOndersteund("De ECB levert alleen rentes.")

    def historie(self, isin, start, eind):
        raise NietOndersteund("De ECB levert alleen rentes.")

    @classmethod
    def sleutel(cls, looptijd: float) -> str:
        dichtst = min(cls.LOOPTIJDEN, key=lambda j: abs(j - looptijd))
        return cls.LOOPTIJDEN[dichtst]

    def rente(self, looptijd: float) -> float:
        url = self.URL.format(sleutel=self.sleutel(looptijd))
        r = _get(url, {"lastNObservations": 1, "format": "csvdata"}, self.sessie)
        return self.lees_csv(r.text)

    @staticmethod
    def lees_csv(tekst: str) -> float:
        df = pd.read_csv(io.StringIO(tekst))
        if df.empty or "OBS_VALUE" not in df.columns:
            raise BronFout("Onverwacht antwoord van de ECB")
        return float(df["OBS_VALUE"].iloc[-1])


class EODHDBron(PrijsBron):
    """EODHD: dagkoersen van aandelen en ETF's. Symbool via instrument of zoekopdracht op ISIN."""

    naam = "eodhd"
    BASIS = "https://eodhd.com/api"

    def __init__(self, sleutel: Optional[str] = None, symbolen: Optional[dict[str, str]] = None, sessie=None):
        self.api_sleutel = sleutel or os.environ.get("EODHD_API_KEY")
        self.symbolen = dict(symbolen or {})
        self.sessie = sessie

    def _sleutel(self) -> str:
        if not self.api_sleutel:
            raise BronFout("Geen API-sleutel: stel EODHD_API_KEY in.")
        return self.api_sleutel

    def symbool(self, isin: str) -> str:
        if isin in self.symbolen:
            return self.symbolen[isin]
        r = _get(f"{self.BASIS}/search/{isin}", {"api_token": self._sleutel(), "fmt": "json"}, self.sessie)
        treffers = r.json()
        if not treffers:
            raise BronFout(f"ISIN {isin} niet gevonden bij EODHD")
        t = treffers[0]
        self.symbolen[isin] = f"{t['Code']}.{t['Exchange']}"
        return self.symbolen[isin]

    def historie(self, isin, start, eind):
        params = {"api_token": self._sleutel(), "fmt": "json", "from": start.isoformat(), "to": eind.isoformat()}
        r = _get(f"{self.BASIS}/eod/{self.symbool(isin)}", params, self.sessie)
        return self.lees_json(r.json())

    @staticmethod
    def lees_json(data: list[dict]) -> pd.DataFrame:
        if not data:
            return pd.DataFrame(columns=KOLOMMEN)
        df = pd.DataFrame(data)
        kolom = "adjusted_close" if "adjusted_close" in df.columns else "close"
        return _schoon(df.rename(columns={"date": "datum", kolom: "koers"}))

    def koers(self, isin):
        return self._laatste(isin)

    def rente(self, looptijd):
        raise NietOndersteund("Gebruik de ECB-bron voor rentes.")


class EODHDObligatieBron(EODHDBron):
    """EODHD EUBOND: koersen van (bedrijfs)obligaties, in % van nominaal. Symbool = ISIN.EUBOND."""

    naam = "eodhd_eubond"

    def symbool(self, isin: str) -> str:
        return self.symbolen.get(isin, f"{isin}.EUBOND")

    @staticmethod
    def lees_json(data: list[dict]) -> pd.DataFrame:
        if not data:
            return pd.DataFrame(columns=KOLOMMEN)
        df = pd.DataFrame(data)
        kolom = "price" if "price" in df.columns else "close"
        return _schoon(df.rename(columns={"date": "datum", kolom: "koers"}))


class TwelveDataBron(PrijsBron):
    """Twelve Data: dagkoersen van aandelen en ETF's."""

    naam = "twelvedata"
    BASIS = "https://api.twelvedata.com"

    def __init__(self, sleutel: Optional[str] = None, symbolen: Optional[dict[str, str]] = None, sessie=None):
        self.api_sleutel = sleutel or os.environ.get("TWELVEDATA_API_KEY")
        self.symbolen = dict(symbolen or {})
        self.sessie = sessie

    def _sleutel(self) -> str:
        if not self.api_sleutel:
            raise BronFout("Geen API-sleutel: stel TWELVEDATA_API_KEY in.")
        return self.api_sleutel

    def _symbool_params(self, isin: str) -> dict:
        if isin in self.symbolen:
            symbool = self.symbolen[isin]
            if ":" in symbool:  # notatie SYMBOOL:BEURS
                s, beurs = symbool.split(":", 1)
                return {"symbol": s, "exchange": beurs}
            return {"symbol": symbool}
        r = _get(f"{self.BASIS}/symbol_search", {"symbol": isin, "apikey": self._sleutel()}, self.sessie)
        treffers = r.json().get("data", [])
        if not treffers:
            raise BronFout(f"ISIN {isin} niet gevonden bij Twelve Data")
        t = treffers[0]
        self.symbolen[isin] = f"{t['symbol']}:{t['exchange']}"
        return {"symbol": t["symbol"], "exchange": t["exchange"]}

    def historie(self, isin, start, eind):
        params = {
            **self._symbool_params(isin),
            "interval": "1day",
            "start_date": start.isoformat(),
            "end_date": eind.isoformat(),
            "outputsize": 5000,
            "apikey": self._sleutel(),
        }
        r = _get(f"{self.BASIS}/time_series", params, self.sessie)
        return self.lees_json(r.json())

    @staticmethod
    def lees_json(data: dict) -> pd.DataFrame:
        if data.get("status") == "error":
            raise BronFout(f"Twelve Data: {data.get('message', 'onbekende fout')}")
        waarden = data.get("values", [])
        if not waarden:
            return pd.DataFrame(columns=KOLOMMEN)
        df = pd.DataFrame(waarden)
        return _schoon(df.rename(columns={"datetime": "datum", "close": "koers"}))

    def koers(self, isin):
        return self._laatste(isin)

    def rente(self, looptijd):
        raise NietOndersteund("Gebruik de ECB-bron voor rentes.")


class CSVBron(PrijsBron):
    """CSV als alternatief voor elke bron.

    Koersen: kolommen isin, datum, koers. Rentes (optioneel): kolommen looptijd, rente.
    """

    naam = "csv"

    def __init__(self, koersen: pd.DataFrame | str | Path | None = None,
                 rentes: pd.DataFrame | str | Path | None = None):
        self._koersen = lees_koersen_csv(koersen) if koersen is not None else None
        self._rentes = self._lees(rentes) if rentes is not None else None

    @staticmethod
    def _lees(bron) -> pd.DataFrame:
        return bron.copy() if isinstance(bron, pd.DataFrame) else lees_csv(bron)

    def historie(self, isin, start, eind):
        if self._koersen is None:
            raise NietOndersteund("Geen koersbestand opgegeven.")
        df = self._koersen[self._koersen["isin"] == isin]
        df = df[(df["datum"] >= start) & (df["datum"] <= eind)]
        return df[KOLOMMEN].reset_index(drop=True)

    def koers(self, isin):
        if self._koersen is None:
            raise NietOndersteund("Geen koersbestand opgegeven.")
        df = self._koersen[self._koersen["isin"] == isin]
        if df.empty:
            raise BronFout(f"Geen koers voor {isin} in het CSV-bestand")
        rij = df.iloc[-1]
        return rij["datum"], float(rij["koers"])

    def rente(self, looptijd):
        if self._rentes is None or self._rentes.empty:
            raise NietOndersteund("Geen rentebestand opgegeven.")
        df = self._rentes.copy()
        df.columns = [c.strip().lower() for c in df.columns]
        df["looptijd"] = pd.to_numeric(df["looptijd"], errors="coerce")
        df["rente"] = pd.to_numeric(df["rente"], errors="coerce")
        df = df.dropna(subset=["looptijd", "rente"])
        rij = df.iloc[(df["looptijd"] - looptijd).abs().argmin()]
        return float(rij["rente"])


def lees_csv(bron) -> pd.DataFrame:
    """Leest een CSV met komma of puntkomma als scheidingsteken."""
    return pd.read_csv(bron, sep=None, engine="python", dtype=str)


def lees_koersen_csv(bron, isin: Optional[str] = None) -> pd.DataFrame:
    """Leest koersen uit CSV. Kolommen: datum, koers en (optioneel als isin is opgegeven) isin.

    Decimale komma's worden geaccepteerd. Ongeldige regels vervallen.
    """
    df = bron.copy() if isinstance(bron, pd.DataFrame) else lees_csv(bron)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "isin" not in df.columns:
        if isin is None:
            raise BronFout("Kolom 'isin' ontbreekt in het koersbestand.")
        df["isin"] = isin
    ontbreekt = {"datum", "koers"} - set(df.columns)
    if ontbreekt:
        raise BronFout(f"Kolom(men) ontbreken in het koersbestand: {', '.join(sorted(ontbreekt))}")
    df["isin"] = df["isin"].astype(str).str.strip().str.upper()
    df["datum"] = pd.to_datetime(df["datum"], errors="coerce").dt.date
    if df["koers"].dtype == object or str(df["koers"].dtype).startswith("str"):
        df["koers"] = df["koers"].astype(str).str.replace(",", ".", regex=False)
    df["koers"] = pd.to_numeric(df["koers"], errors="coerce")
    df = df.dropna(subset=["datum", "koers"])
    df = df.drop_duplicates(["isin", "datum"], keep="last").sort_values(["isin", "datum"])
    return df[["isin", "datum", "koers"]].reset_index(drop=True)


def maak_bron(naam: str, symbolen: Optional[dict[str, str]] = None, config: Optional[dict] = None) -> PrijsBron:
    """Fabriek voor API-bronnen op naam."""
    if naam == "eulerpool":
        return EulerpoolBron((config or {}).get("eulerpool"))
    if naam == "eodhd":
        return EODHDBron(symbolen=symbolen)
    if naam == "eodhd_eubond":
        return EODHDObligatieBron(symbolen=symbolen)
    if naam == "twelvedata":
        return TwelveDataBron(symbolen=symbolen)
    if naam == "ecb":
        return ECBBron()
    raise BronFout(f"Bron '{naam}' wordt niet automatisch ververst (gebruik CSV-import).")


# --------------------------------------------------------------------------- Eulerpool
class EulerpoolBron(PrijsBron):
    """Eulerpool: koershistorie van (bedrijfs)obligaties op ISIN.

    Adressen staan in config.yaml (sectie eulerpool), omdat ze per abonnement kunnen verschillen.
    Het antwoord wordt flexibel gelezen: een lijst (of een lijst onder 'data'/'prices'/'values')
    met een datumveld (date/datum/timestamp) en een koersveld (close/price/value/koers).
    """

    naam = "eulerpool"
    DATUMVELDEN = ("date", "datum", "timestamp", "time", "datetime")
    KOERSVELDEN = ("close", "price", "value", "koers", "last", "mid")

    def __init__(self, instellingen: Optional[dict] = None, sleutel: Optional[str] = None, sessie=None):
        i = instellingen or {}
        self.basis_url = i.get("basis_url", "https://api.eulerpool.com").rstrip("/")
        self.historie_pad = i.get("historie_pad", "/api/1/bonds/{isin}/prices?from={start}&to={eind}")
        self.sleutel_parameter = i.get("sleutel_parameter", "token")
        self.api_sleutel = sleutel or os.environ.get("EULERPOOL_API_KEY")
        self.sessie = sessie

    def historie(self, isin, start, eind):
        if not self.api_sleutel:
            raise BronFout("Geen API-sleutel: stel EULERPOOL_API_KEY in.")
        url = self.basis_url + self.historie_pad.format(isin=isin, start=start.isoformat(), eind=eind.isoformat())
        r = _get(url, {self.sleutel_parameter: self.api_sleutel}, self.sessie)
        return self.lees_json(r.json())

    @classmethod
    def lees_json(cls, data) -> pd.DataFrame:
        if isinstance(data, dict):
            for sleutel in ("data", "prices", "values", "items", "results"):
                if isinstance(data.get(sleutel), list):
                    data = data[sleutel]
                    break
            else:
                raise BronFout("Onverwacht antwoord van Eulerpool (geen lijst met koersen).")
        if not data:
            return pd.DataFrame(columns=KOLOMMEN)
        df = pd.DataFrame(data)
        datum = next((k for k in cls.DATUMVELDEN if k in df.columns), None)
        koers = next((k for k in cls.KOERSVELDEN if k in df.columns), None)
        if datum is None or koers is None:
            raise BronFout(f"Eulerpool-antwoord mist datum- of koersveld (velden: {', '.join(map(str, df.columns))}).")
        if pd.api.types.is_numeric_dtype(df[datum]):  # unix-tijd in seconden of milliseconden
            eenheid = "ms" if df[datum].max() > 1e11 else "s"
            df[datum] = pd.to_datetime(df[datum], unit=eenheid)
        return _schoon(df.rename(columns={datum: "datum", koers: "koers"}))

    def koers(self, isin):
        return self._laatste(isin)

    def rente(self, looptijd):
        raise NietOndersteund("Gebruik de ECB-bron voor rentes.")


# --------------------------------------------------------------------------- wisselkoersen
class ECBWisselkoersBron:
    """ECB-referentiekoersen (dataset EXR): aantal eenheden vreemde valuta per euro. Gratis."""

    naam = "ecb"
    URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{valuta}.EUR.SP00.A"

    def __init__(self, sessie=None):
        self.sessie = sessie

    def wisselkoersen(self, valuta: str, start: date, eind: date) -> pd.DataFrame:
        r = _get(self.URL.format(valuta=valuta.upper()),
                 {"startPeriod": start.isoformat(), "endPeriod": eind.isoformat(), "format": "csvdata"}, self.sessie)
        return self.lees_csv(r.text)

    @staticmethod
    def lees_csv(tekst: str) -> pd.DataFrame:
        df = pd.read_csv(io.StringIO(tekst))
        if df.empty or not {"TIME_PERIOD", "OBS_VALUE"} <= set(df.columns):
            raise BronFout("Onverwacht antwoord van de ECB (wisselkoersen).")
        return _schoon(df.rename(columns={"TIME_PERIOD": "datum", "OBS_VALUE": "koers"}))


def lees_wisselkoersen_csv(bron) -> pd.DataFrame:
    """Wisselkoersen uit CSV. Kolommen: datum, valuta, koers (eenheden valuta per euro)."""
    df = bron.copy() if isinstance(bron, pd.DataFrame) else lees_csv(bron)
    df.columns = [str(c).strip().lower() for c in df.columns]
    ontbreekt = {"datum", "valuta", "koers"} - set(df.columns)
    if ontbreekt:
        raise BronFout(f"Kolom(men) ontbreken in het wisselkoersbestand: {', '.join(sorted(ontbreekt))}")
    df["valuta"] = df["valuta"].astype(str).str.strip().str.upper()
    df["koers"] = pd.to_numeric(df["koers"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    df["datum"] = pd.to_datetime(df["datum"], errors="coerce").dt.date
    df = df.dropna(subset=["datum", "koers"])
    df = df[df["koers"] > 0]
    return df[["valuta", "datum", "koers"]].sort_values(["valuta", "datum"]).reset_index(drop=True)


# --------------------------------------------------------------------------- kengetallen
def _getal(waarde) -> Optional[float]:
    try:
        x = float(waarde)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN eruit


def lees_fundamentals(data: dict) -> dict:
    """Haalt kengetallen uit een EODHD-fundamentals-antwoord.

    Geeft alleen velden terug die gevonden zijn, in de eenheden van het datamodel (percentages in %).
    """
    uit: dict = {}
    if not isinstance(data, dict):
        return uit
    h = data.get("Highlights") or {}
    if (pe := _getal(h.get("PERatio"))) and pe > 0:
        uit["koers_winst"] = round(pe, 2)
    if (dy := _getal(h.get("DividendYield"))) is not None:
        uit["dividendrendement"] = round(dy * 100, 2)

    # Schuld/eigen vermogen uit de meest recente kwartaalbalans
    balans = ((data.get("Financials") or {}).get("Balance_Sheet") or {}).get("quarterly") or {}
    for _, b in sorted(balans.items(), reverse=True):
        schuld = _getal(b.get("shortLongTermDebtTotal"))
        if schuld is None:
            delen = [_getal(b.get(k)) for k in ("shortTermDebt", "longTermDebt")]
            schuld = sum(d for d in delen if d is not None) if any(d is not None for d in delen) else None
        ev = _getal(b.get("totalStockholderEquity"))
        if schuld is not None and ev and ev > 0:
            uit["schuld_eigen_vermogen"] = round(schuld / ev, 2)
            break

    etf = data.get("ETF_Data") or {}
    if etf:
        if (ter := _getal(etf.get("NetExpenseRatio"))) is not None:
            uit["ter"] = round(ter * 100, 3)
        if (n := _getal(etf.get("Holdings_Count"))) and n > 0:
            uit["aantal_posities"] = int(n)
        top = etf.get("Top_10_Holdings") or etf.get("Holdings") or {}
        gewichten = [_getal(x.get("Assets_%")) for x in top.values() if isinstance(x, dict)]
        gewichten = [g for g in gewichten if g is not None]
        if gewichten:
            uit["concentratie"] = round(max(gewichten), 2)
        if "dividendrendement" not in uit and (y := _getal(etf.get("Yield"))) is not None:
            uit["dividendrendement"] = round(y, 2)
    return uit


def haal_kengetallen_eodhd(isin: str, symbool: Optional[str] = None, sleutel: Optional[str] = None,
                           sessie=None) -> dict:
    """Haalt kengetallen op bij EODHD (endpoint fundamentals)."""
    bron = EODHDBron(sleutel=sleutel, symbolen={isin: symbool} if symbool else None, sessie=sessie)
    r = _get(f"{EODHDBron.BASIS}/fundamentals/{bron.symbool(isin)}", {"api_token": bron._sleutel(), "fmt": "json"},
             sessie)
    return lees_fundamentals(r.json())
