from datetime import date

from macro_platform.catalog.tracked_universe import TRACKED_SERIES
from macro_platform.providers.adapters import _parse_bls_observations, _parse_ecb_observations


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
