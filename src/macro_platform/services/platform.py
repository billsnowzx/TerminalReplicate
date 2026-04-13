from __future__ import annotations

import json
import subprocess
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import uuid4

import pandas as pd

from macro_platform.analytics.core import classify_regime, drawdown, screen_assets, year_over_year, yield_curve_slope
from macro_platform.catalog.tracked_universe import DEFAULT_DASHBOARDS, MARKET_UNIVERSE, TRACKED_SERIES
from macro_platform.config import settings
from macro_platform.domain.models import (
    AssetPrice,
    ChangeAlertEvent,
    ChangeAlertRule,
    ChangeSignal,
    DashboardConfig,
    ModelPortfolio,
    NotificationChannel,
    NotificationDelivery,
    Observation,
    ObservationQuery,
    PortfolioSummaryRow,
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
    Watchlist,
)
from macro_platform.providers.adapters import (
    BLSProvider,
    DemoMacroProvider,
    DemoMarketProvider,
    ECBProvider,
    FREDProvider,
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
    NotificationDeliveryRepository,
    ObservationRepository,
    ReportJobRepository,
    ReportJobRunRepository,
    ReportSnapshotRepository,
    ReportTemplateRepository,
    SavedScreenRepository,
    ScenarioRepository,
    SeriesDefinitionRepository,
    WatchlistRepository,
)


