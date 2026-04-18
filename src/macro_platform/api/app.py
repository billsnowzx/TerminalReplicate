from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from macro_platform.config import settings
from macro_platform.domain.models import (
    ChangeAlertRule,
    DashboardConfig,
    ModelPortfolio,
    NotificationChannel,
    ObservationQuery,
    ReportJob,
    ReportJobRun,
    ReportTemplate,
    SavedScreen,
    ScenarioDefinition,
    ScreenSpec,
    SourceHealthPolicy,
    SourceHealthPolicyVersionPreset,
    SourceHealthPolicyVersionPresetImportRequest,
    Watchlist,
)
from macro_platform.services.platform import PlatformService
from macro_platform.services.report_scheduler import ReportScheduler


service = PlatformService()
scheduler = ReportScheduler(service)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.enable_report_scheduler:
        scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()

app = FastAPI(title="Macro Platform API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/series/search")
def search_series(
    q: str = "",
    domain: str | None = None,
    country: str | None = None,
    frequency: str | None = None,
):
    return [item.model_dump(mode="json") for item in service.search_series(q, domain, country, frequency)]


@app.get("/api/series/{series_id:path}")
def get_series(series_id: str):
    try:
        return service.get_series(series_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Series not found.") from exc


@app.post("/api/observations/query")
def observations_query(query: ObservationQuery):
    return [item.model_dump(mode="json") for item in service.query_observations(query)]


@app.get("/api/prices/{ticker}")
def get_prices(
    ticker: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
):
    return [item.model_dump(mode="json") for item in service.get_prices(ticker, start, end)]


@app.get("/api/monitors/global")
def global_monitor():
    return service.get_global_macro_monitor()


@app.get("/api/monitors/country/{country}")
def country_monitor(country: str):
    return service.get_country_snapshot(country)


@app.get("/api/markets/monitor")
def cross_asset_monitor():
    return service.get_cross_asset_monitor()


@app.get("/api/regimes")
def regimes():
    return service.get_regime_snapshot()


@app.get("/api/calendar/releases")
def release_calendar(country: str | None = None, days: int = 60):
    return [item.model_dump(mode="json") for item in service.get_release_calendar(country=country, days=days)]


@app.get("/api/status/freshness")
def freshness_status(country: str | None = None, topic: str | None = None):
    return [item.model_dump(mode="json") for item in service.get_freshness_status(country=country, topic=topic)]


@app.get("/api/status/sources")
def source_health_status(source_kind: str | None = None, status: str | None = None, limit: int = 200):
    return [
        item.model_dump(mode="json")
        for item in service.list_source_health(source_kind=source_kind, status=status, limit=limit)
    ]


@app.get("/api/status/sources/summary")
def source_health_summary():
    return service.get_source_health_summary()


@app.post("/api/status/sources/{source_id:path}/threshold")
def set_source_stale_threshold(source_id: str, minutes: int = Query(ge=1)):
    try:
        return service.set_source_stale_threshold(source_id=source_id, minutes=minutes).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status/sources/policies")
def list_source_health_policies(active_only: bool = False, include_archived: bool = False, limit: int = 200):
    return [
        item.model_dump(mode="json")
        for item in service.list_source_health_policies(
            active_only=active_only,
            include_archived=include_archived,
            limit=limit,
        )
    ]


@app.get("/api/status/sources/policies/compare-versions")
def compare_source_health_policy_versions(left_version_id: str, right_version_id: str):
    try:
        return service.compare_source_health_policy_versions(
            left_version_id=left_version_id,
            right_version_id=right_version_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status/sources/policies/runs")
def list_source_health_policy_runs(trigger: str | None = None, limit: int = 100):
    return [item.model_dump(mode="json") for item in service.list_source_health_policy_runs(trigger=trigger, limit=limit)]


@app.get("/api/status/sources/policies/runs/{run_id}")
def get_source_health_policy_run(run_id: str):
    try:
        return service.get_source_health_policy_run(run_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy run not found.") from exc


@app.get("/api/status/sources/policies/{policy_id}")
def get_source_health_policy(policy_id: str):
    try:
        return service.get_source_health_policy(policy_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc


@app.get("/api/status/sources/policies/{policy_id}/versions")
def list_source_health_policy_versions(policy_id: str, limit: int = 50, action: str | None = None, query: str | None = None):
    try:
        return [
            item.model_dump(mode="json")
            for item in service.list_source_health_policy_versions(policy_id=policy_id, limit=limit, action=action, query=query)
        ]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc


@app.get("/api/status/sources/policies/{policy_id}/version-presets")
def list_source_health_policy_version_presets(
    policy_id: str,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "name",
    order: str = "asc",
    query: str | None = None,
    only_default: bool = False,
):
    try:
        return [
            item.model_dump(mode="json")
            for item in service.list_source_health_policy_version_presets(
                policy_id=policy_id,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                order=order,
                query=query,
                only_default=only_default,
            )
        ]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status/sources/policies/version-presets/{preset_id}")
def get_source_health_policy_version_preset(preset_id: str):
    try:
        return service.get_source_health_policy_version_preset(preset_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version preset not found.") from exc


@app.get("/api/status/sources/policies/version-presets/{preset_id}/versions")
def list_source_health_policy_versions_by_preset(preset_id: str):
    try:
        return [
            item.model_dump(mode="json")
            for item in service.list_source_health_policy_versions_by_preset(preset_id=preset_id)
        ]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version preset not found.") from exc


@app.post("/api/status/sources/policies/version-presets")
def save_source_health_policy_version_preset(preset: SourceHealthPolicyVersionPreset):
    try:
        return service.save_source_health_policy_version_preset(preset).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/status/sources/policies/version-presets/{preset_id}")
def delete_source_health_policy_version_preset(preset_id: str):
    try:
        service.delete_source_health_policy_version_preset(preset_id)
        return {"status": "deleted", "id": preset_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version preset not found.") from exc


@app.post("/api/status/sources/policies/version-presets/{preset_id}/clone")
def clone_source_health_policy_version_preset(preset_id: str, name: str | None = None):
    try:
        return service.clone_source_health_policy_version_preset(preset_id, name=name).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version preset not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/status/sources/policies/version-presets/{preset_id}/set-default")
def set_default_source_health_policy_version_preset(preset_id: str):
    try:
        return service.set_default_source_health_policy_version_preset(preset_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version preset not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/status/sources/policies/version-presets/{preset_id}/rename")
def rename_source_health_policy_version_preset(preset_id: str, name: str):
    try:
        return service.rename_source_health_policy_version_preset(preset_id=preset_id, name=name).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version preset not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status/sources/policies/{policy_id}/version-presets/export")
def export_source_health_policy_version_presets(policy_id: str):
    try:
        return service.export_source_health_policy_version_presets(policy_id=policy_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc


@app.post("/api/status/sources/policies/{policy_id}/version-presets/import")
def import_source_health_policy_version_presets(policy_id: str, request: SourceHealthPolicyVersionPresetImportRequest):
    try:
        return [
            item.model_dump(mode="json")
            for item in service.import_source_health_policy_version_presets(policy_id=policy_id, request=request)
        ]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/status/sources/policies/{policy_id}/version-presets/import/preview")
def preview_source_health_policy_version_presets_import(policy_id: str, request: SourceHealthPolicyVersionPresetImportRequest):
    try:
        return service.preview_source_health_policy_version_presets_import(policy_id=policy_id, request=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc


@app.get("/api/status/sources/policies/versions/{version_id}")
def get_source_health_policy_version(version_id: str):
    try:
        return service.get_source_health_policy_version(version_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version not found.") from exc


@app.post("/api/status/sources/policies/versions/{version_id}/rollback")
def rollback_source_health_policy_version(version_id: str):
    try:
        return service.rollback_source_health_policy_version(version_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy version not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/status/sources/policies")
def save_source_health_policy(policy: SourceHealthPolicy):
    try:
        return service.save_source_health_policy(policy).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification channel not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/status/sources/policies/{policy_id}/archive")
def archive_source_health_policy(policy_id: str, reason: str | None = None):
    try:
        return service.archive_source_health_policy(policy_id=policy_id, reason=reason).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc


@app.post("/api/status/sources/policies/{policy_id}/restore")
def restore_source_health_policy(policy_id: str):
    try:
        return service.restore_source_health_policy(policy_id=policy_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source health policy not found.") from exc


@app.post("/api/status/sources/policies/run")
def run_source_health_policies():
    return service.run_source_health_policies()


@app.get("/api/monitors/changes")
def change_monitor(
    country: str | None = None,
    topic: str | None = None,
    asset_class: str | None = None,
    limit: int = 25,
):
    return [item.model_dump(mode="json") for item in service.get_change_monitor(country=country, topic=topic, asset_class=asset_class, limit=limit)]


@app.get("/api/alerts/rules")
def list_change_alert_rules():
    return [item.model_dump(mode="json") for item in service.list_change_alert_rules()]


@app.get("/api/alerts/rules/{rule_id}")
def get_change_alert_rule(rule_id: str):
    try:
        return service.get_change_alert_rule(rule_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Alert rule not found.") from exc


@app.post("/api/alerts/rules")
def save_change_alert_rule(rule: ChangeAlertRule):
    try:
        return service.save_change_alert_rule(rule).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Alert rule dependency not found.") from exc


@app.get("/api/alerts/events")
def list_change_alert_events(rule_id: str | None = None, status: str | None = None, limit: int = 100):
    return [item.model_dump(mode="json") for item in service.list_change_alert_events(rule_id=rule_id, status=status, limit=limit)]


@app.post("/api/alerts/scan")
def run_change_alert_scan(rule_id: str | None = None):
    try:
        return [item.model_dump(mode="json") for item in service.run_change_alert_scan(rule_id=rule_id)]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Alert rule not found.") from exc


@app.post("/api/alerts/events/{event_id}/status")
def update_change_alert_event_status(event_id: str, status: str = Query()):
    try:
        return service.update_change_alert_event_status(event_id, status).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Alert event not found.") from exc


@app.get("/api/notifications/channels")
def list_notification_channels(active_only: bool = False):
    return [item.model_dump(mode="json") for item in service.list_notification_channels(active_only=active_only)]


@app.get("/api/notifications/channels/{channel_id}")
def get_notification_channel(channel_id: str):
    try:
        return service.get_notification_channel(channel_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification channel not found.") from exc


@app.post("/api/notifications/channels")
def save_notification_channel(channel: NotificationChannel):
    try:
        return service.save_notification_channel(channel).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Referenced notification channel not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/notifications/channels/{channel_id}/pause")
def pause_notification_channel(channel_id: str, minutes: int = Query(default=60, ge=1), reason: str | None = None):
    try:
        return service.pause_notification_channel(channel_id, minutes=minutes, reason=reason).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification channel not found.") from exc


@app.post("/api/notifications/channels/{channel_id}/resume")
def resume_notification_channel(channel_id: str):
    try:
        return service.resume_notification_channel(channel_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification channel not found.") from exc


@app.get("/api/notifications/health")
def list_notification_channel_health(channel_id: str | None = None, window_hours: int = Query(default=24, ge=1, le=168)):
    return [
        item.model_dump(mode="json")
        for item in service.list_notification_channel_health(channel_id=channel_id, window_hours=window_hours)
    ]


@app.post("/api/notifications/channels/{channel_id}/test")
def send_test_notification(channel_id: str, subject: str | None = None):
    try:
        return service.send_test_notification(channel_id, subject=subject).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification channel not found.") from exc


@app.get("/api/notifications/deliveries")
def list_notification_deliveries(
    channel_id: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
):
    return [
        item.model_dump(mode="json")
        for item in service.list_notification_deliveries(
            channel_id=channel_id,
            event_type=event_type,
            status=status,
            limit=limit,
        )
    ]


@app.get("/api/notifications/routing")
def list_notification_routing_audits(
    channel_id: str | None = None,
    event_type: str | None = None,
    decision: str | None = None,
    limit: int = 200,
):
    return [
        item.model_dump(mode="json")
        for item in service.list_notification_routing_audits(
            channel_id=channel_id,
            event_type=event_type,
            decision=decision,
            limit=limit,
        )
    ]


@app.get("/api/notifications/routing/summary")
def get_notification_routing_summary(
    channel_id: str | None = None,
    event_type: str | None = None,
    window_hours: int = Query(default=24, ge=1, le=168),
):
    return service.get_notification_routing_summary(
        channel_id=channel_id,
        event_type=event_type,
        window_hours=window_hours,
    )


@app.post("/api/notifications/routing/export")
def export_notification_routing_audits(
    format: str = Query(default="csv"),
    channel_id: str | None = None,
    event_type: str | None = None,
    decision: str | None = None,
    limit: int = Query(default=5000, ge=1, le=20000),
):
    try:
        return service.export_notification_routing_audits(
            format=format,
            channel_id=channel_id,
            event_type=event_type,
            decision=decision,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/notifications/recovery/run")
def run_notification_channel_recovery():
    return service.run_notification_channel_recovery()


@app.get("/api/ops/incidents")
def list_ops_incidents(
    status: str | None = None,
    source_channel_id: str | None = None,
    overdue_only: bool = False,
    limit: int = 200,
):
    return [
        item.model_dump(mode="json")
        for item in service.list_ops_incidents(
            status=status,
            source_channel_id=source_channel_id,
            overdue_only=overdue_only,
            limit=limit,
        )
    ]


@app.get("/api/ops/incidents/summary")
def get_ops_incident_summary():
    return service.get_ops_incident_summary()


@app.get("/api/ops/incidents/{incident_id}")
def get_ops_incident(incident_id: str):
    try:
        return service.get_ops_incident(incident_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Ops incident not found.") from exc


@app.post("/api/ops/incidents/{incident_id}/status")
def update_ops_incident_status(incident_id: str, status: str = Query(), notes: str | None = None):
    try:
        return service.update_ops_incident_status(incident_id, status=status, notes=notes).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Ops incident not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ops/incidents/{incident_id}/update")
def update_ops_incident(
    incident_id: str,
    status: str | None = None,
    owner: str | None = None,
    priority: str | None = None,
    sla_minutes: int | None = None,
    notes: str | None = None,
):
    try:
        return service.update_ops_incident(
            incident_id=incident_id,
            status=status,
            owner=owner,
            priority=priority,
            sla_minutes=sla_minutes,
            notes=notes,
        ).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Ops incident not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/notifications/routing/{audit_id}")
def get_notification_routing_audit(audit_id: str):
    try:
        return service.get_notification_routing_audit(audit_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification routing audit not found.") from exc


@app.get("/api/notifications/deliveries/{delivery_id}")
def get_notification_delivery(delivery_id: str):
    try:
        return service.get_notification_delivery(delivery_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification delivery not found.") from exc


@app.post("/api/notifications/deliveries/{delivery_id}/retry")
def retry_notification_delivery(delivery_id: str):
    try:
        return service.retry_notification_delivery(delivery_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification delivery not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/notifications/digests")
def list_notification_digests(channel_id: str | None = None, status: str | None = None, limit: int = 100):
    return [
        item.model_dump(mode="json")
        for item in service.list_notification_digests(channel_id=channel_id, status=status, limit=limit)
    ]


@app.get("/api/notifications/digests/{digest_id}")
def get_notification_digest(digest_id: str):
    try:
        return service.get_notification_digest(digest_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification digest not found.") from exc


@app.post("/api/notifications/channels/{channel_id}/digest")
def send_notification_digest(
    channel_id: str,
    status: str = "new",
    limit: int = 25,
    publish_included: bool = False,
):
    try:
        return service.send_notification_digest(
            channel_id=channel_id,
            status=status,
            limit=limit,
            publish_included=publish_included,
        ).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Notification channel not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/notifications/digests/run-due")
def run_due_notification_digests():
    return [item.model_dump(mode="json") for item in service.run_due_notification_digests()]


@app.post("/api/screens/run")
def run_screen(spec: ScreenSpec):
    return service.run_screen(spec)


@app.get("/api/screens/saved")
def list_saved_screens():
    return [item.model_dump(mode="json") for item in service.list_saved_screens()]


@app.get("/api/screens/saved/{screen_id}")
def get_saved_screen(screen_id: str):
    try:
        return service.get_saved_screen(screen_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Saved screen not found.") from exc


@app.post("/api/screens/saved")
def save_saved_screen(screen: SavedScreen):
    return service.save_saved_screen(screen).model_dump(mode="json")


@app.get("/api/watchlists")
def list_watchlists():
    return [item.model_dump(mode="json") for item in service.list_watchlists()]


@app.get("/api/watchlists/{watchlist_id}")
def get_watchlist(watchlist_id: str):
    try:
        return service.get_watchlist(watchlist_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Watchlist not found.") from exc


@app.post("/api/watchlists")
def save_watchlist(watchlist: Watchlist):
    return service.save_watchlist(watchlist).model_dump(mode="json")


@app.get("/api/scenarios")
def list_scenarios():
    return [item.model_dump(mode="json") for item in service.list_scenarios()]


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str):
    try:
        return service.get_scenario(scenario_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Scenario not found.") from exc


@app.post("/api/scenarios")
def save_scenario(scenario: ScenarioDefinition):
    return service.save_scenario(scenario).model_dump(mode="json")


@app.get("/api/portfolios")
def list_portfolios():
    return [item.model_dump(mode="json") for item in service.list_model_portfolios()]


@app.get("/api/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: str):
    try:
        return service.get_model_portfolio(portfolio_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Portfolio not found.") from exc


@app.post("/api/portfolios")
def save_portfolio(portfolio: ModelPortfolio):
    return service.save_model_portfolio(portfolio).model_dump(mode="json")


@app.get("/api/portfolios/{portfolio_id}/summary")
def get_portfolio_summary(portfolio_id: str, scenario_id: str | None = None):
    try:
        return service.get_portfolio_summary(portfolio_id, scenario_id=scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Portfolio or scenario not found.") from exc


@app.get("/api/reports/templates")
def list_report_templates():
    return [item.model_dump(mode="json") for item in service.list_report_templates()]


@app.get("/api/reports/templates/{template_id}")
def get_report_template(template_id: str):
    try:
        return service.get_report_template(template_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report template not found.") from exc


@app.post("/api/reports/templates")
def save_report_template(template: ReportTemplate):
    return service.save_report_template(template).model_dump(mode="json")


@app.get("/api/reports/jobs")
def list_report_jobs():
    return [item.model_dump(mode="json") for item in service.list_report_jobs()]


@app.get("/api/reports/jobs/{job_id}")
def get_report_job(job_id: str):
    try:
        return service.get_report_job(job_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report job not found.") from exc


@app.post("/api/reports/jobs")
def save_report_job(job: ReportJob):
    try:
        return service.save_report_job(job).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report template not found.") from exc


@app.post("/api/reports/jobs/{job_id}/run")
def run_report_job(job_id: str):
    try:
        return service.run_report_job(job_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report job or template not found.") from exc


@app.post("/api/reports/jobs/run-due")
def run_due_report_jobs():
    return [item.model_dump(mode="json") for item in service.run_due_report_jobs()]


@app.get("/api/reports/job-runs")
def list_report_job_runs(job_id: str | None = None, limit: int = 50):
    return [item.model_dump(mode="json") for item in service.list_report_job_runs(job_id=job_id, limit=limit)]


@app.get("/api/reports/jobs/{job_id}/runs")
def list_report_job_runs_for_job(job_id: str, limit: int = 50):
    try:
        service.get_report_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report job not found.") from exc
    return [item.model_dump(mode="json") for item in service.list_report_job_runs(job_id=job_id, limit=limit)]


@app.post("/api/reports/scheduler/poll")
def poll_report_scheduler():
    return scheduler.poll_once()


@app.get("/api/reports/snapshots")
def list_report_snapshots():
    return [item.model_dump(mode="json") for item in service.list_report_snapshots()]


@app.get("/api/reports/snapshots/{snapshot_id}")
def get_report_snapshot(snapshot_id: str):
    try:
        return service.get_report_snapshot(snapshot_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report snapshot not found.") from exc


@app.get("/api/reports/snapshots/{snapshot_id}/exports")
def get_report_snapshot_exports(snapshot_id: str):
    try:
        return service.get_report_snapshot(snapshot_id).export_paths
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report snapshot not found.") from exc


@app.post("/api/reports/snapshots/{snapshot_id}/export")
def export_report_snapshot(snapshot_id: str, export_format: str = Query(alias="format")):
    try:
        return service.export_report_snapshot(snapshot_id, export_format=export_format).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report snapshot not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reports/generate/{template_id}")
def generate_report_snapshot(template_id: str, name_override: str | None = None):
    try:
        return service.generate_report_snapshot(template_id, name_override=name_override).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Report template dependency not found.") from exc


@app.get("/api/dashboards")
def list_dashboards():
    return [item.model_dump(mode="json") for item in service.list_dashboards()]


@app.get("/api/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str):
    try:
        return service.get_dashboard(dashboard_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Dashboard not found.") from exc


@app.post("/api/dashboards")
def save_dashboard(dashboard: DashboardConfig):
    return service.save_dashboard(dashboard).model_dump(mode="json")
