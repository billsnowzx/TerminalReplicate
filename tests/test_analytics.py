import pandas as pd

from macro_platform.analytics.core import classify_regime, drawdown, rolling_zscore, year_over_year, yield_curve_slope


def test_year_over_year_for_monthly_series():
    series = pd.Series(
        [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112],
        index=pd.date_range("2024-01-01", periods=13, freq="MS"),
    )
    result = year_over_year(series)
    assert round(float(result.iloc[-1]), 2) == 12.0


def test_drawdown_stays_negative_after_peak():
    series = pd.Series([100, 110, 105, 95], index=pd.date_range("2024-01-01", periods=4, freq="D"))
    result = drawdown(series)
    assert result.iloc[-1] == -0.13636363636363635


def test_rolling_zscore_returns_zero_when_series_equals_rolling_mean():
    series = pd.Series([1, 2, 3, 4, 5], index=pd.date_range("2024-01-01", periods=5, freq="D"))
    result = rolling_zscore(series, window=1)
    assert pd.isna(result.iloc[-1])


def test_yield_curve_slope_and_regime_classification():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    slope = yield_curve_slope(pd.Series([4.0, 4.1, 4.2], index=dates), pd.Series([4.5, 4.4, 4.3], index=dates))
    assert round(float(slope.iloc[-1]), 2) == -0.1
    assert classify_regime(4.0, 4.1, -0.1) == "stagflation risk"
