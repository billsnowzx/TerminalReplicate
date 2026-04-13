from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SeriesDefinition(BaseModel):
    id: str
    source: str
    source_key: str
    title: str
    topic: str
    country: str
    frequency: Literal["daily", "weekly", "monthly", "quarterly", "annual"]
    unit: str
    seasonal_adjustment: str | None = None
    nominal_real: str | None = None
    release_lag: str | None = None
    tags: list[str] = Field(default_factory=list)
    provider_params: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    series_id: str
    date: date
    value: float | None
    vintage_date: date | None = None
    revision_timestamp: datetime | None = None
    status: str = "final"


class ObservationQuery(BaseModel):
    series_id: str
    start_date: date | None = None
    end_date: date | None = None


class AssetPrice(BaseModel):
    ticker: str
    asset_class: str
    venue: str | None = None
    currency: str | None = None
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | int | None = None
    adjusted_close: float | None = None
    source: str


class ScreenFilter(BaseModel):
    field: str
    operator: Literal["gt", "gte", "lt", "lte"]
    value: float


class ScreenSpec(BaseModel):
    universe: list[str] = Field(default_factory=list)
    transforms: list[str] = Field(default_factory=lambda: ["return_63d", "drawdown"])
    filters: list[ScreenFilter] = Field(default_factory=list)
    ranking: str = "return_63d"
    as_of_date: date | None = None


class ScreenResultRow(BaseModel):
    ticker: str
    asset_class: str
    last_close: float
    return_21d: float
    return_63d: float
    drawdown: float


class DashboardWidget(BaseModel):
    kind: str
    title: str
    series_ids: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class DashboardConfig(BaseModel):
    id: str
    name: str
    widgets: list[DashboardWidget] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    owner_scope: Literal["shared", "private"] = "shared"
    refresh_policy: Literal["daily", "manual"] = "daily"


class ReleaseEvent(BaseModel):
    series_id: str
    title: str
    country: str
    source: str
    frequency: str
    last_observation_date: date | None = None
    expected_next_release: date | None = None
    freshness_status: Literal["fresh", "aging", "stale", "unknown"] = "unknown"
    release_lag: str | None = None


class ChangeSignal(BaseModel):
    entity_type: Literal["series", "asset"]
    key: str
    title: str
    topic: str
    country: str | None = None
    asset_class: str | None = None
    source: str
    observation_date: date
    current_value: float
    previous_value: float
    absolute_change: float
    percent_change: float | None = None
    direction: Literal["up", "down", "flat"] = "flat"
    significance: Literal["high", "medium", "low"] = "low"
    unit: str | None = None


class ChangeAlertRule(BaseModel):
    id: str
    name: str
    entity_type: Literal["series", "asset", "any"] = "any"
    keys: list[str] = Field(default_factory=list)
    country: str | None = None
    topic: str | None = None
    asset_class: str | None = None
    watchlist_id: str | None = None
    min_significance: Literal["high", "medium", "low"] = "medium"
    min_absolute_change: float | None = None
    min_percent_change: float | None = None
    notification_channel_ids: list[str] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    active: bool = True
    notes: str | None = None


class ChangeAlertEvent(BaseModel):
    id: str
    rule_id: str
    rule_name: str
    signal: ChangeSignal
    triggered_at: datetime
    status: Literal["new", "published", "dismissed"] = "new"
    notes: str | None = None


class NotificationChannel(BaseModel):
    id: str
    name: str
    kind: Literal["file", "email", "webhook", "slack"] = "file"
    target: str
    headers: dict[str, str] = Field(default_factory=dict)
    owner_scope: Literal["shared", "private"] = "shared"
    active: bool = True
    notes: str | None = None


class NotificationDelivery(BaseModel):
    id: str
    channel_id: str
    channel_name: str
    event_type: Literal["alert_event", "report_job", "manual"]
    related_id: str
    status: Literal["success", "failed"]
    triggered_at: datetime
    attempt_count: int = 1
    target: str
    output_path: str | None = None
    response_code: int | None = None
    error_message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Watchlist(BaseModel):
    id: str
    name: str
    tickers: list[str] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    notes: str | None = None


class SavedScreen(BaseModel):
    id: str
    name: str
    spec: ScreenSpec
    owner_scope: Literal["shared", "private"] = "shared"


class ScenarioShock(BaseModel):
    label: str
    asset_class: str | None = None
    ticker: str | None = None
    shock_pct: float


class ScenarioDefinition(BaseModel):
    id: str
    name: str
    shocks: list[ScenarioShock] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    notes: str | None = None


class PortfolioHolding(BaseModel):
    ticker: str
    weight: float
    note: str | None = None


class ModelPortfolio(BaseModel):
    id: str
    name: str
    base_currency: str = "USD"
    holdings: list[PortfolioHolding] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    notes: str | None = None


class PortfolioSummaryRow(BaseModel):
    ticker: str
    asset_class: str
    weight: float
    last_close: float | None = None
    return_21d: float | None = None
    return_63d: float | None = None
    stressed_return: float | None = None


class ReportTemplateSection(BaseModel):
    kind: Literal[
        "global_monitor",
        "cross_asset_monitor",
        "change_monitor",
        "alert_monitor",
        "release_calendar",
        "saved_screen",
        "portfolio_summary",
        "dashboard_summary",
    ]
    title: str
    ref_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ReportTemplate(BaseModel):
    id: str
    name: str
    sections: list[ReportTemplateSection] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    notes: str | None = None


class ReportSnapshotSection(BaseModel):
    kind: str
    title: str
    content: str
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ReportSnapshot(BaseModel):
    id: str
    template_id: str
    name: str
    generated_at: datetime
    sections: list[ReportSnapshotSection] = Field(default_factory=list)
    summary: str
    output_path: str | None = None
    export_paths: dict[str, str] = Field(default_factory=dict)


class ReportJob(BaseModel):
    id: str
    name: str
    template_id: str
    cadence: Literal["manual", "daily", "weekly"] = "daily"
    run_hour_local: int = 8
    run_day_of_week: int | None = None
    export_formats: list[Literal["markdown", "json", "csv_zip", "xlsx", "pptx"]] = Field(default_factory=lambda: ["markdown"])
    notification_channel_ids: list[str] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    active: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_snapshot_id: str | None = None
    last_run_status: Literal["success", "failed"] | None = None
    last_error_message: str | None = None
    consecutive_failures: int = 0


class ReportJobRun(BaseModel):
    id: str
    job_id: str
    template_id: str
    trigger: Literal["manual", "due", "worker"]
    status: Literal["success", "failed"]
    started_at: datetime
    finished_at: datetime
    snapshot_id: str | None = None
    export_formats: list[Literal["markdown", "json", "csv_zip", "xlsx", "pptx"]] = Field(default_factory=list)
    export_paths: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None
