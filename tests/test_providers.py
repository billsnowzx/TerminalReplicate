import builtins
from datetime import date

import pandas as pd
import pytest
import requests

from macro_platform.catalog.tracked_universe import TRACKED_SERIES
from macro_platform.config import settings
from macro_platform.providers.adapters import (
    BLSProvider,
    ECBProvider,
    FREDProvider,
    OECDProvider,
    OpenBBMarketProvider,
    WorldBankProvider,
    _frame_to_prices,
    _parse_bls_observations,
    _parse_ecb_observations,
    _parse_imf_observations,
    _parse_oecd_observations,
)


def test_parse_bls_observations_filters_and_sorts():
    definition = next(item for item in TRACKED_SERIES if item.id == "bls:CUUR0000SA0")
    payload = {
        "Results": {
            "series": [
                {
                    "seriesID": "CUUR0000SA0",
                    "data": [
                        {"year": "2025", "period": "M03", "value": "319.8"},
                        {"year": "2025", "period": "M02", "value": "318.1"},
                        {"year": "2025", "period": "M01", "value": "317.6"},
                    ],
                }
            ]
        }
    }
    rows = _parse_bls_observations(definition, payload, date(2025, 2, 1), None)
    assert [row.date for row in rows] == [date(2025, 2, 1), date(2025, 3, 1)]
    assert rows[-1].value == 319.8


def test_parse_bls_observations_skips_non_numeric_values():
    definition = next(item for item in TRACKED_SERIES if item.id == "bls:CUUR0000SA0")
    payload = {
        "Results": {
            "series": [
                {
                    "seriesID": "CUUR0000SA0",
                    "data": [
                        {"year": "2025", "period": "M03", "value": "-"},
                        {"year": "2025", "period": "M02", "value": "318.1"},
                    ],
                }
            ]
        }
    }
    rows = _parse_bls_observations(definition, payload, None, None)
    assert [row.date for row in rows] == [date(2025, 2, 1)]
    assert rows[0].value == 318.1


def test_parse_ecb_observations_reads_jsondata_shape():
    definition = next(item for item in TRACKED_SERIES if item.id == "ecb:EXR/D.USD.EUR.SP00.A")
    payload = {
        "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [1.08], "1": [1.09]}}}}],
        "structure": {
            "dimensions": {
                "observation": [
                    {
                        "values": [
                            {"id": "2025-01-02"},
                            {"id": "2025-01-03"},
                        ]
                    }
                ]
            }
        },
    }
    rows = _parse_ecb_observations(definition, payload, None, None)
    assert [row.date for row in rows] == [date(2025, 1, 2), date(2025, 1, 3)]
    assert rows[0].value == 1.08


def test_parse_imf_observations_filters_invalid_values_and_date_range():
    definition = next(item for item in TRACKED_SERIES if item.id == "imf:US:NGDP_RPCH")
    payload = {
        "values": {
            "NGDP_RPCH": {
                "USA": {
                    "2021": "5.95",
                    "2022": "1.94",
                    "2023": None,
                    "BAD": "1.0",
                }
            }
        }
    }
    rows = _parse_imf_observations(definition, payload, date(2022, 1, 1), date(2022, 12, 31))
    assert len(rows) == 1
    assert rows[0].date == date(2022, 12, 31)
    assert rows[0].value == 1.94


def test_parse_oecd_observations_accepts_standard_columns_and_filters_dates():
    definition = next(item for item in TRACKED_SERIES if item.id == "oecd:US:LRUN64TT")
    frame = pd.DataFrame(
        {
            "TIME_PERIOD": ["2022", "2023", "2024"],
            "OBS_VALUE": [3.7, 3.6, 3.9],
        }
    )
    rows = _parse_oecd_observations(definition, frame, date(2023, 1, 1), None)
    assert [item.date for item in rows] == [date(2023, 12, 31), date(2024, 12, 31)]
    assert rows[0].value == 3.6


def test_parse_oecd_observations_accepts_fallback_column_names():
    definition = next(item for item in TRACKED_SERIES if item.id == "oecd:EA:LRUN64TT")
    frame = pd.DataFrame(
        {
            "time": ["2024-01-31", "2024-02-29"],
            "value": [6.4, 6.5],
        }
    )
    rows = _parse_oecd_observations(definition, frame, None, None)
    assert [item.date for item in rows] == [date(2024, 1, 31), date(2024, 2, 29)]
    assert rows[1].value == 6.5


def test_parse_oecd_observations_raises_for_missing_columns():
    definition = next(item for item in TRACKED_SERIES if item.id == "oecd:US:LRUN64TT")
    frame = pd.DataFrame({"foo": [1], "bar": [2]})
    with pytest.raises(ValueError, match="OECD payload must include"):
        _parse_oecd_observations(definition, frame, None, None)


