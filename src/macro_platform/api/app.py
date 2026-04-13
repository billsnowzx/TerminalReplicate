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
    return service.save_notification_channel(channel).model_dump(mode="json")


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
