from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from typing import Callable

from macro_platform.catalog.tracked_universe import TRACKED_SERIES
from macro_platform.providers.adapters import (
    BLSProvider,
    ECBProvider,
    FREDProvider,
    IMFProvider,
    OECDProvider,
    OpenBBMarketProvider,
    WorldBankProvider,
)


def _series(series_id: str):
    return next(item for item in TRACKED_SERIES if item.id == series_id)


def _classify_error(message: str) -> str:
    lowered = message.lower()
    if "threshold" in lowered or "registration key" in lowered:
        return "quota_limited"
    if "forbidden" in lowered or "just a moment" in lowered or "cloudflare" in lowered:
        return "network_blocked"
    if "bad request" in lowered and "fred" in lowered:
        return "auth_required"
    return "error"


def _run_macro_probe(source: str, series_id: str, fetcher: Callable) -> dict[str, object]:
    start = date.today() - timedelta(days=365 * 5)
    end = date.today()
    try:
        rows = fetcher(_series(series_id), start, end)
        latest = rows[-1].model_dump(mode="json") if rows else None
        return {
            "source": source,
            "series_id": series_id,
            "status": "ok" if rows else "empty",
            "row_count": len(rows),
            "latest": latest,
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - live smoke utility
        error_text = str(exc)
        return {
            "source": source,
            "series_id": series_id,
            "status": _classify_error(error_text),
            "row_count": 0,
            "latest": None,
            "error": error_text,
        }


def _run_market_probe() -> dict[str, object]:
    start = date.today() - timedelta(days=90)
    end = date.today()
    try:
        rows = OpenBBMarketProvider().fetch_prices("SPY", "equities", start, end)
        latest = rows[-1].model_dump(mode="json") if rows else None
        return {
            "source": "openbb:yfinance",
            "ticker": "SPY",
            "status": "ok" if rows else "empty",
            "row_count": len(rows),
            "latest": latest,
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - live smoke utility
        error_text = str(exc)
        return {
            "source": "openbb:yfinance",
            "ticker": "SPY",
            "status": _classify_error(error_text),
            "row_count": 0,
            "latest": None,
            "error": error_text,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke test for V1 public data connectors.")
    parser.add_argument(
        "--allow-fail",
        action="store_true",
        help="Always return exit code 0 after printing connector results.",
    )
    args = parser.parse_args()

    probes = [
        _run_macro_probe("fred", "fred:CPIAUCSL", FREDProvider().fetch_observations),
        _run_macro_probe("bls", "bls:CUUR0000SA0", BLSProvider().fetch_observations),
        _run_macro_probe("ecb", "ecb:EXR/D.USD.EUR.SP00.A", ECBProvider().fetch_observations),
        _run_macro_probe("world_bank", "world_bank:USA:NY.GDP.MKTP.CD", WorldBankProvider().fetch_observations),
        _run_macro_probe("imf", "imf:US:NGDP_RPCH", IMFProvider().fetch_observations),
        _run_macro_probe("oecd", "oecd:US:LRUN64TT", OECDProvider().fetch_observations),
        _run_market_probe(),
    ]
    print(json.dumps({"generated_at": date.today().isoformat(), "probes": probes}, indent=2))
    if args.allow_fail:
        return 0
    return 0 if all(item["status"] == "ok" for item in probes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
