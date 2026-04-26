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


class ReleaseFreshnessSnapshot(BaseModel):
    id: str
    captured_at: datetime
    country: str | None = None
    topic: str | None = None
    days: int = 60
    rows: list[ReleaseEvent] = Field(default_factory=list)


class SourceHealth(BaseModel):
    id: str
    source_kind: Literal["macro", "market"]
    provider: str
    status: Literal["healthy", "degraded", "down", "unknown"] = "unknown"
    last_checked_at: datetime
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    fallback_used: bool = False
    stale_threshold_minutes: int = 360
    is_stale: bool = False
    last_error: str | None = None
    notes: str | None = None


class SourceHealthPolicy(BaseModel):
    id: str
    name: str
    source_kind: Literal["macro", "market"] | None = None
    source_id: str | None = None
    trigger_on_degraded: bool = True
    trigger_on_down: bool = True
    trigger_on_stale: bool = True
    min_consecutive_failures: int = 1
    stale_threshold_minutes: int | None = None
    cooldown_minutes: int = 60
    notification_channel_ids: list[str] = Field(default_factory=list)
    reason_channel_overrides: dict[str, list[str]] = Field(default_factory=dict)
    reason_severity: dict[str, Literal["high", "medium", "low"]] = Field(default_factory=dict)
    reason_subject_templates: dict[str, str] = Field(default_factory=dict)
    escalation_channel_ids: list[str] = Field(default_factory=list)
    escalation_failure_threshold: int = 3
    active_weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    active_hour_start: int = 0
    active_hour_end: int = 24
    allow_down_outside_schedule: bool = True
    timezone: str = "UTC"
    holiday_calendar: Literal["none", "us", "uk", "eu", "jp", "cn"] = "none"
    holiday_dates: list[date] = Field(default_factory=list)
    owner_scope: Literal["shared", "private"] = "shared"
    active: bool = True
    last_triggered_at: datetime | None = None
    archived_at: datetime | None = None
    archived_reason: str | None = None
    notes: str | None = None


class SourceHealthPolicyRun(BaseModel):
    id: str
    trigger: Literal["manual", "worker"]
    started_at: datetime
    finished_at: datetime
    attempted_policies: int = 0
    triggered_actions: int = 0
    skipped_actions: int = 0
    failed_actions: int = 0
    actions: list[dict[str, Any]] = Field(default_factory=list)


class SourceHealthPolicyVersion(BaseModel):
    id: str
    policy_id: str
    version_number: int
    action: Literal["create", "update", "archive", "restore", "rollback"]
    changed_at: datetime
    changed_fields: list[str] = Field(default_factory=list)
    summary: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)


class SourceHealthPolicyVersionPreset(BaseModel):
    id: str
    policy_id: str
    name: str
    action_filter: str | None = None
    query: str | None = None
    limit: int = 20
    is_default: bool = False
    owner_scope: Literal["shared", "private"] = "shared"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_used_at: datetime | None = None
    usage_count: int = 0


class SourceHealthPolicyVersionPresetImportItem(BaseModel):
    name: str
    action_filter: str | None = None
    query: str | None = None
    limit: int = 20
    is_default: bool = False
    owner_scope: Literal["shared", "private"] = "shared"


class SourceHealthPolicyVersionPresetImportRequest(BaseModel):
    mode: Literal["append", "replace", "upsert"] = "append"
    presets: list[SourceHealthPolicyVersionPresetImportItem] = Field(default_factory=list)


class SourceHealthPolicyVersionPresetExportBundle(BaseModel):
    policy_id: str
    exported_at: datetime
    presets: list[SourceHealthPolicyVersionPreset] = Field(default_factory=list)


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
    event_types: list[Literal["alert_event", "report_job", "manual"]] = Field(
        default_factory=lambda: ["alert_event", "report_job", "manual"]
    )
    min_significance: Literal["high", "medium", "low"] = "low"
    delivery_mode: Literal["immediate", "digest"] = "immediate"
    fallback_channel_ids: list[str] = Field(default_factory=list)
    escalation_channel_ids: list[str] = Field(default_factory=list)
    escalation_min_significance: Literal["high", "medium", "low"] = "high"
    cooldown_minutes: int = 0
    duplicate_window_minutes: int = 60
    retry_backoff_minutes: int = 15
    max_retry_attempts: int = 3
    paused_until: datetime | None = None
    pause_reason: str | None = None
    auto_pause_enabled: bool = False
    auto_pause_window_hours: int = 24
    auto_pause_error_rate_threshold: float = 0.5
    auto_pause_consecutive_failures: int = 3
    auto_pause_minutes: int = 60
    auto_resume_enabled: bool = False
    ops_escalation_enabled: bool = False
    ops_escalation_channel_ids: list[str] = Field(default_factory=list)
    ops_escalation_window_hours: int = 6
    ops_escalation_threshold: int = 5
    ops_escalation_cooldown_minutes: int = 60
    recovery_probe_profile: Literal["minimal", "standard", "verbose"] = "standard"
    recovery_probe_payload: dict[str, Any] = Field(default_factory=dict)
    recovery_probe_cooldown_minutes: int = 30
    recovery_probe_max_per_hour: int = 2
    last_auto_paused_at: datetime | None = None
    last_auto_resumed_at: datetime | None = None
    last_recovery_probe_at: datetime | None = None
    last_ops_escalated_at: datetime | None = None
    digest_hour_local: int = 8
    digest_limit: int = 25
    digest_status_filter: Literal["new", "published", "dismissed"] = "new"
    digest_publish_included: bool = False
    last_digest_at: datetime | None = None
    next_digest_at: datetime | None = None
    owner_scope: Literal["shared", "private"] = "shared"
    active: bool = True
    notes: str | None = None


