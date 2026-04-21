from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import delete, select

from macro_platform.domain.models import (
    AssetPrice,
    ChangeAlertEvent,
    ChangeAlertRule,
    CrossCountryPreset,
    DashboardConfig,
    ModelPortfolio,
    NotificationChannel,
    NotificationDigest,
    NotificationDelivery,
    NotificationRoutingAudit,
    OpsIncident,
    Observation,
    ReleaseFreshnessSnapshot,
    ReportJob,
    ReportJobRun,
    ReportSnapshot,
    ReportTemplate,
    SavedScreen,
    ScenarioDefinition,
    SeriesDefinition,
    SourceHealth,
    SourceHealthPolicy,
    SourceHealthPolicyRun,
    SourceHealthPolicyVersion,
    SourceHealthPolicyVersionPreset,
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
    NotificationDigestRecord,
    NotificationDeliveryRecord,
    NotificationRoutingAuditRecord,
    OpsIncidentRecord,
    ObservationRecord,
    ReportJobRecord,
    ReportJobRunRecord,
    ReportSnapshotRecord,
    ReportTemplateRecord,
    ReleaseFreshnessSnapshotRecord,
    SavedScreenRecord,
    CrossCountryPresetRecord,
    ScenarioRecord,
    SeriesDefinitionRecord,
    SourceHealthRecord,
    SourceHealthPolicyRecord,
    SourceHealthPolicyRunRecord,
    SourceHealthPolicyVersionRecord,
    SourceHealthPolicyVersionPresetRecord,
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

    def list_saved(self, owner_scope: str | None = None) -> list[DashboardConfig]:
        with self.database.session_scope() as session:
            statement = select(DashboardRecord).order_by(DashboardRecord.id.asc())
            if owner_scope:
                statement = statement.where(DashboardRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, dashboard_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(DashboardRecord).where(DashboardRecord.id == dashboard_id))


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

    def list_saved(self, owner_scope: str | None = None) -> list[ChangeAlertRule]:
        with self.database.session_scope() as session:
            statement = select(ChangeAlertRuleRecord).order_by(ChangeAlertRuleRecord.id.asc())
            if owner_scope:
                statement = statement.where(ChangeAlertRuleRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, rule_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(ChangeAlertRuleRecord).where(ChangeAlertRuleRecord.id == rule_id))


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

    def list_saved(self, active_only: bool = False, owner_scope: str | None = None) -> list[NotificationChannel]:
        with self.database.session_scope() as session:
            statement = select(NotificationChannelRecord).order_by(NotificationChannelRecord.id.asc())
            if active_only:
                statement = statement.where(NotificationChannelRecord.active == "true")
            if owner_scope:
                statement = statement.where(NotificationChannelRecord.owner_scope == owner_scope)
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

    def delete(self, channel_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(NotificationChannelRecord).where(NotificationChannelRecord.id == channel_id))


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

    def list_for_related(
        self,
        channel_id: str,
        event_type: str,
        related_id: str,
        limit: int = 20,
    ) -> list[NotificationDelivery]:
        rows = self.list_saved(channel_id=channel_id, event_type=event_type, limit=limit)
        return [row for row in rows if row.related_id == related_id]


class NotificationRoutingAuditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        channel_id: str | None = None,
        event_type: str | None = None,
        decision: str | None = None,
        limit: int = 200,
    ) -> list[NotificationRoutingAudit]:
        with self.database.session_scope() as session:
            statement = select(NotificationRoutingAuditRecord).order_by(NotificationRoutingAuditRecord.created_at.desc())
            if channel_id:
                statement = statement.where(NotificationRoutingAuditRecord.channel_id == channel_id)
            if event_type:
                statement = statement.where(NotificationRoutingAuditRecord.event_type == event_type)
            if decision:
                statement = statement.where(NotificationRoutingAuditRecord.decision == decision)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [NotificationRoutingAudit.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, audit_id: str) -> NotificationRoutingAudit | None:
        with self.database.session_scope() as session:
            row = session.get(NotificationRoutingAuditRecord, audit_id)
            if row is None:
                return None
            return NotificationRoutingAudit.model_validate(json.loads(row.payload))

    def save(self, audit: NotificationRoutingAudit) -> NotificationRoutingAudit:
        with self.database.session_scope() as session:
            session.merge(
                NotificationRoutingAuditRecord(
                    id=audit.id,
                    channel_id=audit.channel_id,
                    event_type=audit.event_type,
                    decision=audit.decision,
                    created_at=audit.created_at,
                    payload=json.dumps(audit.model_dump(mode="json")),
                )
            )
        return audit


class OpsIncidentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        status: str | None = None,
        source_channel_id: str | None = None,
        limit: int = 200,
    ) -> list[OpsIncident]:
        with self.database.session_scope() as session:
            statement = select(OpsIncidentRecord).order_by(OpsIncidentRecord.updated_at.desc())
            if status:
                statement = statement.where(OpsIncidentRecord.status == status)
            if source_channel_id:
                statement = statement.where(OpsIncidentRecord.source_channel_id == source_channel_id)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [OpsIncident.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, incident_id: str) -> OpsIncident | None:
        with self.database.session_scope() as session:
            row = session.get(OpsIncidentRecord, incident_id)
            if row is None:
                return None
            return OpsIncident.model_validate(json.loads(row.payload))

    def save(self, incident: OpsIncident) -> OpsIncident:
        with self.database.session_scope() as session:
            session.merge(
                OpsIncidentRecord(
                    id=incident.id,
                    source_channel_id=incident.source_channel_id,
                    status=incident.status,
                    severity=incident.severity,
                    updated_at=incident.updated_at,
                    payload=json.dumps(incident.model_dump(mode="json")),
                )
            )
        return incident


class SourceHealthRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        source_kind: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[SourceHealth]:
        with self.database.session_scope() as session:
            statement = select(SourceHealthRecord).order_by(SourceHealthRecord.last_checked_at.desc())
            if source_kind:
                statement = statement.where(SourceHealthRecord.source_kind == source_kind)
            if status:
                statement = statement.where(SourceHealthRecord.status == status)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [SourceHealth.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, source_id: str) -> SourceHealth | None:
        with self.database.session_scope() as session:
            row = session.get(SourceHealthRecord, source_id)
            if row is None:
                return None
            return SourceHealth.model_validate(json.loads(row.payload))

    def save(self, source_health: SourceHealth) -> SourceHealth:
        with self.database.session_scope() as session:
            session.merge(
                SourceHealthRecord(
                    id=source_health.id,
                    source_kind=source_health.source_kind,
                    status=source_health.status,
                    last_checked_at=source_health.last_checked_at,
                    payload=json.dumps(source_health.model_dump(mode="json")),
                )
            )
        return source_health


class SourceHealthPolicyRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, active_only: bool = False, limit: int = 200) -> list[SourceHealthPolicy]:
        with self.database.session_scope() as session:
            statement = select(SourceHealthPolicyRecord).order_by(SourceHealthPolicyRecord.updated_at.desc())
            if active_only:
                statement = statement.where(SourceHealthPolicyRecord.active == "true")
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [SourceHealthPolicy.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, policy_id: str) -> SourceHealthPolicy | None:
        with self.database.session_scope() as session:
            row = session.get(SourceHealthPolicyRecord, policy_id)
            if row is None:
                return None
            return SourceHealthPolicy.model_validate(json.loads(row.payload))

    def save(self, policy: SourceHealthPolicy) -> SourceHealthPolicy:
        with self.database.session_scope() as session:
            session.merge(
                SourceHealthPolicyRecord(
                    id=policy.id,
                    active="true" if policy.active else "false",
                    updated_at=datetime.now(),
                    payload=json.dumps(policy.model_dump(mode="json")),
                )
            )
        return policy


class SourceHealthPolicyRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, trigger: str | None = None, limit: int = 100) -> list[SourceHealthPolicyRun]:
        with self.database.session_scope() as session:
            statement = select(SourceHealthPolicyRunRecord).order_by(SourceHealthPolicyRunRecord.started_at.desc())
            if trigger:
                statement = statement.where(SourceHealthPolicyRunRecord.trigger == trigger)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [SourceHealthPolicyRun.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, run_id: str) -> SourceHealthPolicyRun | None:
        with self.database.session_scope() as session:
            row = session.get(SourceHealthPolicyRunRecord, run_id)
            if row is None:
                return None
            return SourceHealthPolicyRun.model_validate(json.loads(row.payload))

    def save(self, run: SourceHealthPolicyRun) -> SourceHealthPolicyRun:
        with self.database.session_scope() as session:
            session.merge(
                SourceHealthPolicyRunRecord(
                    id=run.id,
                    trigger=run.trigger,
                    started_at=run.started_at,
                    payload=json.dumps(run.model_dump(mode="json")),
                )
            )
        return run


class SourceHealthPolicyVersionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, policy_id: str, limit: int = 50) -> list[SourceHealthPolicyVersion]:
        with self.database.session_scope() as session:
            statement = (
                select(SourceHealthPolicyVersionRecord)
                .where(SourceHealthPolicyVersionRecord.policy_id == policy_id)
                .order_by(SourceHealthPolicyVersionRecord.version_number.desc())
                .limit(limit)
            )
            rows = session.execute(statement).scalars().all()
            return [SourceHealthPolicyVersion.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, version_id: str) -> SourceHealthPolicyVersion | None:
        with self.database.session_scope() as session:
            row = session.get(SourceHealthPolicyVersionRecord, version_id)
            if row is None:
                return None
            return SourceHealthPolicyVersion.model_validate(json.loads(row.payload))

    def save(self, version: SourceHealthPolicyVersion) -> SourceHealthPolicyVersion:
        with self.database.session_scope() as session:
            session.merge(
                SourceHealthPolicyVersionRecord(
                    id=version.id,
                    policy_id=version.policy_id,
                    version_number=version.version_number,
                    changed_at=version.changed_at,
                    payload=json.dumps(version.model_dump(mode="json")),
                )
            )
        return version


class SourceHealthPolicyVersionPresetRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, policy_id: str, limit: int = 50) -> list[SourceHealthPolicyVersionPreset]:
        with self.database.session_scope() as session:
            statement = (
                select(SourceHealthPolicyVersionPresetRecord)
                .where(SourceHealthPolicyVersionPresetRecord.policy_id == policy_id)
                .order_by(SourceHealthPolicyVersionPresetRecord.name.asc())
                .limit(limit)
            )
            rows = session.execute(statement).scalars().all()
            return [SourceHealthPolicyVersionPreset.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, preset_id: str) -> SourceHealthPolicyVersionPreset | None:
        with self.database.session_scope() as session:
            row = session.get(SourceHealthPolicyVersionPresetRecord, preset_id)
            if row is None:
                return None
            return SourceHealthPolicyVersionPreset.model_validate(json.loads(row.payload))

    def save(self, preset: SourceHealthPolicyVersionPreset) -> SourceHealthPolicyVersionPreset:
        with self.database.session_scope() as session:
            session.merge(
                SourceHealthPolicyVersionPresetRecord(
                    id=preset.id,
                    policy_id=preset.policy_id,
                    name=preset.name,
                    payload=json.dumps(preset.model_dump(mode="json")),
                )
            )
        return preset

    def delete(self, preset_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(SourceHealthPolicyVersionPresetRecord).where(SourceHealthPolicyVersionPresetRecord.id == preset_id))


class NotificationDigestRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        channel_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[NotificationDigest]:
        with self.database.session_scope() as session:
            statement = select(NotificationDigestRecord).order_by(NotificationDigestRecord.triggered_at.desc())
            if channel_id:
                statement = statement.where(NotificationDigestRecord.channel_id == channel_id)
            if status:
                statement = statement.where(NotificationDigestRecord.status == status)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [NotificationDigest.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, digest_id: str) -> NotificationDigest | None:
        with self.database.session_scope() as session:
            row = session.get(NotificationDigestRecord, digest_id)
            if row is None:
                return None
            return NotificationDigest.model_validate(json.loads(row.payload))

    def save(self, digest: NotificationDigest) -> NotificationDigest:
        with self.database.session_scope() as session:
            session.merge(
                NotificationDigestRecord(
                    id=digest.id,
                    channel_id=digest.channel_id,
                    status=digest.status,
                    triggered_at=digest.triggered_at,
                    payload=json.dumps(digest.model_dump(mode="json")),
                )
            )
        return digest


class WatchlistRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, owner_scope: str | None = None) -> list[Watchlist]:
        with self.database.session_scope() as session:
            statement = select(WatchlistRecord).order_by(WatchlistRecord.id.asc())
            if owner_scope:
                statement = statement.where(WatchlistRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, watchlist_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(WatchlistRecord).where(WatchlistRecord.id == watchlist_id))


class SavedScreenRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, owner_scope: str | None = None) -> list[SavedScreen]:
        with self.database.session_scope() as session:
            statement = select(SavedScreenRecord).order_by(SavedScreenRecord.id.asc())
            if owner_scope:
                statement = statement.where(SavedScreenRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, screen_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(SavedScreenRecord).where(SavedScreenRecord.id == screen_id))


class CrossCountryPresetRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, owner_scope: str | None = None) -> list[CrossCountryPreset]:
        with self.database.session_scope() as session:
            statement = select(CrossCountryPresetRecord).order_by(CrossCountryPresetRecord.id.asc())
            if owner_scope:
                statement = statement.where(CrossCountryPresetRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
            return [CrossCountryPreset.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, preset_id: str) -> CrossCountryPreset | None:
        with self.database.session_scope() as session:
            row = session.get(CrossCountryPresetRecord, preset_id)
            if row is None:
                return None
            return CrossCountryPreset.model_validate(json.loads(row.payload))

    def save(self, preset: CrossCountryPreset) -> CrossCountryPreset:
        with self.database.session_scope() as session:
            session.merge(
                CrossCountryPresetRecord(
                    id=preset.id,
                    name=preset.name,
                    owner_scope=preset.owner_scope,
                    payload=json.dumps(preset.model_dump(mode="json")),
                )
            )
        return preset

    def delete(self, preset_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(CrossCountryPresetRecord).where(CrossCountryPresetRecord.id == preset_id))


class ScenarioRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, owner_scope: str | None = None) -> list[ScenarioDefinition]:
        with self.database.session_scope() as session:
            statement = select(ScenarioRecord).order_by(ScenarioRecord.id.asc())
            if owner_scope:
                statement = statement.where(ScenarioRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, scenario_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(ScenarioRecord).where(ScenarioRecord.id == scenario_id))


class ModelPortfolioRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, owner_scope: str | None = None) -> list[ModelPortfolio]:
        with self.database.session_scope() as session:
            statement = select(ModelPortfolioRecord).order_by(ModelPortfolioRecord.id.asc())
            if owner_scope:
                statement = statement.where(ModelPortfolioRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, portfolio_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(ModelPortfolioRecord).where(ModelPortfolioRecord.id == portfolio_id))


class ReportTemplateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(self, owner_scope: str | None = None) -> list[ReportTemplate]:
        with self.database.session_scope() as session:
            statement = select(ReportTemplateRecord).order_by(ReportTemplateRecord.id.asc())
            if owner_scope:
                statement = statement.where(ReportTemplateRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, template_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(ReportTemplateRecord).where(ReportTemplateRecord.id == template_id))


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

    def list_saved(self, owner_scope: str | None = None) -> list[ReportJob]:
        with self.database.session_scope() as session:
            statement = select(ReportJobRecord).order_by(ReportJobRecord.id.asc())
            if owner_scope:
                statement = statement.where(ReportJobRecord.owner_scope == owner_scope)
            rows = session.execute(statement).scalars().all()
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

    def delete(self, job_id: str) -> None:
        with self.database.session_scope() as session:
            session.execute(delete(ReportJobRecord).where(ReportJobRecord.id == job_id))


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


class ReleaseFreshnessSnapshotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_saved(
        self,
        country: str | None = None,
        topic: str | None = None,
        limit: int = 50,
    ) -> list[ReleaseFreshnessSnapshot]:
        with self.database.session_scope() as session:
            statement = select(ReleaseFreshnessSnapshotRecord).order_by(ReleaseFreshnessSnapshotRecord.captured_at.desc())
            if country:
                statement = statement.where(ReleaseFreshnessSnapshotRecord.country == country)
            if topic:
                statement = statement.where(ReleaseFreshnessSnapshotRecord.topic == topic)
            statement = statement.limit(limit)
            rows = session.execute(statement).scalars().all()
            return [ReleaseFreshnessSnapshot.model_validate(json.loads(row.payload)) for row in rows]

    def get(self, snapshot_id: str) -> ReleaseFreshnessSnapshot | None:
        with self.database.session_scope() as session:
            row = session.get(ReleaseFreshnessSnapshotRecord, snapshot_id)
            if row is None:
                return None
            return ReleaseFreshnessSnapshot.model_validate(json.loads(row.payload))

    def save(self, snapshot: ReleaseFreshnessSnapshot) -> ReleaseFreshnessSnapshot:
        with self.database.session_scope() as session:
            session.merge(
                ReleaseFreshnessSnapshotRecord(
                    id=snapshot.id,
                    captured_at=snapshot.captured_at,
                    country=snapshot.country,
                    topic=snapshot.topic,
                    payload=json.dumps(snapshot.model_dump(mode="json")),
                )
            )
        return snapshot
