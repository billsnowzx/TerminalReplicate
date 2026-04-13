import pytest
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from macro_platform.api import app as api_module
from macro_platform.services.platform import PlatformService
from macro_platform.services.report_scheduler import ReportScheduler


@pytest.fixture()
def client():
    db_path = Path("data") / f"test_api_{uuid4().hex}.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    api_module.service = PlatformService(database_url=database_url)
    api_module.scheduler = ReportScheduler(api_module.service, poll_seconds=1)
    try:
        with TestClient(api_module.app) as test_client:
            yield test_client
    finally:
        api_module.service.database.engine.dispose()
        db_path.unlink(missing_ok=True)
        for pattern in ("report-*.md", "report-*.json", "report-*.zip", "report-*.xlsx", "report-*.pptx", "report-*.js"):
            for report_file in Path("data/reports").glob(pattern):
                report_file.unlink(missing_ok=True)
        for notification_dir in Path("data/notifications").glob("*"):
            if notification_dir.is_dir():
                shutil.rmtree(notification_dir, ignore_errors=True)


def test_healthcheck(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_series_search_finds_us_inflation(client):
    response = client.get("/api/series/search", params={"q": "inflation"})
    assert response.status_code == 200
    payload = response.json()
    assert any(item["id"] == "fred:CPIAUCSL" for item in payload)


def test_prices_endpoint_returns_demo_data(client):
    response = client.get("/api/prices/SPY")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) > 50
    assert payload[-1]["ticker"] == "SPY"
    assert api_module.service.asset_price_repo.count_rows("SPY") > 50


def test_dashboard_persistence_round_trip(client):
    payload = {
        "id": "team-layout",
        "name": "Team Layout",
        "widgets": [{"kind": "timeseries", "title": "US CPI", "series_ids": ["fred:CPIAUCSL"], "params": {}}],
        "filters": {},
        "owner_scope": "shared",
        "refresh_policy": "daily",
    }
    response = client.post("/api/dashboards", json=payload)
    assert response.status_code == 200

    fetched = client.get("/api/dashboards/team-layout")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Team Layout"