class NotificationChannelHealth(BaseModel):
    channel_id: str
    channel_name: str
    kind: str
    active: bool
    is_paused: bool
    paused_until: datetime | None = None
    pause_reason: str | None = None
    auto_pause_enabled: bool
    auto_pause_window_hours: int
    auto_pause_error_rate_threshold: float
    auto_pause_consecutive_failures: int
    auto_pause_minutes: int
    auto_resume_enabled: bool
    ops_escalation_enabled: bool
    ops_escalation_window_hours: int
    ops_escalation_threshold: int
    ops_escalation_cooldown_minutes: int
    recovery_probe_profile: str
    recovery_probe_cooldown_minutes: int
    recovery_probe_max_per_hour: int
    last_auto_paused_at: datetime | None = None
    last_auto_resumed_at: datetime | None = None
    last_recovery_probe_at: datetime | None = None
    last_ops_escalated_at: datetime | None = None
    window_hours: int
    total_attempts: int
    success_count: int
    failed_count: int
    success_rate: float
    error_rate: float
    consecutive_failures: int
    last_delivery_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error_message: str | None = None


class NotificationRoutingAudit(BaseModel):
    id: str
    channel_id: str
    channel_name: str
    event_type: Literal["alert_event", "report_job", "manual"]
    related_id: str
    decision: Literal["delivered", "failed", "suppressed", "paused", "inactive", "rejected", "digest_deferred"]
    reason: str | None = None
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class OpsIncident(BaseModel):
    id: str
    source_channel_id: str
    source_channel_name: str
    status: Literal["open", "ack", "resolved"] = "open"
    severity: Literal["low", "medium", "high"] = "medium"
    priority: Literal["low", "medium", "high"] = "medium"
    owner: str | None = None
    opened_at: datetime
    updated_at: datetime
    last_event_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    sla_minutes: int = 240
    due_at: datetime | None = None
    window_hours: int
    threshold: int
    adverse_count: int
    latest_decision: str | None = None
    latest_reason: str | None = None
    escalation_count: int = 0
    last_escalated_at: datetime | None = None
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


class NotificationDigest(BaseModel):
    id: str
    channel_id: str
    channel_name: str
    status: Literal["success", "failed"]
    triggered_at: datetime
    event_count: int
    source_event_ids: list[str] = Field(default_factory=list)
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


class CrossCountryPreset(BaseModel):
    id: str
    name: str
    countries: list[str] = Field(default_factory=lambda: ["US", "CN", "EA", "JP", "GB", "CA"])
    factor_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "growth": 1.0,
            "inflation": 1.0,
            "labor_unemployment": 1.0,
            "policy_rate": 1.0,
            "equity_return_63d": 1.0,
        }
    )
    is_default: bool = False
    owner_scope: Literal["shared", "private"] = "shared"
    notes: str | None = None


class CrossCountryPresetImportItem(BaseModel):
    name: str
    countries: list[str] = Field(default_factory=lambda: ["US", "CN", "EA", "JP", "GB", "CA"])
    factor_weights: dict[str, float] = Field(default_factory=dict)
    is_default: bool = False
    owner_scope: Literal["shared", "private"] = "shared"
    notes: str | None = None


class CrossCountryPresetImportRequest(BaseModel):
    mode: Literal["append", "replace", "upsert"] = "append"
    presets: list[CrossCountryPresetImportItem] = Field(default_factory=list)


class CrossCountryPresetExportBundle(BaseModel):
    exported_at: datetime
    presets: list[CrossCountryPreset] = Field(default_factory=list)


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
        "source_health",
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