def test_fred_provider_gets_real_connector_shape_and_vintage():
    provider = FREDProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "fred:CPIAUCSL")
    captured: dict[str, object] = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "observations": [
                    {
                        "date": "2024-01-01",
                        "value": "308.417",
                        "realtime_start": "2024-02-13",
                    }
                ]
            }

    def fake_get(url, params, timeout):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return DummyResponse()

    provider.session.get = fake_get  # type: ignore[method-assign]
    rows = provider.fetch_observations(definition, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
    assert len(rows) == 1
    assert rows[0].value == 308.417
    assert rows[0].vintage_date == date(2024, 2, 13)
    assert captured["timeout"] == 8
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["series_id"] == "CPIAUCSL"
    assert params["observation_start"] == "2024-01-01"
    assert params["observation_end"] == "2024-12-31"


def test_fred_provider_omits_empty_api_key(monkeypatch):
    provider = FREDProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "fred:CPIAUCSL")
    captured: dict[str, object] = {}
    monkeypatch.setattr(settings, "fred_api_key", None)

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": []}

    def fake_get(url, params, timeout):  # noqa: ANN001
        captured["params"] = params
        captured["timeout"] = timeout
        return DummyResponse()

    provider.session.get = fake_get  # type: ignore[method-assign]
    provider.fetch_observations(definition, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
    params = captured["params"]
    assert isinstance(params, dict)
    assert "api_key" not in params


def test_world_bank_provider_propagates_timeout_error():
    provider = WorldBankProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "world_bank:USA:NY.GDP.MKTP.CD")

    def raise_timeout(*args, **kwargs):  # noqa: ANN002, ANN003
        raise requests.Timeout("timeout")

    provider.session.get = raise_timeout  # type: ignore[method-assign]
    with pytest.raises(requests.Timeout):
        provider.fetch_observations(definition, start_date=date(2020, 1, 1), end_date=date(2024, 12, 31))


def test_oecd_provider_raises_for_malformed_csv_payload():
    provider = OECDProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "oecd:US:LRUN64TT")

    class DummyResponse:
        text = "foo,bar\n1,2\n"

        def raise_for_status(self):
            return None

    provider.session.get = lambda *args, **kwargs: DummyResponse()  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="OECD payload must include"):
        provider.fetch_observations(definition, None, None)


def test_bls_provider_posts_with_timeout_and_expected_year_bounds():
    provider = BLSProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "bls:CUUR0000SA0")
    captured: dict[str, object] = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "Results": {
                    "series": [
                        {
                            "seriesID": definition.source_key,
                            "data": [{"year": "2024", "period": "M01", "value": "300.0"}],
                        }
                    ]
                }
            }

    def fake_post(url, json, timeout):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    provider.session.post = fake_post  # type: ignore[method-assign]
    rows = provider.fetch_observations(definition, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
    assert len(rows) == 1
    assert captured["timeout"] == 12
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["seriesid"] == [definition.source_key]
    assert payload["startyear"] == "2024"
    assert payload["endyear"] == "2024"


def test_bls_provider_raises_when_request_not_processed():
    provider = BLSProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "bls:CUUR0000SA0")

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "REQUEST_NOT_PROCESSED",
                "message": [
                    "Request could not be serviced, as the daily threshold has been reached.",
                ],
                "Results": {},
            }

    provider.session.post = lambda *args, **kwargs: DummyResponse()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="BLS request not processed"):
        provider.fetch_observations(definition, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))


def test_ecb_provider_gets_jsondata_with_period_bounds():
    provider = ECBProvider()
    definition = next(item for item in TRACKED_SERIES if item.id == "ecb:EXR/D.USD.EUR.SP00.A")
    captured: dict[str, object] = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [1.10]}}}}],
                "structure": {
                    "dimensions": {
                        "observation": [
                            {
                                "values": [
                                    {"id": "2024-01-02"},
                                ]
                            }
                        ]
                    }
                },
            }

    def fake_get(url, params, timeout):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return DummyResponse()

    provider.session.get = fake_get  # type: ignore[method-assign]
    rows = provider.fetch_observations(definition, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
    assert len(rows) == 1
    assert rows[0].value == 1.10
    assert definition.source_key in str(captured["url"])
    assert captured["params"] == {
        "format": "jsondata",
        "startPeriod": "2024-01-01",
        "endPeriod": "2024-12-31",
    }
    assert captured["timeout"] == 12


def test_openbb_market_provider_raises_clear_error_when_openbb_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if name == "openbb":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    provider = OpenBBMarketProvider()
    with pytest.raises(RuntimeError, match="OpenBB is not installed"):
        provider.fetch_prices("SPY", "equities", date(2024, 1, 1), date(2024, 2, 1))


def test_frame_to_prices_accepts_index_dates_and_capitalized_columns():
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "AdjClose": [100.4, 101.4, 102.4],
            "Volume": [1_000, 1_100, 1_200],
        },
        index=index,
    )
    rows = _frame_to_prices(frame, ticker="SPY", asset_class="equities", source="openbb:yfinance")
    assert len(rows) == 3
    assert rows[0].date == date(2024, 1, 1)
    assert rows[0].adjusted_close == 100.4
