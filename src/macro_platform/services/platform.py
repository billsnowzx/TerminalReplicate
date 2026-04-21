from __future__ import annotations

import json
import subprocess
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from macro_platform.analytics.core import classify_regime, drawdown, pct_return, screen_assets, year_over_year, yield_curve_slope
from macro_platform.catalog.tracked_universe import (
    DEFAULT_DASHBOARDS,
    MARKET_UNIVERSE,
    TRACKED_SERIES,
    validate_series_definitions,
)
from macro_platform.config import settings
from macro_platform.domain.models import (
    AssetPrice,
    ChangeAlertEvent,
    ChangeAlertRule,
    ChangeSignal,
    CrossCountryPresetExportBundle,
    CrossCountryPresetImportItem,
    CrossCountryPresetImportRequest,
    CrossCountryPreset,
    DashboardConfig,
    ModelPortfolio,
    NotificationChannel,
    NotificationChannelHealth,
    NotificationDigest,
    NotificationDelivery,
    NotificationRoutingAudit,
    OpsIncident,
    Observation,
    ObservationQuery,
    PortfolioSummaryRow,
    ReleaseFreshnessSnapshot,
    ReportJob,
    ReportJobRun,
    ReportSnapshot,
    ReportSnapshotSection,
    ReportTemplate,
    ReportTemplateSection,
    ReleaseEvent,
    SavedScreen,
    ScreenSpec,
    ScenarioDefinition,
    SeriesDefinition,
    SourceHealth,
    SourceHealthPolicy,
    SourceHealthPolicyRun,
    SourceHealthPolicyVersion,
    SourceHealthPolicyVersionPresetExportBundle,
    SourceHealthPolicyVersionPresetImportItem,
    SourceHealthPolicyVersionPresetImportRequest,
    SourceHealthPolicyVersionPreset,
    Watchlist,
)
from macro_platform.providers.adapters import (
    BLSProvider,
    DemoMacroProvider,
    DemoMarketProvider,
    ECBProvider,
    FREDProvider,
    IMFProvider,
    OECDProvider,
    OpenBBMarketProvider,
    WorldBankProvider,
    write_raw_snapshot,
)
from macro_platform.storage.database import Database
from macro_platform.storage.repositories import (
    AssetPriceRepository,
    ChangeAlertEventRepository,
    ChangeAlertRuleRepository,
    DashboardRepository,
    ModelPortfolioRepository,
    NotificationChannelRepository,
    NotificationDigestRepository,
    NotificationDeliveryRepository,
    NotificationRoutingAuditRepository,
    OpsIncidentRepository,
    ObservationRepository,
    ReleaseFreshnessSnapshotRepository,
    ReportJobRepository,
    ReportJobRunRepository,
    ReportSnapshotRepository,
    ReportTemplateRepository,
    SavedScreenRepository,
    CrossCountryPresetRepository,
    ScenarioRepository,
    SeriesDefinitionRepository,
    SourceHealthRepository,
    SourceHealthPolicyRepository,
    SourceHealthPolicyRunRepository,
    SourceHealthPolicyVersionRepository,
    SourceHealthPolicyVersionPresetRepository,
    WatchlistRepository,
)


