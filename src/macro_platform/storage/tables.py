from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from macro_platform.storage.database import Base


class SeriesDefinitionRecord(Base):
    __tablename__ = "series_definitions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(128), nullable=False)
    seasonal_adjustment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nominal_real: Mapped[str | None] = mapped_column(String(32), nullable=True)
    release_lag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_params: Mapped[dict] = mapped_column(JSON, default=dict)


class ObservationRecord(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("series_id", "observation_date", "vintage_date", name="uq_observation_point"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    vintage_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revision_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="final")


class DashboardRecord(Base):
    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    refresh_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="daily")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class AssetPriceRecord(Base):
    __tablename__ = "asset_prices"
    __table_args__ = (
        UniqueConstraint("ticker", "price_date", name="uq_asset_price_point"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    venue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    price_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    adjusted_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class ChangeAlertRuleRecord(Base):
    __tablename__ = "change_alert_rules"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    active: Mapped[str] = mapped_column(String(8), nullable=False, default="true")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ChangeAlertEventRecord(Base):
    __tablename__ = "change_alert_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class NotificationChannelRecord(Base):
    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    active: Mapped[str] = mapped_column(String(8), nullable=False, default="true")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class NotificationDeliveryRecord(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class WatchlistRecord(Base):
    __tablename__ = "watchlists"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class SavedScreenRecord(Base):
    __tablename__ = "saved_screens"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ScenarioRecord(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ModelPortfolioRecord(Base):
    __tablename__ = "model_portfolios"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ReportTemplateRecord(Base):
    __tablename__ = "report_templates"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ReportSnapshotRecord(Base):
    __tablename__ = "report_snapshots"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ReportJobRecord(Base):
    __tablename__ = "report_jobs"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    active: Mapped[str] = mapped_column(String(8), nullable=False, default="true")
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ReportJobRunRecord(Base):
    __tablename__ = "report_job_runs"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
