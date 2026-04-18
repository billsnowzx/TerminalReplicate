from datetime import date

import pandas as pd
import pytest

from macro_platform.catalog.tracked_universe import TRACKED_SERIES
from macro_platform.providers.adapters import (
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
                "US": {
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
