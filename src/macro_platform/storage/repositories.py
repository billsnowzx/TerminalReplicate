from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, select

from macro_platform.domain.models import (
    AssetPrice,
    ChangeAlertEvent,
    ChangeAlertRule,
    DashboardConfig,
    ModelPortfolio,
    NotificationChannel,
    NotificationDelivery,
    Observation,
    ReportJob,
    ReportJobRun,
    ReportSnapshot,
    ReportTemplate,
    SavedScreen,
    ScenarioDefinition,
    SeriesDefinition,
    Watchlist,
)
from macro_platform.storage.database import Database
from macro_platform.storage.tables import (
    AssetPriceRecord,
    ChangeAlertEventRecord,
    ChangeAlertRuleRecord,
    DashboardRecord,
    ModelPortfolioRecord,
    NotificationChannelRecord,
    NotificationDeliveryRecord,
    ObservationRecord,
    ReportJobRecord,
    ReportJobRunRecord,
    ReportSnapshotRecord,
    ReportTemplateRecord,
    SavedScreenRecord,
    ScenarioRecord,
    SeriesDefinitionRecord,
    WatchlistRecord,
)


class SeriesDefinitionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def sync(self, definitions: list[SeriesDefinition]) -> None:
        with self.database.session_scope() as session:
            existing = {
                row.id: row
                for row in session.execute(select(SeriesDefinitionRecord)).scalars()
            }
            for definition in definitions:
                record = existing.get(definition.id, SeriesDefinitionRecord(id=definition.id))
                record.source = definition.source
                record.source_key = definition.source_key
                record.title = definition.title
                record.topic = definition.topic
                record.country = definition.country
                record.frequency = definition.frequency
                record.unit = definition.unit
                record.seasonal_adjustment = definition.seasonal_adjustment
                record.nominal_real = definition.nominal_real
                record.release_lag = definition.release_lag
                record.tags = definition.tags
                record.provider_params = definition.provider_params
                session.merge(record)


class ObservationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_range(
        self,
        series_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Observation]:
        with self.database.session_scope() as session:
            statement = select(ObservationRecord).where(ObservationRecord.series_id == series_id)
            if start_date:
                statement = statement.where(ObservationRecord.observation_date >= start_date)
            if end_date:
                statement = statement.where(ObservationRecord.observation_date <= end_date)
            statement = statement.order_by(ObservationRecord.observation_date.asc())
            rows = session.execute(statement).scalars().all()
            return [
                Observation(
                    series_id=row.series_id,
                    date=row.observation_date,
                    value=row.value,
                    vintage_date=row.vintage_date,
                    revision_timestamp=row.revision_timestamp,
                    status=row.status,
                )
                for row in rows
            ]

    def replace_range(self, series_id: str, rows: list[Observation]) -> None:
        if not rows:
            return
        start_date = min(row.date for row in rows)
        end_date = max(row.date for row in rows)
        with self.database.session_scope() as session:
            session.execute(
                delete(ObservationRecord).where(
                    ObservationRecord.series_id == series_id,
                    ObservationRecord.observation_date >= start_date,
                    ObservationRecord.observation_date <= end_date,
                )
            )
            session.add_all(
                [
                    ObservationRecord(
                        series_id=row.series_id,
                        observation_date=row.date,
                        value=row.value,
                        vintage_date=row.vintage_date,
                        revision_timestamp=row.revision_timestamp,
                        status=row.status,
                    )
                    for row in rows
                ]
            )

    def count_rows(self, series_id: str) -> int:
        return len(self.get_range(series_id))


class DashboardRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[DashboardConfig]:
        with self.database.session_scope() as session:
            rows = session.execute(select(DashboardRecord).order_by(DashboardRecord.id.asc())).scalars().all()
            return [
                DashboardConfig.model_validate(json.loads(row.payload))
                for row in rows
            ]

    def get(self, dashboard_id: str) -> DashboardConfig | None:
        with self.database.session_scope() as session:
            row = session.get(DashboardRecord, dashboard_id)
            if row is None:
                return None
            return DashboardConfig.model_validate(json.loads(row.payload))

    def save(self, dashboard: DashboardConfig) -> DashboardConfig:
        with self.database.session_scope() as session:
            session.merge(
                DashboardRecord(
                    id=dashboard.id,
                    name=dashboard.name,
                    owner_scope=dashboard.owner_scope,
                    refresh_policy=dashboard.refresh_policy,
                    payload=json.dumps(dashboard.model_dump(mode="json")),
                )
            )
        return dashboard


class AssetPriceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_range(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[AssetPrice]:
        with self.database.session_scope() as session:
            statement = select(AssetPriceRecord).where(AssetPriceRecord.ticker == ticker)
            if start_date:
                statement = statement.where(AssetPriceRecord.price_date >= start_date)
            if end_date:
                statement = statement.where(AssetPriceRecord.price_date <= end_date)
            statement = statement.order_by(AssetPriceRecord.price_date.asc())
            rows = session.execute(statement).scalars().all()
            return [
                AssetPrice(
                    ticker=row.ticker,
                    asset_class=row.asset_class,
                    venue=row.venue,
                    currency=row.currency,
                    date=row.price_date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    adjusted_close=row.adjusted_close,
                    source=row.source,
                )
                for row in rows
            ]

    def replace_range(self, ticker: str, rows: list[AssetPrice]) -> None:
        if not rows:
            return
        start_date = min(row.date for row in rows)
        end_date = max(row.date for row in rows)
        with self.database.session_scope() as session:
            session.execute(
                delete(AssetPriceRecord).where(
                    AssetPriceRecord.ticker == ticker,
                    AssetPriceRecord.price_date >= start_date,
                    AssetPriceRecord.price_date <= end_date,
                )
            )
            session.add_all(
                [
                    AssetPriceRecord(
                        ticker=row.ticker,
                        asset_class=row.asset_class,
                        venue=row.venue,
                        currency=row.currency,
                        price_date=row.date,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        volume=float(row.volume) if row.volume is not None else None,
                        adjusted_close=row.adjusted_close,
                        source=row.source,
                    )
                    for row in rows
                ]
            )

    def count_rows(self, ticker: str) -> int:
        return len(self.get_range(ticker))


class ChangeAlertRuleRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[ChangeAlertRule]:
        with self.database.session_scope() as session:
            rows = session.execute(select(ChangeAlertRuleRecord).order_by(ChangeAlertRuleRecord.id.asc())).scalars().all()
            return [ChangeAlertRule.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, rule_id: str) -> ChangeAlertRule | None:
        with self.database.session_scope() as session:
            row = session.get(ChangeAlertRuleRecord, rule_id)
            if row is None:
                return None
            return ChangeAlertRule.model_validate(json.loads(row.payload))

    def save(self, rule: ChangeAlertRule) -> ChangeAlertRule:
        with self.database.session_scope() as session:
            session.merge(
                ChangeAlertRuleRecord(
                    id=rule.id,
                    name=rule.name,
                    owner_scope=rule.owner_scope,
                    active="true" if rule.active else "false",
                    payload=json.dumps(rule.model_dump(mode="json")),
                )
            )
        return rule


class ChangeAlertEventRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        rule_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ChangeAlertEvent]:
        with self.database.session_scope() as session:
            statement = select(ChangeAlertEventRecord).order_by(ChangeAlertEventRecord.triggered_at.desc())
            if rule_id:
                statement = statement.where(ChangeAlertEventRecord.rule_id == rule_id)
            if status:
                statement = statement.where(ChangeAlertEventRecord.status == status)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [ChangeAlertEvent.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, event_id: str) -> ChangeAlertEvent | None:
        with self.database.session_scope() as session:
            row = session.get(ChangeAlertEventRecord, event_id)
            if row is None:
                return None
            return ChangeAlertEvent.model_validate(json.loads(row.payload))

    def save(self, event: ChangeAlertEvent) -> ChangeAlertEvent:
        with self.database.session_scope() as session:
            session.merge(
                ChangeAlertEventRecord(
                    id=event.id,
                    rule_id=event.rule_id,
                    status=event.status,
                    triggered_at=event.triggered_at,
                    payload=json.dumps(event.model_dump(mode="json")),
                )
            )
        return event


class NotificationChannelRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, active_only: bool = False) -> list[NotificationChannel]:
        with self.database.session_scope() as session:
            statement = select(NotificationChannelRecord).order_by(NotificationChannelRecord.id.asc())
            if active_only:
                statement = statement.where(NotificationChannelRecord.active == "true")
            rows = session.execute(statement).scalars().all()
            return [NotificationChannel.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, channel_id: str) -> NotificationChannel | None:
        with self.database.session_scope() as session:
            row = session.get(NotificationChannelRecord, channel_id)
            if row is None:
                return None
            return NotificationChannel.model_validate(json.loads(row.payload))

    def save(self, channel: NotificationChannel) -> NotificationChannel:
        with self.database.session_scope() as session:
            session.merge(
                NotificationChannelRecord(
                    id=channel.id,
                    name=channel.name,
                    kind=channel.kind,
                    owner_scope=channel.owner_scope,
                    active="true" if channel.active else "false",
                    payload=json.dumps(channel.model_dump(mode="json")),
                )
            )
        return channel


class NotificationDeliveryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        channel_id: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[NotificationDelivery]:
        with self.database.session_scope() as session:
            statement = select(NotificationDeliveryRecord).order_by(NotificationDeliveryRecord.triggered_at.desc())
            if channel_id:
                statement = statement.where(NotificationDeliveryRecord.channel_id == channel_id)
            if event_type:
                statement = statement.where(NotificationDeliveryRecord.event_type == event_type)
            if status:
                statement = statement.where(NotificationDeliveryRecord.status == status)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [NotificationDelivery.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, delivery_id: str) -> NotificationDelivery | None:
        with self.database.session_scope() as session:
            row = session.get(NotificationDeliveryRecord, delivery_id)
            if row is None:
                return None
            return NotificationDelivery.model_validate(json.loads(row.payload))

    def save(self, delivery: NotificationDelivery) -> NotificationDelivery:
        with self.database.session_scope() as session:
            session.merge(
                NotificationDeliveryRecord(
                    id=delivery.id,
                    channel_id=delivery.channel_id,
                    event_type=delivery.event_type,
                    status=delivery.status,
                    triggered_at=delivery.triggered_at,
                    payload=json.dumps(delivery.model_dump(mode="json")),
                )
            )
        return delivery


class WatchlistRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[Watchlist]:
        with self.database.session_scope() as session:
            rows = session.execute(select(WatchlistRecord).order_by(WatchlistRecord.id.asc())).scalars().all()
            return [Watchlist.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, watchlist_id: str) -> Watchlist | None:
        with self.database.session_scope() as session:
            row = session.get(WatchlistRecord, watchlist_id)
            if row is None:
                return None
            return Watchlist.model_validate(json.loads(row.payload))

    def save(self, watchlist: Watchlist) -> Watchlist:
        with self.database.session_scope() as session:
            session.merge(
                WatchlistRecord(
                    id=watchlist.id,
                    name=watchlist.name,
                    owner_scope=watchlist.owner_scope,
                    payload=json.dumps(watchlist.model_dump(mode="json")),
                )
            )
        return watchlist


class SavedScreenRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[SavedScreen]:
        with self.database.session_scope() as session:
            rows = session.execute(select(SavedScreenRecord).order_by(SavedScreenRecord.id.asc())).scalars().all()
            return [SavedScreen.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, screen_id: str) -> SavedScreen | None:
        with self.database.session_scope() as session:
            row = session.get(SavedScreenRecord, screen_id)
            if row is None:
                return None
            return SavedScreen.model_validate(json.loads(row.payload))

    def save(self, screen: SavedScreen) -> SavedScreen:
        with self.database.session_scope() as session:
            session.merge(
                SavedScreenRecord(
                    id=screen.id,
                    name=screen.name,
                    owner_scope=screen.owner_scope,
                    payload=json.dumps(screen.model_dump(mode="json")),
                )
            )
        return screen


class ScenarioRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[ScenarioDefinition]:
        with self.database.session_scope() as session:
            rows = session.execute(select(ScenarioRecord).order_by(ScenarioRecord.id.asc())).scalars().all()
            return [ScenarioDefinition.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, scenario_id: str) -> ScenarioDefinition | None:
        with self.database.session_scope() as session:
            row = session.get(ScenarioRecord, scenario_id)
            if row is None:
                return None
            return ScenarioDefinition.model_validate(json.loads(row.payload))

    def save(self, scenario: ScenarioDefinition) -> ScenarioDefinition:
        with self.database.session_scope() as session:
            session.merge(
                ScenarioRecord(
                    id=scenario.id,
                    name=scenario.name,
                    owner_scope=scenario.owner_scope,
                    payload=json.dumps(scenario.model_dump(mode="json")),
                )
            )
        return scenario


class ModelPortfolioRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[ModelPortfolio]:
        with self.database.session_scope() as session:
            rows = session.execute(select(ModelPortfolioRecord).order_by(ModelPortfolioRecord.id.asc())).scalars().all()
            return [ModelPortfolio.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, portfolio_id: str) -> ModelPortfolio | None:
        with self.database.session_scope() as session:
            row = session.get(ModelPortfolioRecord, portfolio_id)
            if row is None:
                return None
            return ModelPortfolio.model_validate(json.loads(row.payload))

    def save(self, portfolio: ModelPortfolio) -> ModelPortfolio:
        with self.database.session_scope() as session:
            session.merge(
                ModelPortfolioRecord(
                    id=portfolio.id,
                    name=portfolio.name,
                    owner_scope=portfolio.owner_scope,
                    payload=json.dumps(portfolio.model_dump(mode="json")),
                )
            )
        return portfolio


class ReportTemplateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[ReportTemplate]:
        with self.database.session_scope() as session:
            rows = session.execute(select(ReportTemplateRecord).order_by(ReportTemplateRecord.id.asc())).scalars().all()
            return [ReportTemplate.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, template_id: str) -> ReportTemplate | None:
        with self.database.session_scope() as session:
            row = session.get(ReportTemplateRecord, template_id)
            if row is None:
                return None
            return ReportTemplate.model_validate(json.loads(row.payload))

    def save(self, template: ReportTemplate) -> ReportTemplate:
        with self.database.session_scope() as session:
            session.merge(
                ReportTemplateRecord(
                    id=template.id,
                    name=template.name,
                    owner_scope=template.owner_scope,
                    payload=json.dumps(template.model_dump(mode="json")),
                )
            )
        return template


class ReportSnapshotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[ReportSnapshot]:
        with self.database.session_scope() as session:
            rows = session.execute(select(ReportSnapshotRecord).order_by(ReportSnapshotRecord.id.asc())).scalars().all()
            return [ReportSnapshot.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, snapshot_id: str) -> ReportSnapshot | None:
        with self.database.session_scope() as session:
            row = session.get(ReportSnapshotRecord, snapshot_id)
            if row is None:
                return None
            return ReportSnapshot.model_validate(json.loads(row.payload))

    def save(self, snapshot: ReportSnapshot) -> ReportSnapshot:
        with self.database.session_scope() as session:
            session.merge(
                ReportSnapshotRecord(
                    id=snapshot.id,
                    name=snapshot.name,
                    template_id=snapshot.template_id,
                    payload=json.dumps(snapshot.model_dump(mode="json")),
                )
            )
        return snapshot


class ReportJobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self) -> list[ReportJob]:
        with self.database.session_scope() as session:
            rows = session.execute(select(ReportJobRecord).order_by(ReportJobRecord.id.asc())).scalars().all()
            return [ReportJob.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, job_id: str) -> ReportJob | None:
        with self.database.session_scope() as session:
            row = session.get(ReportJobRecord, job_id)
            if row is None:
                return None
            return ReportJob.model_validate(json.loads(row.payload))

    def save(self, job: ReportJob) -> ReportJob:
        with self.database.session_scope() as session:
            session.merge(
                ReportJobRecord(
                    id=job.id,
                    name=job.name,
                    template_id=job.template_id,
                    owner_scope=job.owner_scope,
                    active="true" if job.active else "false",
                    payload=json.dumps(job.model_dump(mode="json")),
                )
            )
        return job


class ReportJobRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, job_id: str | None = None, limit: int = 50) -> list[ReportJobRun]:
        with self.database.session_scope() as session:
            statement = select(ReportJobRunRecord).order_by(ReportJobRunRecord.started_at.desc())
            if job_id:
                statement = statement.where(ReportJobRunRecord.job_id == job_id)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [ReportJobRun.model_validate(json.loads(row.payload)) for row in rows]

    def save(self, run: ReportJobRun) -> ReportJobRun:
        with self.database.session_scope() as session:
            session.merge(
                ReportJobRunRecord(
                    id=run.id,
                    job_id=run.job_id,
                    template_id=run.template_id,
                    status=run.status,
                    trigger=run.trigger,
                    started_at=run.started_at,
                    payload=json.dumps(run.model_dump(mode="json")),
                )
            )
        return run
