import pytest

from macro_platform.catalog.tracked_universe import TRACKED_SERIES, validate_series_definitions
from macro_platform.domain.models import SeriesDefinition


def test_validate_tracked_series_definitions_passes():
    validate_series_definitions(TRACKED_SERIES)


def test_validate_series_definitions_rejects_missing_source_provider_params():
    bad_world_bank = SeriesDefinition(
        id="world_bank:BAD:NY.GDP.MKTP.CD",
        source="world_bank",
        source_key="NY.GDP.MKTP.CD",
        title="Bad World Bank Series",
        topic="growth",
        country="US",
        frequency="annual",
        unit="Current US$",
        nominal_real="nominal",
        tags=["bad"],
        provider_params={},
    )
    with pytest.raises(ValueError, match="provider_params.country_code"):
        validate_series_definitions([bad_world_bank])


def test_validate_series_definitions_rejects_non_http_oecd_endpoint():
    bad_oecd = SeriesDefinition(
        id="oecd:BAD:LRUN64TT",
        source="oecd",
        source_key="LRUN64TT",
        title="Bad OECD Series",
        topic="labor",
        country="US",
        frequency="annual",
        unit="Percent",
        seasonal_adjustment="SA",
        nominal_real="real",
        tags=["bad"],
        provider_params={"endpoint": "/relative/path.csv"},
    )
    with pytest.raises(ValueError, match="absolute http"):
        validate_series_definitions([bad_oecd])