class PlatformService:
    def __init__(self, database_url: str | None = None) -> None:
        self.series_map = {item.id: item for item in TRACKED_SERIES}
        validate_series_definitions(list(self.series_map.values()))
        self.market_universe = {item["ticker"]: item["asset_class"] for item in MARKET_UNIVERSE}
        self.fred = FREDProvider()
        self.bls = BLSProvider()
        self.ecb = ECBProvider()
        self.imf = IMFProvider()
        self.oecd = OECDProvider()
        self.world_bank = WorldBankProvider()
        self.demo_macro = DemoMacroProvider()
        self.demo_market = DemoMarketProvider()
        self.database = Database(database_url)
        self.database.create_all()
        self.series_repo = SeriesDefinitionRepository(self.database)
        self.observation_repo = ObservationRepository(self.database)
        self.asset_price_repo = AssetPriceRepository(self.database)
        self.change_alert_rule_repo = ChangeAlertRuleRepository(self.database)
        self.change_alert_event_repo = ChangeAlertEventRepository(self.database)
        self.notification_channel_repo = NotificationChannelRepository(self.database)
        self.notification_delivery_repo = NotificationDeliveryRepository(self.database)
        self.notification_routing_audit_repo = NotificationRoutingAuditRepository(self.database)
        self.ops_incident_repo = OpsIncidentRepository(self.database)
        self.source_health_repo = SourceHealthRepository(self.database)
        self.source_health_policy_repo = SourceHealthPolicyRepository(self.database)
        self.source_health_policy_run_repo = SourceHealthPolicyRunRepository(self.database)
        self.source_health_policy_version_repo = SourceHealthPolicyVersionRepository(self.database)
        self.source_health_policy_version_preset_repo = SourceHealthPolicyVersionPresetRepository(self.database)
        self.notification_digest_repo = NotificationDigestRepository(self.database)
        self.dashboard_repo = DashboardRepository(self.database)
        self.watchlist_repo = WatchlistRepository(self.database)
        self.saved_screen_repo = SavedScreenRepository(self.database)
        self.cross_country_preset_repo = CrossCountryPresetRepository(self.database)
        self.scenario_repo = ScenarioRepository(self.database)
        self.portfolio_repo = ModelPortfolioRepository(self.database)
        self.report_template_repo = ReportTemplateRepository(self.database)
        self.report_snapshot_repo = ReportSnapshotRepository(self.database)
        self.report_job_repo = ReportJobRepository(self.database)
        self.report_job_run_repo = ReportJobRunRepository(self.database)
        self.release_freshness_snapshot_repo = ReleaseFreshnessSnapshotRepository(self.database)
        self.series_repo.sync(list(self.series_map.values()))

    def _normalize_owner_scope_filter(self, owner_scope: Literal["all", "shared", "private"] = "all") -> str | None:
        if owner_scope == "all":
            return None
        return owner_scope

    def _enforce_shared_mutation_policy(
        self,
        *,
        entity_label: str,
        entity_id: str,
        existing_scope: str,
        incoming_scope: str | None = None,
        allow_shared_mutation: bool = False,
    ) -> None:
        if existing_scope != "shared":
            return
        if incoming_scope == "private":
            raise ValueError(f"{entity_label} '{entity_id}' cannot demote owner_scope from shared to private.")
        if not allow_shared_mutation:
            raise PermissionError(
                f"{entity_label} '{entity_id}' is shared and requires allow_shared_mutation=true for updates."
            )

    def search_series(
        self,
        query: str = "",
        domain: str | None = None,
        country: str | None = None,
        frequency: str | None = None,
    ) -> list[SeriesDefinition]:
        query = query.lower().strip()
        rows = list(self.series_map.values())
        if query:
            rows = [
                item
                for item in rows
                if query in item.title.lower()
                or query in item.id.lower()
                or any(query in tag.lower() for tag in item.tags)
            ]
        if domain:
            rows = [item for item in rows if item.topic == domain]
        if country:
            rows = [item for item in rows if item.country.upper() == country.upper()]
        if frequency:
            rows = [item for item in rows if item.frequency == frequency]
        return rows

    def get_series(self, series_id: str) -> SeriesDefinition:
        return self.series_map[series_id]

    def get_series_source_registry(self) -> list[dict[str, object]]:
        required_params = {
            "world_bank": ["country_code"],
            "imf": ["country_code"],
            "oecd": ["endpoint"],
        }
        grouped: dict[str, dict[str, object]] = {}
        for item in self.series_map.values():
            row = grouped.setdefault(
                item.source,
                {
                    "source": item.source,
                    "series_count": 0,
                    "countries": set(),
                    "topics": set(),
                    "missing_required_params": 0,
                },
            )
            row["series_count"] = int(row["series_count"]) + 1
            row["countries"].add(item.country)
            row["topics"].add(item.topic)
            required = required_params.get(item.source, [])
            if required and any(not item.provider_params.get(key) for key in required):
                row["missing_required_params"] = int(row["missing_required_params"]) + 1
        rows: list[dict[str, object]] = []
        for source in sorted(grouped):
            current = grouped[source]
            rows.append(
                {
                    "source": current["source"],
                    "series_count": int(current["series_count"]),
                    "country_count": len(current["countries"]),
                    "topic_count": len(current["topics"]),
                    "countries": sorted(current["countries"]),
                    "topics": sorted(current["topics"]),
                    "missing_required_params": int(current["missing_required_params"]),
                }
            )
        return rows

    def list_series_by_source(
        self,
        source: str,
        country: str | None = None,
        topic: str | None = None,
        limit: int = 200,
    ) -> list[SeriesDefinition]:
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        rows = [item for item in self.series_map.values() if item.source == source]
        if not rows:
            raise KeyError(source)
        if country:
            rows = [item for item in rows if item.country.upper() == country.upper()]
        if topic:
            rows = [item for item in rows if item.topic == topic]
        rows = sorted(rows, key=lambda item: item.id)
        return rows[:limit]

    def query_observations(self, query: ObservationQuery) -> list[Observation]:
        definition = self.get_series(query.series_id)
        cached_rows = self.observation_repo.get_range(query.series_id, query.start_date, query.end_date)
        source_id = f"macro:{definition.source}"
        now = datetime.now()
        try:
            if definition.source == "fred":
                rows = self.fred.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "bls":
                rows = self.bls.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "ecb":
                rows = self.ecb.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "imf":
                rows = self.imf.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "oecd":
                rows = self.oecd.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "world_bank":
                rows = self.world_bank.fetch_observations(definition, query.start_date, query.end_date)
            else:
                rows = self.demo_macro.fetch_observations(definition, query.start_date, query.end_date)
            self._record_source_health(
                source_id=source_id,
                source_kind="macro",
                provider=definition.source,
                status="healthy",
                last_checked_at=now,
                last_success_at=now,
                fallback_used=False,
                notes=f"series_id={query.series_id}",
            )
        except Exception:
            rows = cached_rows or self.demo_macro.fetch_observations(definition, query.start_date, query.end_date)
            degraded_status = "degraded" if rows else "down"
            self._record_source_health(
                source_id=source_id,
                source_kind="macro",
                provider=definition.source,
                status=degraded_status,
                last_checked_at=now,
                last_success_at=now if rows else None,
                last_failure_at=now,
                fallback_used=True,
                error_message=f"{definition.source} fetch failed for {query.series_id}",
                notes="served from cache_or_demo_fallback",
            )

        frame = pd.DataFrame([row.model_dump(mode="json") for row in rows])
        if not frame.empty:
            snapshot_name = query.series_id.replace(":", "_").replace("/", "_").replace(".", "_")
            write_raw_snapshot(snapshot_name, frame)
            self.observation_repo.replace_range(query.series_id, rows)
        return rows

    def get_prices(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[AssetPrice]:
        asset_class = self.market_universe.get(ticker, "unknown")
        start_date = start_date or (date.today() - timedelta(days=365))
        end_date = end_date or date.today()
        now = datetime.now()
        cached_rows = self.asset_price_repo.get_range(ticker, start_date, end_date)
        if settings.use_openbb_market_provider:
            try:
                provider = OpenBBMarketProvider()
                rows = provider.fetch_prices(ticker, asset_class, start_date, end_date)
                self.asset_price_repo.replace_range(ticker, rows)
                self._record_source_health(
                    source_id="market:prices",
                    source_kind="market",
                    provider="openbb",
                    status="healthy",
                    last_checked_at=now,
                    last_success_at=now,
                    fallback_used=False,
                    notes=f"ticker={ticker}",
                )
                return rows
            except Exception:
                self._record_source_health(
                    source_id="market:prices",
                    source_kind="market",
                    provider="openbb",
                    status="degraded",
                    last_checked_at=now,
                    last_failure_at=now,
                    fallback_used=True,
                    error_message=f"openbb fetch failed for {ticker}",
                    notes="falling back to demo_or_cache",
                )
        try:
            rows = self.demo_market.fetch_prices(ticker, asset_class, start_date, end_date)
            self.asset_price_repo.replace_range(ticker, rows)
            self._record_source_health(
                source_id="market:prices",
                source_kind="market",
                provider="demo",
                status="degraded" if settings.use_openbb_market_provider else "healthy",
                last_checked_at=now,
                last_success_at=now,
                fallback_used=settings.use_openbb_market_provider,
                notes=f"ticker={ticker}",
            )
            return rows
        except Exception:
            self._record_source_health(
                source_id="market:prices",
                source_kind="market",
                provider="cache",
                status="degraded" if cached_rows else "down",
                last_checked_at=now,
                last_success_at=now if cached_rows else None,
                last_failure_at=now,
                fallback_used=True,
                error_message=f"demo provider failed for {ticker}",
                notes="served from cached prices" if cached_rows else "no fallback available",
            )
            return cached_rows

    def get_cross_asset_monitor(self) -> list[dict[str, float | str]]:
        rows: list[dict[str, float | str]] = []
        for ticker, asset_class in self.market_universe.items():
            prices = self.get_prices(ticker)
            if not prices:
                continue
            frame = pd.DataFrame([item.model_dump(mode="json") for item in prices])
            closes = frame["close"]
            rows.append(
                {
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "last_close": round(float(closes.iloc[-1]), 2),
                    "return_21d": round(float(closes.pct_change(21).iloc[-1] * 100), 2),
                    "return_63d": round(float(closes.pct_change(63).iloc[-1] * 100), 2),
                    "drawdown": round(float(drawdown(closes).iloc[-63:].min() * 100), 2),
                }
            )
        return rows

    def get_global_macro_monitor(self) -> list[dict[str, float | str | None]]:
        tracked = ["fred:CPIAUCSL", "fred:UNRATE", "fred:PAYEMS", "fred:FEDFUNDS"]
        rows: list[dict[str, float | str | None]] = []
        for series_id in tracked:
            observations = self.query_observations(
                ObservationQuery(series_id=series_id, start_date=date.today() - timedelta(days=365 * 4))
            )
            frame = pd.DataFrame([row.model_dump(mode="json") for row in observations])
            frame["date"] = pd.to_datetime(frame["date"])
            frame["value"] = pd.to_numeric(frame["value"])
            current = float(frame["value"].iloc[-1])
            yoy = None
            if self.series_map[series_id].frequency == "monthly":
                series = frame.sort_values("date").set_index("date")["value"].asfreq("MS")
                yoy = float(year_over_year(series).iloc[-1])
            rows.append(
                {
                    "series_id": series_id,
                    "title": self.series_map[series_id].title,
                    "latest": round(current, 2),
                    "yoy": round(yoy, 2) if yoy is not None and pd.notna(yoy) else None,
                    "unit": self.series_map[series_id].unit,
                }
            )
        return rows

    def get_country_snapshot(self, country: str) -> list[dict[str, float | str]]:
        rows: list[dict[str, float | str]] = []
        for series in self.series_map.values():
            if series.country.upper() != country.upper():
                continue
            observations = self.query_observations(ObservationQuery(series_id=series.id))
            if not observations:
                continue
            rows.append(
                {
                    "series_id": series.id,
                    "title": series.title,
                    "latest": round(float(observations[-1].value or 0), 2),
                    "unit": series.unit,
                    "topic": series.topic,
                }
            )
        return rows

    def get_cross_country_comparison(
        self,
        countries: list[str] | None = None,
        limit: int = 12,
        factor_weights: dict[str, float] | None = None,
    ) -> list[dict[str, object]]:
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        default_countries = ["US", "CN", "EA", "JP", "GB", "CA"]
        selected = countries or default_countries
        selected = [item.upper() for item in selected if item]
        selected = list(dict.fromkeys(selected))
        selected = selected[:limit]

        growth_series_map = {
            "US": "imf:US:NGDP_RPCH",
            "CN": "imf:CN:NGDP_RPCH",
            "EA": "world_bank:EMU:NY.GDP.MKTP.CD",
            "JP": "world_bank:JPN:NY.GDP.MKTP.CD",
            "GB": "world_bank:GBR:NY.GDP.MKTP.CD",
            "CA": "world_bank:CAN:NY.GDP.MKTP.CD",
        }
        inflation_series_map = {
            "US": "world_bank:USA:FP.CPI.TOTL.ZG",
            "CN": "world_bank:CHN:FP.CPI.TOTL.ZG",
            "EA": "world_bank:EMU:FP.CPI.TOTL.ZG",
            "JP": "world_bank:JPN:FP.CPI.TOTL.ZG",
            "GB": "world_bank:GBR:FP.CPI.TOTL.ZG",
            "CA": "world_bank:CAN:FP.CPI.TOTL.ZG",
        }
        labor_series_map = {
            "US": "oecd:US:LRUN64TT",
            "EA": "oecd:EA:LRUN64TT",
            "CN": "world_bank:CHN:SL.UEM.TOTL.ZS",
            "JP": "world_bank:JPN:SL.UEM.TOTL.ZS",
            "GB": "world_bank:GBR:SL.UEM.TOTL.ZS",
            "CA": "world_bank:CAN:SL.UEM.TOTL.ZS",
        }
        policy_series_map = {
            "US": "fred:FEDFUNDS",
            "EA": "ecb:FM/B.U2.EUR.4F.KR.MRR_FR.LEV",
            "CN": "world_bank:CHN:FR.INR.LEND",
            "JP": "world_bank:JPN:FR.INR.LEND",
            "GB": "world_bank:GBR:FR.INR.LEND",
            "CA": "world_bank:CAN:FR.INR.LEND",
        }
        equity_proxy_map = {
            "US": "SPY",
            "CN": "MCHI",
            "EA": "EZU",
            "JP": "EWJ",
            "GB": "EWU",
            "CA": "EWC",
        }

        rows: list[dict[str, object]] = []
        for country in selected:
            growth_value = self._latest_metric_value(growth_series_map.get(country), allow_growth_from_level=True)
            inflation_value = self._latest_metric_value(inflation_series_map.get(country))
            labor_value = self._latest_metric_value(labor_series_map.get(country))
            policy_rate = self._latest_metric_value(policy_series_map.get(country))
            proxy_ticker = equity_proxy_map.get(country)
            return_63d = self._market_return_63d(proxy_ticker) if proxy_ticker else None
            rows.append(
                {
                    "country": country,
                    "growth": growth_value,
                    "inflation": inflation_value,
                    "labor_unemployment": labor_value,
                    "policy_rate": policy_rate,
                    "equity_proxy": proxy_ticker,
                    "equity_return_63d": return_63d,
                }
            )

        score_fields = [
            ("growth", 1.0),
            ("inflation", -1.0),
            ("labor_unemployment", -1.0),
            ("policy_rate", -1.0),
            ("equity_return_63d", 1.0),
        ]
        weights = {
            "growth": 1.0,
            "inflation": 1.0,
            "labor_unemployment": 1.0,
            "policy_rate": 1.0,
            "equity_return_63d": 1.0,
        }
        if factor_weights:
            for key, value in factor_weights.items():
                if key in weights:
                    weights[key] = max(0.0, float(value))
        for field, direction in score_fields:
            values = [row[field] for row in rows if row[field] is not None]
            mean_value = float(sum(values) / len(values)) if values else 0.0
            variance = float(sum((value - mean_value) ** 2 for value in values) / len(values)) if values else 0.0
            std_value = variance ** 0.5
            for row in rows:
                value = row[field]
                score_name = f"score_{field}"
                if value is None or std_value <= 1e-9:
                    row[score_name] = 0.0
                else:
                    row[score_name] = round(((float(value) - mean_value) / std_value) * direction, 4)
        for row in rows:
            components = [
                ("growth", float(row["score_growth"])),
                ("inflation", float(row["score_inflation"])),
                ("labor_unemployment", float(row["score_labor_unemployment"])),
                ("policy_rate", float(row["score_policy_rate"])),
                ("equity_return_63d", float(row["score_equity_return_63d"])),
            ]
            total_weight = sum(weights.get(name, 0.0) for name, _ in components)
            if total_weight <= 1e-9:
                row["composite_score"] = 0.0
            else:
                weighted = sum(score * weights.get(name, 0.0) for name, score in components)
                row["composite_score"] = round(weighted / total_weight, 4)
            row["factor_weights"] = weights
        rows.sort(key=lambda item: float(item["composite_score"]), reverse=True)
        return rows

    def _latest_metric_value(self, series_id: str | None, allow_growth_from_level: bool = False) -> float | None:
        if not series_id:
            return None
        observations = self.query_observations(
            ObservationQuery(series_id=series_id, start_date=date.today() - timedelta(days=365 * 10))
        )
        valid = [item for item in observations if item.value is not None]
        if not valid:
            return None
        latest = float(valid[-1].value or 0.0)
        if not allow_growth_from_level:
            return round(latest, 4)
        if len(valid) < 2:
            return round(latest, 4)
        previous = float(valid[-2].value or 0.0)
        if abs(previous) <= 1e-9:
            return round(latest, 4)
        growth_rate = ((latest - previous) / abs(previous)) * 100
        return round(growth_rate, 4)

    def _market_return_63d(self, ticker: str | None) -> float | None:
        if not ticker:
            return None
        prices = self.get_prices(ticker, start_date=date.today() - timedelta(days=180))
        if len(prices) < 64:
            return None
        latest = float(prices[-1].close)
        previous = float(prices[-64].close)
        if abs(previous) <= 1e-9:
            return None
        return round(((latest - previous) / abs(previous)) * 100, 4)

    def get_regime_snapshot(self) -> dict[str, float | str]:
        cpi = self.query_observations(
            ObservationQuery(series_id="fred:CPIAUCSL", start_date=date.today() - timedelta(days=365 * 4))
        )
        unrate = self.query_observations(
            ObservationQuery(series_id="fred:UNRATE", start_date=date.today() - timedelta(days=365 * 4))
        )
        dgs10 = self.query_observations(ObservationQuery(series_id="fred:DGS10", start_date=date.today() - timedelta(days=365)))
        dgs2 = self.query_observations(ObservationQuery(series_id="fred:DGS2", start_date=date.today() - timedelta(days=365)))
        cpi_series = _observations_to_series(cpi, freq="MS")
        inflation_yoy = float(year_over_year(cpi_series).dropna().iloc[-1])
        unemployment_rate = float(_observations_to_series(unrate, freq="MS").dropna().iloc[-1])
        slope = float(
            yield_curve_slope(
                _observations_to_series(dgs10, freq="B"),
                _observations_to_series(dgs2, freq="B"),
            )
            .dropna()
            .iloc[-1]
        )
        regime = classify_regime(inflation_yoy, unemployment_rate, slope)
        return {
            "inflation_yoy": round(inflation_yoy, 2),
            "unemployment_rate": round(unemployment_rate, 2),
            "yield_curve_slope": round(slope, 2),
            "regime": regime,
        }

    def get_regime_explanation(self) -> dict[str, object]:
        snapshot = self.get_regime_snapshot()
        inflation = float(snapshot["inflation_yoy"])
        unemployment = float(snapshot["unemployment_rate"])
        slope = float(snapshot["yield_curve_slope"])
        rules = [
            {
                "regime": "stagflation risk",
                "condition": "inflation_yoy > 3.5 and yield_curve_slope < 0",
                "matched": inflation > 3.5 and slope < 0,
            },
            {
                "regime": "overheating",
                "condition": "inflation_yoy > 3.0 and unemployment_rate < 4.5",
                "matched": inflation > 3.0 and unemployment < 4.5,
            },
            {
                "regime": "growth rebound",
                "condition": "inflation_yoy < 2.5 and yield_curve_slope > 0 and unemployment_rate < 5.5",
                "matched": inflation < 2.5 and slope > 0 and unemployment < 5.5,
            },
            {
                "regime": "slowdown",
                "condition": "unemployment_rate > 6.0 or yield_curve_slope < -0.5",
                "matched": unemployment > 6.0 or slope < -0.5,
            },
            {
                "regime": "disinflation",
                "condition": "fallback default when none of the above match",
                "matched": snapshot["regime"] == "disinflation",
            },
        ]
        return {
            "snapshot": snapshot,
            "rules": rules,
            "selected_regime": snapshot["regime"],
        }

    def get_release_calendar(
        self,
        country: str | None = None,
        days: int = 60,
    ) -> list[ReleaseEvent]:
        rows: list[ReleaseEvent] = []
        horizon = date.today() + timedelta(days=days)
        for definition in self.series_map.values():
            if country and definition.country.upper() != country.upper():
                continue
            latest = self.query_observations(
                ObservationQuery(series_id=definition.id, start_date=date.today() - timedelta(days=365 * 3))
            )
            latest_date = latest[-1].date if latest else None
            expected = _expected_next_release(latest_date, definition.frequency)
            freshness = _freshness_status(latest_date, definition.frequency)
            if expected and expected <= horizon:
                rows.append(
                    ReleaseEvent(
                        series_id=definition.id,
                        title=definition.title,
                        country=definition.country,
                        source=definition.source,
                        frequency=definition.frequency,
                        last_observation_date=latest_date,
                        expected_next_release=expected,
                        freshness_status=freshness,
                        release_lag=definition.release_lag,
                    )
                )
        return sorted(rows, key=lambda item: (item.expected_next_release or date.max, item.country, item.title))

    def get_release_alerts(
        self,
        country: str | None = None,
        days: int = 14,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        if days < 1:
            raise ValueError("days must be at least 1.")
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        today = date.today()
        due_horizon = today + timedelta(days=days)
        severity_rank = {"high": 3, "medium": 2, "low": 1}
        alerts: list[dict[str, object]] = []
        for event in self.get_freshness_status(country=country):
            expected = event.expected_next_release
            days_to_release = (expected - today).days if expected is not None else None
            alert_type: str | None = None
            severity: str | None = None
            if event.freshness_status == "stale":
                alert_type = "stale"
                severity = "high"
            elif expected is not None and expected < today:
                alert_type = "overdue"
                severity = "medium"
            elif expected is not None and expected <= due_horizon:
                alert_type = "due_soon"
                severity = "low"
            if alert_type is None or severity is None:
                continue
            alerts.append(
                {
                    "series_id": event.series_id,
                    "title": event.title,
                    "country": event.country,
                    "source": event.source,
                    "frequency": event.frequency,
                    "last_observation_date": event.last_observation_date,
                    "expected_next_release": event.expected_next_release,
                    "freshness_status": event.freshness_status,
                    "alert_type": alert_type,
                    "severity": severity,
                    "days_to_release": days_to_release,
                }
            )
        alerts = sorted(
            alerts,
            key=lambda item: (
                -severity_rank[str(item["severity"])],
                item["days_to_release"] if item["days_to_release"] is not None else 10**9,
                str(item["series_id"]),
            ),
        )
        return alerts[:limit]

    def get_freshness_status(
        self,
        country: str | None = None,
        topic: str | None = None,
    ) -> list[ReleaseEvent]:
        rows: list[ReleaseEvent] = []
        for definition in self.series_map.values():
            if country and definition.country.upper() != country.upper():
                continue
            if topic and definition.topic != topic:
                continue
            latest = self.query_observations(
                ObservationQuery(series_id=definition.id, start_date=date.today() - timedelta(days=365 * 3))
            )
            latest_date = latest[-1].date if latest else None
            rows.append(
                ReleaseEvent(
                    series_id=definition.id,
                    title=definition.title,
                    country=definition.country,
                    source=definition.source,
                    frequency=definition.frequency,
                    last_observation_date=latest_date,
                    expected_next_release=_expected_next_release(latest_date, definition.frequency),
                    freshness_status=_freshness_status(latest_date, definition.frequency),
                    release_lag=definition.release_lag,
                )
            )
        return sorted(rows, key=lambda item: (item.freshness_status, item.country, item.title))

    def capture_release_freshness_snapshot(
        self,
        country: str | None = None,
        topic: str | None = None,
        days: int = 60,
    ) -> ReleaseFreshnessSnapshot:
        if days < 1:
            raise ValueError("days must be at least 1.")
        rows = self.get_freshness_status(country=country, topic=topic)
        snapshot = ReleaseFreshnessSnapshot(
            id=f"release-freshness-{uuid4().hex[:10]}",
            captured_at=datetime.now(),
            country=country,
            topic=topic,
            days=days,
            rows=rows,
        )
        return self.release_freshness_snapshot_repo.save(snapshot)

    def list_release_freshness_snapshots(
        self,
        country: str | None = None,
        topic: str | None = None,
        limit: int = 50,
    ) -> list[ReleaseFreshnessSnapshot]:
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        return self.release_freshness_snapshot_repo.list_saved(country=country, topic=topic, limit=limit)

    def get_release_freshness_snapshot(self, snapshot_id: str) -> ReleaseFreshnessSnapshot:
        snapshot = self.release_freshness_snapshot_repo.get(snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        return snapshot

    def get_release_freshness_delta(
        self,
        country: str | None = None,
        topic: str | None = None,
    ) -> dict[str, object]:
        snapshots = self.list_release_freshness_snapshots(country=country, topic=topic, limit=2)
        if not snapshots:
            return {
                "has_baseline": False,
                "message": "No release freshness snapshots available.",
                "current_snapshot_id": None,
                "previous_snapshot_id": None,
                "changes": [],
            }
        current = snapshots[0]
        previous = snapshots[1] if len(snapshots) > 1 else None
        if previous is None:
            return {
                "has_baseline": False,
                "message": "Only one snapshot available. Capture another snapshot to compute deltas.",
                "current_snapshot_id": current.id,
                "previous_snapshot_id": None,
                "changes": [],
            }
        prev_map = {item.series_id: item for item in previous.rows}
        curr_map = {item.series_id: item for item in current.rows}
        keys = sorted(set(prev_map.keys()) | set(curr_map.keys()))
        changes: list[dict[str, object]] = []
        for key in keys:
            old = prev_map.get(key)
            new = curr_map.get(key)
            if old is None and new is not None:
                changes.append(
                    {
                        "series_id": key,
                        "title": new.title,
                        "country": new.country,
                        "change_type": "added",
                        "previous_freshness": None,
                        "current_freshness": new.freshness_status,
                        "previous_expected_next_release": None,
                        "current_expected_next_release": new.expected_next_release,
                    }
                )
                continue
            if old is not None and new is None:
                changes.append(
                    {
                        "series_id": key,
                        "title": old.title,
                        "country": old.country,
                        "change_type": "removed",
                        "previous_freshness": old.freshness_status,
                        "current_freshness": None,
                        "previous_expected_next_release": old.expected_next_release,
                        "current_expected_next_release": None,
                    }
                )
                continue
            if old is None or new is None:
                continue
            if (
                old.freshness_status != new.freshness_status
                or old.expected_next_release != new.expected_next_release
                or old.last_observation_date != new.last_observation_date
            ):
                change_type = "freshness_changed" if old.freshness_status != new.freshness_status else "date_updated"
                changes.append(
                    {
                        "series_id": key,
                        "title": new.title,
                        "country": new.country,
                        "change_type": change_type,
                        "previous_freshness": old.freshness_status,
                        "current_freshness": new.freshness_status,
                        "previous_expected_next_release": old.expected_next_release,
                        "current_expected_next_release": new.expected_next_release,
                        "previous_last_observation_date": old.last_observation_date,
                        "current_last_observation_date": new.last_observation_date,
                    }
                )
        return {
            "has_baseline": True,
            "current_snapshot_id": current.id,
            "previous_snapshot_id": previous.id,
            "current_captured_at": current.captured_at,
            "previous_captured_at": previous.captured_at,
            "changes": changes,
            "change_count": len(changes),
        }

    def list_source_health(
        self,
        source_kind: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[SourceHealth]:
        rows = self.source_health_repo.list_saved(source_kind=source_kind, status=status, limit=limit)
        now = datetime.now()
        updated: list[SourceHealth] = []
        for item in rows:
            if _source_health_is_stale(item, now=now):
                item.is_stale = True
                if item.status == "healthy":
                    item.status = "degraded"
            else:
                item.is_stale = False
            updated.append(item)
        return updated

    def get_source_health_summary(self) -> dict[str, int]:
        rows = self.list_source_health(limit=2000)
        return {
            "total": len(rows),
            "healthy": sum(1 for item in rows if item.status == "healthy"),
            "degraded": sum(1 for item in rows if item.status == "degraded"),
            "down": sum(1 for item in rows if item.status == "down"),
            "unknown": sum(1 for item in rows if item.status == "unknown"),
            "stale": sum(1 for item in rows if item.is_stale),
        }

    def get_normalization_qa_summary(self, max_series_scan: int = 500) -> dict[str, object]:
        if max_series_scan < 1:
            raise ValueError("max_series_scan must be at least 1.")
        allowed_frequencies = {"daily", "weekly", "monthly", "quarterly", "annual"}
        rows = list(self.series_map.values())
        frequency_counts = Counter(item.frequency for item in rows)
        topic_counts = Counter(item.topic for item in rows)
        country_counts = Counter(item.country for item in rows)
        invalid_frequency = [item.id for item in rows if item.frequency not in allowed_frequencies]
        unitless_series = [item.id for item in rows if not str(item.unit or "").strip()]
        issues: list[dict[str, object]] = []
        if invalid_frequency:
            issues.append(
                {
                    "check": "invalid_frequency",
                    "severity": "high",
                    "count": len(invalid_frequency),
                    "samples": invalid_frequency[:10],
                    "message": "One or more series use unsupported frequency labels.",
                }
            )
        if unitless_series:
            issues.append(
                {
                    "check": "missing_unit",
                    "severity": "medium",
                    "count": len(unitless_series),
                    "samples": unitless_series[:10],
                    "message": "One or more series are missing a normalized unit.",
                }
            )

        scanned_series = 0
        scanned_observations = 0
        missing_value_count = 0
        revision_row_count = 0
        revision_tz_aware_count = 0
        revision_tz_naive_count = 0
        for series_id in sorted(self.series_map.keys())[:max_series_scan]:
            observations = self.observation_repo.get_range(series_id)
            if not observations:
                continue
            scanned_series += 1
            scanned_observations += len(observations)
            missing_value_count += sum(1 for item in observations if item.value is None)
            for item in observations:
                if item.vintage_date is not None or item.revision_timestamp is not None:
                    revision_row_count += 1
                if item.revision_timestamp is None:
                    continue
                if item.revision_timestamp.tzinfo is None:
                    revision_tz_naive_count += 1
                else:
                    revision_tz_aware_count += 1

        missing_value_ratio = (
            (missing_value_count / scanned_observations) if scanned_observations > 0 else 0.0
        )
        if scanned_series == 0:
            issues.append(
                {
                    "check": "observation_scan_empty",
                    "severity": "low",
                    "count": 0,
                    "samples": [],
                    "message": "No cached observations were available for normalization QA observation checks.",
                }
            )
        elif missing_value_ratio > 0.05:
            issues.append(
                {
                    "check": "missing_values",
                    "severity": "medium",
                    "count": missing_value_count,
                    "samples": [],
                    "message": "Cached observations include a high missing-value ratio.",
                }
            )
        if revision_tz_aware_count > 0 and revision_tz_naive_count > 0:
            issues.append(
                {
                    "check": "mixed_revision_timestamp_timezone",
                    "severity": "medium",
                    "count": revision_tz_aware_count + revision_tz_naive_count,
                    "samples": [],
                    "message": "Revision timestamps mix timezone-aware and timezone-naive datetime values.",
                }
            )

        return {
            "series_total": len(rows),
            "country_count": len(country_counts),
            "topic_count": len(topic_counts),
            "frequency_counts": dict(sorted(frequency_counts.items())),
            "invalid_frequency_count": len(invalid_frequency),
            "missing_unit_count": len(unitless_series),
            "scanned_series_count": scanned_series,
            "scanned_observation_count": scanned_observations,
            "missing_value_count": missing_value_count,
            "missing_value_ratio": round(missing_value_ratio, 6),
            "revision_row_count": revision_row_count,
            "revision_timezone_aware_count": revision_tz_aware_count,
            "revision_timezone_naive_count": revision_tz_naive_count,
            "issues": issues,
        }

    def get_source_health_alerts(self, limit: int = 50) -> list[dict[str, object]]:
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        rows = self.list_source_health(limit=2000)
        alerts: list[dict[str, object]] = []
        severity_rank = {"high": 3, "medium": 2, "low": 1}
        for item in rows:
            reasons: list[str] = []
            if item.status == "down":
                reasons.append("down")
            elif item.status == "degraded":
                reasons.append("degraded")
            if item.is_stale:
                reasons.append("stale")
            if not reasons:
                continue
            if "down" in reasons:
                severity = "high"
            elif "degraded" in reasons:
                severity = "medium"
            else:
                severity = "low"
            alerts.append(
                {
                    "source_id": item.id,
                    "source_kind": item.source_kind,
                    "provider": item.provider,
                    "status": item.status,
                    "reasons": reasons,
                    "severity": severity,
                    "is_stale": item.is_stale,
                    "stale_threshold_minutes": item.stale_threshold_minutes,
                    "consecutive_failures": item.consecutive_failures,
                    "last_checked_at": item.last_checked_at,
                    "last_success_at": item.last_success_at,
                    "last_failure_at": item.last_failure_at,
                    "fallback_used": item.fallback_used,
                    "last_error": item.last_error,
                }
            )
        alerts = sorted(
            alerts,
            key=lambda item: (
                -severity_rank[str(item["severity"])],
                str(item["source_id"]),
            ),
        )
        return alerts[:limit]

    def list_source_health_policies(
        self,
        active_only: bool = False,
        include_archived: bool = False,
        limit: int = 200,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[SourceHealthPolicy]:
        rows = self.source_health_policy_repo.list_saved(
            active_only=active_only,
            limit=limit,
            owner_scope=self._normalize_owner_scope_filter(owner_scope),
        )
        if not include_archived:
            rows = [item for item in rows if item.archived_at is None]
        return rows

    def get_source_health_policy(self, policy_id: str) -> SourceHealthPolicy:
        policy = self.source_health_policy_repo.get(policy_id)
        if policy is None:
            raise KeyError(policy_id)
        return policy

    def save_source_health_policy(self, policy: SourceHealthPolicy, allow_shared_mutation: bool = True) -> SourceHealthPolicy:
        existing_policy = self.source_health_policy_repo.get(policy.id)
        if existing_policy is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Source health policy",
                entity_id=policy.id,
                existing_scope=existing_policy.owner_scope,
                incoming_scope=policy.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        self._validate_source_health_policy(policy)
        self._verify_source_health_policy_channels(policy)
        self._apply_source_health_policy_threshold(policy)
        saved = self.source_health_policy_repo.save(policy)
        self._record_source_health_policy_version(
            policy=saved,
            action="create" if existing_policy is None else "update",
            previous=existing_policy,
        )
        return saved

    def archive_source_health_policy(
        self,
        policy_id: str,
        reason: str | None = None,
        allow_shared_mutation: bool = True,
    ) -> SourceHealthPolicy:
        policy = self.get_source_health_policy(policy_id)
        self._enforce_shared_mutation_policy(
            entity_label="Source health policy",
            entity_id=policy_id,
            existing_scope=policy.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        previous = policy.model_copy(deep=True)
        policy.active = False
        policy.archived_at = datetime.now()
        policy.archived_reason = reason
        saved = self.source_health_policy_repo.save(policy)
        self._record_source_health_policy_version(policy=saved, action="archive", previous=previous)
        return saved

    def restore_source_health_policy(self, policy_id: str, allow_shared_mutation: bool = True) -> SourceHealthPolicy:
        policy = self.get_source_health_policy(policy_id)
        self._enforce_shared_mutation_policy(
            entity_label="Source health policy",
            entity_id=policy_id,
            existing_scope=policy.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        previous = policy.model_copy(deep=True)
        policy.archived_at = None
        policy.archived_reason = None
        policy.active = True
        saved = self.source_health_policy_repo.save(policy)
        self._record_source_health_policy_version(policy=saved, action="restore", previous=previous)
        return saved

    def rollback_source_health_policy_version(self, version_id: str) -> SourceHealthPolicy:
        version = self.get_source_health_policy_version(version_id)
        current_policy = self.get_source_health_policy(version.policy_id)
        restored_snapshot = {**version.snapshot, "id": version.policy_id}
        restored_policy = SourceHealthPolicy.model_validate(restored_snapshot)
        self._validate_source_health_policy(restored_policy)
        self._verify_source_health_policy_channels(restored_policy)
        self._apply_source_health_policy_threshold(restored_policy)
        saved = self.source_health_policy_repo.save(restored_policy)
        self._record_source_health_policy_version(policy=saved, action="rollback", previous=current_policy)
        return saved

    def list_source_health_policy_runs(self, trigger: str | None = None, limit: int = 100) -> list[SourceHealthPolicyRun]:
        return self.source_health_policy_run_repo.list_saved(trigger=trigger, limit=limit)

    def get_source_health_policy_run(self, run_id: str) -> SourceHealthPolicyRun:
        run = self.source_health_policy_run_repo.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def list_source_health_policy_versions(
        self,
        policy_id: str,
        limit: int = 50,
        action: str | None = None,
        query: str | None = None,
    ) -> list[SourceHealthPolicyVersion]:
        self.get_source_health_policy(policy_id)
        rows = self.source_health_policy_version_repo.list_saved(policy_id=policy_id, limit=max(limit * 3, limit))
        if action:
            rows = [row for row in rows if row.action == action]
        if query:
            needle = query.strip().lower()
            if needle:
                rows = [
                    row
                    for row in rows
                    if needle in row.summary.lower()
                    or needle in row.action.lower()
                    or any(needle in field.lower() for field in row.changed_fields)
                ]
        return rows[:limit]

    def list_source_health_policy_version_presets(
        self,
        policy_id: str,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "name",
        order: str = "asc",
        query: str | None = None,
        only_default: bool = False,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[SourceHealthPolicyVersionPreset]:
        self.get_source_health_policy(policy_id)
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        if offset < 0:
            raise ValueError("offset must be at least 0.")
        if order not in {"asc", "desc"}:
            raise ValueError("order must be either 'asc' or 'desc'.")
        sort_key_map = {
            "name": lambda item: item.name.lower(),
            "usage_count": lambda item: int(item.usage_count),
            "last_used_at": lambda item: item.last_used_at or datetime.min,
            "updated_at": lambda item: item.updated_at or datetime.min,
            "created_at": lambda item: item.created_at or datetime.min,
            "is_default": lambda item: 1 if item.is_default else 0,
        }
        key_fn = sort_key_map.get(sort_by)
        if key_fn is None:
            raise ValueError(
                "sort_by must be one of: name, usage_count, last_used_at, updated_at, created_at, is_default."
            )
        rows = self.source_health_policy_version_preset_repo.list_saved(policy_id=policy_id, limit=1000)
        if owner_scope != "all":
            rows = [item for item in rows if item.owner_scope == owner_scope]
        if only_default:
            rows = [item for item in rows if item.is_default]
        if query is not None:
            needle = query.strip().lower()
            if needle:
                rows = [
                    item
                    for item in rows
                    if needle in item.name.lower()
                    or needle in (item.action_filter or "").lower()
                    or needle in (item.query or "").lower()
                ]
        rows = sorted(rows, key=key_fn, reverse=order == "desc")
        return rows[offset : offset + limit]

    def get_source_health_policy_version_preset_summary(self, policy_id: str) -> dict[str, object]:
        rows = self.list_source_health_policy_version_presets(
            policy_id=policy_id,
            limit=1000,
            offset=0,
            sort_by="name",
            order="asc",
        )
        default_preset = next((item for item in rows if item.is_default), None)
        most_used = max(rows, key=lambda item: int(item.usage_count), default=None)
        used_rows = [item for item in rows if item.last_used_at is not None]
        last_used = max(used_rows, key=lambda item: item.last_used_at, default=None) if used_rows else None
        return {
            "policy_id": policy_id,
            "total_presets": len(rows),
            "default_preset_id": default_preset.id if default_preset is not None else None,
            "default_preset_name": default_preset.name if default_preset is not None else None,
            "most_used_preset_id": most_used.id if most_used is not None else None,
            "most_used_preset_name": most_used.name if most_used is not None else None,
            "most_used_count": int(most_used.usage_count) if most_used is not None else 0,
            "last_used_preset_id": last_used.id if last_used is not None else None,
            "last_used_preset_name": last_used.name if last_used is not None else None,
            "last_used_at": last_used.last_used_at if last_used is not None else None,
        }

    def list_source_health_policy_versions_by_preset(
        self,
        preset_id: str,
    ) -> list[SourceHealthPolicyVersion]:
        preset = self.touch_source_health_policy_version_preset_usage(preset_id)
        return self.list_source_health_policy_versions(
            policy_id=preset.policy_id,
            limit=int(preset.limit),
            action=preset.action_filter,
            query=preset.query,
        )

    def get_source_health_policy_version_preset(self, preset_id: str) -> SourceHealthPolicyVersionPreset:
        preset = self.source_health_policy_version_preset_repo.get(preset_id)
        if preset is None:
            raise KeyError(preset_id)
        return preset

    def save_source_health_policy_version_preset(
        self,
        preset: SourceHealthPolicyVersionPreset,
        allow_shared_mutation: bool = True,
    ) -> SourceHealthPolicyVersionPreset:
        self.get_source_health_policy(preset.policy_id)
        existing = self.source_health_policy_version_preset_repo.get(preset.id)
        if existing is not None:
            if existing.policy_id != preset.policy_id:
                raise ValueError("Cannot move preset to a different policy.")
            self._enforce_shared_mutation_policy(
                entity_label="Source health policy version preset",
                entity_id=preset.id,
                existing_scope=existing.owner_scope,
                incoming_scope=preset.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
            if preset.created_at is None:
                preset.created_at = existing.created_at
            if preset.last_used_at is None:
                preset.last_used_at = existing.last_used_at
            preset.usage_count = existing.usage_count
        now = datetime.now()
        if preset.created_at is None:
            preset.created_at = now
        if preset.usage_count < 0:
            raise ValueError("usage_count must be at least 0.")
        preset.updated_at = now
        if preset.limit < 1:
            raise ValueError("limit must be at least 1.")
        if preset.action_filter is not None and preset.action_filter not in {"create", "update", "archive", "restore", "rollback"}:
            raise ValueError("action_filter must be one of: create, update, archive, restore, rollback.")
        if preset.query is not None:
            preset.query = preset.query.strip() or None
        preset.name = preset.name.strip()
        if not preset.name:
            raise ValueError("name must be non-empty.")
        self._validate_source_health_policy_version_preset_name_uniqueness(
            policy_id=preset.policy_id,
            name=preset.name,
            current_preset_id=preset.id,
        )
        saved = self.source_health_policy_version_preset_repo.save(preset)
        if saved.is_default:
            for item in self.list_source_health_policy_version_presets(saved.policy_id, limit=500):
                if item.id == saved.id:
                    continue
                if item.is_default:
                    item.is_default = False
                    self.source_health_policy_version_preset_repo.save(item)
        self._ensure_source_health_policy_version_preset_default(
            policy_id=saved.policy_id,
            preferred_preset_id=saved.id,
        )
        return self.get_source_health_policy_version_preset(saved.id)

    def delete_source_health_policy_version_preset(self, preset_id: str, allow_shared_mutation: bool = True) -> None:
        deleted = self.get_source_health_policy_version_preset(preset_id)
        self._enforce_shared_mutation_policy(
            entity_label="Source health policy version preset",
            entity_id=preset_id,
            existing_scope=deleted.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.source_health_policy_version_preset_repo.delete(preset_id)
        if deleted.is_default:
            remaining = self.list_source_health_policy_version_presets(deleted.policy_id, limit=500)
            if remaining:
                promoted = remaining[0]
                promoted.is_default = True
                self.save_source_health_policy_version_preset(promoted, allow_shared_mutation=True)
        self._ensure_source_health_policy_version_preset_default(policy_id=deleted.policy_id)

    def clone_source_health_policy_version_preset(
        self,
        preset_id: str,
        name: str | None = None,
        allow_shared_mutation: bool = True,
    ) -> SourceHealthPolicyVersionPreset:
        preset = self.get_source_health_policy_version_preset(preset_id)
        clone = preset.model_copy(deep=True)
        clone.id = f"source-policy-version-preset-{uuid4().hex[:8]}"
        if name is None:
            clone.name = self._next_available_source_health_policy_version_preset_name(
                policy_id=preset.policy_id,
                base_name=f"{preset.name} copy",
            )
        else:
            clone.name = name.strip()
        clone.is_default = False
        clone.created_at = None
        clone.updated_at = None
        clone.last_used_at = None
        clone.usage_count = 0
        if not clone.name:
            raise ValueError("name must be non-empty.")
        return self.save_source_health_policy_version_preset(clone, allow_shared_mutation=allow_shared_mutation)

    def set_default_source_health_policy_version_preset(
        self,
        preset_id: str,
        allow_shared_mutation: bool = True,
    ) -> SourceHealthPolicyVersionPreset:
        preset = self.get_source_health_policy_version_preset(preset_id)
        preset.is_default = True
        return self.save_source_health_policy_version_preset(preset, allow_shared_mutation=allow_shared_mutation)

    def touch_source_health_policy_version_preset_usage(
        self,
        preset_id: str,
    ) -> SourceHealthPolicyVersionPreset:
        preset = self.get_source_health_policy_version_preset(preset_id)
        touched = preset.model_copy(deep=True)
        now = datetime.now()
        if touched.created_at is None:
            touched.created_at = now
        touched.updated_at = now
        touched.last_used_at = now
        touched.usage_count = max(0, int(touched.usage_count)) + 1
        self.source_health_policy_version_preset_repo.save(touched)
        return touched

    def export_source_health_policy_version_presets(
        self,
        policy_id: str,
        limit: int = 500,
    ) -> SourceHealthPolicyVersionPresetExportBundle:
        self.get_source_health_policy(policy_id)
        presets = self.list_source_health_policy_version_presets(policy_id=policy_id, limit=limit)
        return SourceHealthPolicyVersionPresetExportBundle(
            policy_id=policy_id,
            exported_at=datetime.now(),
            presets=presets,
        )

    def import_source_health_policy_version_presets(
        self,
        policy_id: str,
        request: SourceHealthPolicyVersionPresetImportRequest,
    ) -> list[SourceHealthPolicyVersionPreset]:
        self.get_source_health_policy(policy_id)
        presets = request.presets
        if not presets:
            raise ValueError("presets must include at least one entry.")
        preview = self.preview_source_health_policy_version_presets_import(policy_id=policy_id, request=request)
        if not bool(preview.get("valid", False)):
            errors = preview.get("errors", [])
            if errors:
                raise ValueError("; ".join(str(item) for item in errors))
            raise ValueError("preset import is invalid.")
        existing = self.list_source_health_policy_version_presets(policy_id=policy_id, limit=1000)
        snapshot = [item.model_copy(deep=True) for item in existing]
        existing_default_id = next((item.id for item in existing if item.is_default), None)
        try:
            if request.mode == "replace":
                for item in existing:
                    self.source_health_policy_version_preset_repo.delete(item.id)
            imported: list[SourceHealthPolicyVersionPreset] = []
            force_first_default = request.mode == "replace" and not any(item.is_default for item in presets)
            existing_by_name = {item.name.strip().lower(): item for item in existing}
            for index, item in enumerate(presets):
                match = existing_by_name.get(item.name.strip().lower()) if request.mode == "upsert" else None
                if match is not None:
                    imported_item = SourceHealthPolicyVersionPreset(
                        id=match.id,
                        policy_id=policy_id,
                        name=item.name,
                        action_filter=item.action_filter,
                        query=item.query,
                        limit=item.limit,
                        is_default=item.is_default,
                        owner_scope=item.owner_scope,
                        created_at=match.created_at,
                        updated_at=match.updated_at,
                        last_used_at=match.last_used_at,
                        usage_count=match.usage_count,
                    )
                else:
                    imported_item = self._create_source_health_policy_version_preset_from_import(
                        policy_id=policy_id,
                        item=item,
                        is_default_override=True if force_first_default and index == 0 else None,
                    )
                imported.append(self.save_source_health_policy_version_preset(imported_item))
            preferred_default_id = existing_default_id
            if preferred_default_id is None and imported:
                preferred_default_id = imported[0].id
            self._ensure_source_health_policy_version_preset_default(
                policy_id=policy_id,
                preferred_preset_id=preferred_default_id,
            )
            return [self.get_source_health_policy_version_preset(item.id) for item in imported]
        except Exception:
            self._restore_source_health_policy_version_preset_snapshot(policy_id=policy_id, snapshot=snapshot)
            raise

    def preview_source_health_policy_version_presets_import(
        self,
        policy_id: str,
        request: SourceHealthPolicyVersionPresetImportRequest,
    ) -> dict[str, object]:
        self.get_source_health_policy(policy_id)
        existing = self.list_source_health_policy_version_presets(policy_id=policy_id, limit=1000)
        existing_by_name = {
            item.name.strip().lower(): item
            for item in existing
        }
        names = [item.name.strip() for item in request.presets]
        normalized_names = [item.lower() for item in names]
        errors: list[str] = []
        if not request.presets:
            errors.append("presets must include at least one entry.")
        if any(not item for item in names):
            errors.append("import preset names must be non-empty.")
        duplicate_names = sorted({name for name in normalized_names if normalized_names.count(name) > 1})
        if duplicate_names:
            errors.append("import contains duplicate preset names: " + ", ".join(duplicate_names))
        actions: list[dict[str, object]] = []
        create_count = 0
        update_count = 0
        conflict_count = 0
        for item in request.presets:
            clean_name = item.name.strip()
            normalized = clean_name.lower()
            if item.limit < 1:
                errors.append(
                    f"preset '{clean_name or '<blank>'}' limit must be at least 1."
                )
            if item.action_filter is not None and item.action_filter not in {
                "create",
                "update",
                "archive",
                "restore",
                "rollback",
            }:
                errors.append(
                    f"preset '{clean_name or '<blank>'}' action_filter must be one of: "
                    "create, update, archive, restore, rollback."
                )
            match = existing_by_name.get(normalized)
            action = "create"
            if request.mode == "replace":
                action = "create"
            elif request.mode == "upsert":
                action = "update" if match is not None else "create"
            elif request.mode == "append":
                if match is not None:
                    action = "conflict"
                    conflict_count += 1
                    errors.append(f"preset names already exist for this policy: {normalized}")
                else:
                    action = "create"
            if action == "create":
                create_count += 1
            if action == "update":
                update_count += 1
            actions.append(
                {
                    "name": clean_name,
                    "action": action,
                    "valid": item.limit >= 1
                    and (
                        item.action_filter is None
                        or item.action_filter in {"create", "update", "archive", "restore", "rollback"}
                    ),
                    "existing_preset_id": match.id if match is not None else None,
                    "incoming_is_default": bool(item.is_default),
                    "incoming_limit": int(item.limit),
                }
            )
        replaced_count = len(existing) if request.mode == "replace" else 0
        unique_errors = sorted(set(errors))
        return {
            "policy_id": policy_id,
            "mode": request.mode,
            "valid": len(unique_errors) == 0,
            "existing_count": len(existing),
            "incoming_count": len(request.presets),
            "replaced_count": replaced_count,
            "create_count": create_count,
            "update_count": update_count,
            "conflict_count": conflict_count,
            "errors": unique_errors,
            "actions": actions,
        }

    def rename_source_health_policy_version_preset(
        self,
        preset_id: str,
        name: str,
        allow_shared_mutation: bool = True,
    ) -> SourceHealthPolicyVersionPreset:
        preset = self.get_source_health_policy_version_preset(preset_id)
        updated = preset.model_copy(deep=True)
        updated.name = name
        return self.save_source_health_policy_version_preset(updated, allow_shared_mutation=allow_shared_mutation)

    def _validate_source_health_policy_version_preset_name_uniqueness(
        self,
        policy_id: str,
        name: str,
        current_preset_id: str | None = None,
    ) -> None:
        normalized = name.strip().lower()
        for item in self.list_source_health_policy_version_presets(policy_id=policy_id, limit=1000):
            if current_preset_id is not None and item.id == current_preset_id:
                continue
            if item.name.strip().lower() == normalized:
                raise ValueError(f"preset name '{name.strip()}' already exists for this policy.")

    def _next_available_source_health_policy_version_preset_name(
        self,
        policy_id: str,
        base_name: str,
    ) -> str:
        seed = base_name.strip()
        if not seed:
            raise ValueError("base_name must be non-empty.")
        existing = {
            item.name.strip().lower()
            for item in self.list_source_health_policy_version_presets(policy_id=policy_id, limit=1000)
        }
        if seed.lower() not in existing:
            return seed
        index = 2
        while True:
            candidate = f"{seed} {index}"
            if candidate.lower() not in existing:
                return candidate
            index += 1

    def _ensure_source_health_policy_version_preset_default(
        self,
        policy_id: str,
        preferred_preset_id: str | None = None,
    ) -> None:
        rows = self.list_source_health_policy_version_presets(policy_id=policy_id, limit=1000)
        if not rows:
            return
        defaults = [item for item in rows if item.is_default]
        if len(defaults) == 1:
            return
        keep_id: str
        if defaults:
            default_ids = {item.id for item in defaults}
            if preferred_preset_id and preferred_preset_id in default_ids:
                keep_id = preferred_preset_id
            else:
                keep_id = defaults[0].id
        else:
            row_ids = {item.id for item in rows}
            if preferred_preset_id and preferred_preset_id in row_ids:
                keep_id = preferred_preset_id
            else:
                keep_id = rows[0].id
        for item in rows:
            desired_default = item.id == keep_id
            if item.is_default != desired_default:
                item.is_default = desired_default
                self.source_health_policy_version_preset_repo.save(item)

    def _restore_source_health_policy_version_preset_snapshot(
        self,
        policy_id: str,
        snapshot: list[SourceHealthPolicyVersionPreset],
    ) -> None:
        current = self.list_source_health_policy_version_presets(policy_id=policy_id, limit=1000)
        for item in current:
            self.source_health_policy_version_preset_repo.delete(item.id)
        for item in snapshot:
            self.source_health_policy_version_preset_repo.save(item.model_copy(deep=True))
        preferred_default_id = next((item.id for item in snapshot if item.is_default), None)
        self._ensure_source_health_policy_version_preset_default(
            policy_id=policy_id,
            preferred_preset_id=preferred_default_id,
        )

    def get_source_health_policy_version(self, version_id: str) -> SourceHealthPolicyVersion:
        version = self.source_health_policy_version_repo.get(version_id)
        if version is None:
            raise KeyError(version_id)
        return version

    def compare_source_health_policy_versions(
        self,
        left_version_id: str,
        right_version_id: str,
    ) -> dict[str, object]:
        left_version = self.get_source_health_policy_version(left_version_id)
        right_version = self.get_source_health_policy_version(right_version_id)
        if left_version.policy_id != right_version.policy_id:
            raise ValueError("Version comparison requires both versions to belong to the same policy.")
        left_snapshot = left_version.snapshot
        right_snapshot = right_version.snapshot
        changed_fields = sorted(set(left_snapshot.keys()) | set(right_snapshot.keys()))
        diffs: list[dict[str, object]] = []
        for field in changed_fields:
            left_value = left_snapshot.get(field)
            right_value = right_snapshot.get(field)
            if left_value != right_value:
                diffs.append(
                    {
                        "field": field,
                        "left_value": left_value,
                        "right_value": right_value,
                    }
                )
        return {
            "policy_id": left_version.policy_id,
            "left_version_id": left_version.id,
            "left_version_number": left_version.version_number,
            "left_action": left_version.action,
            "right_version_id": right_version.id,
            "right_version_number": right_version.version_number,
            "right_action": right_version.action,
            "changed_fields": [item["field"] for item in diffs],
            "diffs": diffs,
        }

    def set_source_stale_threshold(self, source_id: str, minutes: int) -> SourceHealth:
        if minutes < 1:
            raise ValueError("minutes must be at least 1.")
        source = self.source_health_repo.get(source_id)
        now = datetime.now()
        if source is None:
            source_kind, provider = _parse_source_id(source_id)
            source = SourceHealth(
                id=source_id,
                source_kind=source_kind,
                provider=provider,
                status="unknown",
                last_checked_at=now,
            )
        source.stale_threshold_minutes = minutes
        source.is_stale = _source_health_is_stale(source, now=now)
        return self.source_health_repo.save(source)

    def run_source_health_policies(
        self,
        now: datetime | None = None,
        trigger: str = "manual",
    ) -> list[dict[str, object]]:
        started_at = now or datetime.now()
        sources = self.list_source_health(limit=5000)
        policies = self.list_source_health_policies(active_only=True, limit=500)
        actions: list[dict[str, object]] = []
        for policy in policies:
            if not policy.notification_channel_ids:
                continue
            if policy.last_triggered_at and policy.cooldown_minutes > 0:
                if policy.last_triggered_at > (started_at - timedelta(minutes=policy.cooldown_minutes)):
                    actions.append(
                        {
                            "policy_id": policy.id,
                            "status": "skipped",
                            "reason": "cooldown_active",
                        }
                    )
                    continue
            policy_triggered = False
            for source in sources:
                if policy.source_kind and source.source_kind != policy.source_kind:
                    continue
                if policy.source_id and source.id != policy.source_id:
                    continue
                reasons: list[str] = []
                if policy.trigger_on_down and source.status == "down":
                    reasons.append("down")
                if policy.trigger_on_degraded and source.status == "degraded":
                    reasons.append("degraded")
                if policy.trigger_on_stale and source.is_stale:
                    reasons.append("stale")
                if source.consecutive_failures < policy.min_consecutive_failures:
                    continue
                if not reasons:
                    continue
                for reason in reasons:
                    if not _source_policy_reason_in_schedule(
                        policy=policy,
                        reason=reason,
                        check_time=started_at,
                    ):
                        actions.append(
                            {
                                "policy_id": policy.id,
                                "source_id": source.id,
                                "status": "skipped",
                                "reason": "outside_policy_window",
                                "reason_type": reason,
                            }
                        )
                        continue
                    severity = policy.reason_severity.get(reason) or _source_reason_default_severity(reason)
                    subject_template = policy.reason_subject_templates.get(reason)
                    subject = (
                        _render_source_health_subject_template(
                            subject_template,
                            source_id=source.id,
                            source_kind=source.source_kind,
                            provider=source.provider,
                            reason=reason,
                            status=source.status,
                            severity=severity,
                        )
                        if subject_template
                        else f"Source Health Alert [{severity.upper()}]: {source.id} ({reason})"
                    )
                    channel_ids = list(policy.reason_channel_overrides.get(reason, [])) or list(policy.notification_channel_ids)
                    escalated = source.consecutive_failures >= policy.escalation_failure_threshold
                    if escalated:
                        channel_ids = channel_ids + policy.escalation_channel_ids
                    seen_channels: set[str] = set()
                    deduped_channels: list[str] = []
                    for channel_id in channel_ids:
                        if channel_id in seen_channels:
                            continue
                        seen_channels.add(channel_id)
                        deduped_channels.append(channel_id)
                    related_id = f"source-health-{policy.id}-{source.id}-{reason}"
                    payload = {
                        "source_health_alert": True,
                        "policy_id": policy.id,
                        "policy_name": policy.name,
                        "source_id": source.id,
                        "source_kind": source.source_kind,
                        "provider": source.provider,
                        "status": source.status,
                        "is_stale": source.is_stale,
                        "consecutive_failures": source.consecutive_failures,
                        "reason": reason,
                        "severity": severity,
                        "escalated": escalated,
                        "last_checked_at": source.last_checked_at.isoformat(),
                        "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
                        "last_failure_at": source.last_failure_at.isoformat() if source.last_failure_at else None,
                        "last_error": source.last_error,
                    }
                    delivered = 0
                    for channel_id in deduped_channels:
                        delivery = self._send_governed_notification(
                            channel_id=channel_id,
                            event_type="manual",
                            related_id=related_id,
                            subject=subject,
                            payload=payload,
                            apply_suppression=True,
                            apply_followups=False,
                        )
                        if delivery is not None:
                            delivered += 1
                    policy_triggered = True
                    actions.append(
                        {
                            "policy_id": policy.id,
                            "source_id": source.id,
                            "status": "triggered",
                            "reason": reason,
                            "severity": severity,
                            "escalated": escalated,
                            "channels": deduped_channels,
                            "deliveries": delivered,
                        }
                    )
            if policy_triggered:
                policy.last_triggered_at = started_at
                self.source_health_policy_repo.save(policy)
        finished_at = datetime.now()
        run_trigger = trigger if trigger in {"manual", "worker"} else "manual"
        run = SourceHealthPolicyRun(
            id=f"source-health-policy-run-{uuid4().hex[:10]}",
            trigger=run_trigger,
            started_at=started_at,
            finished_at=finished_at,
            attempted_policies=len(policies),
            triggered_actions=sum(1 for item in actions if item.get("status") == "triggered"),
            skipped_actions=sum(1 for item in actions if item.get("status") == "skipped"),
            failed_actions=sum(1 for item in actions if item.get("status") == "failed"),
            actions=actions,
        )
        self.source_health_policy_run_repo.save(run)
        return actions

    def get_change_monitor(
        self,
        country: str | None = None,
        topic: str | None = None,
        asset_class: str | None = None,
        limit: int = 25,
    ) -> list[ChangeSignal]:
        rows: list[ChangeSignal] = []
        for definition in self.series_map.values():
            if country and definition.country.upper() != country.upper():
                continue
            if topic and definition.topic != topic:
                continue
            observations = self.query_observations(
                ObservationQuery(series_id=definition.id, start_date=date.today() - timedelta(days=365 * 3))
            )
            signal = _build_series_change_signal(definition, observations)
            if signal is not None:
                rows.append(signal)
        for ticker, ticker_asset_class in self.market_universe.items():
            if asset_class and ticker_asset_class != asset_class:
                continue
            prices = self.get_prices(ticker, start_date=date.today() - timedelta(days=120))
            signal = _build_asset_change_signal(ticker, ticker_asset_class, prices)
            if signal is not None:
                rows.append(signal)
        rows.sort(
            key=lambda item: (
                _significance_rank(item.significance),
                abs(item.percent_change) if item.percent_change is not None else abs(item.absolute_change),
            ),
            reverse=True,
        )
        return rows[:limit]

    def get_change_monitor_deltas(
        self,
        country: str | None = None,
        topic: str | None = None,
        asset_class: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for definition in self.series_map.values():
            if country and definition.country.upper() != country.upper():
                continue
            if topic and definition.topic != topic:
                continue
            observations = self.query_observations(
                ObservationQuery(series_id=definition.id, start_date=date.today() - timedelta(days=365 * 3))
            )
            delta_row = _build_series_change_delta(definition, observations)
            if delta_row is not None:
                rows.append(delta_row)
        for ticker, ticker_asset_class in self.market_universe.items():
            if asset_class and ticker_asset_class != asset_class:
                continue
            prices = self.get_prices(ticker, start_date=date.today() - timedelta(days=120))
            delta_row = _build_asset_change_delta(ticker, ticker_asset_class, prices)
            if delta_row is not None:
                rows.append(delta_row)
        rows.sort(
            key=lambda item: (
                _significance_rank(str(item["significance"])),
                abs(float(item["delta_percent_change"])) if item["delta_percent_change"] is not None else abs(float(item["delta_absolute_change"])),
            ),
            reverse=True,
        )
        return rows[:limit]

    def run_screen(self, spec: ScreenSpec) -> list[dict[str, float | str]]:
        universe = spec.universe or list(self.market_universe.keys())
        frames = []
        for ticker in universe:
            prices = self.get_prices(ticker, start_date=date.today() - timedelta(days=365))
            if not prices:
                continue
            frame = pd.DataFrame([row.model_dump(mode="json") for row in prices])
            frames.append(frame)
        if not frames:
            return []
        combined = pd.concat(frames, ignore_index=True)
        results = screen_assets(combined, self.market_universe, spec.filters)
        return [item.model_dump() for item in results]

    def run_screen_explain(self, spec: ScreenSpec) -> dict[str, object]:
        universe = spec.universe or list(self.market_universe.keys())
        traces: list[dict[str, object]] = []
        for ticker in universe:
            prices = self.get_prices(ticker, start_date=date.today() - timedelta(days=365))
            if not prices:
                traces.append(
                    {
                        "ticker": ticker,
                        "asset_class": self.market_universe.get(ticker, "unknown"),
                        "has_data": False,
                        "passed": False,
                        "reason": "no_price_data",
                        "metrics": {},
                        "filter_trace": [],
                    }
                )
                continue
            frame = pd.DataFrame([row.model_dump(mode="json") for row in prices]).sort_values("date")
            closes = frame["close"].reset_index(drop=True)
            if len(closes) < 64:
                traces.append(
                    {
                        "ticker": ticker,
                        "asset_class": self.market_universe.get(ticker, "unknown"),
                        "has_data": True,
                        "passed": False,
                        "reason": "insufficient_history",
                        "metrics": {"rows": int(len(closes))},
                        "filter_trace": [],
                    }
                )
                continue
            metrics = {
                "last_close": float(closes.iloc[-1]),
                "return_21d": float(pct_return(closes, 21).iloc[-1]),
                "return_63d": float(pct_return(closes, 63).iloc[-1]),
                "drawdown": float(drawdown(closes).iloc[-63:].min()),
            }
            filter_trace = _evaluate_screen_filters(metrics, spec.filters)
            passed = all(item["passed"] for item in filter_trace) if filter_trace else True
            traces.append(
                {
                    "ticker": ticker,
                    "asset_class": self.market_universe.get(ticker, "unknown"),
                    "has_data": True,
                    "passed": passed,
                    "reason": "passed" if passed else "filtered_out",
                    "metrics": metrics,
                    "filter_trace": filter_trace,
                }
            )
        ranked = self.run_screen(spec)
        ranked_tickers = {item["ticker"] for item in ranked}
        for item in traces:
            item["ranked"] = item["ticker"] in ranked_tickers
        return {
            "spec": spec.model_dump(mode="json"),
            "ranked_results": ranked,
            "explanations": traces,
        }

    def list_change_alert_rules(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[ChangeAlertRule]:
        return self.change_alert_rule_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_change_alert_rule(self, rule_id: str) -> ChangeAlertRule:
        rule = self.change_alert_rule_repo.get(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        return rule

    def save_change_alert_rule(self, rule: ChangeAlertRule, allow_shared_mutation: bool = True) -> ChangeAlertRule:
        existing = self.change_alert_rule_repo.get(rule.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Change alert rule",
                entity_id=rule.id,
                existing_scope=existing.owner_scope,
                incoming_scope=rule.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        if rule.watchlist_id:
            self.get_watchlist(rule.watchlist_id)
        for channel_id in rule.notification_channel_ids:
            self.get_notification_channel(channel_id)
        return self.change_alert_rule_repo.save(rule)

    def delete_change_alert_rule(self, rule_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.change_alert_rule_repo.get(rule_id)
        if existing is None:
            raise KeyError(rule_id)
        self._enforce_shared_mutation_policy(
            entity_label="Change alert rule",
            entity_id=rule_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.change_alert_rule_repo.delete(rule_id)

    def list_change_alert_events(
        self,
        rule_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ChangeAlertEvent]:
        return self.change_alert_event_repo.list_saved(rule_id=rule_id, status=status, limit=limit)

    def get_change_alert_event(self, event_id: str) -> ChangeAlertEvent:
        event = self.change_alert_event_repo.get(event_id)
        if event is None:
            raise KeyError(event_id)
        return event

    def update_change_alert_event_status(self, event_id: str, status: str) -> ChangeAlertEvent:
        event = self.get_change_alert_event(event_id)
        event.status = status
        return self.change_alert_event_repo.save(event)

    def list_notification_channels(
        self,
        active_only: bool = False,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[NotificationChannel]:
        return self.notification_channel_repo.list_saved(
            active_only=active_only,
            owner_scope=self._normalize_owner_scope_filter(owner_scope),
        )

    def get_notification_channel(self, channel_id: str) -> NotificationChannel:
        channel = self.notification_channel_repo.get(channel_id)
        if channel is None:
            raise KeyError(channel_id)
        return channel

    def save_notification_channel(self, channel: NotificationChannel, allow_shared_mutation: bool = True) -> NotificationChannel:
        existing = self.notification_channel_repo.get(channel.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Notification channel",
                entity_id=channel.id,
                existing_scope=existing.owner_scope,
                incoming_scope=channel.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        referenced_channels = set(
            channel.fallback_channel_ids
            + channel.escalation_channel_ids
            + channel.ops_escalation_channel_ids
        )
        if channel.id in referenced_channels:
            raise ValueError("Notification channel cannot reference itself as a fallback or escalation target.")
        for referenced_channel_id in referenced_channels:
            self.get_notification_channel(referenced_channel_id)
        if channel.auto_pause_window_hours < 1:
            raise ValueError("auto_pause_window_hours must be at least 1.")
        if channel.auto_pause_error_rate_threshold < 0 or channel.auto_pause_error_rate_threshold > 1:
            raise ValueError("auto_pause_error_rate_threshold must be between 0 and 1.")
        if channel.auto_pause_consecutive_failures < 1:
            raise ValueError("auto_pause_consecutive_failures must be at least 1.")
        if channel.auto_pause_minutes < 1:
            raise ValueError("auto_pause_minutes must be at least 1.")
        if channel.ops_escalation_window_hours < 1:
            raise ValueError("ops_escalation_window_hours must be at least 1.")
        if channel.ops_escalation_threshold < 1:
            raise ValueError("ops_escalation_threshold must be at least 1.")
        if channel.ops_escalation_cooldown_minutes < 0:
            raise ValueError("ops_escalation_cooldown_minutes must be at least 0.")
        if channel.recovery_probe_cooldown_minutes < 0:
            raise ValueError("recovery_probe_cooldown_minutes must be at least 0.")
        if channel.recovery_probe_max_per_hour < 1:
            raise ValueError("recovery_probe_max_per_hour must be at least 1.")
        if channel.delivery_mode == "digest" and "alert_event" in channel.event_types and channel.active:
            if channel.next_digest_at is None:
                channel.next_digest_at = _compute_next_run_at(
                    cadence="daily",
                    run_hour_local=channel.digest_hour_local,
                    run_day_of_week=None,
                    base_time=datetime.now(),
                )
        else:
            channel.last_digest_at = None if channel.delivery_mode != "digest" else channel.last_digest_at
            channel.next_digest_at = None
        return self.notification_channel_repo.save(channel)

    def delete_notification_channel(self, channel_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.notification_channel_repo.get(channel_id)
        if existing is None:
            raise KeyError(channel_id)
        self._enforce_shared_mutation_policy(
            entity_label="Notification channel",
            entity_id=channel_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.notification_channel_repo.delete(channel_id)

    def pause_notification_channel(
        self,
        channel_id: str,
        minutes: int,
        reason: str | None = None,
        allow_shared_mutation: bool = True,
    ) -> NotificationChannel:
        channel = self.get_notification_channel(channel_id)
        channel.paused_until = datetime.now() + timedelta(minutes=max(minutes, 1))
        channel.pause_reason = reason or channel.pause_reason
        return self.save_notification_channel(channel, allow_shared_mutation=allow_shared_mutation)

    def resume_notification_channel(self, channel_id: str, allow_shared_mutation: bool = True) -> NotificationChannel:
        channel = self.get_notification_channel(channel_id)
        channel.paused_until = None
        channel.pause_reason = None
        return self.save_notification_channel(channel, allow_shared_mutation=allow_shared_mutation)

    def list_notification_deliveries(
        self,
        channel_id: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[NotificationDelivery]:
        rows = self.notification_delivery_repo.list_saved(
            channel_id=channel_id,
            event_type=event_type,
            status=status,
            limit=limit,
        )
        normalized_owner_scope = self._normalize_owner_scope_filter(owner_scope)
        if normalized_owner_scope == "all":
            return rows
        allowed_channel_ids = {
            item.id for item in self.list_notification_channels(active_only=False, owner_scope=normalized_owner_scope)
        }
        if channel_id is not None and channel_id not in allowed_channel_ids:
            return []
        return [item for item in rows if item.channel_id in allowed_channel_ids]

    def list_notification_channel_health(
        self,
        channel_id: str | None = None,
        window_hours: int = 24,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[NotificationChannelHealth]:
        now = datetime.now()
        window_hours = max(1, int(window_hours))
        cutoff = now - timedelta(hours=window_hours)
        channels = self.list_notification_channels(
            active_only=False,
            owner_scope=self._normalize_owner_scope_filter(owner_scope),
        )
        if channel_id:
            channels = [channel for channel in channels if channel.id == channel_id]
        rows: list[NotificationChannelHealth] = []
        for channel in channels:
            deliveries = self.notification_delivery_repo.list_saved(channel_id=channel.id, limit=1000)
            recent = [item for item in deliveries if item.triggered_at >= cutoff]
            successes = [item for item in recent if item.status == "success"]
            failures = [item for item in recent if item.status == "failed"]
            total = len(recent)
            success_count = len(successes)
            failed_count = len(failures)
            success_rate = round(success_count / total, 4) if total else 1.0
            error_rate = round(failed_count / total, 4) if total else 0.0
            consecutive_failures = 0
            for item in deliveries:
                if item.status == "failed":
                    consecutive_failures += 1
                    continue
                break
            rows.append(
                NotificationChannelHealth(
                    channel_id=channel.id,
                    channel_name=channel.name,
                    kind=channel.kind,
                    active=channel.active,
                    is_paused=_channel_is_paused(channel, now=now),
                    paused_until=channel.paused_until,
                    pause_reason=channel.pause_reason,
                    auto_pause_enabled=channel.auto_pause_enabled,
                    auto_pause_window_hours=channel.auto_pause_window_hours,
                    auto_pause_error_rate_threshold=channel.auto_pause_error_rate_threshold,
                    auto_pause_consecutive_failures=channel.auto_pause_consecutive_failures,
                    auto_pause_minutes=channel.auto_pause_minutes,
                    auto_resume_enabled=channel.auto_resume_enabled,
                    ops_escalation_enabled=channel.ops_escalation_enabled,
                    ops_escalation_window_hours=channel.ops_escalation_window_hours,
                    ops_escalation_threshold=channel.ops_escalation_threshold,
                    ops_escalation_cooldown_minutes=channel.ops_escalation_cooldown_minutes,
                    recovery_probe_profile=channel.recovery_probe_profile,
                    recovery_probe_cooldown_minutes=channel.recovery_probe_cooldown_minutes,
                    recovery_probe_max_per_hour=channel.recovery_probe_max_per_hour,
                    last_auto_paused_at=channel.last_auto_paused_at,
                    last_auto_resumed_at=channel.last_auto_resumed_at,
                    last_recovery_probe_at=channel.last_recovery_probe_at,
                    last_ops_escalated_at=channel.last_ops_escalated_at,
                    window_hours=window_hours,
                    total_attempts=total,
                    success_count=success_count,
                    failed_count=failed_count,
                    success_rate=success_rate,
                    error_rate=error_rate,
                    consecutive_failures=consecutive_failures,
                    last_delivery_at=deliveries[0].triggered_at if deliveries else None,
                    last_success_at=successes[0].triggered_at if successes else None,
                    last_failure_at=failures[0].triggered_at if failures else None,
                    last_error_message=failures[0].error_message if failures else None,
                )
            )
        return sorted(
            rows,
            key=lambda item: (-item.error_rate, -item.failed_count, item.channel_name),
        )

    def run_notification_channel_recovery(self, now: datetime | None = None) -> list[dict[str, object]]:
        now = now or datetime.now()
        results: list[dict[str, object]] = []
        for channel in self.list_notification_channels(active_only=True):
            if not channel.auto_resume_enabled:
                continue
            if channel.last_auto_paused_at is None:
                continue
            if channel.paused_until is None or channel.paused_until > now:
                continue
            recovery_block_reason = self._recovery_probe_block_reason(channel, now=now)
            if recovery_block_reason:
                self._record_notification_routing_audit(
                    channel=channel,
                    event_type="manual",
                    related_id=f"auto-resume-probe-skip-{uuid4().hex[:8]}",
                    decision="suppressed",
                    reason=recovery_block_reason,
                    payload={"auto_resume_probe": True, "probe_profile": channel.recovery_probe_profile},
                )
                results.append(
                    {
                        "channel_id": channel.id,
                        "status": "skipped",
                        "reason": recovery_block_reason,
                        "probe_profile": channel.recovery_probe_profile,
                    }
                )
                continue
            probe = self.dispatch_notification(
                channel_id=channel.id,
                event_type="manual",
                related_id=f"auto-resume-probe-{uuid4().hex[:8]}",
                subject=f"Auto-resume probe ({channel.recovery_probe_profile}): {channel.name}",
                payload=self._build_recovery_probe_payload(channel),
            )
            if probe.status == "success":
                channel.paused_until = None
                channel.pause_reason = None
                channel.last_auto_resumed_at = now
                channel.last_recovery_probe_at = probe.triggered_at
                self.save_notification_channel(channel)
                self._record_notification_routing_audit(
                    channel=channel,
                    event_type="manual",
                    related_id=probe.related_id,
                    decision="delivered",
                    reason="auto_resume_probe_success",
                    payload={"delivery_id": probe.id},
                )
                results.append(
                    {
                        "channel_id": channel.id,
                        "status": "resumed",
                        "probe_delivery_id": probe.id,
                        "probe_profile": channel.recovery_probe_profile,
                    }
                )
            else:
                channel.paused_until = now + timedelta(minutes=channel.auto_pause_minutes)
                channel.pause_reason = (
                    "Auto-resume probe failed. "
                    f"Last error: {probe.error_message or 'unknown'}"
                )
                channel.last_recovery_probe_at = probe.triggered_at
                self.save_notification_channel(channel)
                self._record_notification_routing_audit(
                    channel=channel,
                    event_type="manual",
                    related_id=probe.related_id,
                    decision="failed",
                    reason="auto_resume_probe_failed",
                    payload={"delivery_id": probe.id, "error": probe.error_message},
                )
                results.append(
                    {
                        "channel_id": channel.id,
                        "status": "probe_failed",
                        "probe_delivery_id": probe.id,
                        "probe_profile": channel.recovery_probe_profile,
                    }
                )
        return results

    def list_notification_digests(
        self,
        channel_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[NotificationDigest]:
        rows = self.notification_digest_repo.list_saved(channel_id=channel_id, status=status, limit=limit)
        normalized_owner_scope = self._normalize_owner_scope_filter(owner_scope)
        if normalized_owner_scope == "all":
            return rows
        allowed_channel_ids = {
            item.id for item in self.list_notification_channels(active_only=False, owner_scope=normalized_owner_scope)
        }
        if channel_id is not None and channel_id not in allowed_channel_ids:
            return []
        return [item for item in rows if item.channel_id in allowed_channel_ids]

    def get_notification_digest(self, digest_id: str) -> NotificationDigest:
        digest = self.notification_digest_repo.get(digest_id)
        if digest is None:
            raise KeyError(digest_id)
        return digest

    def list_notification_routing_audits(
        self,
        channel_id: str | None = None,
        event_type: str | None = None,
        decision: str | None = None,
        limit: int = 200,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[NotificationRoutingAudit]:
        rows = self.notification_routing_audit_repo.list_saved(
            channel_id=channel_id,
            event_type=event_type,
            decision=decision,
            limit=limit,
        )
        normalized_owner_scope = self._normalize_owner_scope_filter(owner_scope)
        if normalized_owner_scope == "all":
            return rows
        allowed_channel_ids = {
            item.id for item in self.list_notification_channels(active_only=False, owner_scope=normalized_owner_scope)
        }
        if channel_id is not None and channel_id not in allowed_channel_ids:
            return []
        return [item for item in rows if item.channel_id in allowed_channel_ids]

    def get_notification_routing_audit(self, audit_id: str) -> NotificationRoutingAudit:
        audit = self.notification_routing_audit_repo.get(audit_id)
        if audit is None:
            raise KeyError(audit_id)
        return audit

    def list_ops_incidents(
        self,
        status: str | None = None,
        source_channel_id: str | None = None,
        overdue_only: bool = False,
        limit: int = 200,
    ) -> list[OpsIncident]:
        rows = self.ops_incident_repo.list_saved(
            status=status,
            source_channel_id=source_channel_id,
            limit=limit,
        )
        if overdue_only:
            now = datetime.now()
            rows = [item for item in rows if _incident_is_overdue(item, now=now)]
        return rows

    def get_ops_incident(self, incident_id: str) -> OpsIncident:
        incident = self.ops_incident_repo.get(incident_id)
        if incident is None:
            raise KeyError(incident_id)
        return incident

    def update_ops_incident(
        self,
        incident_id: str,
        status: str | None = None,
        owner: str | None = None,
        priority: str | None = None,
        sla_minutes: int | None = None,
        notes: str | None = None,
    ) -> OpsIncident:
        if status is not None and status not in {"open", "ack", "resolved"}:
            raise ValueError("status must be one of: open, ack, resolved")
        if priority is not None and priority not in {"low", "medium", "high"}:
            raise ValueError("priority must be one of: low, medium, high")
        if sla_minutes is not None and sla_minutes < 0:
            raise ValueError("sla_minutes must be at least 0")
        incident = self.get_ops_incident(incident_id)
        now = datetime.now()
        if status is not None:
            incident.status = status
            if status == "ack":
                incident.acknowledged_at = now
                incident.resolved_at = None
            elif status == "resolved":
                incident.resolved_at = now
            elif status == "open":
                incident.resolved_at = None
        if owner is not None:
            incident.owner = owner or None
        if priority is not None:
            incident.priority = priority
        if sla_minutes is not None:
            incident.sla_minutes = sla_minutes
        incident.due_at = incident.opened_at + timedelta(minutes=incident.sla_minutes)
        incident.updated_at = now
        incident.notes = notes or incident.notes
        return self.ops_incident_repo.save(incident)

    def update_ops_incident_status(self, incident_id: str, status: str, notes: str | None = None) -> OpsIncident:
        return self.update_ops_incident(incident_id=incident_id, status=status, notes=notes)

    def get_ops_incident_summary(self) -> dict[str, int]:
        rows = self.list_ops_incidents(limit=2000)
        now = datetime.now()
        open_count = sum(1 for item in rows if item.status == "open")
        ack_count = sum(1 for item in rows if item.status == "ack")
        resolved_count = sum(1 for item in rows if item.status == "resolved")
        overdue_active = sum(1 for item in rows if _incident_is_overdue(item, now=now))
        return {
            "total": len(rows),
            "open": open_count,
            "ack": ack_count,
            "resolved": resolved_count,
            "overdue_active": overdue_active,
        }

    def get_notification_routing_summary(
        self,
        channel_id: str | None = None,
        event_type: str | None = None,
        window_hours: int = 24,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> list[dict[str, object]]:
        now = datetime.now()
        since = now - timedelta(hours=max(1, int(window_hours)))
        rows = self.list_notification_routing_audits(
            channel_id=channel_id,
            event_type=event_type,
            owner_scope=owner_scope,
            limit=5000,
        )
        scoped = [item for item in rows if item.created_at >= since]
        by_channel: dict[str, list[NotificationRoutingAudit]] = {}
        for row in scoped:
            by_channel.setdefault(row.channel_id, []).append(row)
        summary: list[dict[str, object]] = []
        for channel_key, items in by_channel.items():
            decision_counts = Counter(item.decision for item in items)
            top_reason = Counter((item.reason or "none") for item in items).most_common(1)[0][0]
            summary.append(
                {
                    "channel_id": channel_key,
                    "channel_name": items[0].channel_name,
                    "window_hours": window_hours,
                    "total": len(items),
                    "delivered": decision_counts.get("delivered", 0),
                    "failed": decision_counts.get("failed", 0),
                    "suppressed": decision_counts.get("suppressed", 0),
                    "paused": decision_counts.get("paused", 0),
                    "inactive": decision_counts.get("inactive", 0),
                    "rejected": decision_counts.get("rejected", 0),
                    "digest_deferred": decision_counts.get("digest_deferred", 0),
                    "top_reason": top_reason,
                }
            )
        return sorted(summary, key=lambda row: (-(row["failed"] + row["suppressed"] + row["paused"]), -row["total"]))

    def export_notification_routing_audits(
        self,
        format: str = "csv",
        channel_id: str | None = None,
        event_type: str | None = None,
        decision: str | None = None,
        limit: int = 5000,
        owner_scope: Literal["all", "shared", "private"] = "all",
    ) -> dict[str, object]:
        rows = self.list_notification_routing_audits(
            channel_id=channel_id,
            event_type=event_type,
            decision=decision,
            owner_scope=owner_scope,
            limit=limit,
        )
        export_dir = settings.notification_output_dir / "routing_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filters = "_".join(
            [
                _slugify(channel_id) if channel_id else "all-channels",
                _slugify(event_type) if event_type else "all-events",
                _slugify(decision) if decision else "all-decisions",
            ]
        )
        if format == "json":
            output_path = export_dir / f"routing_audit_{stamp}_{filters}.json"
            payload = [item.model_dump(mode="json") for item in rows]
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        elif format == "csv":
            output_path = export_dir / f"routing_audit_{stamp}_{filters}.csv"
            frame = pd.DataFrame([item.model_dump(mode="json") for item in rows])
            if frame.empty:
                frame = pd.DataFrame(
                    columns=[
                        "id",
                        "channel_id",
                        "channel_name",
                        "event_type",
                        "related_id",
                        "decision",
                        "reason",
                        "created_at",
                        "payload",
                    ]
                )
            frame.to_csv(output_path, index=False)
        else:
            raise ValueError("Unsupported format. Use 'csv' or 'json'.")
        return {
            "format": format,
            "count": len(rows),
            "path": str(output_path),
        }

    def get_notification_delivery(self, delivery_id: str) -> NotificationDelivery:
        delivery = self.notification_delivery_repo.get(delivery_id)
        if delivery is None:
            raise KeyError(delivery_id)
        return delivery

    def dispatch_notification(
        self,
        channel_id: str,
        event_type: str,
        related_id: str,
        subject: str,
        payload: dict[str, object],
        attempt_count: int = 1,
        fallback_from_channel_id: str | None = None,
    ) -> NotificationDelivery:
        channel = self.get_notification_channel(channel_id)
        triggered_at = datetime.now()
        delivery = NotificationDelivery(
            id=f"notification-{uuid4().hex[:10]}",
            channel_id=channel.id,
            channel_name=channel.name,
            event_type=event_type,
            related_id=related_id,
            status="success",
            triggered_at=triggered_at,
            attempt_count=attempt_count,
            target=channel.target,
            payload={
                "subject": subject,
                **payload,
                **({"fallback_from_channel_id": fallback_from_channel_id} if fallback_from_channel_id else {}),
            },
        )
        try:
            if channel.kind in {"file", "email"}:
                output_path = self._write_notification_message(channel, delivery)
                delivery.output_path = str(output_path)
            elif channel.kind in {"webhook", "slack"}:
                response_code = self._post_notification(channel, {"subject": subject, **payload})
                delivery.response_code = response_code
            else:
                raise ValueError(f"Unsupported notification channel kind: {channel.kind}")
        except Exception as exc:
            delivery.status = "failed"
            delivery.error_message = str(exc)
        persisted = self.notification_delivery_repo.save(delivery)
        self._maybe_auto_pause_channel(channel, persisted)
        return persisted

    def route_notification(
        self,
        channel_id: str,
        event_type: str,
        related_id: str,
        subject: str,
        payload: dict[str, object],
    ) -> NotificationDelivery | None:
        return self._send_governed_notification(
            channel_id=channel_id,
            event_type=event_type,
            related_id=related_id,
            subject=subject,
            payload=payload,
            attempt_count=1,
            apply_suppression=True,
            apply_followups=True,
        )

    def send_test_notification(self, channel_id: str, subject: str | None = None) -> NotificationDelivery:
        channel = self.get_notification_channel(channel_id)
        return self.dispatch_notification(
            channel_id=channel.id,
            event_type="manual",
            related_id=f"test-{uuid4().hex[:8]}",
            subject=subject or f"Test notification: {channel.name}",
            payload={
                "message": f"Test notification sent through {channel.kind} channel '{channel.name}'.",
                "channel_kind": channel.kind,
            },
        )

    def retry_notification_delivery(self, delivery_id: str) -> NotificationDelivery:
        existing = self.get_notification_delivery(delivery_id)
        channel = self.get_notification_channel(existing.channel_id)
        if existing.attempt_count >= channel.max_retry_attempts:
            raise ValueError(f"Maximum retry attempts reached for channel '{channel.name}'.")
        if channel.retry_backoff_minutes > 0:
            next_retry_at = existing.triggered_at + timedelta(minutes=channel.retry_backoff_minutes)
            if next_retry_at > datetime.now():
                raise ValueError(
                    f"Retry backoff active for channel '{channel.name}' until {next_retry_at.isoformat()}."
                )
        subject = str(existing.payload.get("subject", existing.channel_name))
        retry_payload = dict(existing.payload)
        retry_payload["retried_from_delivery_id"] = existing.id
        return self.dispatch_notification(
            channel_id=existing.channel_id,
            event_type=existing.event_type,
            related_id=existing.related_id,
            subject=subject,
            payload=retry_payload,
            attempt_count=existing.attempt_count + 1,
        )

    def send_notification_digest(
        self,
        channel_id: str,
        status: str = "new",
        limit: int = 25,
        publish_included: bool = False,
    ) -> NotificationDigest:
        channel = self.get_notification_channel(channel_id)
        if not channel.active:
            raise ValueError(f"Notification channel '{channel.name}' is inactive.")
        if _channel_is_paused(channel):
            raise ValueError(f"Notification channel '{channel.name}' is paused.")
        events = self._collect_digest_alert_events(channel, status=status, limit=limit)
        digest = NotificationDigest(
            id=f"digest-{uuid4().hex[:10]}",
            channel_id=channel.id,
            channel_name=channel.name,
            status="success",
            triggered_at=datetime.now(),
            event_count=len(events),
            source_event_ids=[event.id for event in events],
            target=channel.target,
            payload={
                "subject": f"Alert Digest: {channel.name}",
                "event_count": len(events),
                "events": [
                    {
                        "event_id": event.id,
                        "rule_name": event.rule_name,
                        "key": event.signal.key,
                        "title": event.signal.title,
                        "significance": event.signal.significance,
                        "direction": event.signal.direction,
                        "absolute_change": event.signal.absolute_change,
                        "percent_change": event.signal.percent_change,
                        "observation_date": event.signal.observation_date,
                    }
                    for event in events
                ],
            },
        )
        try:
            if channel.kind in {"file", "email"}:
                output_path = self._write_notification_digest(channel, digest)
                digest.output_path = str(output_path)
            elif channel.kind in {"webhook", "slack"}:
                response_code = self._post_notification(channel, digest.payload)
                digest.response_code = response_code
            else:
                raise ValueError(f"Unsupported notification channel kind: {channel.kind}")
            if publish_included:
                for event in events:
                    if event.status == "new":
                        self.update_change_alert_event_status(event.id, "published")
        except Exception as exc:
            digest.status = "failed"
            digest.error_message = str(exc)
        persisted = self.notification_digest_repo.save(digest)
        if persisted.status == "failed":
            for fallback_channel_id in channel.fallback_channel_ids:
                fallback_channel = self.get_notification_channel(fallback_channel_id)
                if not _notification_channel_accepts(fallback_channel, "manual", digest.payload):
                    continue
                self.dispatch_notification(
                    channel_id=fallback_channel_id,
                    event_type="manual",
                    related_id=digest.id,
                    subject=f"Fallback Digest Delivery: {channel.name}",
                    payload={
                        "digest_id": digest.id,
                        "source_channel_id": channel.id,
                        "source_channel_name": channel.name,
                        "digest_payload": digest.payload,
                    },
                    fallback_from_channel_id=channel.id,
                )
        return persisted

    def run_due_notification_digests(self, now: datetime | None = None) -> list[NotificationDigest]:
        now = now or datetime.now()
        completed: list[NotificationDigest] = []
        for channel in self.list_notification_channels(active_only=True):
            if _channel_is_paused(channel, now=now):
                continue
            if channel.delivery_mode != "digest" or "alert_event" not in channel.event_types:
                continue
            if channel.next_digest_at is None:
                channel.next_digest_at = _compute_next_run_at(
                    cadence="daily",
                    run_hour_local=channel.digest_hour_local,
                    run_day_of_week=None,
                    base_time=now,
                )
                self.notification_channel_repo.save(channel)
                continue
            if channel.next_digest_at <= now:
                digest = self.send_notification_digest(
                    channel_id=channel.id,
                    status=channel.digest_status_filter,
                    limit=channel.digest_limit,
                    publish_included=channel.digest_publish_included,
                )
                channel.last_digest_at = digest.triggered_at
                channel.next_digest_at = _compute_next_run_at(
                    cadence="daily",
                    run_hour_local=channel.digest_hour_local,
                    run_day_of_week=None,
                    base_time=digest.triggered_at,
                )
                self.notification_channel_repo.save(channel)
                completed.append(digest)
        return completed

    def run_change_alert_scan(self, rule_id: str | None = None) -> list[ChangeAlertEvent]:
        rules = [self.get_change_alert_rule(rule_id)] if rule_id else self.list_change_alert_rules()
        created: list[ChangeAlertEvent] = []
        for rule in rules:
            if not rule.active:
                continue
            signals = self.get_change_monitor(
                country=rule.country,
                topic=rule.topic,
                asset_class=rule.asset_class,
                limit=100,
            )
            allowed_keys = set(rule.keys)
            if rule.watchlist_id:
                allowed_keys.update(self.get_watchlist(rule.watchlist_id).tickers)
            for signal in signals:
                if not _matches_change_alert_rule(rule, signal, allowed_keys):
                    continue
                event_id = _change_alert_event_id(rule.id, signal.key, signal.observation_date)
                if self.change_alert_event_repo.get(event_id) is not None:
                    continue
                event = ChangeAlertEvent(
                    id=event_id,
                    rule_id=rule.id,
                    rule_name=rule.name,
                    signal=signal,
                    triggered_at=datetime.now(),
                )
                self.change_alert_event_repo.save(event)
                created.append(event)
                for channel_id in rule.notification_channel_ids:
                    self.route_notification(
                        channel_id=channel_id,
                        event_type="alert_event",
                        related_id=event.id,
                        subject=f"{rule.name}: {signal.title}",
                        payload={
                            "rule_id": rule.id,
                            "rule_name": rule.name,
                            "event_id": event.id,
                            "signal": event.signal.model_dump(mode="json"),
                        },
                    )
        return created

    def list_watchlists(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[Watchlist]:
        return self.watchlist_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_watchlist(self, watchlist_id: str) -> Watchlist:
        watchlist = self.watchlist_repo.get(watchlist_id)
        if watchlist is None:
            raise KeyError(watchlist_id)
        return watchlist

    def save_watchlist(self, watchlist: Watchlist, allow_shared_mutation: bool = False) -> Watchlist:
        existing = self.watchlist_repo.get(watchlist.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Watchlist",
                entity_id=watchlist.id,
                existing_scope=existing.owner_scope,
                incoming_scope=watchlist.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        return self.watchlist_repo.save(watchlist)

    def delete_watchlist(self, watchlist_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.watchlist_repo.get(watchlist_id)
        if existing is None:
            raise KeyError(watchlist_id)
        self._enforce_shared_mutation_policy(
            entity_label="Watchlist",
            entity_id=watchlist_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.watchlist_repo.delete(watchlist_id)

    def list_saved_screens(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[SavedScreen]:
        return self.saved_screen_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_saved_screen(self, screen_id: str) -> SavedScreen:
        screen = self.saved_screen_repo.get(screen_id)
        if screen is None:
            raise KeyError(screen_id)
        return screen

    def save_saved_screen(self, screen: SavedScreen, allow_shared_mutation: bool = False) -> SavedScreen:
        existing = self.saved_screen_repo.get(screen.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Saved screen",
                entity_id=screen.id,
                existing_scope=existing.owner_scope,
                incoming_scope=screen.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        return self.saved_screen_repo.save(screen)

    def delete_saved_screen(self, screen_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.saved_screen_repo.get(screen_id)
        if existing is None:
            raise KeyError(screen_id)
        self._enforce_shared_mutation_policy(
            entity_label="Saved screen",
            entity_id=screen_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.saved_screen_repo.delete(screen_id)

    def list_cross_country_presets(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[CrossCountryPreset]:
        rows = self.cross_country_preset_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))
        rows.sort(key=lambda item: (not item.is_default, item.name.lower()))
        return rows

    def get_cross_country_preset(self, preset_id: str) -> CrossCountryPreset:
        preset = self.cross_country_preset_repo.get(preset_id)
        if preset is None:
            raise KeyError(preset_id)
        return preset

    def get_default_cross_country_preset(self, owner_scope: Literal["all", "shared", "private"] = "all") -> CrossCountryPreset | None:
        rows = self.list_cross_country_presets(owner_scope=owner_scope)
        if not rows:
            return None
        default_rows = [item for item in rows if item.is_default]
        return default_rows[0] if default_rows else rows[0]

    def save_cross_country_preset(self, preset: CrossCountryPreset, allow_shared_mutation: bool = False) -> CrossCountryPreset:
        preset.name = preset.name.strip()
        if not preset.name:
            raise ValueError("name must be non-empty.")
        preset.countries = [item.upper() for item in preset.countries if item]
        preset.countries = list(dict.fromkeys(preset.countries))
        if not preset.countries:
            raise ValueError("countries must include at least one country.")
        allowed_weights = {"growth", "inflation", "labor_unemployment", "policy_rate", "equity_return_63d"}
        if set(preset.factor_weights.keys()) - allowed_weights:
            raise ValueError("factor_weights includes unsupported keys.")
        if any(float(value) < 0 for value in preset.factor_weights.values()):
            raise ValueError("factor_weights values must be non-negative.")
        existing_preset = self.cross_country_preset_repo.get(preset.id)
        if existing_preset is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Cross-country preset",
                entity_id=preset.id,
                existing_scope=existing_preset.owner_scope,
                incoming_scope=preset.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        self._validate_cross_country_preset_name_uniqueness(name=preset.name, current_preset_id=preset.id)
        existing = self.list_cross_country_presets()
        if preset.is_default:
            for item in existing:
                if item.id == preset.id:
                    continue
                if item.is_default:
                    item.is_default = False
                    self.cross_country_preset_repo.save(item)
        elif not existing:
            preset.is_default = True
        saved = self.cross_country_preset_repo.save(preset)
        return saved

    def set_default_cross_country_preset(self, preset_id: str, allow_shared_mutation: bool = False) -> CrossCountryPreset:
        target = self.get_cross_country_preset(preset_id)
        self._enforce_shared_mutation_policy(
            entity_label="Cross-country preset",
            entity_id=preset_id,
            existing_scope=target.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        rows = self.list_cross_country_presets()
        for item in rows:
            if item.id == target.id:
                item.is_default = True
            elif item.is_default:
                item.is_default = False
            self.cross_country_preset_repo.save(item)
        return self.get_cross_country_preset(preset_id)

    def delete_cross_country_preset(self, preset_id: str, allow_shared_mutation: bool = False) -> None:
        target = self.get_cross_country_preset(preset_id)
        self._enforce_shared_mutation_policy(
            entity_label="Cross-country preset",
            entity_id=preset_id,
            existing_scope=target.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.cross_country_preset_repo.delete(preset_id)
        rows = self.list_cross_country_presets()
        if target.is_default and rows and not any(item.is_default for item in rows):
            first = rows[0]
            first.is_default = True
            self.cross_country_preset_repo.save(first)

    def export_cross_country_presets(self, limit: int = 500) -> CrossCountryPresetExportBundle:
        rows = self.list_cross_country_presets()[:limit]
        return CrossCountryPresetExportBundle(exported_at=datetime.now(), presets=rows)

    def preview_import_cross_country_presets(
        self,
        request: CrossCountryPresetImportRequest,
    ) -> dict[str, object]:
        existing = self.list_cross_country_presets()
        existing_by_name = {item.name.strip().lower(): item for item in existing}
        errors: list[str] = []
        actions: list[dict[str, object]] = []
        names = [item.name.strip() for item in request.presets]
        normalized = [item.lower() for item in names]
        if not request.presets:
            errors.append("presets must include at least one entry.")
        if any(not item for item in names):
            errors.append("import preset names must be non-empty.")
        duplicate_names = sorted({name for name in normalized if normalized.count(name) > 1})
        if duplicate_names:
            errors.append("import contains duplicate preset names: " + ", ".join(duplicate_names))
        create_count = 0
        update_count = 0
        conflict_count = 0
        for item in request.presets:
            clean_name = item.name.strip()
            key = clean_name.lower()
            match = existing_by_name.get(key)
            action = "create"
            if request.mode == "replace":
                action = "create"
            elif request.mode == "upsert":
                action = "update" if match is not None else "create"
            elif request.mode == "append":
                if match is not None:
                    action = "conflict"
                    conflict_count += 1
                    errors.append(f"preset names already exist: {clean_name}")
            if action == "create":
                create_count += 1
            if action == "update":
                update_count += 1
            weights = item.factor_weights or {}
            if any(float(value) < 0 for value in weights.values()):
                errors.append(f"preset '{clean_name or '<blank>'}' has negative factor_weights values.")
            actions.append(
                {
                    "name": clean_name,
                    "action": action,
                    "existing_preset_id": match.id if match is not None else None,
                    "incoming_is_default": bool(item.is_default),
                    "incoming_country_count": len(item.countries),
                }
            )
        return {
            "mode": request.mode,
            "valid": len(errors) == 0,
            "errors": sorted(set(errors)),
            "summary": {
                "incoming_count": len(request.presets),
                "existing_count": len(existing),
                "create_count": create_count,
                "update_count": update_count,
                "conflict_count": conflict_count,
            },
            "actions": actions,
        }

    def import_cross_country_presets(
        self,
        request: CrossCountryPresetImportRequest,
    ) -> list[CrossCountryPreset]:
        preview = self.preview_import_cross_country_presets(request=request)
        if not bool(preview.get("valid", False)):
            errors = preview.get("errors", [])
            if errors:
                raise ValueError("; ".join(str(item) for item in errors))
            raise ValueError("preset import is invalid.")
        existing = self.list_cross_country_presets()
        snapshot = [item.model_copy(deep=True) for item in existing]
        existing_by_name = {item.name.strip().lower(): item for item in existing}
        existing_default_id = next((item.id for item in existing if item.is_default), None)
        try:
            if request.mode == "replace":
                for item in existing:
                    self.cross_country_preset_repo.delete(item.id)
            imported: list[CrossCountryPreset] = []
            force_first_default = request.mode == "replace" and not any(item.is_default for item in request.presets)
            for idx, item in enumerate(request.presets):
                match = existing_by_name.get(item.name.strip().lower()) if request.mode == "upsert" else None
                if match is not None:
                    preset = CrossCountryPreset(
                        id=match.id,
                        name=item.name,
                        countries=item.countries,
                        factor_weights=item.factor_weights or match.factor_weights,
                        is_default=item.is_default,
                        owner_scope=item.owner_scope,
                        notes=item.notes,
                    )
                else:
                    preset = self._create_cross_country_preset_from_import(
                        item=item,
                        is_default_override=True if force_first_default and idx == 0 else None,
                    )
                imported.append(self.save_cross_country_preset(preset, allow_shared_mutation=True))
            preferred_default_id = existing_default_id
            if preferred_default_id is None and imported:
                preferred_default_id = imported[0].id
            self._ensure_cross_country_default(preferred_preset_id=preferred_default_id)
            return [self.get_cross_country_preset(item.id) for item in imported]
        except Exception:
            self._restore_cross_country_preset_snapshot(snapshot=snapshot)
            raise

    def _validate_cross_country_preset_name_uniqueness(
        self,
        name: str,
        current_preset_id: str | None = None,
    ) -> None:
        key = name.strip().lower()
        for item in self.list_cross_country_presets():
            if current_preset_id is not None and item.id == current_preset_id:
                continue
            if item.name.strip().lower() == key:
                raise ValueError(f"preset name '{name.strip()}' already exists.")

    def _ensure_cross_country_default(self, preferred_preset_id: str | None = None) -> None:
        rows = self.list_cross_country_presets()
        if not rows:
            return
        defaults = [item for item in rows if item.is_default]
        if defaults:
            keep_id = preferred_preset_id if preferred_preset_id and any(item.id == preferred_preset_id for item in defaults) else defaults[0].id
        else:
            keep_id = preferred_preset_id if preferred_preset_id and any(item.id == preferred_preset_id for item in rows) else rows[0].id
        for item in rows:
            should_default = item.id == keep_id
            if item.is_default != should_default:
                item.is_default = should_default
                self.cross_country_preset_repo.save(item)

    def _restore_cross_country_preset_snapshot(self, snapshot: list[CrossCountryPreset]) -> None:
        current = self.list_cross_country_presets()
        for item in current:
            self.cross_country_preset_repo.delete(item.id)
        for item in snapshot:
            self.cross_country_preset_repo.save(item.model_copy(deep=True))
        preferred = next((item.id for item in snapshot if item.is_default), None)
        self._ensure_cross_country_default(preferred_preset_id=preferred)

    def _create_cross_country_preset_from_import(
        self,
        item: CrossCountryPresetImportItem,
        is_default_override: bool | None = None,
    ) -> CrossCountryPreset:
        return CrossCountryPreset(
            id=f"cross-country-preset-{uuid4().hex[:8]}",
            name=item.name,
            countries=item.countries,
            factor_weights=item.factor_weights
            or {
                "growth": 1.0,
                "inflation": 1.0,
                "labor_unemployment": 1.0,
                "policy_rate": 1.0,
                "equity_return_63d": 1.0,
            },
            is_default=item.is_default if is_default_override is None else bool(is_default_override),
            owner_scope=item.owner_scope,
            notes=item.notes,
        )

    def list_scenarios(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[ScenarioDefinition]:
        return self.scenario_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_scenario(self, scenario_id: str) -> ScenarioDefinition:
        scenario = self.scenario_repo.get(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        return scenario

    def save_scenario(self, scenario: ScenarioDefinition, allow_shared_mutation: bool = False) -> ScenarioDefinition:
        existing = self.scenario_repo.get(scenario.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Scenario",
                entity_id=scenario.id,
                existing_scope=existing.owner_scope,
                incoming_scope=scenario.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        return self.scenario_repo.save(scenario)

    def delete_scenario(self, scenario_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.scenario_repo.get(scenario_id)
        if existing is None:
            raise KeyError(scenario_id)
        self._enforce_shared_mutation_policy(
            entity_label="Scenario",
            entity_id=scenario_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.scenario_repo.delete(scenario_id)

    def list_model_portfolios(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[ModelPortfolio]:
        return self.portfolio_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_model_portfolio(self, portfolio_id: str) -> ModelPortfolio:
        portfolio = self.portfolio_repo.get(portfolio_id)
        if portfolio is None:
            raise KeyError(portfolio_id)
        return portfolio

    def save_model_portfolio(self, portfolio: ModelPortfolio, allow_shared_mutation: bool = False) -> ModelPortfolio:
        existing = self.portfolio_repo.get(portfolio.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Model portfolio",
                entity_id=portfolio.id,
                existing_scope=existing.owner_scope,
                incoming_scope=portfolio.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        return self.portfolio_repo.save(portfolio)

    def delete_model_portfolio(self, portfolio_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.portfolio_repo.get(portfolio_id)
        if existing is None:
            raise KeyError(portfolio_id)
        self._enforce_shared_mutation_policy(
            entity_label="Model portfolio",
            entity_id=portfolio_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.portfolio_repo.delete(portfolio_id)

    def get_portfolio_summary(
        self,
        portfolio_id: str,
        scenario_id: str | None = None,
    ) -> list[dict[str, float | str | None]]:
        portfolio = self.get_model_portfolio(portfolio_id)
        scenario = self.get_scenario(scenario_id) if scenario_id else None
        rows: list[PortfolioSummaryRow] = []
        for holding in portfolio.holdings:
            prices = self.get_prices(holding.ticker, start_date=date.today() - timedelta(days=365))
            if not prices:
                rows.append(
                    PortfolioSummaryRow(
                        ticker=holding.ticker,
                        asset_class=self.market_universe.get(holding.ticker, "unknown"),
                        weight=holding.weight,
                    )
                )
                continue
            frame = pd.DataFrame([item.model_dump(mode="json") for item in prices])
            closes = frame["close"]
            last_close = float(closes.iloc[-1])
            return_21d = float(closes.pct_change(21).iloc[-1] * 100) if len(closes) > 21 else None
            return_63d = float(closes.pct_change(63).iloc[-1] * 100) if len(closes) > 63 else None
            stressed = _apply_scenario_to_holding(
                ticker=holding.ticker,
                asset_class=self.market_universe.get(holding.ticker, "unknown"),
                base_return=return_21d,
                scenario=scenario,
            )
            rows.append(
                PortfolioSummaryRow(
                    ticker=holding.ticker,
                    asset_class=self.market_universe.get(holding.ticker, "unknown"),
                    weight=holding.weight,
                    last_close=round(last_close, 2),
                    return_21d=round(return_21d, 2) if return_21d is not None else None,
                    return_63d=round(return_63d, 2) if return_63d is not None else None,
                    stressed_return=round(stressed, 2) if stressed is not None else None,
                )
            )
        return [item.model_dump() for item in rows]

    def list_report_templates(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[ReportTemplate]:
        return self.report_template_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_report_template(self, template_id: str) -> ReportTemplate:
        template = self.report_template_repo.get(template_id)
        if template is None:
            raise KeyError(template_id)
        return template

    def save_report_template(self, template: ReportTemplate, allow_shared_mutation: bool = False) -> ReportTemplate:
        existing = self.report_template_repo.get(template.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Report template",
                entity_id=template.id,
                existing_scope=existing.owner_scope,
                incoming_scope=template.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        return self.report_template_repo.save(template)

    def delete_report_template(self, template_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.report_template_repo.get(template_id)
        if existing is None:
            raise KeyError(template_id)
        self._enforce_shared_mutation_policy(
            entity_label="Report template",
            entity_id=template_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.report_template_repo.delete(template_id)

    def list_report_snapshots(self) -> list[ReportSnapshot]:
        return self.report_snapshot_repo.list_saved()

    def get_report_snapshot(self, snapshot_id: str) -> ReportSnapshot:
        snapshot = self.report_snapshot_repo.get(snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        return snapshot

    def generate_report_snapshot(self, template_id: str, name_override: str | None = None) -> ReportSnapshot:
        template = self.get_report_template(template_id)
        sections = [self._build_report_section(section) for section in template.sections]
        generated_at = datetime.now()
        snapshot_id = f"report-{uuid4().hex[:10]}"
        snapshot_name = name_override or f"{template.name} Snapshot"
        snapshot = ReportSnapshot(
            id=snapshot_id,
            template_id=template.id,
            name=snapshot_name,
            generated_at=generated_at,
            sections=sections,
            summary=self._summarize_report(sections),
        )
        output_path = self._write_report_markdown(snapshot)
        snapshot.output_path = str(output_path)
        snapshot.export_paths["markdown"] = str(output_path)
        json_path = self._write_report_json(snapshot)
        snapshot.export_paths["json"] = str(json_path)
        return self.report_snapshot_repo.save(snapshot)

    def export_report_snapshot(self, snapshot_id: str, export_format: str) -> ReportSnapshot:
        snapshot = self.get_report_snapshot(snapshot_id)
        if export_format == "markdown":
            output_path = self._write_report_markdown(snapshot)
            snapshot.output_path = str(output_path)
            snapshot.export_paths["markdown"] = str(output_path)
        elif export_format == "json":
            output_path = self._write_report_json(snapshot)
            snapshot.export_paths["json"] = str(output_path)
        elif export_format == "csv_zip":
            output_path = self._write_report_csv_zip(snapshot)
            snapshot.export_paths["csv_zip"] = str(output_path)
        elif export_format == "xlsx":
            output_path = self._write_report_xlsx(snapshot)
            snapshot.export_paths["xlsx"] = str(output_path)
        elif export_format == "pptx":
            paths = self._write_report_pptx(snapshot)
            snapshot.export_paths.update(paths)
        else:
            raise ValueError(f"Unsupported export format: {export_format}")
        return self.report_snapshot_repo.save(snapshot)

    def list_report_jobs(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[ReportJob]:
        return self.report_job_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_report_job(self, job_id: str) -> ReportJob:
        job = self.report_job_repo.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def save_report_job(self, job: ReportJob, allow_shared_mutation: bool = False) -> ReportJob:
        existing = self.report_job_repo.get(job.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Report job",
                entity_id=job.id,
                existing_scope=existing.owner_scope,
                incoming_scope=job.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        self.get_report_template(job.template_id)
        for channel_id in job.notification_channel_ids:
            self.get_notification_channel(channel_id)
        if job.next_run_at is None:
            job.next_run_at = _compute_next_run_at(
                cadence=job.cadence,
                run_hour_local=job.run_hour_local,
                run_day_of_week=job.run_day_of_week,
                base_time=datetime.now(),
            )
        return self.report_job_repo.save(job)

    def delete_report_job(self, job_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.report_job_repo.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        self._enforce_shared_mutation_policy(
            entity_label="Report job",
            entity_id=job_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.report_job_repo.delete(job_id)

    def list_report_job_runs(self, job_id: str | None = None, limit: int = 50) -> list[ReportJobRun]:
        return self.report_job_run_repo.list_saved(job_id=job_id, limit=limit)

    def run_report_job(self, job_id: str, trigger: str = "manual", now: datetime | None = None) -> ReportJob:
        job = self.get_report_job(job_id)
        run_started_at = now or datetime.now()
        try:
            template = self.get_report_template(job.template_id)
            snapshot_name = f"{job.name} {run_started_at.strftime('%Y-%m-%d %H:%M')}"
            snapshot = self.generate_report_snapshot(template.id, name_override=snapshot_name)
            for export_format in job.export_formats:
                snapshot = self.export_report_snapshot(snapshot.id, export_format)
            for channel_id in job.notification_channel_ids:
                self.route_notification(
                    channel_id=channel_id,
                    event_type="report_job",
                    related_id=job.id,
                    subject=f"Report job completed: {job.name}",
                    payload={
                        "job_id": job.id,
                        "job_name": job.name,
                        "template_id": job.template_id,
                        "snapshot_id": snapshot.id,
                        "snapshot_name": snapshot.name,
                        "exports": snapshot.export_paths,
                        "summary": snapshot.summary,
                    },
                )
            run_finished_at = now or datetime.now()
            job.last_run_at = run_finished_at
            job.last_snapshot_id = snapshot.id
            job.last_run_status = "success"
            job.last_error_message = None
            job.consecutive_failures = 0
            job.next_run_at = _compute_next_run_at(
                cadence=job.cadence,
                run_hour_local=job.run_hour_local,
                run_day_of_week=job.run_day_of_week,
                base_time=run_finished_at,
            )
            persisted = self.report_job_repo.save(job)
            self.report_job_run_repo.save(
                ReportJobRun(
                    id=f"report-run-{uuid4().hex[:10]}",
                    job_id=job.id,
                    template_id=job.template_id,
                    trigger=trigger,
                    status="success",
                    started_at=run_started_at,
                    finished_at=run_finished_at,
                    snapshot_id=snapshot.id,
                    export_formats=job.export_formats,
                    export_paths=snapshot.export_paths,
                )
            )
            return persisted
        except Exception as exc:
            run_finished_at = now or datetime.now()
            job.last_run_at = run_finished_at
            job.last_run_status = "failed"
            job.last_error_message = str(exc)
            job.consecutive_failures += 1
            job.next_run_at = _compute_next_run_at(
                cadence=job.cadence,
                run_hour_local=job.run_hour_local,
                run_day_of_week=job.run_day_of_week,
                base_time=run_finished_at,
            )
            self.report_job_repo.save(job)
            self.report_job_run_repo.save(
                ReportJobRun(
                    id=f"report-run-{uuid4().hex[:10]}",
                    job_id=job.id,
                    template_id=job.template_id,
                    trigger=trigger,
                    status="failed",
                    started_at=run_started_at,
                    finished_at=run_finished_at,
                    export_formats=job.export_formats,
                    error_message=str(exc),
                )
            )
            raise

    def run_due_report_jobs(self, now: datetime | None = None, trigger: str = "due") -> list[ReportJob]:
        now = now or datetime.now()
        completed: list[ReportJob] = []
        for job in self.list_report_jobs():
            if not job.active or job.cadence == "manual":
                continue
            if job.next_run_at is None:
                job.next_run_at = _compute_next_run_at(
                    cadence=job.cadence,
                    run_hour_local=job.run_hour_local,
                    run_day_of_week=job.run_day_of_week,
                    base_time=now,
                )
                self.report_job_repo.save(job)
                continue
            if job.next_run_at <= now:
                try:
                    completed.append(self.run_report_job(job.id, trigger=trigger, now=now))
                except Exception:
                    completed.append(self.get_report_job(job.id))
        return completed

    def list_dashboards(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[DashboardConfig]:
        persisted = self.dashboard_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))
        merged = {dashboard.id: dashboard for dashboard in DEFAULT_DASHBOARDS}
        for item in persisted:
            merged[item.id] = item
        if owner_scope != "all":
            merged = {key: value for key, value in merged.items() if value.owner_scope == owner_scope}
        return list(merged.values())

    def list_persisted_dashboards(self, owner_scope: Literal["all", "shared", "private"] = "all") -> list[DashboardConfig]:
        return self.dashboard_repo.list_saved(owner_scope=self._normalize_owner_scope_filter(owner_scope))

    def get_dashboard(self, dashboard_id: str) -> DashboardConfig:
        persisted = self.dashboard_repo.get(dashboard_id)
        if persisted is not None:
            return persisted
        dashboards = {item.id: item for item in DEFAULT_DASHBOARDS}
        return dashboards[dashboard_id]

    def save_dashboard(self, dashboard: DashboardConfig, allow_shared_mutation: bool = False) -> DashboardConfig:
        existing = self.dashboard_repo.get(dashboard.id)
        if existing is not None:
            self._enforce_shared_mutation_policy(
                entity_label="Dashboard",
                entity_id=dashboard.id,
                existing_scope=existing.owner_scope,
                incoming_scope=dashboard.owner_scope,
                allow_shared_mutation=allow_shared_mutation,
            )
        return self.dashboard_repo.save(dashboard)

    def delete_dashboard(self, dashboard_id: str, allow_shared_mutation: bool = False) -> None:
        existing = self.dashboard_repo.get(dashboard_id)
        if existing is None:
            raise KeyError(dashboard_id)
        self._enforce_shared_mutation_policy(
            entity_label="Dashboard",
            entity_id=dashboard_id,
            existing_scope=existing.owner_scope,
            allow_shared_mutation=allow_shared_mutation,
        )
        self.dashboard_repo.delete(dashboard_id)

    def _build_report_section(self, section: ReportTemplateSection) -> ReportSnapshotSection:
        if section.kind == "global_monitor":
            rows = self.get_global_macro_monitor()
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title,
                content=f"{len(rows)} global macro indicators included.",
                rows=rows,
            )
        if section.kind == "cross_asset_monitor":
            rows = self.get_cross_asset_monitor()
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title,
                content=f"{len(rows)} cross-asset rows included.",
                rows=rows,
            )
        if section.kind == "change_monitor":
            country = section.params.get("country")
            topic = section.params.get("topic")
            asset_class = section.params.get("asset_class")
            limit = int(section.params.get("limit", 15))
            rows = [item.model_dump(mode="json") for item in self.get_change_monitor(country=country, topic=topic, asset_class=asset_class, limit=limit)]
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title,
                content=f"{len(rows)} latest change signals ranked by significance.",
                rows=rows,
            )
        if section.kind == "alert_monitor":
            rule_id = section.params.get("rule_id")
            status = section.params.get("status", "new")
            limit = int(section.params.get("limit", 15))
            scan_current = bool(section.params.get("scan_current", True))
            publish_included = bool(section.params.get("publish_included", False))
            if scan_current:
                self.run_change_alert_scan(rule_id=rule_id)
            events = self.list_change_alert_events(rule_id=rule_id, status=status, limit=limit)
            rows = [
                {
                    "event_id": event.id,
                    "rule_name": event.rule_name,
                    "key": event.signal.key,
                    "title": event.signal.title,
                    "significance": event.signal.significance,
                    "direction": event.signal.direction,
                    "absolute_change": event.signal.absolute_change,
                    "percent_change": event.signal.percent_change,
                    "observation_date": event.signal.observation_date,
                    "status": event.status,
                }
                for event in events
            ]
            if publish_included:
                for event in events:
                    if event.status == "new":
                        self.update_change_alert_event_status(event.id, "published")
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title,
                content=f"{len(rows)} alert events matched current alert rules.",
                rows=rows,
            )
        if section.kind == "release_calendar":
            country = section.params.get("country")
            days = int(section.params.get("days", 60))
            rows = [item.model_dump() for item in self.get_release_calendar(country=country, days=days)]
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title,
                content=f"{len(rows)} expected releases over the next {days} days.",
                rows=rows,
            )
        if section.kind == "saved_screen":
            screen = self.get_saved_screen(section.ref_id or "")
            rows = self.run_screen(screen.spec)
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title or screen.name,
                content=f"{len(rows)} securities matched saved screen '{screen.name}'.",
                rows=rows,
            )
        if section.kind == "portfolio_summary":
            portfolio_id = section.ref_id or ""
            scenario_id = section.params.get("scenario_id")
            rows = self.get_portfolio_summary(portfolio_id, scenario_id=scenario_id)
            portfolio = self.get_model_portfolio(portfolio_id)
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title or portfolio.name,
                content=f"{len(rows)} holdings summarized for portfolio '{portfolio.name}'.",
                rows=rows,
            )
        if section.kind == "dashboard_summary":
            dashboard = self.get_dashboard(section.ref_id or "")
            rows = [
                {"title": widget.title, "kind": widget.kind, "series_count": len(widget.series_ids)}
                for widget in dashboard.widgets
            ]
            return ReportSnapshotSection(
                kind=section.kind,
                title=section.title or dashboard.name,
                content=f"Dashboard '{dashboard.name}' contains {len(rows)} widgets.",
                rows=rows,
            )
        return ReportSnapshotSection(kind=section.kind, title=section.title, content="Unsupported section type.", rows=[])

    def _summarize_report(self, sections: list[ReportSnapshotSection]) -> str:
        parts = [f"{section.title}: {section.content}" for section in sections[:3]]
        return " | ".join(parts)

    def _write_report_markdown(self, snapshot: ReportSnapshot) -> Path:
        output_path = settings.report_output_dir / f"{snapshot.id}.md"
        lines = [f"# {snapshot.name}", "", f"Generated: {snapshot.generated_at.isoformat()}", "", snapshot.summary, ""]
        for section in snapshot.sections:
            lines.append(f"## {section.title}")
            lines.append(section.content)
            if section.rows:
                frame = pd.DataFrame(section.rows)
                lines.append("")
                lines.append(_frame_to_markdown_table(frame.head(10)))
            lines.append("")
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def _write_report_json(self, snapshot: ReportSnapshot) -> Path:
        output_path = settings.report_output_dir / f"{snapshot.id}.json"
        output_path.write_text(json.dumps(snapshot.model_dump(mode="json"), indent=2), encoding="utf-8")
        return output_path

    def _write_report_csv_zip(self, snapshot: ReportSnapshot) -> Path:
        output_path = settings.report_output_dir / f"{snapshot.id}.zip"
        with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = {
                "snapshot_id": snapshot.id,
                "template_id": snapshot.template_id,
                "name": snapshot.name,
                "generated_at": snapshot.generated_at.isoformat(),
                "sections": [
                    {"title": section.title, "kind": section.kind, "row_count": len(section.rows)}
                    for section in snapshot.sections
                ],
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            for index, section in enumerate(snapshot.sections, start=1):
                frame = pd.DataFrame(section.rows)
                filename = f"{index:02d}_{_slugify(section.title)}.csv"
                if frame.empty:
                    archive.writestr(filename, "message\nNo rows\n")
                else:
                    archive.writestr(filename, frame.to_csv(index=False))
        return output_path

    def _write_report_xlsx(self, snapshot: ReportSnapshot) -> Path:
        output_path = settings.report_output_dir / f"{snapshot.id}.xlsx"
        summary_frame = pd.DataFrame(
            [
                {
                    "snapshot_id": snapshot.id,
                    "template_id": snapshot.template_id,
                    "name": snapshot.name,
                    "generated_at": snapshot.generated_at.isoformat(),
                    "summary": snapshot.summary,
                }
            ]
        )
        with pd.ExcelWriter(output_path) as writer:
            summary_frame.to_excel(writer, sheet_name="summary", index=False)
            for index, section in enumerate(snapshot.sections, start=1):
                frame = pd.DataFrame(section.rows)
                if frame.empty:
                    frame = pd.DataFrame([{"message": "No rows"}])
                sheet_name = f"{index:02d}_{_slugify(section.title)[:25]}" or f"section_{index}"
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
        return output_path

    def _write_report_pptx(self, snapshot: ReportSnapshot) -> dict[str, str]:
        snapshot_json_path = self._write_report_json(snapshot)
        source_path = settings.report_output_dir / f"{snapshot.id}.js"
        output_path = settings.report_output_dir / f"{snapshot.id}.pptx"
        script_path = Path("tools/report_deck_export/render_snapshot_report.js").resolve()
        source_lines = [
            '"use strict";',
            "",
            "// Generated from the macro platform report export pipeline.",
            f'const renderSnapshotReport = require("{_node_path(script_path)}");',
            f'renderSnapshotReport("{_node_path(snapshot_json_path)}", "{_node_path(output_path)}").catch((error) => {{',
            "  console.error(error);",
            "  process.exit(1);",
            "});",
        ]
        source_path.write_text("\n".join(source_lines), encoding="utf-8")
        subprocess.run(
            [
                "node",
                str(script_path),
                str(snapshot_json_path),
                str(output_path),
            ],
            check=True,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
        )
        return {"pptx": str(output_path), "pptx_js": str(source_path)}

    def _write_notification_message(self, channel: NotificationChannel, delivery: NotificationDelivery) -> Path:
        channel_dir = settings.notification_output_dir / _slugify(channel.id)
        channel_dir.mkdir(parents=True, exist_ok=True)
        suffix = "json" if channel.kind == "file" else "eml"
        output_path = channel_dir / f"{delivery.id}.{suffix}"
        if channel.kind == "file":
            output_path.write_text(json.dumps(delivery.model_dump(mode="json"), indent=2), encoding="utf-8")
        else:
            subject = str(delivery.payload.get("subject", delivery.channel_name))
            lines = [
                f"To: {channel.target}",
                f"Subject: {subject}",
                f"X-Channel-Id: {channel.id}",
                f"X-Related-Id: {delivery.related_id}",
                "",
                json.dumps(delivery.payload, indent=2),
            ]
            output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def _write_notification_digest(self, channel: NotificationChannel, digest: NotificationDigest) -> Path:
        channel_dir = settings.notification_output_dir / _slugify(channel.id)
        channel_dir.mkdir(parents=True, exist_ok=True)
        suffix = "json" if channel.kind == "file" else "eml"
        output_path = channel_dir / f"{digest.id}.{suffix}"
        if channel.kind == "file":
            output_path.write_text(json.dumps(digest.model_dump(mode="json"), indent=2), encoding="utf-8")
        else:
            subject = str(digest.payload.get("subject", digest.channel_name))
            lines = [
                f"To: {channel.target}",
                f"Subject: {subject}",
                f"X-Channel-Id: {channel.id}",
                f"X-Event-Count: {digest.event_count}",
                "",
                json.dumps(digest.payload, indent=2),
            ]
            output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def _send_governed_notification(
        self,
        channel_id: str,
        event_type: str,
        related_id: str,
        subject: str,
        payload: dict[str, object],
        attempt_count: int = 1,
        fallback_from_channel_id: str | None = None,
        apply_suppression: bool = True,
        apply_followups: bool = False,
    ) -> NotificationDelivery | None:
        channel = self.get_notification_channel(channel_id)
        if not channel.active:
            self._record_notification_routing_audit(
                channel=channel,
                event_type=event_type,
                related_id=related_id,
                decision="inactive",
                reason="channel_inactive",
                payload=payload,
            )
            return None
        if _channel_is_paused(channel):
            self._record_notification_routing_audit(
                channel=channel,
                event_type=event_type,
                related_id=related_id,
                decision="paused",
                reason=channel.pause_reason or "channel_paused",
                payload=payload,
            )
            return None
        accepts, reject_reason = _notification_channel_acceptance(channel, event_type, payload)
        if not accepts:
            self._record_notification_routing_audit(
                channel=channel,
                event_type=event_type,
                related_id=related_id,
                decision="rejected",
                reason=reject_reason,
                payload=payload,
            )
            return None
        if channel.delivery_mode == "digest" and event_type == "alert_event":
            self._record_notification_routing_audit(
                channel=channel,
                event_type=event_type,
                related_id=related_id,
                decision="digest_deferred",
                reason="digest_mode",
                payload=payload,
            )
            return None
        suppression_reason = (
            self._notification_suppression_reason(channel, event_type, related_id)
            if apply_suppression
            else None
        )
        if suppression_reason:
            self._record_notification_routing_audit(
                channel=channel,
                event_type=event_type,
                related_id=related_id,
                decision="suppressed",
                reason=suppression_reason,
                payload=payload,
            )
            return None
        delivery = self.dispatch_notification(
            channel_id=channel_id,
            event_type=event_type,
            related_id=related_id,
            subject=subject,
            payload=payload,
            attempt_count=attempt_count,
            fallback_from_channel_id=fallback_from_channel_id,
        )
        self._record_notification_routing_audit(
            channel=channel,
            event_type=event_type,
            related_id=related_id,
            decision="delivered" if delivery.status == "success" else "failed",
            reason=delivery.error_message,
            payload={
                **payload,
                "delivery_id": delivery.id,
                "delivery_status": delivery.status,
            },
        )
        if apply_followups:
            self._route_follow_up_channels(
                primary_channel=channel,
                primary_delivery=delivery,
                event_type=event_type,
                related_id=related_id,
                subject=subject,
                payload=payload,
            )
        return delivery

    def _notification_suppression_reason(
        self,
        channel: NotificationChannel,
        event_type: str,
        related_id: str,
    ) -> str | None:
        now = datetime.now()
        recent = self.notification_delivery_repo.list_for_related(
            channel_id=channel.id,
            event_type=event_type,
            related_id=related_id,
            limit=25,
        )
        if not recent:
            return None
        if channel.cooldown_minutes > 0:
            cooldown_since = now - timedelta(minutes=channel.cooldown_minutes)
            if any(item.triggered_at >= cooldown_since for item in recent):
                return "cooldown_window"
        if channel.duplicate_window_minutes > 0:
            duplicate_since = now - timedelta(minutes=channel.duplicate_window_minutes)
            if any(item.status == "success" and item.triggered_at >= duplicate_since for item in recent):
                return "duplicate_window"
        return None

    def _maybe_auto_pause_channel(self, channel: NotificationChannel, delivery: NotificationDelivery) -> None:
        if not channel.auto_pause_enabled:
            return
        if _channel_is_paused(channel):
            return
        if delivery.status != "failed":
            return
        now = datetime.now()
        window_since = now - timedelta(hours=channel.auto_pause_window_hours)
        rows = self.notification_delivery_repo.list_saved(channel_id=channel.id, limit=500)
        recent = [item for item in rows if item.triggered_at >= window_since]
        if not recent:
            return
        failed_count = sum(1 for item in recent if item.status == "failed")
        error_rate = failed_count / len(recent)
        consecutive_failures = 0
        for item in rows:
            if item.status == "failed":
                consecutive_failures += 1
                continue
            break
        should_pause = error_rate >= channel.auto_pause_error_rate_threshold
        should_pause = should_pause or consecutive_failures >= channel.auto_pause_consecutive_failures
        if not should_pause:
            return
        channel.paused_until = now + timedelta(minutes=channel.auto_pause_minutes)
        if consecutive_failures >= channel.auto_pause_consecutive_failures:
            channel.pause_reason = (
                f"Auto-paused after {consecutive_failures} consecutive failures "
                f"(threshold={channel.auto_pause_consecutive_failures})."
            )
        else:
            error_pct = round(error_rate * 100, 2)
            threshold_pct = round(channel.auto_pause_error_rate_threshold * 100, 2)
            channel.pause_reason = (
                f"Auto-paused due to {error_pct}% error rate over {channel.auto_pause_window_hours}h "
                f"(threshold={threshold_pct}%)."
            )
        channel.last_auto_paused_at = now
        self.notification_channel_repo.save(channel)

    def _route_follow_up_channels(
        self,
        primary_channel: NotificationChannel,
        primary_delivery: NotificationDelivery,
        event_type: str,
        related_id: str,
        subject: str,
        payload: dict[str, object],
    ) -> None:
        attempted = {primary_channel.id}
        if primary_delivery.status == "failed":
            for fallback_channel_id in primary_channel.fallback_channel_ids:
                if fallback_channel_id in attempted:
                    continue
                attempted.add(fallback_channel_id)
                self._send_governed_notification(
                    channel_id=fallback_channel_id,
                    event_type=event_type,
                    related_id=related_id,
                    subject=subject,
                    payload=payload,
                    fallback_from_channel_id=primary_channel.id,
                    apply_suppression=True,
                    apply_followups=False,
                )
        if event_type == "alert_event" and _payload_meets_escalation_threshold(primary_channel, payload):
            for escalation_channel_id in primary_channel.escalation_channel_ids:
                if escalation_channel_id in attempted:
                    continue
                attempted.add(escalation_channel_id)
                self._send_governed_notification(
                    channel_id=escalation_channel_id,
                    event_type=event_type,
                    related_id=related_id,
                    subject=f"Escalated Alert: {subject}",
                    payload={
                        **payload,
                        "escalated_from_channel_id": primary_channel.id,
                    },
                    apply_suppression=True,
                    apply_followups=False,
                )

    def _record_notification_routing_audit(
        self,
        channel: NotificationChannel,
        event_type: str,
        related_id: str,
        decision: str,
        reason: str | None,
        payload: dict[str, object],
    ) -> None:
        audit = NotificationRoutingAudit(
            id=f"route-{uuid4().hex[:10]}",
            channel_id=channel.id,
            channel_name=channel.name,
            event_type=event_type,
            related_id=related_id,
            decision=decision,
            reason=reason,
            created_at=datetime.now(),
            payload=payload,
        )
        saved = self.notification_routing_audit_repo.save(audit)
        self._maybe_escalate_routing_policy(channel, saved)

    def _build_recovery_probe_payload(self, channel: NotificationChannel) -> dict[str, object]:
        payload: dict[str, object] = {
            "auto_resume_probe": True,
            "probe_profile": channel.recovery_probe_profile,
        }
        if channel.last_auto_paused_at is not None:
            payload["last_auto_paused_at"] = channel.last_auto_paused_at.isoformat()
        if channel.recovery_probe_profile in {"standard", "verbose"}:
            payload.update(
                {
                    "channel_id": channel.id,
                    "channel_name": channel.name,
                    "channel_kind": channel.kind,
                    "probe_mode": "post_ping" if channel.kind in {"webhook", "slack"} else "store_only",
                }
            )
        if channel.recovery_probe_profile == "verbose":
            payload.update(
                {
                    "paused_until": channel.paused_until.isoformat() if channel.paused_until else None,
                    "event_types": list(channel.event_types),
                    "auto_pause_window_hours": channel.auto_pause_window_hours,
                    "auto_pause_error_rate_threshold": channel.auto_pause_error_rate_threshold,
                    "auto_pause_consecutive_failures": channel.auto_pause_consecutive_failures,
                    "headers_count": len(channel.headers),
                }
            )
        payload.update(channel.recovery_probe_payload)
        return payload

    def _recovery_probe_block_reason(self, channel: NotificationChannel, now: datetime) -> str | None:
        rows = self.notification_delivery_repo.list_saved(
            channel_id=channel.id,
            event_type="manual",
            limit=500,
        )
        probe_rows = [item for item in rows if item.payload.get("auto_resume_probe") is True]
        if channel.recovery_probe_cooldown_minutes > 0:
            cooldown_since = now - timedelta(minutes=channel.recovery_probe_cooldown_minutes)
            if any(item.triggered_at >= cooldown_since for item in probe_rows):
                return "recovery_probe_cooldown"
        hour_since = now - timedelta(hours=1)
        probes_last_hour = sum(1 for item in probe_rows if item.triggered_at >= hour_since)
        if probes_last_hour >= channel.recovery_probe_max_per_hour:
            return "recovery_probe_rate_limited"
        return None

    def _maybe_escalate_routing_policy(
        self,
        source_channel: NotificationChannel,
        audit: NotificationRoutingAudit,
    ) -> None:
        if not source_channel.ops_escalation_enabled:
            return
        if not source_channel.ops_escalation_channel_ids:
            return
        if audit.payload.get("policy_escalation") is True:
            return
        if audit.decision not in {"failed", "suppressed", "paused"}:
            return
        now = datetime.now()
        if source_channel.last_ops_escalated_at and source_channel.ops_escalation_cooldown_minutes > 0:
            cooldown_until = source_channel.last_ops_escalated_at + timedelta(
                minutes=source_channel.ops_escalation_cooldown_minutes
            )
            if cooldown_until > now:
                return
        window_start = now - timedelta(hours=source_channel.ops_escalation_window_hours)
        recent = self.list_notification_routing_audits(
            channel_id=source_channel.id,
            event_type=None,
            decision=None,
            limit=5000,
        )
        adverse = [
            item
            for item in recent
            if item.created_at >= window_start and item.decision in {"failed", "suppressed", "paused"}
        ]
        incident = self._upsert_ops_incident(source_channel, audit, adverse_count=len(adverse))
        if len(adverse) < source_channel.ops_escalation_threshold:
            return
        payload = {
            "policy_escalation": True,
            "ops_incident_id": incident.id,
            "source_channel_id": source_channel.id,
            "source_channel_name": source_channel.name,
            "window_hours": source_channel.ops_escalation_window_hours,
            "threshold": source_channel.ops_escalation_threshold,
            "adverse_count": len(adverse),
            "latest_decision": audit.decision,
            "latest_reason": audit.reason,
        }
        for target_channel_id in source_channel.ops_escalation_channel_ids:
            self._send_governed_notification(
                channel_id=target_channel_id,
                event_type="manual",
                related_id=f"ops-escalation-{source_channel.id}-{audit.id}",
                subject=f"Ops escalation: {source_channel.name}",
                payload=payload,
                apply_suppression=False,
                apply_followups=False,
            )
        source_channel.last_ops_escalated_at = now
        self.notification_channel_repo.save(source_channel)
        incident.escalation_count += 1
        incident.last_escalated_at = now
        incident.updated_at = now
        self.ops_incident_repo.save(incident)

    def _upsert_ops_incident(
        self,
        source_channel: NotificationChannel,
        audit: NotificationRoutingAudit,
        adverse_count: int,
    ) -> OpsIncident:
        incident_id = f"ops-incident-{source_channel.id}"
        now = datetime.now()
        severity = _incident_severity(adverse_count, source_channel.ops_escalation_threshold)
        existing = self.ops_incident_repo.get(incident_id)
        if existing is None:
            default_priority = _incident_default_priority(severity)
            default_sla = _incident_default_sla_minutes(default_priority)
            incident = OpsIncident(
                id=incident_id,
                source_channel_id=source_channel.id,
                source_channel_name=source_channel.name,
                status="open",
                severity=severity,
                priority=default_priority,
                opened_at=now,
                updated_at=now,
                last_event_at=audit.created_at,
                sla_minutes=default_sla,
                due_at=now + timedelta(minutes=default_sla),
                window_hours=source_channel.ops_escalation_window_hours,
                threshold=source_channel.ops_escalation_threshold,
                adverse_count=adverse_count,
                latest_decision=audit.decision,
                latest_reason=audit.reason,
            )
            return self.ops_incident_repo.save(incident)
        if existing.status == "resolved":
            existing.status = "open"
            existing.resolved_at = None
        existing.source_channel_name = source_channel.name
        existing.severity = severity
        existing.updated_at = now
        existing.last_event_at = audit.created_at
        existing.window_hours = source_channel.ops_escalation_window_hours
        existing.threshold = source_channel.ops_escalation_threshold
        existing.adverse_count = adverse_count
        existing.latest_decision = audit.decision
        existing.latest_reason = audit.reason
        return self.ops_incident_repo.save(existing)

    def _record_source_health(
        self,
        source_id: str,
        source_kind: str,
        provider: str,
        status: str,
        last_checked_at: datetime,
        last_success_at: datetime | None = None,
        last_failure_at: datetime | None = None,
        fallback_used: bool = False,
        error_message: str | None = None,
        notes: str | None = None,
    ) -> SourceHealth:
        existing = self.source_health_repo.get(source_id)
        if existing is None:
            existing = SourceHealth(
                id=source_id,
                source_kind=source_kind,
                provider=provider,
                status="unknown",
                last_checked_at=last_checked_at,
            )
        existing.source_kind = source_kind
        existing.provider = provider
        existing.status = status
        existing.last_checked_at = last_checked_at
        existing.fallback_used = fallback_used
        if last_success_at is not None:
            existing.last_success_at = last_success_at
        if last_failure_at is not None:
            existing.last_failure_at = last_failure_at
            existing.consecutive_failures += 1
        elif status == "healthy":
            existing.consecutive_failures = 0
        if error_message is not None:
            existing.last_error = error_message
        elif status == "healthy":
            existing.last_error = None
        if notes is not None:
            existing.notes = notes
        existing.is_stale = _source_health_is_stale(existing, now=last_checked_at)
        return self.source_health_repo.save(existing)

    def _record_source_health_policy_version(
        self,
        policy: SourceHealthPolicy,
        action: str,
        previous: SourceHealthPolicy | None = None,
    ) -> SourceHealthPolicyVersion:
        current_versions = self.source_health_policy_version_repo.list_saved(policy_id=policy.id, limit=1)
        next_version = (current_versions[0].version_number + 1) if current_versions else 1
        current_snapshot = policy.model_dump(mode="json")
        previous_snapshot = previous.model_dump(mode="json") if previous is not None else {}
        changed_fields = sorted(
            {
                key
                for key in set(current_snapshot.keys()) | set(previous_snapshot.keys())
                if current_snapshot.get(key) != previous_snapshot.get(key)
            }
        )
        summary = f"{action} policy '{policy.name}'"
        safe_action = action if action in {"create", "update", "archive", "restore", "rollback"} else "update"
        version = SourceHealthPolicyVersion(
            id=f"source-health-policy-version-{uuid4().hex[:10]}",
            policy_id=policy.id,
            version_number=next_version,
            action=safe_action,
            changed_at=datetime.now(),
            changed_fields=changed_fields,
            summary=summary,
            snapshot=current_snapshot,
        )
        return self.source_health_policy_version_repo.save(version)

    def _validate_source_health_policy(self, policy: SourceHealthPolicy) -> None:
        if policy.min_consecutive_failures < 1:
            raise ValueError("min_consecutive_failures must be at least 1.")
        if policy.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be at least 0.")
        if policy.escalation_failure_threshold < 1:
            raise ValueError("escalation_failure_threshold must be at least 1.")
        if policy.active_hour_start < 0 or policy.active_hour_start > 23:
            raise ValueError("active_hour_start must be between 0 and 23.")
        if policy.active_hour_end < 1 or policy.active_hour_end > 24:
            raise ValueError("active_hour_end must be between 1 and 24.")
        if policy.holiday_calendar not in {"none", "us", "uk", "eu", "jp", "cn"}:
            raise ValueError("holiday_calendar must be one of: none, us, uk, eu, jp, cn.")
        for weekday in policy.active_weekdays:
            if weekday < 0 or weekday > 6:
                raise ValueError("active_weekdays values must be between 0 (Mon) and 6 (Sun).")
        try:
            ZoneInfo(policy.timezone)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone, e.g. Asia/Shanghai.") from exc
        if policy.stale_threshold_minutes is not None and policy.stale_threshold_minutes < 1:
            raise ValueError("stale_threshold_minutes must be at least 1 when provided.")
        for reason, severity in policy.reason_severity.items():
            if reason not in {"degraded", "down", "stale"}:
                raise ValueError("reason_severity keys must be one of: degraded, down, stale.")
            if severity not in {"low", "medium", "high"}:
                raise ValueError("reason_severity values must be one of: low, medium, high.")
        for reason, template in policy.reason_subject_templates.items():
            if reason not in {"degraded", "down", "stale"}:
                raise ValueError("reason_subject_templates keys must be one of: degraded, down, stale.")
            if not template.strip():
                raise ValueError("reason_subject_templates values must be non-empty.")
        for reason, channel_ids in policy.reason_channel_overrides.items():
            if reason not in {"degraded", "down", "stale"}:
                raise ValueError("reason_channel_overrides keys must be one of: degraded, down, stale.")

    def _verify_source_health_policy_channels(self, policy: SourceHealthPolicy) -> None:
        for channel_ids in policy.reason_channel_overrides.values():
            for channel_id in channel_ids:
                self.get_notification_channel(channel_id)
        for channel_id in policy.notification_channel_ids:
            self.get_notification_channel(channel_id)
        for channel_id in policy.escalation_channel_ids:
            self.get_notification_channel(channel_id)

    def _apply_source_health_policy_threshold(self, policy: SourceHealthPolicy) -> None:
        if policy.source_id and policy.stale_threshold_minutes is not None:
            self.set_source_stale_threshold(policy.source_id, policy.stale_threshold_minutes)

    def _collect_digest_alert_events(
        self,
        channel: NotificationChannel,
        status: str = "new",
        limit: int = 25,
    ) -> list[ChangeAlertEvent]:
        allowed_rule_ids = {
            rule.id
            for rule in self.list_change_alert_rules()
            if channel.id in rule.notification_channel_ids and rule.active
        }
        rows: list[ChangeAlertEvent] = []
        for event in self.list_change_alert_events(status=status, limit=max(limit * 4, limit)):
            if event.rule_id not in allowed_rule_ids:
                continue
            payload = {
                "rule_id": event.rule_id,
                "rule_name": event.rule_name,
                "event_id": event.id,
                "signal": event.signal.model_dump(mode="json"),
            }
            if not _notification_channel_accepts(channel, "alert_event", payload):
                continue
            rows.append(event)
            if len(rows) >= limit:
                break
        return rows

    def _post_notification(self, channel: NotificationChannel, payload: dict[str, object]) -> int:
        request = urlrequest.Request(
            channel.target,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **channel.headers},
            method="POST",
        )
        try:
            with urlrequest.urlopen(request, timeout=10) as response:
                status_code = int(getattr(response, "status", 200))
                if status_code >= 400:
                    raise RuntimeError(f"Notification endpoint returned HTTP {status_code}")
                return status_code
        except urlerror.HTTPError as exc:
            raise RuntimeError(f"Notification endpoint returned HTTP {exc.code}") from exc

    def _create_source_health_policy_version_preset_from_import(
        self,
        policy_id: str,
        item: SourceHealthPolicyVersionPresetImportItem,
        is_default_override: bool | None = None,
    ) -> SourceHealthPolicyVersionPreset:
        return SourceHealthPolicyVersionPreset(
            id=f"source-policy-version-preset-{uuid4().hex[:8]}",
            policy_id=policy_id,
            name=item.name,
            action_filter=item.action_filter,
            query=item.query,
            limit=item.limit,
            is_default=item.is_default if is_default_override is None else bool(is_default_override),
            owner_scope=item.owner_scope,
        )


def _observations_to_series(rows: list[Observation], freq: str) -> pd.Series:
    frame = pd.DataFrame([row.model_dump(mode="json") for row in rows])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"])
    return frame.sort_values("date").set_index("date")["value"].asfreq(freq)


def _expected_next_release(last_observation_date: date | None, frequency: str) -> date | None:
    if not last_observation_date:
        return None
    offsets = {
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "monthly": timedelta(days=31),
        "quarterly": timedelta(days=92),
        "annual": timedelta(days=366),
    }
    return last_observation_date + offsets[frequency]


def _freshness_status(last_observation_date: date | None, frequency: str) -> str:
    if not last_observation_date:
        return "unknown"
    expected = _expected_next_release(last_observation_date, frequency)
    if expected is None:
        return "unknown"
    days_from_expected = (date.today() - expected).days
    if days_from_expected <= 0:
        return "fresh"
    if days_from_expected <= 14:
        return "aging"
    return "stale"


def _apply_scenario_to_holding(
    ticker: str,
    asset_class: str,
    base_return: float | None,
    scenario: ScenarioDefinition | None,
) -> float | None:
    if base_return is None:
        return None
    if scenario is None:
        return base_return
    adjustment = 0.0
    for shock in scenario.shocks:
        if shock.ticker and shock.ticker == ticker:
            adjustment += shock.shock_pct
        elif shock.asset_class and shock.asset_class == asset_class:
            adjustment += shock.shock_pct
    return base_return + adjustment


def _frame_to_markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows_"
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in frame.iterrows():
        values = [str(row[column]) for column in frame.columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _slugify(value: str) -> str:
    safe = [char.lower() if char.isalnum() else "_" for char in value.strip()]
    collapsed = "".join(safe).strip("_")
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed or "section"


def _node_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\")


def _compute_next_run_at(
    cadence: str,
    run_hour_local: int,
    run_day_of_week: int | None,
    base_time: datetime,
) -> datetime | None:
    if cadence == "manual":
        return None
    hour = min(max(run_hour_local, 0), 23)
    candidate = base_time.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cadence == "daily":
        if candidate <= base_time:
            candidate += timedelta(days=1)
        return candidate
    if cadence == "weekly":
        target_day = run_day_of_week if run_day_of_week is not None else base_time.weekday()
        days_ahead = (target_day - base_time.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= base_time:
            candidate += timedelta(days=7)
        return candidate
    return None


def _build_series_change_signal(
    definition: SeriesDefinition,
    observations: list[Observation],
) -> ChangeSignal | None:
    valid = [item for item in observations if item.value is not None]
    if len(valid) < 2:
        return None
    previous = valid[-2]
    current = valid[-1]
    current_value = float(current.value or 0.0)
    previous_value = float(previous.value or 0.0)
    absolute_change = current_value - previous_value
    percent_change = None
    if abs(previous_value) > 1e-9:
        percent_change = (absolute_change / abs(previous_value)) * 100
    direction = _direction_for_change(absolute_change)
    significance = _score_significance(
        absolute_change=absolute_change,
        percent_change=percent_change,
        unit=definition.unit,
    )
    return ChangeSignal(
        entity_type="series",
        key=definition.id,
        title=definition.title,
        topic=definition.topic,
        country=definition.country,
        source=definition.source,
        observation_date=current.date,
        current_value=round(current_value, 4),
        previous_value=round(previous_value, 4),
        absolute_change=round(absolute_change, 4),
        percent_change=round(percent_change, 2) if percent_change is not None else None,
        direction=direction,
        significance=significance,
        unit=definition.unit,
    )


def _build_asset_change_signal(
    ticker: str,
    asset_class: str,
    prices: list[AssetPrice],
) -> ChangeSignal | None:
    if len(prices) < 2:
        return None
    previous = prices[-2]
    current = prices[-1]
    current_value = float(current.close)
    previous_value = float(previous.close)
    absolute_change = current_value - previous_value
    percent_change = None
    if abs(previous_value) > 1e-9:
        percent_change = (absolute_change / abs(previous_value)) * 100
    direction = _direction_for_change(absolute_change)
    significance = _score_significance(
        absolute_change=absolute_change,
        percent_change=percent_change,
        unit="price",
    )
    return ChangeSignal(
        entity_type="asset",
        key=ticker,
        title=ticker,
        topic="markets",
        asset_class=asset_class,
        source=current.source,
        observation_date=current.date,
        current_value=round(current_value, 4),
        previous_value=round(previous_value, 4),
        absolute_change=round(absolute_change, 4),
        percent_change=round(percent_change, 2) if percent_change is not None else None,
        direction=direction,
        significance=significance,
        unit=current.currency or "price",
    )


def _build_series_change_delta(
    definition: SeriesDefinition,
    observations: list[Observation],
) -> dict[str, object] | None:
    valid = [item for item in observations if item.value is not None]
    if len(valid) < 3:
        return None
    older = valid[-3]
    previous = valid[-2]
    current = valid[-1]
    current_value = float(current.value or 0.0)
    previous_value = float(previous.value or 0.0)
    older_value = float(older.value or 0.0)
    current_abs = current_value - previous_value
    previous_abs = previous_value - older_value
    current_pct = ((current_abs / abs(previous_value)) * 100) if abs(previous_value) > 1e-9 else None
    previous_pct = ((previous_abs / abs(older_value)) * 100) if abs(older_value) > 1e-9 else None
    delta_abs = current_abs - previous_abs
    delta_pct = None
    if current_pct is not None and previous_pct is not None:
        delta_pct = current_pct - previous_pct
    return {
        "entity_type": "series",
        "key": definition.id,
        "title": definition.title,
        "topic": definition.topic,
        "country": definition.country,
        "asset_class": None,
        "source": definition.source,
        "observation_date": current.date,
        "current_value": round(current_value, 4),
        "previous_value": round(previous_value, 4),
        "absolute_change": round(current_abs, 4),
        "percent_change": round(current_pct, 2) if current_pct is not None else None,
        "previous_absolute_change": round(previous_abs, 4),
        "previous_percent_change": round(previous_pct, 2) if previous_pct is not None else None,
        "delta_absolute_change": round(delta_abs, 4),
        "delta_percent_change": round(delta_pct, 2) if delta_pct is not None else None,
        "direction": _direction_for_change(current_abs),
        "trend": _change_trend(current_abs, previous_abs),
        "significance": _score_significance(absolute_change=current_abs, percent_change=current_pct, unit=definition.unit),
        "unit": definition.unit,
    }


def _build_asset_change_delta(
    ticker: str,
    asset_class: str,
    prices: list[AssetPrice],
) -> dict[str, object] | None:
    if len(prices) < 3:
        return None
    older = prices[-3]
    previous = prices[-2]
    current = prices[-1]
    current_value = float(current.close)
    previous_value = float(previous.close)
    older_value = float(older.close)
    current_abs = current_value - previous_value
    previous_abs = previous_value - older_value
    current_pct = ((current_abs / abs(previous_value)) * 100) if abs(previous_value) > 1e-9 else None
    previous_pct = ((previous_abs / abs(older_value)) * 100) if abs(older_value) > 1e-9 else None
    delta_abs = current_abs - previous_abs
    delta_pct = None
    if current_pct is not None and previous_pct is not None:
        delta_pct = current_pct - previous_pct
    return {
        "entity_type": "asset",
        "key": ticker,
        "title": ticker,
        "topic": "markets",
        "country": None,
        "asset_class": asset_class,
        "source": current.source,
        "observation_date": current.date,
        "current_value": round(current_value, 4),
        "previous_value": round(previous_value, 4),
        "absolute_change": round(current_abs, 4),
        "percent_change": round(current_pct, 2) if current_pct is not None else None,
        "previous_absolute_change": round(previous_abs, 4),
        "previous_percent_change": round(previous_pct, 2) if previous_pct is not None else None,
        "delta_absolute_change": round(delta_abs, 4),
        "delta_percent_change": round(delta_pct, 2) if delta_pct is not None else None,
        "direction": _direction_for_change(current_abs),
        "trend": _change_trend(current_abs, previous_abs),
        "significance": _score_significance(absolute_change=current_abs, percent_change=current_pct, unit="price"),
        "unit": current.currency or "price",
    }


def _evaluate_screen_filters(metrics: dict[str, float], filters: list[object]) -> list[dict[str, object]]:
    trace: list[dict[str, object]] = []
    for current in filters:
        field = getattr(current, "field", "")
        operator = getattr(current, "operator", "")
        threshold = getattr(current, "value", None)
        value = metrics.get(str(field))
        passed = False
        if value is not None and threshold is not None:
            if operator == "gt":
                passed = value > threshold
            elif operator == "gte":
                passed = value >= threshold
            elif operator == "lt":
                passed = value < threshold
            elif operator == "lte":
                passed = value <= threshold
        trace.append(
            {
                "field": field,
                "operator": operator,
                "threshold": threshold,
                "value": value,
                "passed": passed,
            }
        )
    return trace


def _change_trend(current_change: float, previous_change: float, tolerance: float = 1e-9) -> str:
    if abs(current_change) <= tolerance and abs(previous_change) <= tolerance:
        return "stable"
    if current_change * previous_change < 0:
        return "reversing"
    if abs(current_change) > abs(previous_change):
        return "accelerating"
    if abs(current_change) < abs(previous_change):
        return "decelerating"
    return "stable"


def _direction_for_change(change: float) -> str:
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _score_significance(
    absolute_change: float,
    percent_change: float | None,
    unit: str | None,
) -> str:
    abs_percent = abs(percent_change) if percent_change is not None else 0.0
    unit_text = (unit or "").lower()
    if "percent" in unit_text or "yield" in unit_text:
        abs_level = abs(absolute_change)
        if abs_level >= 0.5:
            return "high"
        if abs_level >= 0.1:
            return "medium"
        return "low"
    if abs_percent >= 5:
        return "high"
    if abs_percent >= 1:
        return "medium"
    return "low"


def _significance_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def _notification_channel_accepts(
    channel: NotificationChannel,
    event_type: str,
    payload: dict[str, object],
) -> bool:
    allowed, _ = _notification_channel_acceptance(channel, event_type, payload)
    return allowed


def _channel_is_paused(channel: NotificationChannel, now: datetime | None = None) -> bool:
    if channel.paused_until is None:
        return False
    now = now or datetime.now()
    return channel.paused_until > now


def _notification_channel_acceptance(
    channel: NotificationChannel,
    event_type: str,
    payload: dict[str, object],
) -> tuple[bool, str | None]:
    if not channel.active:
        return False, "channel_inactive"
    if _channel_is_paused(channel):
        return False, "channel_paused"
    if event_type not in channel.event_types:
        return False, "event_type_not_enabled"
    if event_type != "alert_event":
        return True, None
    significance = _extract_payload_significance(payload)
    if significance is None:
        return False, "missing_significance"
    if _significance_rank(significance) < _significance_rank(channel.min_significance):
        return False, "significance_below_threshold"
    return True, None


def _incident_severity(adverse_count: int, threshold: int) -> str:
    if adverse_count >= threshold * 3:
        return "high"
    if adverse_count >= threshold * 2:
        return "medium"
    return "low"


def _incident_default_priority(severity: str) -> str:
    return {"high": "high", "medium": "medium", "low": "low"}.get(severity, "medium")


def _incident_default_sla_minutes(priority: str) -> int:
    return {"high": 60, "medium": 240, "low": 1440}.get(priority, 240)


def _incident_is_overdue(incident: OpsIncident, now: datetime) -> bool:
    if incident.status == "resolved":
        return False
    if incident.due_at is None:
        return False
    return incident.due_at < now


def _source_health_is_stale(source: SourceHealth, now: datetime) -> bool:
    if source.last_success_at is None:
        return False
    stale_after = max(1, int(source.stale_threshold_minutes))
    return source.last_success_at < (now - timedelta(minutes=stale_after))


def _parse_source_id(source_id: str) -> tuple[str, str]:
    if ":" not in source_id:
        return "macro", source_id
    left, right = source_id.split(":", 1)
    source_kind = left if left in {"macro", "market"} else "macro"
    provider = right or source_id
    return source_kind, provider


def _source_reason_default_severity(reason: str) -> str:
    return {
        "down": "high",
        "degraded": "medium",
        "stale": "low",
    }.get(reason, "medium")


def _render_source_health_subject_template(
    template: str,
    source_id: str,
    source_kind: str,
    provider: str,
    reason: str,
    status: str,
    severity: str,
) -> str:
    context = {
        "source_id": source_id,
        "source_kind": source_kind,
        "provider": provider,
        "reason": reason,
        "status": status,
        "severity": severity,
    }
    try:
        return template.format(**context)
    except Exception:
        return f"Source Health Alert [{severity.upper()}]: {source_id} ({reason})"


def _source_policy_reason_in_schedule(
    policy: SourceHealthPolicy,
    reason: str,
    check_time: datetime,
) -> bool:
    if reason == "down" and policy.allow_down_outside_schedule:
        return True
    local_time = _source_policy_local_time(policy, check_time)
    local_date = local_time.date()
    local_weekday = local_time.weekday()
    if policy.active_weekdays and local_weekday not in set(policy.active_weekdays):
        return False
    if local_date in _source_policy_holiday_set(policy, local_date.year):
        return False
    start_hour = int(policy.active_hour_start)
    end_hour = int(policy.active_hour_end)
    hour = local_time.hour
    if start_hour == 0 and end_hour == 24:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _source_policy_local_time(policy: SourceHealthPolicy, check_time: datetime) -> datetime:
    policy_tz = ZoneInfo(policy.timezone)
    if check_time.tzinfo is None:
        system_tz = datetime.now().astimezone().tzinfo
        if system_tz is None:
            system_tz = ZoneInfo("UTC")
        check_time = check_time.replace(tzinfo=system_tz)
    return check_time.astimezone(policy_tz)


def _source_policy_holiday_set(policy: SourceHealthPolicy, year: int) -> set[date]:
    holidays = set(policy.holiday_dates)
    holidays.update(_builtin_market_holidays(policy.holiday_calendar, year))
    return holidays


def _builtin_market_holidays(calendar: str, year: int) -> set[date]:
    if calendar == "none":
        return set()
    # Baseline market holiday seeds; extend as needed per desk policy.
    common = {date(year, 1, 1), date(year, 12, 25)}
    if calendar == "us":
        return common | {
            _nth_weekday_of_month(year, 1, 0, 3),   # MLK Day
            _nth_weekday_of_month(year, 2, 0, 3),   # Presidents' Day
            _last_weekday_of_month(year, 5, 0),     # Memorial Day
            _observed_fixed_holiday(date(year, 7, 4)),
            _nth_weekday_of_month(year, 9, 0, 1),   # Labor Day
            _nth_weekday_of_month(year, 11, 3, 4),  # Thanksgiving
        }
    if calendar == "uk":
        return common | {date(year, 12, 26)}
    if calendar == "eu":
        return common
    if calendar == "jp":
        return {date(year, 1, 1), date(year, 2, 11), date(year, 12, 31)}
    if calendar == "cn":
        return {date(year, 1, 1), date(year, 5, 1), date(year, 10, 1), date(year, 10, 2), date(year, 10, 3)}
    return set()


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    first_offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=first_offset + (n - 1) * 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    current = first_next - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _observed_fixed_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _payload_meets_escalation_threshold(
    channel: NotificationChannel,
    payload: dict[str, object],
) -> bool:
    significance = _extract_payload_significance(payload)
    if significance is None:
        return False
    return _significance_rank(significance) >= _significance_rank(channel.escalation_min_significance)


def _extract_payload_significance(payload: dict[str, object]) -> str | None:
    signal = payload.get("signal")
    if isinstance(signal, ChangeSignal):
        return signal.significance
    if isinstance(signal, dict):
        value = signal.get("significance")
        return str(value) if value is not None else None
    return None


def _matches_change_alert_rule(
    rule: ChangeAlertRule,
    signal: ChangeSignal,
    allowed_keys: set[str],
) -> bool:
    if rule.entity_type != "any" and signal.entity_type != rule.entity_type:
        return False
    if allowed_keys and signal.key not in allowed_keys:
        return False
    if rule.country and signal.country != rule.country:
        return False
    if rule.topic and signal.topic != rule.topic:
        return False
    if rule.asset_class and signal.asset_class != rule.asset_class:
        return False
    if _significance_rank(signal.significance) < _significance_rank(rule.min_significance):
        return False
    if rule.min_absolute_change is not None and abs(signal.absolute_change) < rule.min_absolute_change:
        return False
    if rule.min_percent_change is not None:
        if signal.percent_change is None or abs(signal.percent_change) < rule.min_percent_change:
            return False
    return True


def _change_alert_event_id(rule_id: str, key: str, observation_date: date) -> str:
    safe_key = _slugify(key)
    return f"alert-{rule_id}-{safe_key}-{observation_date.isoformat()}"