def test_observation_query_persists_rows(client):
    response = client.post(
        "/api/observations/query",
        json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert response.status_code == 200
    assert api_module.service.observation_repo.count_rows("fred:CPIAUCSL") > 0


def test_watchlist_persistence_round_trip(client):
    payload = {
        "id": "macro-core",
        "name": "Macro Core",
        "tickers": ["SPY", "TLT", "GLD"],
        "owner_scope": "shared",
        "notes": "Core cross-asset basket",
    }
    response = client.post("/api/watchlists", json=payload)
    assert response.status_code == 200
    listing = client.get("/api/watchlists")
    fetched = client.get("/api/watchlists/macro-core")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert any(item["id"] == "macro-core" for item in listing.json())
    assert fetched.json()["tickers"] == ["SPY", "TLT", "GLD"]


def test_saved_screen_persistence_round_trip(client):
    payload = {
        "id": "momentum-screen",
        "name": "Momentum Screen",
        "owner_scope": "shared",
        "spec": {
            "universe": ["SPY", "QQQ"],
            "filters": [{"field": "return_63d", "operator": "gte", "value": 0.0}],
            "ranking": "return_63d",
        },
    }
    response = client.post("/api/screens/saved", json=payload)
    assert response.status_code == 200
    listing = client.get("/api/screens/saved")
    fetched = client.get("/api/screens/saved/momentum-screen")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert any(item["id"] == "momentum-screen" for item in listing.json())
    assert fetched.json()["spec"]["universe"] == ["SPY", "QQQ"]


def test_scenario_persistence_round_trip(client):
    payload = {
        "id": "stagflation-lite",
        "name": "Stagflation Lite",
        "owner_scope": "shared",
        "shocks": [{"label": "Rates down", "asset_class": "rates", "shock_pct": 3.0}],
        "notes": "Simple rates shock",
    }
    response = client.post("/api/scenarios", json=payload)
    assert response.status_code == 200
    listing = client.get("/api/scenarios")
    fetched = client.get("/api/scenarios/stagflation-lite")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert any(item["id"] == "stagflation-lite" for item in listing.json())
    assert fetched.json()["shocks"][0]["asset_class"] == "rates"


def test_portfolio_persistence_and_summary(client):
    portfolio_payload = {
        "id": "balanced-macro",
        "name": "Balanced Macro",
        "base_currency": "USD",
        "owner_scope": "shared",
        "holdings": [
            {"ticker": "SPY", "weight": 40.0},
            {"ticker": "TLT", "weight": 30.0},
            {"ticker": "GLD", "weight": 30.0},
        ],
    }
    scenario_payload = {
        "id": "risk-off",
        "name": "Risk Off",
        "owner_scope": "shared",
        "shocks": [{"label": "Equity shock", "asset_class": "equities", "shock_pct": -7.5}],
    }
    assert client.post("/api/portfolios", json=portfolio_payload).status_code == 200
    assert client.post("/api/scenarios", json=scenario_payload).status_code == 200
    listing = client.get("/api/portfolios")
    fetched = client.get("/api/portfolios/balanced-macro")
    summary = client.get("/api/portfolios/balanced-macro/summary", params={"scenario_id": "risk-off"})
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert summary.status_code == 200
    assert any(item["id"] == "balanced-macro" for item in listing.json())
    assert fetched.json()["holdings"][0]["ticker"] == "SPY"
    assert len(summary.json()) == 3
    assert "stressed_return" in summary.json()[0]


def test_report_template_and_snapshot_generation(client):
    screen_payload = {
        "id": "quality-screen",
        "name": "Quality Screen",
        "owner_scope": "shared",
        "spec": {
            "universe": ["SPY", "QQQ"],
            "filters": [{"field": "return_63d", "operator": "gte", "value": -100.0}],
            "ranking": "return_63d",
        },
    }
    assert client.post("/api/screens/saved", json=screen_payload).status_code == 200
    template_payload = {
        "id": "weekly-pack",
        "name": "Weekly Pack",
        "owner_scope": "shared",
        "sections": [
            {"kind": "global_monitor", "title": "Macro"},
            {"kind": "saved_screen", "title": "Screen Results", "ref_id": "quality-screen"},
        ],
    }
    response = client.post("/api/reports/templates", json=template_payload)
    assert response.status_code == 200
    generated = client.post("/api/reports/generate/weekly-pack", params={"name_override": "Weekly Pack Snapshot"})
    assert generated.status_code == 200
    snapshot = generated.json()
    listing = client.get("/api/reports/snapshots")
    fetched = client.get(f"/api/reports/snapshots/{snapshot['id']}")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert snapshot["name"] == "Weekly Pack Snapshot"
    assert len(snapshot["sections"]) == 2
    assert Path(snapshot["output_path"]).exists()
    assert Path(snapshot["export_paths"]["json"]).exists()


def test_report_snapshot_export_formats(client):
    template_payload = {
        "id": "exports-pack",
        "name": "Exports Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    generated = client.post("/api/reports/generate/exports-pack")
    assert generated.status_code == 200
    snapshot_id = generated.json()["id"]
    xlsx_export = client.post(f"/api/reports/snapshots/{snapshot_id}/export", params={"format": "xlsx"})
    zip_export = client.post(f"/api/reports/snapshots/{snapshot_id}/export", params={"format": "csv_zip"})
    pptx_export = client.post(f"/api/reports/snapshots/{snapshot_id}/export", params={"format": "pptx"})
    exports = client.get(f"/api/reports/snapshots/{snapshot_id}/exports")
    assert xlsx_export.status_code == 200
    assert zip_export.status_code == 200
    assert pptx_export.status_code == 200
    assert exports.status_code == 200
    payload = exports.json()
    assert Path(payload["xlsx"]).exists()
    assert Path(payload["csv_zip"]).exists()
    assert Path(payload["pptx"]).exists()
    assert Path(payload["pptx_js"]).exists()


def test_change_monitor_endpoint_returns_ranked_signals(client):
    response = client.get("/api/monitors/changes", params={"country": "US", "limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) > 0
    assert payload[0]["significance"] in {"high", "medium", "low"}
    assert any(item["entity_type"] == "series" for item in payload)


def test_change_monitor_report_section_generation(client):
    template_payload = {
        "id": "change-pack",
        "name": "Change Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "change_monitor", "title": "Latest Changes", "params": {"limit": 8, "topic": "inflation"}}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    generated = client.post("/api/reports/generate/change-pack")
    assert generated.status_code == 200
    payload = generated.json()
    assert payload["sections"][0]["kind"] == "change_monitor"
    assert len(payload["sections"][0]["rows"]) > 0


def test_change_alert_rule_scan_and_status_update(client):
    channel_payload = {
        "id": "alert-drop",
        "name": "Alert Drop",
        "kind": "file",
        "target": "alerts",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    watchlist_payload = {
        "id": "rates-watch",
        "name": "Rates Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "rates-alert",
        "name": "Rates Alert",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "rates-watch",
        "min_significance": "low",
        "notification_channel_ids": ["alert-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    saved = client.post("/api/alerts/rules", json=rule_payload)
    assert saved.status_code == 200
    scanned = client.post("/api/alerts/scan", params={"rule_id": "rates-alert"})
    assert scanned.status_code == 200
    events = scanned.json()
    assert len(events) > 0
    listed = client.get("/api/alerts/events", params={"rule_id": "rates-alert", "status": "new"})
    assert listed.status_code == 200
    event_id = listed.json()[0]["id"]
    updated = client.post(f"/api/alerts/events/{event_id}/status", params={"status": "published"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "published"
    deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "alert-drop", "event_type": "alert_event"})
    assert deliveries.status_code == 200
    assert len(deliveries.json()) > 0
    assert Path(deliveries.json()[0]["output_path"]).exists()
    scanned_again = client.post("/api/alerts/scan", params={"rule_id": "rates-alert"})
    assert scanned_again.status_code == 200
    assert scanned_again.json() == []


def test_alert_monitor_report_section_generation(client):
    rule_payload = {
        "id": "us-change-alert",
        "name": "US Change Alert",
        "entity_type": "series",
        "country": "US",
        "min_significance": "low",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    template_payload = {
        "id": "alert-pack",
        "name": "Alert Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "alert_monitor", "title": "Active Alerts", "params": {"rule_id": "us-change-alert", "limit": 10}}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    generated = client.post("/api/reports/generate/alert-pack")
    assert generated.status_code == 200
    payload = generated.json()
    assert payload["sections"][0]["kind"] == "alert_monitor"
    assert len(payload["sections"][0]["rows"]) > 0


def test_report_job_persistence_and_execution(client):
    channel_payload = {
        "id": "report-drop",
        "name": "Report Drop",
        "kind": "file",
        "target": "reports",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    template_payload = {
        "id": "job-pack",
        "name": "Job Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "daily-open",
        "name": "Daily Open",
        "template_id": "job-pack",
        "cadence": "daily",
        "run_hour_local": 7,
        "export_formats": ["markdown", "xlsx"],
        "notification_channel_ids": ["report-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    saved = client.post("/api/reports/jobs", json=job_payload)
    assert saved.status_code == 200
    listing = client.get("/api/reports/jobs")
    fetched = client.get("/api/reports/jobs/daily-open")
    ran = client.post("/api/reports/jobs/daily-open/run")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert ran.status_code == 200
    ran_payload = ran.json()
    assert ran_payload["last_snapshot_id"] is not None
    deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "report-drop", "event_type": "report_job"})
    assert deliveries.status_code == 200
    assert len(deliveries.json()) > 0
    assert Path(deliveries.json()[0]["output_path"]).exists()
    snapshot = client.get(f"/api/reports/snapshots/{ran_payload['last_snapshot_id']}")
    assert snapshot.status_code == 200
    assert Path(snapshot.json()["export_paths"]["xlsx"]).exists()
    runs = client.get("/api/reports/jobs/daily-open/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["status"] == "success"
    assert runs.json()[0]["trigger"] == "manual"


def test_notification_channel_persistence_round_trip(client):
    payload = {
        "id": "desk-email",
        "name": "Desk Email",
        "kind": "email",
        "target": "desk@example.com",
        "owner_scope": "shared",
        "active": True,
        "notes": "Macro desk outbox",
    }
    response = client.post("/api/notifications/channels", json=payload)
    assert response.status_code == 200
    listing = client.get("/api/notifications/channels")
    fetched = client.get("/api/notifications/channels/desk-email")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert any(item["id"] == "desk-email" for item in listing.json())
    assert fetched.json()["kind"] == "email"


def test_run_due_report_jobs_executes_overdue_active_jobs(client):
    template_payload = {
        "id": "due-pack",
        "name": "Due Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    overdue_job = {
        "id": "weekly-due",
        "name": "Weekly Due",
        "template_id": "due-pack",
        "cadence": "weekly",
        "run_hour_local": 6,
        "run_day_of_week": 0,
        "export_formats": ["json"],
        "owner_scope": "shared",
        "active": True,
        "next_run_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    assert client.post("/api/reports/jobs", json=overdue_job).status_code == 200
    due = client.post("/api/reports/jobs/run-due")
    assert due.status_code == 200
    payload = due.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "weekly-due"
    assert payload[0]["last_snapshot_id"] is not None
    runs = client.get("/api/reports/jobs/weekly-due/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["trigger"] == "due"


def test_failed_report_job_records_failure_state_and_history(client):
    template_payload = {
        "id": "broken-pack",
        "name": "Broken Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "saved_screen", "title": "Missing Screen", "ref_id": "missing-screen"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "broken-job",
        "name": "Broken Job",
        "template_id": "broken-pack",
        "cadence": "daily",
        "run_hour_local": 7,
        "export_formats": ["json"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    ran = client.post("/api/reports/jobs/broken-job/run")
    assert ran.status_code == 404
    job = client.get("/api/reports/jobs/broken-job")
    runs = client.get("/api/reports/jobs/broken-job/runs")
    assert job.status_code == 200
    assert runs.status_code == 200
    assert job.json()["last_run_status"] == "failed"
    assert job.json()["consecutive_failures"] == 1
    assert "missing-screen" in job.json()["last_error_message"]
    assert runs.json()[0]["status"] == "failed"
    assert runs.json()[0]["error_message"] is not None


def test_scheduler_poll_endpoint_returns_summary(client):
    template_payload = {
        "id": "poll-pack",
        "name": "Poll Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    overdue_job = {
        "id": "poll-job",
        "name": "Poll Job",
        "template_id": "poll-pack",
        "cadence": "weekly",
        "run_hour_local": 6,
        "run_day_of_week": 0,
        "export_formats": ["json"],
        "owner_scope": "shared",
        "active": True,
        "next_run_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    assert client.post("/api/reports/jobs", json=overdue_job).status_code == 200
    poll = client.post("/api/reports/scheduler/poll")
    assert poll.status_code == 200
    payload = poll.json()
    assert payload["attempted"] == 1
    assert payload["succeeded"] == 1
    assert payload["failed"] == 0


def test_price_cache_survives_provider_failure(client):
    initial = client.get("/api/prices/QQQ")
    assert initial.status_code == 200
    original = api_module.service.demo_market.fetch_prices
    api_module.service.demo_market.fetch_prices = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        cached = api_module.service.get_prices("QQQ")
    finally:
        api_module.service.demo_market.fetch_prices = original
    assert len(cached) > 50
    assert cached[-1].ticker == "QQQ"


def test_screen_endpoint_returns_ranked_assets(client):
    response = client.post(
        "/api/screens/run",
        json={
            "universe": ["SPY", "QQQ", "TLT"],
            "filters": [{"field": "return_63d", "operator": "gte", "value": -100.0}],
            "ranking": "return_63d",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 3
    assert "return_63d" in payload[0]


def test_calendar_and_freshness_endpoints_return_payloads(client):
    calendar = client.get("/api/calendar/releases", params={"country": "US", "days": 45})
    freshness = client.get("/api/status/freshness", params={"country": "EA"})
    assert calendar.status_code == 200
    assert freshness.status_code == 200
    assert isinstance(calendar.json(), list)
    assert isinstance(freshness.json(), list)
    assert any(item["country"] == "EA" for item in freshness.json())