class PlatformService:
    def __init__(self, database_url: str | None = None) -> None:
        self.series_map = {item.id: item for item in TRACKED_SERIES}
        self.market_universe = {item["ticker"]: item["asset_class"] for item in MARKET_UNIVERSE}
        self.fred = FREDProvider()
        self.bls = BLSProvider()
        self.ecb = ECBProvider()
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
        self.dashboard_repo = DashboardRepository(self.database)
        self.watchlist_repo = WatchlistRepository(self.database)
        self.saved_screen_repo = SavedScreenRepository(self.database)
        self.scenario_repo = ScenarioRepository(self.database)
        self.portfolio_repo = ModelPortfolioRepository(self.database)
        self.report_template_repo = ReportTemplateRepository(self.database)
        self.report_snapshot_repo = ReportSnapshotRepository(self.database)
        self.report_job_repo = ReportJobRepository(self.database)
        self.report_job_run_repo = ReportJobRunRepository(self.database)
        self.series_repo.sync(list(self.series_map.values()))

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

    def query_observations(self, query: ObservationQuery) -> list[Observation]:
        definition = self.get_series(query.series_id)
        cached_rows = self.observation_repo.get_range(query.series_id, query.start_date, query.end_date)
        try:
            if definition.source == "fred":
                rows = self.fred.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "bls":
                rows = self.bls.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "ecb":
                rows = self.ecb.fetch_observations(definition, query.start_date, query.end_date)
            elif definition.source == "world_bank":
                rows = self.world_bank.fetch_observations(definition, query.start_date, query.end_date)
            else:
                rows = self.demo_macro.fetch_observations(definition, query.start_date, query.end_date)
        except Exception:
            rows = cached_rows or self.demo_macro.fetch_observations(definition, query.start_date, query.end_date)

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
        cached_rows = self.asset_price_repo.get_range(ticker, start_date, end_date)
        if settings.use_openbb_market_provider:
            try:
                provider = OpenBBMarketProvider()
                rows = provider.fetch_prices(ticker, asset_class, start_date, end_date)
                self.asset_price_repo.replace_range(ticker, rows)
                return rows
            except Exception:
                pass
        try:
            rows = self.demo_market.fetch_prices(ticker, asset_class, start_date, end_date)
            self.asset_price_repo.replace_range(ticker, rows)
            return rows
        except Exception:
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

    def list_change_alert_rules(self) -> list[ChangeAlertRule]:
        return self.change_alert_rule_repo.list_saved()

    def get_change_alert_rule(self, rule_id: str) -> ChangeAlertRule:
        rule = self.change_alert_rule_repo.get(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        return rule

    def save_change_alert_rule(self, rule: ChangeAlertRule) -> ChangeAlertRule:
        if rule.watchlist_id:
            self.get_watchlist(rule.watchlist_id)
        for channel_id in rule.notification_channel_ids:
            self.get_notification_channel(channel_id)
        return self.change_alert_rule_repo.save(rule)

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

    def list_notification_channels(self, active_only: bool = False) -> list[NotificationChannel]:
        return self.notification_channel_repo.list_saved(active_only=active_only)

    def get_notification_channel(self, channel_id: str) -> NotificationChannel:
        channel = self.notification_channel_repo.get(channel_id)
        if channel is None:
            raise KeyError(channel_id)
        return channel

    def save_notification_channel(self, channel: NotificationChannel) -> NotificationChannel:
        return self.notification_channel_repo.save(channel)

    def list_notification_deliveries(
        self,
        channel_id: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[NotificationDelivery]:
        return self.notification_delivery_repo.list_saved(
            channel_id=channel_id,
            event_type=event_type,
            status=status,
            limit=limit,
        )

    def dispatch_notification(
        self,
        channel_id: str,
        event_type: str,
        related_id: str,
        subject: str,
        payload: dict[str, object],
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
            target=channel.target,
            payload={"subject": subject, **payload},
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
        return self.notification_delivery_repo.save(delivery)

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
                    self.dispatch_notification(
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

    def list_watchlists(self) -> list[Watchlist]:
        return self.watchlist_repo.list_saved()

    def get_watchlist(self, watchlist_id: str) -> Watchlist:
        watchlist = self.watchlist_repo.get(watchlist_id)
        if watchlist is None:
            raise KeyError(watchlist_id)
        return watchlist

    def save_watchlist(self, watchlist: Watchlist) -> Watchlist:
        return self.watchlist_repo.save(watchlist)

    def list_saved_screens(self) -> list[SavedScreen]:
        return self.saved_screen_repo.list_saved()

    def get_saved_screen(self, screen_id: str) -> SavedScreen:
        screen = self.saved_screen_repo.get(screen_id)
        if screen is None:
            raise KeyError(screen_id)
        return screen

    def save_saved_screen(self, screen: SavedScreen) -> SavedScreen:
        return self.saved_screen_repo.save(screen)

    def list_scenarios(self) -> list[ScenarioDefinition]:
        return self.scenario_repo.list_saved()

    def get_scenario(self, scenario_id: str) -> ScenarioDefinition:
        scenario = self.scenario_repo.get(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        return scenario

    def save_scenario(self, scenario: ScenarioDefinition) -> ScenarioDefinition:
        return self.scenario_repo.save(scenario)

    def list_model_portfolios(self) -> list[ModelPortfolio]:
        return self.portfolio_repo.list_saved()

    def get_model_portfolio(self, portfolio_id: str) -> ModelPortfolio:
        portfolio = self.portfolio_repo.get(portfolio_id)
        if portfolio is None:
            raise KeyError(portfolio_id)
        return portfolio

    def save_model_portfolio(self, portfolio: ModelPortfolio) -> ModelPortfolio:
        return self.portfolio_repo.save(portfolio)

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

    def list_report_templates(self) -> list[ReportTemplate]:
        return self.report_template_repo.list_saved()

    def get_report_template(self, template_id: str) -> ReportTemplate:
        template = self.report_template_repo.get(template_id)
        if template is None:
            raise KeyError(template_id)
        return template

    def save_report_template(self, template: ReportTemplate) -> ReportTemplate:
        return self.report_template_repo.save(template)

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

    def list_report_jobs(self) -> list[ReportJob]:
        return self.report_job_repo.list_saved()

    def get_report_job(self, job_id: str) -> ReportJob:
        job = self.report_job_repo.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def save_report_job(self, job: ReportJob) -> ReportJob:
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
                self.dispatch_notification(
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

    def list_dashboards(self) -> list[DashboardConfig]:
        persisted = self.dashboard_repo.list_saved()
        merged = {dashboard.id: dashboard for dashboard in DEFAULT_DASHBOARDS}
        for item in persisted:
            merged[item.id] = item
        return list(merged.values())

    def get_dashboard(self, dashboard_id: str) -> DashboardConfig:
        persisted = self.dashboard_repo.get(dashboard_id)
        if persisted is not None:
            return persisted
        dashboards = {item.id: item for item in DEFAULT_DASHBOARDS}
        return dashboards[dashboard_id]

    def save_dashboard(self, dashboard: DashboardConfig) -> DashboardConfig:
        return self.dashboard_repo.save(dashboard)

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
