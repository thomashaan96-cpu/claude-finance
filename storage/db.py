"""SQLite-opslag: instrumenten, gecachte koersen, rentes en verversingsmomenten."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from data.modellen import Instrument

SCHEMA = """
CREATE TABLE IF NOT EXISTS instrumenten (
    isin TEXT PRIMARY KEY,
    gegevens TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS koersen (
    isin TEXT NOT NULL,
    datum TEXT NOT NULL,
    koers REAL NOT NULL,
    bron TEXT NOT NULL,
    PRIMARY KEY (isin, datum)
);
CREATE TABLE IF NOT EXISTS rentes (
    looptijd REAL PRIMARY KEY,
    rente REAL NOT NULL,
    bron TEXT NOT NULL,
    opgehaald TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verversingen (
    sleutel TEXT PRIMARY KEY,
    tijdstip TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class Opslag:
    def __init__(self, pad: str | Path = ":memory:"):
        self.pad = str(pad)
        if self.pad != ":memory:":
            Path(self.pad).parent.mkdir(parents=True, exist_ok=True)
        # Eén verbinding; Streamlit draait herhalingen in verschillende threads.
        self._conn = sqlite3.connect(self.pad, check_same_thread=False)
        self._conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # --- instrumenten -------------------------------------------------
    def bewaar_instrument(self, instrument: Instrument) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO instrumenten (isin, gegevens) VALUES (?, ?) "
                "ON CONFLICT(isin) DO UPDATE SET gegevens = excluded.gegevens",
                (instrument.isin, instrument.model_dump_json()),
            )

    def instrumenten(self) -> list[Instrument]:
        rijen = self._conn.execute("SELECT gegevens FROM instrumenten ORDER BY isin").fetchall()
        return [Instrument.model_validate_json(r[0]) for r in rijen]

    def instrument(self, isin: str) -> Optional[Instrument]:
        r = self._conn.execute("SELECT gegevens FROM instrumenten WHERE isin = ?", (isin,)).fetchone()
        return Instrument.model_validate_json(r[0]) if r else None

    def verwijder_instrument(self, isin: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM instrumenten WHERE isin = ?", (isin,))
            c.execute("DELETE FROM koersen WHERE isin = ?", (isin,))
            c.execute("DELETE FROM verversingen WHERE sleutel = ?", (isin,))

    # --- koersen ------------------------------------------------------
    def bewaar_koersen(self, isin: str, df: pd.DataFrame, bron: str) -> int:
        """Slaat koersen op (datum, koers). Koersen van nul of lager worden geweigerd."""
        if df is None or df.empty:
            return 0
        rijen = [
            (isin, pd.Timestamp(d).date().isoformat(), float(k), bron)
            for d, k in zip(df["datum"], df["koers"])
            if pd.notna(k) and float(k) > 0
        ]
        with self._tx() as c:
            c.executemany(
                "INSERT INTO koersen (isin, datum, koers, bron) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(isin, datum) DO UPDATE SET koers = excluded.koers, bron = excluded.bron",
                rijen,
            )
        return len(rijen)

    def historie(self, isin: str) -> pd.DataFrame:
        df = pd.read_sql_query(
            "SELECT datum, koers, bron FROM koersen WHERE isin = ? ORDER BY datum", self._conn, params=(isin,)
        )
        df["datum"] = pd.to_datetime(df["datum"]).dt.date
        return df

    def verwijder_koersen(self, isin: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM koersen WHERE isin = ?", (isin,))

    # --- rentes -------------------------------------------------------
    def bewaar_rente(self, looptijd: float, rente: float, bron: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO rentes (looptijd, rente, bron, opgehaald) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(looptijd) DO UPDATE SET rente = excluded.rente, bron = excluded.bron, "
                "opgehaald = excluded.opgehaald",
                (float(looptijd), float(rente), bron, datetime.now().isoformat(timespec="seconds")),
            )

    def rente(self, looptijd: float) -> Optional[tuple[float, str, str]]:
        """Gecachte rente voor de dichtstbijzijnde looptijd: (rente, bron, opgehaald)."""
        r = self._conn.execute(
            "SELECT rente, bron, opgehaald FROM rentes ORDER BY ABS(looptijd - ?) LIMIT 1", (float(looptijd),)
        ).fetchone()
        return (r[0], r[1], r[2]) if r else None

    # --- verversingen -------------------------------------------------
    def noteer_verversing(self, sleutel: str, status: str, tijdstip: Optional[datetime] = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO verversingen (sleutel, tijdstip, status) VALUES (?, ?, ?) "
                "ON CONFLICT(sleutel) DO UPDATE SET tijdstip = excluded.tijdstip, status = excluded.status",
                (sleutel, (tijdstip or datetime.now()).isoformat(timespec="seconds"), status),
            )

    def laatste_verversing(self, sleutel: str) -> Optional[tuple[datetime, str]]:
        r = self._conn.execute("SELECT tijdstip, status FROM verversingen WHERE sleutel = ?", (sleutel,)).fetchone()
        return (datetime.fromisoformat(r[0]), r[1]) if r else None

    def moet_verversen(self, sleutel: str, na_dagen: int = 7, nu: Optional[datetime] = None) -> bool:
        """True als de laatste poging ouder is dan `na_dagen` (of er nooit een was)."""
        laatste = self.laatste_verversing(sleutel)
        if laatste is None:
            return True
        return (nu or datetime.now()) - laatste[0] >= timedelta(days=na_dagen)
