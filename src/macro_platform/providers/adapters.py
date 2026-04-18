from __future__ import annotations

from datetime import date
from pathlib import Path
import hashlib
import io

import pandas as pd
import requests

from macro_platform.config import settings
from macro_platform.domain.models import AssetPrice, Observation, SeriesDefinition


class FREDProvider:
    base_url = "https://api.stlouisfed.org/fred"

    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        params = {
            "series_id": definition.source_key,
            "api_key": settings.fred_api_key or "",
            "file_type": "json",
        }
        if start_date:
            params["observation_start"] = start_date.isoformat()
        if end_date:
            params["observation_end"] = end_date.isoformat()
        response = self.session.get(
            f"{self.base_url}/series/observations",
            params=params,
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()["observations"]
        rows: list[Observation] = []
        for item in payload:
            value = None if item.get("value") in {".", None} else float(item["value"])
            rows.append(
                Observation(
                    series_id=definition.id,
                    date=date.fromisoformat(item["date"]),
                    value=value,
                    vintage_date=date.fromisoformat(item["realtime_start"]),
                    status="final",
                )
            )
        return rows


class WorldBankProvider:
    base_url = "https://api.worldbank.org/v2"

    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        country_code = definition.provider_params["country_code"]
        response = self.session.get(
            f"{self.base_url}/country/{country_code}/indicator/{definition.source_key}",
            params={"format": "json", "per_page": 2000},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if len(payload) < 2:
            return []
        rows: list[Observation] = []
        for item in payload[1]:
            if item.get("value") is None:
                continue
            current_date = date(int(item["date"]), 12, 31)
            if start_date and current_date < start_date:
                continue
            if end_date and current_date > end_date:
                continue
            rows.append(
                Observation(
                    series_id=definition.id,
                    date=current_date,
                    value=float(item["value"]),
                    status="final",
                )
            )
        return list(sorted(rows, key=lambda row: row.date))


class BLSProvider:
    base_url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        current_year = date.today().year
        payload = {
            "seriesid": [definition.source_key],
            "startyear": str(start_date.year if start_date else current_year - 5),
            "endyear": str(end_date.year if end_date else current_year),
        }
        response = self.session.post(self.base_url, json=payload, timeout=12)
        response.raise_for_status()
        return _parse_bls_observations(definition, response.json(), start_date, end_date)


class ECBProvider:
    base_url = "https://data-api.ecb.europa.eu/service/data"

    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        params = {"format": "jsondata"}
        if start_date:
            params["startPeriod"] = start_date.isoformat()
        if end_date:
            params["endPeriod"] = end_date.isoformat()
        response = self.session.get(
            f"{self.base_url}/{definition.source_key}",
            params=params,
            timeout=12,
        )
        response.raise_for_status()
        return _parse_ecb_observations(definition, response.json(), start_date, end_date)


class IMFProvider:
    base_url = "https://www.imf.org/external/datamapper/api/v1"

    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        country_code = definition.provider_params.get("country_code")
        if not country_code:
            raise ValueError("IMF provider requires provider_params.country_code.")
        response = self.session.get(
            f"{self.base_url}/{definition.source_key}/{country_code}",
            timeout=12,
        )
        response.raise_for_status()
        return _parse_imf_observations(definition, response.json(), start_date, end_date)


class OECDProvider:
    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        endpoint = definition.provider_params.get("endpoint")
        if not endpoint:
            raise ValueError("OECD provider requires provider_params.endpoint.")
        response = self.session.get(endpoint, timeout=12)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
        return _parse_oecd_observations(definition, frame, start_date, end_date)


class OpenBBMarketProvider:
    def fetch_prices(
        self,
        ticker: str,
        asset_class: str,
        start_date: date,
        end_date: date,
    ) -> list[AssetPrice]:
        try:
            from openbb import obb
        except ImportError as exc:
            raise RuntimeError("OpenBB is not installed.") from exc

        data = obb.equity.price.historical(
            symbol=ticker,
            provider="yfinance",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        ).to_df()
        return _frame_to_prices(data, ticker=ticker, asset_class=asset_class, source="openbb:yfinance")


class DemoMacroProvider:
    def fetch_observations(
        self,
        definition: SeriesDefinition,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        start_date = start_date or date(2018, 1, 1)
        end_date = end_date or date.today()
        freq = {"daily": "B", "weekly": "W", "monthly": "MS", "quarterly": "QS", "annual": "YS"}[
            definition.frequency
        ]
        index = pd.date_range(start=start_date, end=end_date, freq=freq)
        base = _stable_seed(definition.id) % 100
        trend = pd.Series(range(len(index)), index=index) * 0.2
        wave = pd.Series([((i % 12) - 6) * 0.15 for i in range(len(index))], index=index)
        values = base + trend + wave
        if definition.topic == "inflation":
            values = values / 10 + 2
        if definition.topic == "rates":
            values = values / 20 + 1
        if definition.topic == "labor":
            values = values / 10 + 4
        return [
            Observation(
                series_id=definition.id,
                date=current.date(),
                value=float(values.loc[current]),
                status="demo",
            )
            for current in index
        ]


class DemoMarketProvider:
    def fetch_prices(
        self,
        ticker: str,
        asset_class: str,
        start_date: date,
        end_date: date,
    ) -> list[AssetPrice]:
        index = pd.date_range(start_date, end_date, freq="B")
        seed = _stable_seed(ticker)
        base = 50 + seed % 80
        trend = pd.Series(range(len(index)), index=index) * ((seed % 7) + 1) / 500
        wave = pd.Series([((i % 10) - 5) / 50 for i in range(len(index))], index=index)
        close = base + trend.cumsum() + wave.cumsum()
        open_ = close.shift(1).fillna(close.iloc[0])
        high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
        low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
        volume = pd.Series(
            [1_000_000 + (seed % 1000) * 100 + i * 100 for i in range(len(index))],
            index=index,
        )
        frame = pd.DataFrame(
            {
                "date": index,
                "open": open_.values,
                "high": high.values,
                "low": low.values,
                "close": close.values,
                "volume": volume.values,
            }
        )
        return _frame_to_prices(frame, ticker=ticker, asset_class=asset_class, source="demo")


def write_raw_snapshot(name: str, frame: pd.DataFrame) -> Path:
    destination = settings.raw_snapshot_dir / f"{name}.parquet"
    frame.to_parquet(destination, index=False)
    return destination


def _frame_to_prices(frame: pd.DataFrame, ticker: str, asset_class: str, source: str) -> list[AssetPrice]:
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.date
    if "adj_close" not in normalized.columns:
        normalized["adj_close"] = normalized["close"]
    return [
        AssetPrice(
            ticker=ticker,
            asset_class=asset_class,
            date=row["date"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) if pd.notna(row.get("volume")) else None,
            adjusted_close=float(row["adj_close"]) if pd.notna(row.get("adj_close")) else float(row["close"]),
            source=source,
        )
        for _, row in normalized.iterrows()
    ]


def _stable_seed(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _parse_bls_observations(
    definition: SeriesDefinition,
    payload: dict,
    start_date: date | None,
    end_date: date | None,
) -> list[Observation]:
    series_rows = payload.get("Results", {}).get("series", [])
    if not series_rows:
        return []
    rows: list[Observation] = []
    for item in series_rows[0].get("data", []):
        if not item.get("period", "").startswith("M"):
            continue
        current_date = date(int(item["year"]), int(item["period"][1:]), 1)
        if start_date and current_date < start_date:
            continue
        if end_date and current_date > end_date:
            continue
        rows.append(
            Observation(
                series_id=definition.id,
                date=current_date,
                value=float(item["value"]),
                status="final",
            )
        )
    return list(sorted(rows, key=lambda row: row.date))


def _parse_ecb_observations(
    definition: SeriesDefinition,
    payload: dict,
    start_date: date | None,
    end_date: date | None,
) -> list[Observation]:
    time_values = payload.get("structure", {}).get("dimensions", {}).get("observation", [])
    if not time_values:
        return []
    observation_dates = time_values[0]["values"]
    series_bucket = payload.get("dataSets", [{}])[0].get("series", {})
    if not series_bucket:
        return []
    series_key = next(iter(series_bucket))
    observations = series_bucket[series_key].get("observations", {})
    rows: list[Observation] = []
    for idx, values in observations.items():
        date_text = observation_dates[int(idx)]["id"]
        current_date = date.fromisoformat(date_text)
        if start_date and current_date < start_date:
            continue
        if end_date and current_date > end_date:
            continue
        rows.append(
            Observation(
                series_id=definition.id,
                date=current_date,
                value=float(values[0]),
                status="final",
            )
        )
    return list(sorted(rows, key=lambda row: row.date))


def _parse_imf_observations(
    definition: SeriesDefinition,
    payload: dict,
    start_date: date | None,
    end_date: date | None,
) -> list[Observation]:
    values = payload.get("values", {}).get(definition.source_key, {})
    country_code = definition.provider_params.get("country_code")
    country_values = values.get(country_code, {}) if isinstance(values, dict) else {}
    rows: list[Observation] = []
    for year_text, raw_value in country_values.items():
        try:
            year = int(year_text)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        current_date = date(year, 12, 31)
        if start_date and current_date < start_date:
            continue
        if end_date and current_date > end_date:
            continue
        rows.append(
            Observation(
                series_id=definition.id,
                date=current_date,
                value=value,
                status="final",
            )
        )
    return list(sorted(rows, key=lambda row: row.date))


def _parse_oecd_observations(
    definition: SeriesDefinition,
    frame: pd.DataFrame,
    start_date: date | None,
    end_date: date | None,
) -> list[Observation]:
    if frame.empty:
        return []
    date_column = "TIME_PERIOD" if "TIME_PERIOD" in frame.columns else "time"
    value_column = "OBS_VALUE" if "OBS_VALUE" in frame.columns else "value"
    if date_column not in frame.columns or value_column not in frame.columns:
        raise ValueError("OECD payload must include TIME_PERIOD/time and OBS_VALUE/value columns.")
    rows: list[Observation] = []
    for _, item in frame.iterrows():
        raw_date = str(item[date_column])
        if len(raw_date) == 4 and raw_date.isdigit():
            current_date = date(int(raw_date), 12, 31)
        else:
            parsed = pd.to_datetime(raw_date, errors="coerce")
            if pd.isna(parsed):
                continue
            current_date = parsed.date()
        try:
            value = float(item[value_column])
        except (TypeError, ValueError):
            continue
        if start_date and current_date < start_date:
            continue
        if end_date and current_date > end_date:
            continue
        rows.append(
            Observation(
                series_id=definition.id,
                date=current_date,
                value=value,
                status="final",
            )
        )
    return list(sorted(rows, key=lambda row: row.date))
