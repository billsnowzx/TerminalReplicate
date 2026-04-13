from __future__ import annotations

import math

import pandas as pd

from macro_platform.domain.models import ScreenFilter, ScreenResultRow


def year_over_year(series: pd.Series, periods: int | None = None) -> pd.Series:
    if periods is None:
        inferred = pd.infer_freq(series.index)
        periods = 12 if inferred in {"MS", "M"} else 4 if inferred in {"QS", "Q"} else 1
    return series.pct_change(periods=periods) * 100


def rolling_zscore(series: pd.Series, window: int = 12) -> pd.Series:
    mean = series.rolling(window).mean()
    std = series.rolling(window).std(ddof=0)
    return (series - mean) / std.replace(0, pd.NA)


def drawdown(series: pd.Series) -> pd.Series:
    peak = series.cummax()
    return series / peak - 1.0


def pct_return(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods=periods) * 100


def yield_curve_slope(long_rate: pd.Series, short_rate: pd.Series) -> pd.Series:
    aligned = pd.concat([long_rate, short_rate], axis=1).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    return aligned.iloc[:, 0] - aligned.iloc[:, 1]


def classify_regime(inflation_yoy: float, unemployment_rate: float, curve_slope: float) -> str:
    if math.isnan(inflation_yoy) or math.isnan(unemployment_rate) or math.isnan(curve_slope):
        return "unknown"
    if inflation_yoy > 3.5 and curve_slope < 0:
        return "stagflation risk"
    if inflation_yoy > 3.0 and unemployment_rate < 4.5:
        return "overheating"
    if inflation_yoy < 2.5 and curve_slope > 0 and unemployment_rate < 5.5:
        return "growth rebound"
    if unemployment_rate > 6.0 or curve_slope < -0.5:
        return "slowdown"
    return "disinflation"


def screen_assets(
    price_frame: pd.DataFrame,
    asset_classes: dict[str, str],
    filters: list[ScreenFilter],
) -> list[ScreenResultRow]:
    rows: list[ScreenResultRow] = []
    for ticker, frame in price_frame.groupby("ticker"):
        closes = frame.sort_values("date")["close"].reset_index(drop=True)
        if len(closes) < 64:
            continue
        return_21d = float(pct_return(closes, 21).iloc[-1])
        return_63d = float(pct_return(closes, 63).iloc[-1])
        max_drawdown = float(drawdown(closes).iloc[-63:].min())
        candidate = ScreenResultRow(
            ticker=ticker,
            asset_class=asset_classes.get(ticker, "unknown"),
            last_close=float(closes.iloc[-1]),
            return_21d=return_21d,
            return_63d=return_63d,
            drawdown=max_drawdown,
        )
        if _passes_filters(candidate, filters):
            rows.append(candidate)
    return sorted(rows, key=lambda item: getattr(item, "return_63d"), reverse=True)


def _passes_filters(row: ScreenResultRow, filters: list[ScreenFilter]) -> bool:
    for current in filters:
        value = getattr(row, current.field)
        if current.operator == "gt" and not value > current.value:
            return False
        if current.operator == "gte" and not value >= current.value:
            return False
        if current.operator == "lt" and not value < current.value:
            return False
        if current.operator == "lte" and not value <= current.value:
            return False
    return True
