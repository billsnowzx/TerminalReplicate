import pytest
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from macro_platform.api import app as api_module
from macro_platform.domain.models import Observation, ReleaseEvent
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


def test_series_search_includes_imf_and_oecd_catalog_entries(client):
    response_imf = client.get("/api/series/search", params={"q": "imf"})
    response_oecd = client.get("/api/series/search", params={"q": "oecd"})
    assert response_imf.status_code == 200
    assert response_oecd.status_code == 200
    assert any(item["id"] == "imf:US:NGDP_RPCH" for item in response_imf.json())
    assert any(item["id"] == "oecd:US:LRUN64TT" for item in response_oecd.json())


def test_series_source_registry_endpoint_returns_source_coverage(client):
    response = client.get("/api/series/sources")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) > 0
    by_source = {item["source"]: item for item in payload}
    for source in ["fred", "world_bank", "imf", "oecd", "bls", "ecb"]:
        assert source in by_source
        assert by_source[source]["series_count"] >= 1
        assert "countries" in by_source[source]
        assert "topics" in by_source[source]


def test_series_source_drilldown_endpoint_filters_country_topic_and_limit(client):
    response = client.get(
        "/api/series/sources/imf/series",
        params={"country": "US", "topic": "growth", "limit": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) >= 1
    assert len(payload) <= 5
    assert all(item["source"] == "imf" for item in payload)
    assert all(item["country"] == "US" for item in payload)
    assert all(item["topic"] == "growth" for item in payload)
    assert any(item["id"] == "imf:US:NGDP_RPCH" for item in payload)


def test_series_source_drilldown_endpoint_returns_404_for_unknown_source(client):
    response = client.get("/api/series/sources/not_a_source/series")
    assert response.status_code == 404
    assert response.json()["detail"] == "Series source not found."


def test_source_health_alerts_endpoint_returns_degraded_and_stale_entries(client):
    api_module.service._record_source_health(  # noqa: SLF001
        source_id="macro:fred",
        source_kind="macro",
        provider="fred",
        status="degraded",
        last_checked_at=datetime.now(),
        last_success_at=datetime.now() - timedelta(days=3),
        last_failure_at=datetime.now(),
        fallback_used=True,
        error_message="degraded test",
    )
    response = client.get("/api/status/sources/alerts", params={"limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) >= 1
    alert = next(item for item in payload if item["source_id"] == "macro:fred")
    assert alert["status"] == "degraded"
    assert "degraded" in alert["reasons"]
    assert "stale" in alert["reasons"]
    assert alert["severity"] == "medium"


def test_normalization_status_endpoint_returns_summary_payload(client):
    response = client.get("/api/status/normalization", params={"max_series_scan": 200})
    assert response.status_code == 200
    payload = response.json()
    assert payload["series_total"] >= 1
    assert "frequency_counts" in payload
    assert "issues" in payload
    assert isinstance(payload["issues"], list)


def test_normalization_status_flags_missing_value_issue(client):
    api_module.service.observation_repo.replace_range(
        "fred:CPIAUCSL",
        [
            Observation(
                series_id="fred:CPIAUCSL",
                date=date(2024, 1, 1),
                value=None,
                vintage_date=date(2024, 1, 2),
                revision_timestamp=datetime(2024, 1, 2, 10, 0, 0),
                status="final",
            ),
            Observation(
                series_id="fred:CPIAUCSL",
                date=date(2024, 2, 1),
                value=301.0,
                vintage_date=date(2024, 2, 2),
                revision_timestamp=datetime(2024, 2, 2, 10, 0, 0, tzinfo=ZoneInfo("UTC")),
                status="final",
            ),
        ],
    )
    response = client.get("/api/status/normalization", params={"max_series_scan": 500})
    assert response.status_code == 200
    payload = response.json()
    checks = {item["check"] for item in payload["issues"]}
    assert "missing_values" in checks


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


def test_observation_query_uses_imf_provider_path(client):
    called = {"value": False}
    original = api_module.service.imf.fetch_observations

    def fake_fetch(definition, start_date, end_date):
        called["value"] = True
        return [
            Observation(
                series_id=definition.id,
                date=date(2024, 12, 31),
                value=2.5,
                status="final",
            )
        ]

    api_module.service.imf.fetch_observations = fake_fetch
    try:
        response = client.post(
            "/api/observations/query",
            json={"series_id": "imf:US:NGDP_RPCH", "start_date": "2020-01-01", "end_date": "2025-12-31"},
        )
    finally:
        api_module.service.imf.fetch_observations = original
    assert response.status_code == 200
    assert called["value"] is True
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["series_id"] == "imf:US:NGDP_RPCH"


def test_observation_query_uses_oecd_provider_path(client):
    called = {"value": False}
    original = api_module.service.oecd.fetch_observations

    def fake_fetch(definition, start_date, end_date):
        called["value"] = True
        return [
            Observation(
                series_id=definition.id,
                date=date(2024, 12, 31),
                value=4.1,
                status="final",
            )
        ]

    api_module.service.oecd.fetch_observations = fake_fetch
    try:
        response = client.post(
            "/api/observations/query",
            json={"series_id": "oecd:US:LRUN64TT", "start_date": "2020-01-01", "end_date": "2025-12-31"},
        )
    finally:
        api_module.service.oecd.fetch_observations = original
    assert response.status_code == 200
    assert called["value"] is True
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["series_id"] == "oecd:US:LRUN64TT"


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


def test_change_monitor_delta_endpoint_returns_trend_rows(client):
    response = client.get("/api/monitors/changes/delta", params={"country": "US", "limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) > 0
    first = payload[0]
    assert first["entity_type"] in {"series", "asset"}
    assert first["trend"] in {"accelerating", "decelerating", "reversing", "stable"}
    assert "delta_absolute_change" in first
    assert "previous_absolute_change" in first


def test_cross_country_monitor_endpoint_returns_scored_rows(client):
    response = client.get(
        "/api/monitors/cross-country",
        params={"countries": "US,CN,EA,JP,GB,CA", "limit": 6},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 6
    assert all("country" in row for row in payload)
    assert all("composite_score" in row for row in payload)
    scores = [float(row["composite_score"]) for row in payload]
    assert scores == sorted(scores, reverse=True)
    by_country = {row["country"]: row for row in payload}
    for country in ["US", "CN", "EA", "JP", "GB", "CA"]:
        assert country in by_country
    labor_covered = sum(1 for country in by_country if by_country[country].get("labor_unemployment") is not None)
    policy_covered = sum(1 for country in by_country if by_country[country].get("policy_rate") is not None)
    assert labor_covered >= 4
    assert policy_covered >= 4


def test_cross_country_preset_crud_and_monitor_with_preset(client):
    preset_payload = {
        "id": "cross-country-growth-tilt",
        "name": "Growth Tilt",
        "countries": ["US", "CN", "EA"],
        "factor_weights": {
            "growth": 2.0,
            "inflation": 0.5,
            "labor_unemployment": 0.5,
            "policy_rate": 0.5,
            "equity_return_63d": 1.0,
        },
        "owner_scope": "shared",
        "notes": "Lean into growth momentum",
    }
    saved = client.post("/api/monitors/cross-country/presets", json=preset_payload)
    assert saved.status_code == 200
    listing = client.get("/api/monitors/cross-country/presets")
    fetched = client.get("/api/monitors/cross-country/presets/cross-country-growth-tilt")
    assert listing.status_code == 200
    assert fetched.status_code == 200
    assert any(item["id"] == "cross-country-growth-tilt" for item in listing.json())
    monitor = client.get(
        "/api/monitors/cross-country",
        params={"preset_id": "cross-country-growth-tilt", "limit": 3},
    )
    assert monitor.status_code == 200
    payload = monitor.json()
    assert len(payload) == 3
    assert all(item["country"] in {"US", "CN", "EA"} for item in payload)
    assert all("factor_weights" in item for item in payload)
    assert payload[0]["factor_weights"]["growth"] == 2.0


def test_cross_country_preset_set_default_and_delete_reassigns_default(client):
    first_payload = {
        "id": "cross-country-default-a",
        "name": "Default A",
        "countries": ["US", "EA"],
        "factor_weights": {
            "growth": 1.0,
            "inflation": 1.0,
            "labor_unemployment": 1.0,
            "policy_rate": 1.0,
            "equity_return_63d": 1.0,
        },
        "owner_scope": "shared",
    }
    second_payload = {
        "id": "cross-country-default-b",
        "name": "Default B",
        "countries": ["US", "CN"],
        "factor_weights": {
            "growth": 2.0,
            "inflation": 0.5,
            "labor_unemployment": 1.0,
            "policy_rate": 1.0,
            "equity_return_63d": 1.5,
        },
        "owner_scope": "shared",
    }
    assert client.post("/api/monitors/cross-country/presets", json=first_payload).status_code == 200
    assert client.post("/api/monitors/cross-country/presets", json=second_payload).status_code == 200
    set_default = client.post("/api/monitors/cross-country/presets/cross-country-default-b/set-default")
    assert set_default.status_code == 200
    listing = client.get("/api/monitors/cross-country/presets")
    assert listing.status_code == 200
    by_id = {item["id"]: item for item in listing.json()}
    assert by_id["cross-country-default-b"]["is_default"] is True
    assert by_id["cross-country-default-a"]["is_default"] is False
    deleted = client.delete("/api/monitors/cross-country/presets/cross-country-default-b")
    assert deleted.status_code == 200
    listing_after = client.get("/api/monitors/cross-country/presets")
    assert listing_after.status_code == 200
    by_id_after = {item["id"]: item for item in listing_after.json()}
    assert by_id_after["cross-country-default-a"]["is_default"] is True


def test_cross_country_monitor_uses_default_preset_when_no_params(client):
    preset_payload = {
        "id": "cross-country-default-monitor",
        "name": "Default Monitor Preset",
        "countries": ["US", "CN", "EA"],
        "factor_weights": {
            "growth": 2.5,
            "inflation": 0.3,
            "labor_unemployment": 0.3,
            "policy_rate": 0.3,
            "equity_return_63d": 1.0,
        },
        "owner_scope": "shared",
        "is_default": True,
    }
    assert client.post("/api/monitors/cross-country/presets", json=preset_payload).status_code == 200
    response = client.get("/api/monitors/cross-country", params={"limit": 3})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 3
    assert all(item["country"] in {"US", "CN", "EA"} for item in payload)
    assert all(item["factor_weights"]["growth"] == 2.5 for item in payload)


def test_cross_country_preset_export_import_preview_workflow(client):
    source_payload = {
        "id": "cross-country-export-source",
        "name": "Export Source",
        "countries": ["US", "EA"],
        "factor_weights": {
            "growth": 1.2,
            "inflation": 0.8,
            "labor_unemployment": 1.0,
            "policy_rate": 1.0,
            "equity_return_63d": 1.0,
        },
        "is_default": True,
        "owner_scope": "shared",
    }
    assert client.post("/api/monitors/cross-country/presets", json=source_payload).status_code == 200
    exported = client.get("/api/monitors/cross-country/presets/export")
    assert exported.status_code == 200
    export_payload = exported.json()
    assert "exported_at" in export_payload
    assert len(export_payload["presets"]) >= 1

    preview = client.post(
        "/api/monitors/cross-country/presets/import/preview",
        json={"mode": "replace", "presets": export_payload["presets"]},
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["valid"] is True
    imported = client.post(
        "/api/monitors/cross-country/presets/import",
        json={"mode": "replace", "presets": export_payload["presets"]},
    )
    assert imported.status_code == 200
    imported_rows = imported.json()
    assert len(imported_rows) >= 1
    defaults = [item for item in imported_rows if item.get("is_default")]
    assert len(defaults) == 1


def test_cross_country_monitor_degrades_gracefully_when_single_source_outages(client):
    original_imf_fetch = api_module.service.imf.fetch_observations
    api_module.service.imf.fetch_observations = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("imf down"))
    try:
        response = client.get("/api/monitors/cross-country", params={"countries": "US,CN,EA,JP", "limit": 4})
    finally:
        api_module.service.imf.fetch_observations = original_imf_fetch
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert all("composite_score" in item for item in payload)


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


def test_notification_test_send_and_retry(client):
    channel_payload = {
        "id": "ops-drop",
        "name": "Ops Drop",
        "kind": "file",
        "target": "ops",
        "retry_backoff_minutes": 0,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    sent = client.post("/api/notifications/channels/ops-drop/test", params={"subject": "Ops Test"})
    assert sent.status_code == 200
    first_delivery = sent.json()
    assert first_delivery["status"] == "success"
    assert Path(first_delivery["output_path"]).exists()
    fetched = client.get(f"/api/notifications/deliveries/{first_delivery['id']}")
    assert fetched.status_code == 200
    retried = client.post(f"/api/notifications/deliveries/{first_delivery['id']}/retry")
    assert retried.status_code == 200
    second_delivery = retried.json()
    assert second_delivery["attempt_count"] == 2
    assert second_delivery["id"] != first_delivery["id"]
    assert Path(second_delivery["output_path"]).exists()


def test_digest_channel_batches_alert_events_and_publishes_included(client):
    channel_payload = {
        "id": "digest-drop",
        "name": "Digest Drop",
        "kind": "file",
        "target": "digest",
        "event_types": ["alert_event"],
        "delivery_mode": "digest",
        "min_significance": "low",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    watchlist_payload = {
        "id": "digest-watch",
        "name": "Digest Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "digest-rule",
        "name": "Digest Rule",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "digest-watch",
        "min_significance": "low",
        "notification_channel_ids": ["digest-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    scanned = client.post("/api/alerts/scan", params={"rule_id": "digest-rule"})
    assert scanned.status_code == 200
    deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "digest-drop", "event_type": "alert_event"})
    assert deliveries.status_code == 200
    assert deliveries.json() == []
    digest = client.post(
        "/api/notifications/channels/digest-drop/digest",
        params={"status": "new", "limit": 10, "publish_included": True},
    )
    assert digest.status_code == 200
    digest_payload = digest.json()
    assert digest_payload["event_count"] > 0
    assert Path(digest_payload["output_path"]).exists()
    listed = client.get("/api/notifications/digests", params={"channel_id": "digest-drop"})
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == digest_payload["id"]
    events = client.get("/api/alerts/events", params={"rule_id": "digest-rule", "status": "published"})
    assert events.status_code == 200
    assert len(events.json()) > 0


def test_alert_channel_min_significance_filters_low_signal_delivery(client):
    channel_payload = {
        "id": "high-only-drop",
        "name": "High Only Drop",
        "kind": "file",
        "target": "high-only",
        "event_types": ["alert_event"],
        "delivery_mode": "immediate",
        "min_significance": "high",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    watchlist_payload = {
        "id": "high-only-watch",
        "name": "High Only Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "high-only-rule",
        "name": "High Only Rule",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "high-only-watch",
        "min_significance": "low",
        "notification_channel_ids": ["high-only-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    scanned = client.post("/api/alerts/scan", params={"rule_id": "high-only-rule"})
    assert scanned.status_code == 200
    assert len(scanned.json()) > 0
    deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "high-only-drop", "event_type": "alert_event"})
    assert deliveries.status_code == 200
    assert deliveries.json() == []


def test_failed_primary_notification_routes_to_fallback_channel(client):
    fallback_payload = {
        "id": "fallback-drop",
        "name": "Fallback Drop",
        "kind": "file",
        "target": "fallback",
        "event_types": ["alert_event"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=fallback_payload).status_code == 200
    primary_payload = {
        "id": "broken-webhook",
        "name": "Broken Webhook",
        "kind": "webhook",
        "target": "http://127.0.0.1:9/notify",
        "event_types": ["alert_event"],
        "fallback_channel_ids": ["fallback-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=primary_payload).status_code == 200
    watchlist_payload = {
        "id": "fallback-watch",
        "name": "Fallback Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "fallback-rule",
        "name": "Fallback Rule",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "fallback-watch",
        "min_significance": "low",
        "notification_channel_ids": ["broken-webhook"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    scanned = client.post("/api/alerts/scan", params={"rule_id": "fallback-rule"})
    assert scanned.status_code == 200
    primary_deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "broken-webhook", "event_type": "alert_event"})
    fallback_deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "fallback-drop", "event_type": "alert_event"})
    assert primary_deliveries.status_code == 200
    assert fallback_deliveries.status_code == 200
    assert primary_deliveries.json()[0]["status"] == "failed"
    assert len(fallback_deliveries.json()) > 0
    assert fallback_deliveries.json()[0]["payload"]["fallback_from_channel_id"] == "broken-webhook"
    assert Path(fallback_deliveries.json()[0]["output_path"]).exists()


def test_alert_channel_escalates_to_secondary_destinations(client):
    escalation_payload = {
        "id": "escalation-drop",
        "name": "Escalation Drop",
        "kind": "file",
        "target": "escalation",
        "event_types": ["alert_event"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=escalation_payload).status_code == 200
    primary_payload = {
        "id": "primary-drop",
        "name": "Primary Drop",
        "kind": "file",
        "target": "primary",
        "event_types": ["alert_event"],
        "escalation_channel_ids": ["escalation-drop"],
        "escalation_min_significance": "low",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=primary_payload).status_code == 200
    watchlist_payload = {
        "id": "escalation-watch",
        "name": "Escalation Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "escalation-rule",
        "name": "Escalation Rule",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "escalation-watch",
        "min_significance": "low",
        "notification_channel_ids": ["primary-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    scanned = client.post("/api/alerts/scan", params={"rule_id": "escalation-rule"})
    assert scanned.status_code == 200
    primary_deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "primary-drop", "event_type": "alert_event"})
    escalation_deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "escalation-drop", "event_type": "alert_event"})
    assert primary_deliveries.status_code == 200
    assert escalation_deliveries.status_code == 200
    assert len(primary_deliveries.json()) > 0
    assert len(escalation_deliveries.json()) > 0
    assert escalation_deliveries.json()[0]["payload"]["escalated_from_channel_id"] == "primary-drop"
    assert Path(escalation_deliveries.json()[0]["output_path"]).exists()


def test_duplicate_suppression_skips_second_report_job_delivery(client):
    channel_payload = {
        "id": "dedupe-drop",
        "name": "Dedupe Drop",
        "kind": "file",
        "target": "dedupe",
        "event_types": ["report_job"],
        "duplicate_window_minutes": 1440,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    template_payload = {
        "id": "dedupe-pack",
        "name": "Dedupe Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "dedupe-job",
        "name": "Dedupe Job",
        "template_id": "dedupe-pack",
        "cadence": "manual",
        "run_hour_local": 8,
        "export_formats": ["json"],
        "notification_channel_ids": ["dedupe-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    assert client.post("/api/reports/jobs/dedupe-job/run").status_code == 200
    assert client.post("/api/reports/jobs/dedupe-job/run").status_code == 200
    deliveries = client.get("/api/notifications/deliveries", params={"channel_id": "dedupe-drop", "event_type": "report_job"})
    assert deliveries.status_code == 200
    assert len(deliveries.json()) == 1


def test_retry_backoff_and_max_attempts_are_enforced(client):
    channel_payload = {
        "id": "retry-guard-drop",
        "name": "Retry Guard Drop",
        "kind": "file",
        "target": "retry-guard",
        "retry_backoff_minutes": 60,
        "max_retry_attempts": 2,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    sent = client.post("/api/notifications/channels/retry-guard-drop/test", params={"subject": "Retry Guard"})
    assert sent.status_code == 200
    delivery_id = sent.json()["id"]
    backoff_retry = client.post(f"/api/notifications/deliveries/{delivery_id}/retry")
    assert backoff_retry.status_code == 400
    delivery = api_module.service.get_notification_delivery(delivery_id)
    delivery.triggered_at = delivery.triggered_at - timedelta(hours=2)
    delivery.attempt_count = 2
    api_module.service.notification_delivery_repo.save(delivery)
    max_retry = client.post(f"/api/notifications/deliveries/{delivery_id}/retry")
    assert max_retry.status_code == 400


def test_run_due_notification_digests_executes_overdue_digest_channel(client):
    channel_payload = {
        "id": "due-digest-drop",
        "name": "Due Digest Drop",
        "kind": "file",
        "target": "due-digest",
        "event_types": ["alert_event"],
        "delivery_mode": "digest",
        "min_significance": "low",
        "digest_hour_local": 8,
        "digest_limit": 10,
        "digest_status_filter": "new",
        "digest_publish_included": True,
        "next_digest_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    watchlist_payload = {
        "id": "due-digest-watch",
        "name": "Due Digest Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "due-digest-rule",
        "name": "Due Digest Rule",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "due-digest-watch",
        "min_significance": "low",
        "notification_channel_ids": ["due-digest-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    assert client.post("/api/alerts/scan", params={"rule_id": "due-digest-rule"}).status_code == 200
    due = client.post("/api/notifications/digests/run-due")
    assert due.status_code == 200
    payload = due.json()
    assert len(payload) == 1
    assert payload[0]["channel_id"] == "due-digest-drop"
    assert payload[0]["event_count"] > 0
    assert Path(payload[0]["output_path"]).exists()
    channel = client.get("/api/notifications/channels/due-digest-drop")
    assert channel.status_code == 200
    assert channel.json()["last_digest_at"] is not None
    assert channel.json()["next_digest_at"] is not None


def test_paused_digest_channel_is_skipped_by_due_digest_runner(client):
    channel_payload = {
        "id": "paused-digest-drop",
        "name": "Paused Digest Drop",
        "kind": "file",
        "target": "paused-digest",
        "event_types": ["alert_event"],
        "delivery_mode": "digest",
        "min_significance": "low",
        "digest_hour_local": 8,
        "digest_limit": 10,
        "digest_status_filter": "new",
        "digest_publish_included": False,
        "paused_until": (datetime.now() + timedelta(hours=2)).isoformat(),
        "pause_reason": "Maintenance window",
        "next_digest_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    due = client.post("/api/notifications/digests/run-due")
    assert due.status_code == 200
    assert due.json() == []
    digests = client.get("/api/notifications/digests", params={"channel_id": "paused-digest-drop"})
    assert digests.status_code == 200
    assert digests.json() == []


def test_notification_health_and_pause_resume_endpoints(client):
    success_channel = {
        "id": "health-file",
        "name": "Health File",
        "kind": "file",
        "target": "health-file",
        "owner_scope": "shared",
        "active": True,
    }
    fail_channel = {
        "id": "health-broken",
        "name": "Health Broken",
        "kind": "webhook",
        "target": "http://127.0.0.1:9/health",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=success_channel).status_code == 200
    assert client.post("/api/notifications/channels", json=fail_channel).status_code == 200
    assert client.post("/api/notifications/channels/health-file/test").status_code == 200
    failed_send = client.post("/api/notifications/channels/health-broken/test")
    assert failed_send.status_code == 200
    assert failed_send.json()["status"] == "failed"
    paused = client.post(
        "/api/notifications/channels/health-file/pause",
        params={"minutes": 30, "reason": "Ops freeze"},
    )
    assert paused.status_code == 200
    assert paused.json()["paused_until"] is not None
    health = client.get("/api/notifications/health", params={"window_hours": 24})
    assert health.status_code == 200
    rows = {item["channel_id"]: item for item in health.json()}
    assert rows["health-file"]["is_paused"] is True
    assert rows["health-file"]["success_count"] >= 1
    assert rows["health-broken"]["failed_count"] >= 1
    resumed = client.post("/api/notifications/channels/health-file/resume")
    assert resumed.status_code == 200
    assert resumed.json()["paused_until"] is None


def test_auto_pause_stops_repeated_failed_report_job_notifications(client):
    channel_payload = {
        "id": "auto-pause-webhook",
        "name": "Auto Pause Webhook",
        "kind": "webhook",
        "target": "http://127.0.0.1:9/notify",
        "event_types": ["report_job"],
        "auto_pause_enabled": True,
        "auto_pause_window_hours": 24,
        "auto_pause_error_rate_threshold": 1.0,
        "auto_pause_consecutive_failures": 1,
        "auto_pause_minutes": 180,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    template_payload = {
        "id": "auto-pause-pack",
        "name": "Auto Pause Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "auto-pause-job",
        "name": "Auto Pause Job",
        "template_id": "auto-pause-pack",
        "cadence": "manual",
        "run_hour_local": 8,
        "export_formats": ["json"],
        "notification_channel_ids": ["auto-pause-webhook"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    assert client.post("/api/reports/jobs/auto-pause-job/run").status_code == 200
    channel = client.get("/api/notifications/channels/auto-pause-webhook")
    assert channel.status_code == 200
    channel_payload = channel.json()
    assert channel_payload["paused_until"] is not None
    assert channel_payload["last_auto_paused_at"] is not None
    assert "Auto-paused" in channel_payload["pause_reason"]
    assert client.post("/api/reports/jobs/auto-pause-job/run").status_code == 200
    deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "auto-pause-webhook", "event_type": "report_job"},
    )
    assert deliveries.status_code == 200
    rows = deliveries.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"


def test_notification_routing_audit_tracks_delivered_suppressed_and_paused(client):
    channel_payload = {
        "id": "audit-drop",
        "name": "Audit Drop",
        "kind": "file",
        "target": "audit",
        "event_types": ["report_job"],
        "duplicate_window_minutes": 1440,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    template_payload = {
        "id": "audit-pack",
        "name": "Audit Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "audit-job",
        "name": "Audit Job",
        "template_id": "audit-pack",
        "cadence": "manual",
        "run_hour_local": 8,
        "export_formats": ["json"],
        "notification_channel_ids": ["audit-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    assert client.post("/api/reports/jobs/audit-job/run").status_code == 200
    assert client.post("/api/reports/jobs/audit-job/run").status_code == 200
    assert client.post("/api/notifications/channels/audit-drop/pause", params={"minutes": 30}).status_code == 200
    assert client.post("/api/reports/jobs/audit-job/run").status_code == 200
    audits = client.get("/api/notifications/routing", params={"channel_id": "audit-drop", "event_type": "report_job"})
    assert audits.status_code == 200
    decisions = {item["decision"] for item in audits.json()}
    assert "delivered" in decisions
    assert "suppressed" in decisions
    assert "paused" in decisions


def test_notification_routing_summary_and_export_endpoints(client):
    channel_payload = {
        "id": "audit-export-drop",
        "name": "Audit Export Drop",
        "kind": "file",
        "target": "audit-export",
        "event_types": ["report_job"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    template_payload = {
        "id": "audit-export-pack",
        "name": "Audit Export Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "audit-export-job",
        "name": "Audit Export Job",
        "template_id": "audit-export-pack",
        "cadence": "manual",
        "run_hour_local": 8,
        "export_formats": ["json"],
        "notification_channel_ids": ["audit-export-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    assert client.post("/api/reports/jobs/audit-export-job/run").status_code == 200
    routing = client.get(
        "/api/notifications/routing",
        params={"channel_id": "audit-export-drop", "event_type": "report_job"},
    )
    assert routing.status_code == 200
    assert len(routing.json()) > 0
    summary = client.get(
        "/api/notifications/routing/summary",
        params={"channel_id": "audit-export-drop", "event_type": "report_job", "window_hours": 24},
    )
    assert summary.status_code == 200
    rows = summary.json()
    assert len(rows) == 1
    assert rows[0]["channel_id"] == "audit-export-drop"
    assert rows[0]["delivered"] >= 1
    export_csv = client.post(
        "/api/notifications/routing/export",
        params={
            "format": "csv",
            "channel_id": "audit-export-drop",
            "event_type": "report_job",
            "limit": 1000,
        },
    )
    assert export_csv.status_code == 200
    csv_payload = export_csv.json()
    assert csv_payload["format"] == "csv"
    assert csv_payload["count"] >= 1
    assert Path(csv_payload["path"]).exists()
    export_json = client.post(
        "/api/notifications/routing/export",
        params={
            "format": "json",
            "channel_id": "audit-export-drop",
            "event_type": "report_job",
            "limit": 1000,
        },
    )
    assert export_json.status_code == 200
    json_payload = export_json.json()
    assert json_payload["format"] == "json"
    assert json_payload["count"] >= 1
    assert Path(json_payload["path"]).exists()


def test_notification_recovery_auto_resumes_paused_file_channel(client):
    channel_payload = {
        "id": "recovery-file-drop",
        "name": "Recovery File Drop",
        "kind": "file",
        "target": "recovery-file",
        "event_types": ["manual"],
        "auto_resume_enabled": True,
        "recovery_probe_profile": "verbose",
        "recovery_probe_payload": {"probe_tag": "ops-recovery"},
        "recovery_probe_cooldown_minutes": 120,
        "recovery_probe_max_per_hour": 1,
        "last_auto_paused_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "paused_until": (datetime.now() - timedelta(minutes=1)).isoformat(),
        "pause_reason": "Auto-paused test",
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    recovery = client.post("/api/notifications/recovery/run")
    assert recovery.status_code == 200
    rows = recovery.json()
    assert len(rows) == 1
    assert rows[0]["channel_id"] == "recovery-file-drop"
    assert rows[0]["status"] == "resumed"
    assert rows[0]["probe_profile"] == "verbose"
    probe_delivery = client.get(f"/api/notifications/deliveries/{rows[0]['probe_delivery_id']}")
    assert probe_delivery.status_code == 200
    probe_payload = probe_delivery.json()["payload"]
    assert probe_payload["auto_resume_probe"] is True
    assert probe_payload["probe_profile"] == "verbose"
    assert probe_payload["probe_tag"] == "ops-recovery"
    assert probe_payload["channel_id"] == "recovery-file-drop"
    channel = client.get("/api/notifications/channels/recovery-file-drop")
    assert channel.status_code == 200
    saved = channel.json()
    assert saved["paused_until"] is None
    assert saved["last_auto_resumed_at"] is not None
    saved["paused_until"] = (datetime.now() - timedelta(minutes=1)).isoformat()
    saved["last_auto_paused_at"] = datetime.now().isoformat()
    saved["pause_reason"] = "Auto-paused test again"
    assert client.post("/api/notifications/channels", json=saved).status_code == 200
    second_recovery = client.post("/api/notifications/recovery/run")
    assert second_recovery.status_code == 200
    second_rows = second_recovery.json()
    assert len(second_rows) == 1
    assert second_rows[0]["channel_id"] == "recovery-file-drop"
    assert second_rows[0]["status"] == "skipped"
    assert second_rows[0]["reason"] in {"recovery_probe_cooldown", "recovery_probe_rate_limited"}
    probe_deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "recovery-file-drop", "event_type": "manual"},
    )
    assert probe_deliveries.status_code == 200
    probe_rows = [row for row in probe_deliveries.json() if row["payload"].get("auto_resume_probe") is True]
    assert len(probe_rows) == 1


def test_ops_escalation_policy_notifies_target_and_respects_cooldown(client):
    ops_channel_payload = {
        "id": "ops-escalation-drop",
        "name": "Ops Escalation Drop",
        "kind": "file",
        "target": "ops-escalation",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=ops_channel_payload).status_code == 200
    primary_payload = {
        "id": "policy-broken-webhook",
        "name": "Policy Broken Webhook",
        "kind": "webhook",
        "target": "http://127.0.0.1:9/policy",
        "event_types": ["report_job"],
        "ops_escalation_enabled": True,
        "ops_escalation_channel_ids": ["ops-escalation-drop"],
        "ops_escalation_window_hours": 24,
        "ops_escalation_threshold": 1,
        "ops_escalation_cooldown_minutes": 120,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=primary_payload).status_code == 200
    template_payload = {
        "id": "policy-escalation-pack",
        "name": "Policy Escalation Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "policy-escalation-job",
        "name": "Policy Escalation Job",
        "template_id": "policy-escalation-pack",
        "cadence": "manual",
        "run_hour_local": 8,
        "export_formats": ["json"],
        "notification_channel_ids": ["policy-broken-webhook"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    assert client.post("/api/reports/jobs/policy-escalation-job/run").status_code == 200
    ops_deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "ops-escalation-drop", "event_type": "manual"},
    )
    assert ops_deliveries.status_code == 200
    rows = ops_deliveries.json()
    assert len(rows) == 1
    assert rows[0]["payload"]["policy_escalation"] is True
    assert rows[0]["payload"]["source_channel_id"] == "policy-broken-webhook"
    source_channel = client.get("/api/notifications/channels/policy-broken-webhook")
    assert source_channel.status_code == 200
    assert source_channel.json()["last_ops_escalated_at"] is not None
    assert client.post("/api/reports/jobs/policy-escalation-job/run").status_code == 200
    ops_deliveries_again = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "ops-escalation-drop", "event_type": "manual"},
    )
    assert ops_deliveries_again.status_code == 200
    assert len(ops_deliveries_again.json()) == 1


def test_ops_incident_lifecycle_endpoints(client):
    ops_channel_payload = {
        "id": "ops-incident-drop",
        "name": "Ops Incident Drop",
        "kind": "file",
        "target": "ops-incident",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=ops_channel_payload).status_code == 200
    primary_payload = {
        "id": "incident-broken-webhook",
        "name": "Incident Broken Webhook",
        "kind": "webhook",
        "target": "http://127.0.0.1:9/incident",
        "event_types": ["report_job"],
        "ops_escalation_enabled": True,
        "ops_escalation_channel_ids": ["ops-incident-drop"],
        "ops_escalation_window_hours": 24,
        "ops_escalation_threshold": 1,
        "ops_escalation_cooldown_minutes": 0,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=primary_payload).status_code == 200
    template_payload = {
        "id": "incident-pack",
        "name": "Incident Pack",
        "owner_scope": "shared",
        "sections": [{"kind": "global_monitor", "title": "Macro"}],
    }
    assert client.post("/api/reports/templates", json=template_payload).status_code == 200
    job_payload = {
        "id": "incident-job",
        "name": "Incident Job",
        "template_id": "incident-pack",
        "cadence": "manual",
        "run_hour_local": 8,
        "export_formats": ["json"],
        "notification_channel_ids": ["incident-broken-webhook"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/reports/jobs", json=job_payload).status_code == 200
    assert client.post("/api/reports/jobs/incident-job/run").status_code == 200
    incidents = client.get("/api/ops/incidents", params={"status": "open"})
    assert incidents.status_code == 200
    rows = incidents.json()
    assert len(rows) >= 1
    incident = next(item for item in rows if item["source_channel_id"] == "incident-broken-webhook")
    incident_id = incident["id"]
    assert incident["priority"] in {"low", "medium", "high"}
    assert incident["sla_minutes"] >= 0
    assert incident["due_at"] is not None

    summary_open = client.get("/api/ops/incidents/summary")
    assert summary_open.status_code == 200
    assert summary_open.json()["open"] >= 1

    fetched = client.get(f"/api/ops/incidents/{incident_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "open"
    ack = client.post(
        f"/api/ops/incidents/{incident_id}/update",
        params={
            "status": "ack",
            "owner": "macro-ops",
            "priority": "high",
            "sla_minutes": 0,
            "notes": "Investigating",
        },
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "ack"
    assert ack.json()["owner"] == "macro-ops"
    assert ack.json()["priority"] == "high"
    assert ack.json()["acknowledged_at"] is not None

    overdue = client.get("/api/ops/incidents", params={"overdue_only": True})
    assert overdue.status_code == 200
    assert any(item["id"] == incident_id for item in overdue.json())

    resolved = client.post(
        f"/api/ops/incidents/{incident_id}/status",
        params={"status": "resolved", "notes": "Fixed"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"] is not None

    summary_resolved = client.get("/api/ops/incidents/summary")
    assert summary_resolved.status_code == 200
    assert summary_resolved.json()["resolved"] >= 1


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
    digest_channel_payload = {
        "id": "poll-digest-drop",
        "name": "Poll Digest Drop",
        "kind": "file",
        "target": "poll-digest",
        "event_types": ["alert_event"],
        "delivery_mode": "digest",
        "min_significance": "low",
        "digest_hour_local": 8,
        "digest_limit": 10,
        "digest_status_filter": "new",
        "next_digest_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=digest_channel_payload).status_code == 200
    watchlist_payload = {
        "id": "poll-digest-watch",
        "name": "Poll Digest Watch",
        "tickers": ["TLT"],
        "owner_scope": "shared",
    }
    assert client.post("/api/watchlists", json=watchlist_payload).status_code == 200
    rule_payload = {
        "id": "poll-digest-rule",
        "name": "Poll Digest Rule",
        "entity_type": "asset",
        "asset_class": "rates",
        "watchlist_id": "poll-digest-watch",
        "min_significance": "low",
        "notification_channel_ids": ["poll-digest-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/alerts/rules", json=rule_payload).status_code == 200
    assert client.post("/api/alerts/scan", params={"rule_id": "poll-digest-rule"}).status_code == 200
    poll = client.post("/api/reports/scheduler/poll")
    assert poll.status_code == 200
    payload = poll.json()
    assert payload["attempted"] == 2
    assert payload["succeeded"] == 2
    assert payload["failed"] == 0
    assert payload["job_succeeded"] == 1
    assert payload["digest_succeeded"] == 1
    assert len(payload["digests"]) == 1


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
    alerts = client.get("/api/calendar/release-alerts", params={"country": "US", "days": 30, "limit": 25})
    assert calendar.status_code == 200
    assert freshness.status_code == 200
    assert alerts.status_code == 200
    assert isinstance(calendar.json(), list)
    assert isinstance(freshness.json(), list)
    assert isinstance(alerts.json(), list)
    assert any(item["country"] == "EA" for item in freshness.json())
    if alerts.json():
        first = alerts.json()[0]
        assert first["alert_type"] in {"stale", "overdue", "due_soon"}
        assert first["severity"] in {"high", "medium", "low"}


def test_release_freshness_snapshot_capture_list_get_endpoints(client):
    captured = client.post("/api/calendar/freshness-snapshots/capture", params={"country": "US", "days": 60})
    assert captured.status_code == 200
    payload = captured.json()
    assert payload["id"].startswith("release-freshness-")
    listing = client.get("/api/calendar/freshness-snapshots", params={"country": "US", "limit": 10})
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) >= 1
    fetched = client.get(f"/api/calendar/freshness-snapshots/{payload['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == payload["id"]


def test_release_freshness_delta_reports_series_changes_between_snapshots(client):
    original = api_module.service.get_freshness_status
    snapshots = [
        [
            ReleaseEvent(
                series_id="fred:CPIAUCSL",
                title="US CPI All Items",
                country="US",
                source="fred",
                frequency="monthly",
                last_observation_date=date(2024, 1, 1),
                expected_next_release=date(2024, 2, 1),
                freshness_status="fresh",
            )
        ],
        [
            ReleaseEvent(
                series_id="fred:CPIAUCSL",
                title="US CPI All Items",
                country="US",
                source="fred",
                frequency="monthly",
                last_observation_date=date(2024, 1, 1),
                expected_next_release=date(2024, 2, 1),
                freshness_status="stale",
            )
        ],
    ]
    call_index = {"value": 0}

    def fake_freshness(country=None, topic=None):  # noqa: ANN001
        idx = min(call_index["value"], len(snapshots) - 1)
        call_index["value"] += 1
        return snapshots[idx]

    api_module.service.get_freshness_status = fake_freshness
    try:
        assert client.post("/api/calendar/freshness-snapshots/capture", params={"country": "US"}).status_code == 200
        assert client.post("/api/calendar/freshness-snapshots/capture", params={"country": "US"}).status_code == 200
        delta = client.get("/api/calendar/freshness-delta", params={"country": "US"})
    finally:
        api_module.service.get_freshness_status = original
    assert delta.status_code == 200
    payload = delta.json()
    assert payload["has_baseline"] is True
    assert payload["change_count"] >= 1
    assert any(item["change_type"] == "freshness_changed" for item in payload["changes"])


def test_source_health_endpoints_reflect_macro_fallback_degradation(client):
    warm = client.post(
        "/api/observations/query",
        json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert warm.status_code == 200
    original_fetch = api_module.service.fred.fetch_observations
    api_module.service.fred.fetch_observations = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fred down"))
    try:
        degraded = client.post(
            "/api/observations/query",
            json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        )
    finally:
        api_module.service.fred.fetch_observations = original_fetch
    assert degraded.status_code == 200
    summary = client.get("/api/status/sources/summary")
    assert summary.status_code == 200
    assert summary.json()["total"] >= 1
    sources = client.get("/api/status/sources", params={"source_kind": "macro"})
    assert sources.status_code == 200
    rows = sources.json()
    fred_row = next(item for item in rows if item["id"] == "macro:fred")
    assert fred_row["status"] in {"degraded", "down"}
    assert fred_row["fallback_used"] is True
    assert fred_row["consecutive_failures"] >= 1


def test_source_health_policy_run_sends_manual_alert_notification(client):
    channel_payload = {
        "id": "source-policy-drop",
        "name": "Source Policy Drop",
        "kind": "file",
        "target": "source-policy",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    warm = client.post(
        "/api/observations/query",
        json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert warm.status_code == 200
    original_fetch = api_module.service.fred.fetch_observations
    api_module.service.fred.fetch_observations = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fred down"))
    try:
        degraded = client.post(
            "/api/observations/query",
            json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        )
    finally:
        api_module.service.fred.fetch_observations = original_fetch
    assert degraded.status_code == 200
    threshold = client.post("/api/status/sources/macro:fred/threshold", params={"minutes": 15})
    assert threshold.status_code == 200
    assert threshold.json()["stale_threshold_minutes"] == 15
    policy_payload = {
        "id": "source-policy-1",
        "name": "Macro Source Degraded Policy",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": True,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    saved = client.post("/api/status/sources/policies", json=policy_payload)
    assert saved.status_code == 200
    fetched = client.get("/api/status/sources/policies/source-policy-1")
    assert fetched.status_code == 200
    assert fetched.json()["source_id"] == "macro:fred"
    ran = client.post("/api/status/sources/policies/run")
    assert ran.status_code == 200
    actions = ran.json()
    assert any(item.get("policy_id") == "source-policy-1" and item.get("status") == "triggered" for item in actions)
    runs = client.get("/api/status/sources/policies/runs")
    assert runs.status_code == 200
    run_rows = runs.json()
    assert len(run_rows) >= 1
    assert run_rows[0]["trigger"] == "manual"
    run_id = run_rows[0]["id"]
    run_detail = client.get(f"/api/status/sources/policies/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["triggered_actions"] >= 1
    deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-drop", "event_type": "manual"},
    )
    assert deliveries.status_code == 200
    rows = deliveries.json()
    assert len(rows) >= 1
    assert rows[0]["payload"]["source_health_alert"] is True


def test_source_health_policy_can_be_updated_and_deactivated(client):
    channel_payload = {
        "id": "source-policy-edit-drop",
        "name": "Source Policy Edit Drop",
        "kind": "file",
        "target": "source-policy-edit",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    create_payload = {
        "id": "source-policy-edit-1",
        "name": "Source Policy Edit",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-edit-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=create_payload).status_code == 200
    update_payload = {
        **create_payload,
        "name": "Source Policy Edit Updated",
        "trigger_on_stale": True,
        "active": False,
    }
    updated = client.post("/api/status/sources/policies", json=update_payload)
    assert updated.status_code == 200
    fetched = client.get("/api/status/sources/policies/source-policy-edit-1")
    assert fetched.status_code == 200
    row = fetched.json()
    assert row["name"] == "Source Policy Edit Updated"
    assert row["trigger_on_stale"] is True
    assert row["active"] is False
    active_only = client.get("/api/status/sources/policies", params={"active_only": True})
    assert active_only.status_code == 200
    assert all(item["id"] != "source-policy-edit-1" for item in active_only.json())


def test_source_health_policy_archive_restore_keeps_run_history(client):
    channel_payload = {
        "id": "source-policy-archive-drop",
        "name": "Source Policy Archive Drop",
        "kind": "file",
        "target": "source-policy-archive",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    assert client.post(
        "/api/observations/query",
        json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    ).status_code == 200
    original_fetch = api_module.service.fred.fetch_observations
    api_module.service.fred.fetch_observations = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fred down"))
    try:
        assert client.post(
            "/api/observations/query",
            json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        ).status_code == 200
    finally:
        api_module.service.fred.fetch_observations = original_fetch
    policy_payload = {
        "id": "source-policy-archive-1",
        "name": "Source Policy Archive",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-archive-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    run = client.post("/api/status/sources/policies/run")
    assert run.status_code == 200
    runs_before = client.get("/api/status/sources/policies/runs")
    assert runs_before.status_code == 200
    assert len(runs_before.json()) >= 1

    archived = client.post(
        "/api/status/sources/policies/source-policy-archive-1/archive",
        params={"reason": "retired"},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert archived.json()["active"] is False
    list_default = client.get("/api/status/sources/policies")
    assert list_default.status_code == 200
    assert all(item["id"] != "source-policy-archive-1" for item in list_default.json())
    list_archived = client.get("/api/status/sources/policies", params={"include_archived": True})
    assert list_archived.status_code == 200
    assert any(item["id"] == "source-policy-archive-1" for item in list_archived.json())
    runs_after_archive = client.get("/api/status/sources/policies/runs")
    assert runs_after_archive.status_code == 200
    assert len(runs_after_archive.json()) >= len(runs_before.json())

    restored = client.post("/api/status/sources/policies/source-policy-archive-1/restore")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["active"] is True


def test_source_health_policy_version_history_tracks_create_update_archive_restore(client):
    channel_payload = {
        "id": "source-policy-version-drop",
        "name": "Source Policy Version Drop",
        "kind": "file",
        "target": "source-policy-version",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    create_payload = {
        "id": "source-policy-version-1",
        "name": "Source Policy Version",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-version-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=create_payload).status_code == 200
    update_payload = {**create_payload, "trigger_on_stale": True}
    assert client.post("/api/status/sources/policies", json=update_payload).status_code == 200
    assert client.post("/api/status/sources/policies/source-policy-version-1/archive", params={"reason": "version-test"}).status_code == 200
    assert client.post("/api/status/sources/policies/source-policy-version-1/restore").status_code == 200

    versions = client.get("/api/status/sources/policies/source-policy-version-1/versions")
    assert versions.status_code == 200
    rows = versions.json()
    assert len(rows) >= 4
    actions = {item["action"] for item in rows}
    assert {"create", "update", "archive", "restore"}.issubset(actions)
    assert any("trigger_on_stale" in item["changed_fields"] for item in rows if item["action"] == "update")
    assert any("archived_at" in item["changed_fields"] for item in rows if item["action"] in {"archive", "restore"})
    version_detail = client.get(f"/api/status/sources/policies/versions/{rows[0]['id']}")
    assert version_detail.status_code == 200
    assert version_detail.json()["policy_id"] == "source-policy-version-1"


def test_source_health_policy_version_rollback_restores_snapshot(client):
    channel_payload = {
        "id": "source-policy-rollback-drop",
        "name": "Source Policy Rollback Drop",
        "kind": "file",
        "target": "source-policy-rollback",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    create_payload = {
        "id": "source-policy-rollback-1",
        "name": "Source Policy Rollback",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-rollback-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=create_payload).status_code == 200
    update_payload = {**create_payload, "name": "Source Policy Rollback Updated", "trigger_on_stale": True}
    assert client.post("/api/status/sources/policies", json=update_payload).status_code == 200
    versions = client.get("/api/status/sources/policies/source-policy-rollback-1/versions")
    assert versions.status_code == 200
    rows = versions.json()
    create_version = next(item for item in rows if item["action"] == "create")
    rollback = client.post(f"/api/status/sources/policies/versions/{create_version['id']}/rollback")
    assert rollback.status_code == 200
    restored = rollback.json()
    assert restored["name"] == "Source Policy Rollback"
    assert restored["trigger_on_stale"] is False
    fetched = client.get("/api/status/sources/policies/source-policy-rollback-1")
    assert fetched.status_code == 200
    row = fetched.json()
    assert row["name"] == "Source Policy Rollback"
    assert row["trigger_on_stale"] is False
    versions_after = client.get("/api/status/sources/policies/source-policy-rollback-1/versions")
    assert versions_after.status_code == 200
    actions = [item["action"] for item in versions_after.json()]
    assert "rollback" in actions


def test_source_health_policy_version_compare_reports_field_diffs(client):
    channel_payload = {
        "id": "source-policy-compare-drop",
        "name": "Source Policy Compare Drop",
        "kind": "file",
        "target": "source-policy-compare",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    create_payload = {
        "id": "source-policy-compare-1",
        "name": "Source Policy Compare",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-compare-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=create_payload).status_code == 200
    update_payload = {**create_payload, "name": "Source Policy Compare Updated", "trigger_on_stale": True}
    assert client.post("/api/status/sources/policies", json=update_payload).status_code == 200
    versions = client.get("/api/status/sources/policies/source-policy-compare-1/versions")
    assert versions.status_code == 200
    rows = versions.json()
    create_version = next(item for item in rows if item["action"] == "create")
    update_version = next(item for item in rows if item["action"] == "update")
    compare = client.get(
        "/api/status/sources/policies/compare-versions",
        params={"left_version_id": create_version["id"], "right_version_id": update_version["id"]},
    )
    assert compare.status_code == 200
    payload = compare.json()
    assert payload["policy_id"] == "source-policy-compare-1"
    assert "name" in payload["changed_fields"]
    assert "trigger_on_stale" in payload["changed_fields"]
    diffs = {item["field"]: item for item in payload["diffs"]}
    assert diffs["name"]["left_value"] == "Source Policy Compare"
    assert diffs["name"]["right_value"] == "Source Policy Compare Updated"
    assert diffs["trigger_on_stale"]["left_value"] is False
    assert diffs["trigger_on_stale"]["right_value"] is True


def test_source_health_policy_version_list_supports_action_and_text_filters(client):
    channel_payload = {
        "id": "source-policy-filter-drop",
        "name": "Source Policy Filter Drop",
        "kind": "file",
        "target": "source-policy-filter",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    create_payload = {
        "id": "source-policy-filter-1",
        "name": "Source Policy Filter",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-filter-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=create_payload).status_code == 200
    update_payload = {**create_payload, "name": "Source Policy Filter Updated", "trigger_on_stale": True}
    assert client.post("/api/status/sources/policies", json=update_payload).status_code == 200
    versions = client.get(
        "/api/status/sources/policies/source-policy-filter-1/versions",
        params={"action": "update"},
    )
    assert versions.status_code == 200
    update_rows = versions.json()
    assert len(update_rows) == 1
    assert update_rows[0]["action"] == "update"
    assert "trigger_on_stale" in update_rows[0]["changed_fields"]
    filtered = client.get(
        "/api/status/sources/policies/source-policy-filter-1/versions",
        params={"query": "updated"},
    )
    assert filtered.status_code == 200
    filtered_rows = filtered.json()
    assert len(filtered_rows) == 1
    assert filtered_rows[0]["action"] == "update"


def test_source_health_policy_version_presets_can_be_saved_and_loaded(client):
    channel_payload = {
        "id": "source-policy-preset-drop",
        "name": "Source Policy Preset Drop",
        "kind": "file",
        "target": "source-policy-preset",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-1",
        "name": "Source Policy Preset",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    assert client.post("/api/status/sources/policies", json={**policy_payload, "trigger_on_stale": True}).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-1",
        "policy_id": "source-policy-preset-1",
        "name": "Updates only",
        "action_filter": "update",
        "query": "trigger_on_stale",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    saved = client.post("/api/status/sources/policies/version-presets", json=preset_payload)
    assert saved.status_code == 200
    rows = client.get("/api/status/sources/policies/source-policy-preset-1/version-presets")
    assert rows.status_code == 200
    preset_rows = rows.json()
    assert len(preset_rows) == 1
    assert preset_rows[0]["name"] == "Updates only"
    assert preset_rows[0]["is_default"] is True
    fetched = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-1")
    assert fetched.status_code == 200
    assert fetched.json()["query"] == "trigger_on_stale"
    updated = client.post(
        "/api/status/sources/policies/version-presets",
        json={**preset_payload, "query": "updated", "limit": 5},
    )
    assert updated.status_code == 200
    fetched_updated = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-1")
    assert fetched_updated.status_code == 200
    assert fetched_updated.json()["query"] == "updated"
    second_preset = client.post(
        "/api/status/sources/policies/version-presets",
        json={
            "id": "source-policy-version-preset-2",
            "policy_id": "source-policy-preset-1",
            "name": "All events",
            "action_filter": None,
            "query": None,
            "limit": 20,
            "is_default": False,
            "owner_scope": "shared",
        },
    )
    assert second_preset.status_code == 200
    set_default = client.post("/api/status/sources/policies/version-presets/source-policy-version-preset-2/set-default")
    assert set_default.status_code == 200
    assert set_default.json()["is_default"] is True
    rows_after_default = client.get("/api/status/sources/policies/source-policy-preset-1/version-presets")
    assert rows_after_default.status_code == 200
    defaults = [item for item in rows_after_default.json() if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "source-policy-version-preset-2"
    deleted = client.delete("/api/status/sources/policies/version-presets/source-policy-version-preset-1")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-1").status_code == 404
    remaining = client.get("/api/status/sources/policies/source-policy-preset-1/version-presets")
    assert remaining.status_code == 200
    assert len(remaining.json()) == 1
    assert remaining.json()[0]["id"] == "source-policy-version-preset-2"
    assert remaining.json()[0]["is_default"] is True


def test_source_health_policy_version_preset_auto_assigns_default_when_missing(client):
    channel_payload = {
        "id": "source-policy-preset-auto-default-drop",
        "name": "Source Policy Preset Auto Default Drop",
        "kind": "file",
        "target": "source-policy-preset-auto-default",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-auto-default-1",
        "name": "Source Policy Preset Auto Default",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-auto-default-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-auto-default-1",
        "policy_id": "source-policy-preset-auto-default-1",
        "name": "Only preset",
        "action_filter": "update",
        "query": "field",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    saved = client.post("/api/status/sources/policies/version-presets", json=preset_payload)
    assert saved.status_code == 200
    assert saved.json()["is_default"] is True
    rows = client.get("/api/status/sources/policies/source-policy-preset-auto-default-1/version-presets")
    assert rows.status_code == 200
    defaults = [item for item in rows.json() if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "source-policy-version-preset-auto-default-1"


def test_source_health_policy_version_preset_timestamps_are_recorded_and_created_at_is_stable(client):
    channel_payload = {
        "id": "source-policy-preset-timestamps-drop",
        "name": "Source Policy Preset Timestamps Drop",
        "kind": "file",
        "target": "source-policy-preset-timestamps",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-timestamps-1",
        "name": "Source Policy Preset Timestamps",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-timestamps-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-timestamps-1",
        "policy_id": "source-policy-preset-timestamps-1",
        "name": "Timestamp preset",
        "action_filter": "update",
        "query": "field",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    saved = client.post("/api/status/sources/policies/version-presets", json=preset_payload)
    assert saved.status_code == 200
    first = saved.json()
    assert first["created_at"] is not None
    assert first["updated_at"] is not None
    assert first["last_used_at"] is None
    assert first["usage_count"] == 0
    updated = client.post(
        "/api/status/sources/policies/version-presets",
        json={**preset_payload, "query": "field-updated"},
    )
    assert updated.status_code == 200
    second = updated.json()
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] is not None
    assert second["last_used_at"] is None
    assert second["usage_count"] == 0


def test_source_health_policy_version_preset_versions_endpoint_applies_filters(client):
    channel_payload = {
        "id": "source-policy-preset-versions-drop",
        "name": "Source Policy Preset Versions Drop",
        "kind": "file",
        "target": "source-policy-preset-versions",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-versions-1",
        "name": "Source Policy Preset Versions",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-versions-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    assert client.post("/api/status/sources/policies", json={**policy_payload, "trigger_on_stale": True}).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-versions-1",
        "policy_id": "source-policy-preset-versions-1",
        "name": "Updates trigger filter",
        "action_filter": "update",
        "query": "trigger_on_stale",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_payload).status_code == 200
    rows = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-versions-1/versions")
    assert rows.status_code == 200
    payload = rows.json()
    assert len(payload) == 1
    assert payload[0]["action"] == "update"
    assert "trigger_on_stale" in payload[0]["changed_fields"]
    fetched = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-versions-1")
    assert fetched.status_code == 200
    assert fetched.json()["last_used_at"] is not None
    assert fetched.json()["usage_count"] == 1
    second_run = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-versions-1/versions")
    assert second_run.status_code == 200
    fetched_again = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-versions-1")
    assert fetched_again.status_code == 200
    assert fetched_again.json()["usage_count"] == 2


def test_source_health_policy_version_preset_listing_supports_usage_sorting(client):
    channel_payload = {
        "id": "source-policy-preset-sort-drop",
        "name": "Source Policy Preset Sort Drop",
        "kind": "file",
        "target": "source-policy-preset-sort",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-sort-1",
        "name": "Source Policy Preset Sort",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-sort-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    first = {
        "id": "source-policy-version-preset-sort-a",
        "policy_id": "source-policy-preset-sort-1",
        "name": "Preset A",
        "action_filter": None,
        "query": None,
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    second = {
        "id": "source-policy-version-preset-sort-b",
        "policy_id": "source-policy-preset-sort-1",
        "name": "Preset B",
        "action_filter": None,
        "query": None,
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=first).status_code == 200
    assert client.post("/api/status/sources/policies/version-presets", json=second).status_code == 200
    assert client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-sort-b/versions").status_code == 200
    assert client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-sort-b/versions").status_code == 200
    listing = client.get(
        "/api/status/sources/policies/source-policy-preset-sort-1/version-presets",
        params={"sort_by": "usage_count", "order": "desc"},
    )
    assert listing.status_code == 200
    payload = listing.json()
    assert len(payload) == 2
    assert payload[0]["id"] == "source-policy-version-preset-sort-b"
    assert payload[0]["usage_count"] >= payload[1]["usage_count"]


def test_source_health_policy_version_preset_listing_rejects_invalid_sort_params(client):
    channel_payload = {
        "id": "source-policy-preset-sort-invalid-drop",
        "name": "Source Policy Preset Sort Invalid Drop",
        "kind": "file",
        "target": "source-policy-preset-sort-invalid",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-sort-invalid-1",
        "name": "Source Policy Preset Sort Invalid",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-sort-invalid-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    invalid_sort = client.get(
        "/api/status/sources/policies/source-policy-preset-sort-invalid-1/version-presets",
        params={"sort_by": "bad_field"},
    )
    assert invalid_sort.status_code == 400
    assert "sort_by must be one of" in invalid_sort.json()["detail"]
    invalid_order = client.get(
        "/api/status/sources/policies/source-policy-preset-sort-invalid-1/version-presets",
        params={"order": "upward"},
    )
    assert invalid_order.status_code == 400
    assert "order must be either 'asc' or 'desc'" in invalid_order.json()["detail"]
    invalid_offset = client.get(
        "/api/status/sources/policies/source-policy-preset-sort-invalid-1/version-presets",
        params={"offset": -1},
    )
    assert invalid_offset.status_code == 400
    assert "offset must be at least 0" in invalid_offset.json()["detail"]


def test_source_health_policy_version_preset_listing_supports_query_and_default_filter(client):
    channel_payload = {
        "id": "source-policy-preset-filter-drop",
        "name": "Source Policy Preset Filter Drop",
        "kind": "file",
        "target": "source-policy-preset-filter",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-filter-1",
        "name": "Source Policy Preset Filter",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-filter-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_a = {
        "id": "source-policy-version-preset-filter-a",
        "policy_id": "source-policy-preset-filter-1",
        "name": "Alpha Policy View",
        "action_filter": "update",
        "query": "trigger_on_stale",
        "limit": 15,
        "is_default": True,
        "owner_scope": "shared",
    }
    preset_b = {
        "id": "source-policy-version-preset-filter-b",
        "policy_id": "source-policy-preset-filter-1",
        "name": "Beta Backup",
        "action_filter": "archive",
        "query": "holiday",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_a).status_code == 200
    assert client.post("/api/status/sources/policies/version-presets", json=preset_b).status_code == 200
    query_by_name = client.get(
        "/api/status/sources/policies/source-policy-preset-filter-1/version-presets",
        params={"query": "alpha"},
    )
    assert query_by_name.status_code == 200
    assert len(query_by_name.json()) == 1
    assert query_by_name.json()[0]["id"] == "source-policy-version-preset-filter-a"
    query_by_action = client.get(
        "/api/status/sources/policies/source-policy-preset-filter-1/version-presets",
        params={"query": "archive"},
    )
    assert query_by_action.status_code == 200
    assert len(query_by_action.json()) == 1
    assert query_by_action.json()[0]["id"] == "source-policy-version-preset-filter-b"
    only_default = client.get(
        "/api/status/sources/policies/source-policy-preset-filter-1/version-presets",
        params={"only_default": "true"},
    )
    assert only_default.status_code == 200
    assert len(only_default.json()) == 1
    assert only_default.json()[0]["is_default"] is True
    assert only_default.json()[0]["id"] == "source-policy-version-preset-filter-a"


def test_source_health_policy_version_preset_listing_supports_offset_pagination(client):
    channel_payload = {
        "id": "source-policy-preset-offset-drop",
        "name": "Source Policy Preset Offset Drop",
        "kind": "file",
        "target": "source-policy-preset-offset",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-offset-1",
        "name": "Source Policy Preset Offset",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-offset-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    presets = [
        {
            "id": "source-policy-version-preset-offset-a",
            "policy_id": "source-policy-preset-offset-1",
            "name": "Preset A",
            "action_filter": None,
            "query": None,
            "limit": 10,
            "is_default": True,
            "owner_scope": "shared",
        },
        {
            "id": "source-policy-version-preset-offset-b",
            "policy_id": "source-policy-preset-offset-1",
            "name": "Preset B",
            "action_filter": None,
            "query": None,
            "limit": 10,
            "is_default": False,
            "owner_scope": "shared",
        },
        {
            "id": "source-policy-version-preset-offset-c",
            "policy_id": "source-policy-preset-offset-1",
            "name": "Preset C",
            "action_filter": None,
            "query": None,
            "limit": 10,
            "is_default": False,
            "owner_scope": "shared",
        },
    ]
    for payload in presets:
        assert client.post("/api/status/sources/policies/version-presets", json=payload).status_code == 200
    page = client.get(
        "/api/status/sources/policies/source-policy-preset-offset-1/version-presets",
        params={"sort_by": "name", "order": "asc", "offset": 1, "limit": 1},
    )
    assert page.status_code == 200
    rows = page.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Preset B"


def test_source_health_policy_version_preset_summary_endpoint_reports_key_stats(client):
    channel_payload = {
        "id": "source-policy-preset-summary-drop",
        "name": "Source Policy Preset Summary Drop",
        "kind": "file",
        "target": "source-policy-preset-summary",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-summary-1",
        "name": "Source Policy Preset Summary",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-summary-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_a = {
        "id": "source-policy-version-preset-summary-a",
        "policy_id": "source-policy-preset-summary-1",
        "name": "Summary A",
        "action_filter": "update",
        "query": "a",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    preset_b = {
        "id": "source-policy-version-preset-summary-b",
        "policy_id": "source-policy-preset-summary-1",
        "name": "Summary B",
        "action_filter": "update",
        "query": "b",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_a).status_code == 200
    assert client.post("/api/status/sources/policies/version-presets", json=preset_b).status_code == 200
    assert client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-summary-b/versions").status_code == 200
    assert client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-summary-b/versions").status_code == 200
    summary = client.get("/api/status/sources/policies/source-policy-preset-summary-1/version-presets/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["policy_id"] == "source-policy-preset-summary-1"
    assert payload["total_presets"] == 2
    assert payload["default_preset_id"] == "source-policy-version-preset-summary-a"
    assert payload["most_used_preset_id"] == "source-policy-version-preset-summary-b"
    assert payload["most_used_count"] == 2
    assert payload["last_used_preset_id"] == "source-policy-version-preset-summary-b"
    assert payload["last_used_at"] is not None


def test_source_health_policy_version_preset_rejects_duplicate_names_per_policy(client):
    channel_payload = {
        "id": "source-policy-preset-dup-drop",
        "name": "Source Policy Preset Dup Drop",
        "kind": "file",
        "target": "source-policy-preset-dup",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preset-dup-1",
        "name": "Source Policy Preset Dup",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preset-dup-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    first = {
        "id": "source-policy-version-preset-dup-1",
        "policy_id": "source-policy-preset-dup-1",
        "name": "Duplicates Blocked",
        "action_filter": "update",
        "query": "name",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=first).status_code == 200
    second = {
        "id": "source-policy-version-preset-dup-2",
        "policy_id": "source-policy-preset-dup-1",
        "name": "  duplicates blocked  ",
        "action_filter": None,
        "query": None,
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    duplicate = client.post("/api/status/sources/policies/version-presets", json=second)
    assert duplicate.status_code == 400
    assert "already exists for this policy" in duplicate.json()["detail"]
    imported = client.post(
        "/api/status/sources/policies/source-policy-preset-dup-1/version-presets/import",
        json={
            "mode": "append",
            "presets": [
                {"name": "DuplicateS Blocked", "action_filter": "update", "query": "x", "limit": 5},
                {"name": "Fresh Name", "action_filter": None, "query": None, "limit": 5},
            ],
        },
    )
    assert imported.status_code == 400
    assert "already exist for this policy" in imported.json()["detail"]


def test_source_health_policy_version_preset_import_upsert_updates_and_creates(client):
    channel_payload = {
        "id": "source-policy-upsert-drop",
        "name": "Source Policy Upsert Drop",
        "kind": "file",
        "target": "source-policy-upsert",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-upsert-1",
        "name": "Source Policy Upsert",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-upsert-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    original = {
        "id": "source-policy-version-preset-upsert-original",
        "policy_id": "source-policy-upsert-1",
        "name": "Core filter",
        "action_filter": "update",
        "query": "old",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=original).status_code == 200
    assert client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-upsert-original/versions").status_code == 200
    upsert = client.post(
        "/api/status/sources/policies/source-policy-upsert-1/version-presets/import",
        json={
            "mode": "upsert",
            "presets": [
                {
                    "name": " core FILTER ",
                    "action_filter": "archive",
                    "query": "updated",
                    "limit": 7,
                    "is_default": True,
                    "owner_scope": "shared",
                },
                {
                    "name": "Fresh upsert preset",
                    "action_filter": None,
                    "query": None,
                    "limit": 20,
                    "is_default": False,
                    "owner_scope": "shared",
                },
            ],
        },
    )
    assert upsert.status_code == 200
    payload = upsert.json()
    assert len(payload) == 2
    updated_match = next(item for item in payload if item["name"] == "core FILTER")
    assert updated_match["id"] == "source-policy-version-preset-upsert-original"
    assert updated_match["action_filter"] == "archive"
    assert updated_match["query"] == "updated"
    assert updated_match["limit"] == 7
    assert updated_match["is_default"] is True
    assert updated_match["usage_count"] == 1
    rows = client.get("/api/status/sources/policies/source-policy-upsert-1/version-presets")
    assert rows.status_code == 200
    row_payload = rows.json()
    assert len(row_payload) == 2
    defaults = [item for item in row_payload if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "source-policy-version-preset-upsert-original"
    assert any(item["name"] == "Fresh upsert preset" for item in row_payload)


def test_source_health_policy_version_preset_upsert_preserves_single_default_when_bundle_unsets_it(client):
    channel_payload = {
        "id": "source-policy-upsert-default-drop",
        "name": "Source Policy Upsert Default Drop",
        "kind": "file",
        "target": "source-policy-upsert-default",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-upsert-default-1",
        "name": "Source Policy Upsert Default",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-upsert-default-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    first = {
        "id": "source-policy-version-preset-upsert-default-1",
        "policy_id": "source-policy-upsert-default-1",
        "name": "Alpha preset",
        "action_filter": "update",
        "query": "a",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    second = {
        "id": "source-policy-version-preset-upsert-default-2",
        "policy_id": "source-policy-upsert-default-1",
        "name": "Beta preset",
        "action_filter": None,
        "query": None,
        "limit": 20,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=first).status_code == 200
    assert client.post("/api/status/sources/policies/version-presets", json=second).status_code == 200
    upsert = client.post(
        "/api/status/sources/policies/source-policy-upsert-default-1/version-presets/import",
        json={
            "mode": "upsert",
            "presets": [
                {
                    "name": "alpha preset",
                    "action_filter": "archive",
                    "query": "updated",
                    "limit": 9,
                    "is_default": False,
                    "owner_scope": "shared",
                }
            ],
        },
    )
    assert upsert.status_code == 200
    rows = client.get("/api/status/sources/policies/source-policy-upsert-default-1/version-presets")
    assert rows.status_code == 200
    payload = rows.json()
    defaults = [item for item in payload if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "source-policy-version-preset-upsert-default-1"


def test_source_health_policy_version_preset_import_preview_reports_conflicts_without_writes(client):
    channel_payload = {
        "id": "source-policy-preview-drop",
        "name": "Source Policy Preview Drop",
        "kind": "file",
        "target": "source-policy-preview",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preview-1",
        "name": "Source Policy Preview",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preview-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    existing = {
        "id": "source-policy-version-preset-preview-existing",
        "policy_id": "source-policy-preview-1",
        "name": "Existing Preset",
        "action_filter": "update",
        "query": "old",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=existing).status_code == 200
    preview = client.post(
        "/api/status/sources/policies/source-policy-preview-1/version-presets/import/preview",
        json={
            "mode": "append",
            "presets": [
                {"name": "existing preset", "action_filter": "archive", "query": "new", "limit": 8},
                {"name": "New Preset", "action_filter": None, "query": None, "limit": 12},
            ],
        },
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["valid"] is False
    assert payload["conflict_count"] == 1
    assert payload["create_count"] == 1
    assert any("already exist for this policy" in item for item in payload["errors"])
    assert any(item["action"] == "conflict" for item in payload["actions"])
    assert any(item["action"] == "create" for item in payload["actions"])
    rows = client.get("/api/status/sources/policies/source-policy-preview-1/version-presets")
    assert rows.status_code == 200
    assert len(rows.json()) == 1
    assert rows.json()[0]["name"] == "Existing Preset"


def test_source_health_policy_version_preset_import_invalid_fields_are_blocked_without_partial_writes(client):
    channel_payload = {
        "id": "source-policy-preview-invalid-drop",
        "name": "Source Policy Preview Invalid Drop",
        "kind": "file",
        "target": "source-policy-preview-invalid",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-preview-invalid-1",
        "name": "Source Policy Preview Invalid",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-preview-invalid-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    seed_preset = {
        "id": "source-policy-version-preset-preview-invalid-seed",
        "policy_id": "source-policy-preview-invalid-1",
        "name": "Seed Preset",
        "action_filter": "update",
        "query": "seed",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=seed_preset).status_code == 200
    preview = client.post(
        "/api/status/sources/policies/source-policy-preview-invalid-1/version-presets/import/preview",
        json={
            "mode": "append",
            "presets": [
                {"name": "New Valid", "action_filter": "archive", "query": "ok", "limit": 8},
                {"name": "Broken Preset", "action_filter": "bad_action", "query": "bad", "limit": 0},
            ],
        },
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["valid"] is False
    assert any("limit must be at least 1" in item for item in preview_payload["errors"])
    assert any("action_filter must be one of" in item for item in preview_payload["errors"])
    failed_import = client.post(
        "/api/status/sources/policies/source-policy-preview-invalid-1/version-presets/import",
        json={
            "mode": "append",
            "presets": [
                {"name": "New Valid", "action_filter": "archive", "query": "ok", "limit": 8},
                {"name": "Broken Preset", "action_filter": "bad_action", "query": "bad", "limit": 0},
            ],
        },
    )
    assert failed_import.status_code == 400
    rows = client.get("/api/status/sources/policies/source-policy-preview-invalid-1/version-presets")
    assert rows.status_code == 200
    assert len(rows.json()) == 1
    assert rows.json()[0]["name"] == "Seed Preset"


def test_source_health_policy_version_preset_import_rolls_back_on_runtime_failure(client):
    channel_payload = {
        "id": "source-policy-import-rollback-drop",
        "name": "Source Policy Import Rollback Drop",
        "kind": "file",
        "target": "source-policy-import-rollback",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-import-rollback-1",
        "name": "Source Policy Import Rollback",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-import-rollback-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    seed = {
        "id": "source-policy-version-preset-import-rollback-seed",
        "policy_id": "source-policy-import-rollback-1",
        "name": "Seed Preset",
        "action_filter": "update",
        "query": "seed",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=seed).status_code == 200
    original_save = api_module.service.save_source_health_policy_version_preset
    call_count = {"value": 0}

    def flaky_save(preset):
        call_count["value"] += 1
        if call_count["value"] == 2:
            raise ValueError("simulated import failure")
        return original_save(preset)

    api_module.service.save_source_health_policy_version_preset = flaky_save
    try:
        failed = client.post(
            "/api/status/sources/policies/source-policy-import-rollback-1/version-presets/import",
            json={
                "mode": "replace",
                "presets": [
                    {"name": "New A", "action_filter": "archive", "query": "a", "limit": 8, "is_default": True},
                    {"name": "New B", "action_filter": "update", "query": "b", "limit": 7, "is_default": False},
                ],
            },
        )
    finally:
        api_module.service.save_source_health_policy_version_preset = original_save
    assert failed.status_code == 400
    assert "simulated import failure" in failed.json()["detail"]
    rows = client.get("/api/status/sources/policies/source-policy-import-rollback-1/version-presets")
    assert rows.status_code == 200
    payload = rows.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "source-policy-version-preset-import-rollback-seed"
    assert payload[0]["name"] == "Seed Preset"
    assert payload[0]["is_default"] is True


def test_source_health_policy_version_preset_clone_creates_new_preset(client):
    channel_payload = {
        "id": "source-policy-clone-drop",
        "name": "Source Policy Clone Drop",
        "kind": "file",
        "target": "source-policy-clone",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-clone-1",
        "name": "Source Policy Clone",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-clone-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-clone-source",
        "policy_id": "source-policy-clone-1",
        "name": "Base preset",
        "action_filter": "update",
        "query": "name",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_payload).status_code == 200
    clone = client.post(
        "/api/status/sources/policies/version-presets/source-policy-version-preset-clone-source/clone",
        params={"name": "Cloned preset"},
    )
    assert clone.status_code == 200
    clone_row = clone.json()
    assert clone_row["name"] == "Cloned preset"
    assert clone_row["id"] != "source-policy-version-preset-clone-source"
    assert clone_row["is_default"] is False
    assert clone_row["usage_count"] == 0
    rows = client.get("/api/status/sources/policies/source-policy-clone-1/version-presets")
    assert rows.status_code == 200
    names = sorted(item["name"] for item in rows.json())
    assert names == ["Base preset", "Cloned preset"]
    defaults = [item for item in rows.json() if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "source-policy-version-preset-clone-source"


def test_source_health_policy_version_preset_clone_auto_names_are_unique(client):
    channel_payload = {
        "id": "source-policy-clone-auto-drop",
        "name": "Source Policy Clone Auto Drop",
        "kind": "file",
        "target": "source-policy-clone-auto",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-clone-auto-1",
        "name": "Source Policy Clone Auto",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-clone-auto-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-clone-auto-source",
        "policy_id": "source-policy-clone-auto-1",
        "name": "Base preset",
        "action_filter": "update",
        "query": "name",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_payload).status_code == 200
    clone_one = client.post(
        "/api/status/sources/policies/version-presets/source-policy-version-preset-clone-auto-source/clone",
    )
    clone_two = client.post(
        "/api/status/sources/policies/version-presets/source-policy-version-preset-clone-auto-source/clone",
    )
    assert clone_one.status_code == 200
    assert clone_two.status_code == 200
    rows = client.get("/api/status/sources/policies/source-policy-clone-auto-1/version-presets")
    assert rows.status_code == 200
    names = sorted(item["name"] for item in rows.json())
    assert names == ["Base preset", "Base preset copy", "Base preset copy 2"]


def test_source_health_policy_version_preset_rename_updates_name_and_validates_non_empty(client):
    channel_payload = {
        "id": "source-policy-rename-drop",
        "name": "Source Policy Rename Drop",
        "kind": "file",
        "target": "source-policy-rename",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-rename-1",
        "name": "Source Policy Rename",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-rename-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    preset_payload = {
        "id": "source-policy-version-preset-rename-source",
        "policy_id": "source-policy-rename-1",
        "name": "Before rename",
        "action_filter": "update",
        "query": "name",
        "limit": 10,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_payload).status_code == 200
    renamed = client.post(
        "/api/status/sources/policies/version-presets/source-policy-version-preset-rename-source/rename",
        params={"name": "After rename"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "After rename"
    fetched = client.get("/api/status/sources/policies/version-presets/source-policy-version-preset-rename-source")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "After rename"
    invalid = client.post(
        "/api/status/sources/policies/version-presets/source-policy-version-preset-rename-source/rename",
        params={"name": "   "},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "name must be non-empty."


def test_source_health_policy_version_preset_export_and_import_bundle(client):
    channel_payload = {
        "id": "source-policy-export-import-drop",
        "name": "Source Policy Export Import Drop",
        "kind": "file",
        "target": "source-policy-export-import",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    source_policy_payload = {
        "id": "source-policy-export-source",
        "name": "Source Policy Export Source",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-export-import-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    target_policy_payload = {
        "id": "source-policy-export-target",
        "name": "Source Policy Export Target",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-export-import-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=source_policy_payload).status_code == 200
    assert client.post("/api/status/sources/policies", json=target_policy_payload).status_code == 200
    preset_one = {
        "id": "source-policy-version-preset-export-1",
        "policy_id": "source-policy-export-source",
        "name": "Export Default",
        "action_filter": "update",
        "query": "trigger",
        "limit": 10,
        "is_default": True,
        "owner_scope": "shared",
    }
    preset_two = {
        "id": "source-policy-version-preset-export-2",
        "policy_id": "source-policy-export-source",
        "name": "Export Secondary",
        "action_filter": None,
        "query": None,
        "limit": 25,
        "is_default": False,
        "owner_scope": "shared",
    }
    assert client.post("/api/status/sources/policies/version-presets", json=preset_one).status_code == 200
    assert client.post("/api/status/sources/policies/version-presets", json=preset_two).status_code == 200
    exported = client.get("/api/status/sources/policies/source-policy-export-source/version-presets/export")
    assert exported.status_code == 200
    export_payload = exported.json()
    assert export_payload["policy_id"] == "source-policy-export-source"
    assert len(export_payload["presets"]) == 2
    imported = client.post(
        "/api/status/sources/policies/source-policy-export-target/version-presets/import",
        json={"mode": "replace", "presets": export_payload["presets"]},
    )
    assert imported.status_code == 200
    imported_rows = imported.json()
    assert len(imported_rows) == 2
    assert all(item["policy_id"] == "source-policy-export-target" for item in imported_rows)
    assert all(item["id"] not in {"source-policy-version-preset-export-1", "source-policy-version-preset-export-2"} for item in imported_rows)
    target_rows = client.get("/api/status/sources/policies/source-policy-export-target/version-presets")
    assert target_rows.status_code == 200
    assert len(target_rows.json()) == 2
    defaults = [item for item in target_rows.json() if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "Export Default"


def test_scheduler_poll_runs_source_health_policy_worker_cycle(client):
    channel_payload = {
        "id": "worker-source-policy-drop",
        "name": "Worker Source Policy Drop",
        "kind": "file",
        "target": "worker-source-policy",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    assert client.post(
        "/api/observations/query",
        json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    ).status_code == 200
    original_fetch = api_module.service.fred.fetch_observations
    api_module.service.fred.fetch_observations = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fred down"))
    try:
        assert client.post(
            "/api/observations/query",
            json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        ).status_code == 200
    finally:
        api_module.service.fred.fetch_observations = original_fetch
    policy_payload = {
        "id": "worker-source-policy-1",
        "name": "Worker Macro Source Policy",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": True,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["worker-source-policy-drop"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    poll = client.post("/api/reports/scheduler/poll")
    assert poll.status_code == 200
    payload = poll.json()
    assert "source_policy_actions" in payload
    assert payload["source_policy_triggered"] >= 1
    runs = client.get("/api/status/sources/policies/runs", params={"trigger": "worker"})
    assert runs.status_code == 200
    assert len(runs.json()) >= 1


def test_source_health_policy_reason_routing_and_escalation_channels(client):
    base_channel = {
        "id": "source-policy-base",
        "name": "Source Policy Base",
        "kind": "file",
        "target": "source-policy-base",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    override_channel = {
        "id": "source-policy-override",
        "name": "Source Policy Override",
        "kind": "file",
        "target": "source-policy-override",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    escalation_channel = {
        "id": "source-policy-escalation",
        "name": "Source Policy Escalation",
        "kind": "file",
        "target": "source-policy-escalation",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=base_channel).status_code == 200
    assert client.post("/api/notifications/channels", json=override_channel).status_code == 200
    assert client.post("/api/notifications/channels", json=escalation_channel).status_code == 200
    assert client.post(
        "/api/observations/query",
        json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    ).status_code == 200
    original_fetch = api_module.service.fred.fetch_observations
    api_module.service.fred.fetch_observations = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fred down"))
    try:
        assert client.post(
            "/api/observations/query",
            json={"series_id": "fred:CPIAUCSL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        ).status_code == 200
    finally:
        api_module.service.fred.fetch_observations = original_fetch
    policy_payload = {
        "id": "source-policy-routing-1",
        "name": "Source Policy Routing",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": True,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-base"],
        "reason_channel_overrides": {"degraded": ["source-policy-override"]},
        "reason_severity": {"degraded": "high"},
        "reason_subject_templates": {"degraded": "[{severity}] {source_id} {reason}"},
        "escalation_channel_ids": ["source-policy-escalation"],
        "escalation_failure_threshold": 1,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200
    ran = client.post("/api/status/sources/policies/run")
    assert ran.status_code == 200
    actions = ran.json()
    triggered = next(item for item in actions if item.get("policy_id") == "source-policy-routing-1")
    assert triggered["reason"] == "degraded"
    assert triggered["severity"] == "high"
    assert triggered["escalated"] is True
    base_deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-base", "event_type": "manual"},
    )
    override_deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-override", "event_type": "manual"},
    )
    escalation_deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-escalation", "event_type": "manual"},
    )
    assert base_deliveries.status_code == 200
    assert override_deliveries.status_code == 200
    assert escalation_deliveries.status_code == 200
    assert len(base_deliveries.json()) == 0
    assert len(override_deliveries.json()) >= 1
    assert len(escalation_deliveries.json()) >= 1
    assert override_deliveries.json()[0]["payload"]["severity"] == "high"


def test_source_health_policy_schedule_window_suppresses_degraded_but_allows_down_when_configured(client):
    channel_payload = {
        "id": "source-policy-window-drop",
        "name": "Source Policy Window Drop",
        "kind": "file",
        "target": "source-policy-window",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    degraded_policy = {
        "id": "source-policy-window-degraded",
        "name": "Window Degraded",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-window-drop"],
        "active_weekdays": [0, 1, 2, 3, 4],
        "active_hour_start": 9,
        "active_hour_end": 18,
        "allow_down_outside_schedule": False,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=degraded_policy).status_code == 200
    down_policy = {
        "id": "source-policy-window-down",
        "name": "Window Down",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": False,
        "trigger_on_down": True,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-window-drop"],
        "active_weekdays": [0, 1, 2, 3, 4],
        "active_hour_start": 9,
        "active_hour_end": 18,
        "allow_down_outside_schedule": True,
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=down_policy).status_code == 200

    api_module.service._record_source_health(  # noqa: SLF001
        source_id="macro:fred",
        source_kind="macro",
        provider="fred",
        status="degraded",
        last_checked_at=datetime(2026, 4, 19, 2, 0, 0),  # Sunday 02:00 local
        last_success_at=datetime(2026, 4, 18, 12, 0, 0),
        last_failure_at=datetime(2026, 4, 19, 2, 0, 0),
        fallback_used=True,
        error_message="window test degraded",
    )
    actions = api_module.service.run_source_health_policies(
        now=datetime(2026, 4, 19, 2, 0, 0),
        trigger="manual",
    )
    degraded_actions = [item for item in actions if item.get("policy_id") == "source-policy-window-degraded"]
    assert any(item.get("status") == "skipped" and item.get("reason") == "outside_policy_window" for item in degraded_actions)
    deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-window-drop", "event_type": "manual"},
    )
    assert deliveries.status_code == 200
    assert deliveries.json() == []

    api_module.service._record_source_health(  # noqa: SLF001
        source_id="macro:fred",
        source_kind="macro",
        provider="fred",
        status="down",
        last_checked_at=datetime(2026, 4, 19, 2, 5, 0),
        last_failure_at=datetime(2026, 4, 19, 2, 5, 0),
        fallback_used=True,
        error_message="window test down",
    )
    actions_down = api_module.service.run_source_health_policies(
        now=datetime(2026, 4, 19, 2, 5, 0),
        trigger="manual",
    )
    down_actions = [item for item in actions_down if item.get("policy_id") == "source-policy-window-down"]
    assert any(item.get("status") == "triggered" and item.get("reason") == "down" for item in down_actions)
    deliveries_after = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-window-drop", "event_type": "manual"},
    )
    assert deliveries_after.status_code == 200
    assert len(deliveries_after.json()) >= 1
    assert deliveries_after.json()[0]["payload"]["reason"] == "down"


def test_source_health_policy_timezone_and_holiday_controls(client):
    channel_payload = {
        "id": "source-policy-tz-holiday-drop",
        "name": "Source Policy TZ Holiday Drop",
        "kind": "file",
        "target": "source-policy-tz-holiday",
        "event_types": ["manual"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/notifications/channels", json=channel_payload).status_code == 200
    policy_payload = {
        "id": "source-policy-tz-holiday-1",
        "name": "TZ Holiday Policy",
        "source_kind": "macro",
        "source_id": "macro:fred",
        "trigger_on_degraded": True,
        "trigger_on_down": False,
        "trigger_on_stale": False,
        "min_consecutive_failures": 1,
        "cooldown_minutes": 0,
        "notification_channel_ids": ["source-policy-tz-holiday-drop"],
        "active_weekdays": [0, 1, 2, 3, 4],
        "active_hour_start": 9,
        "active_hour_end": 18,
        "allow_down_outside_schedule": False,
        "timezone": "Asia/Shanghai",
        "holiday_calendar": "none",
        "holiday_dates": ["2026-04-22"],
        "owner_scope": "shared",
        "active": True,
    }
    assert client.post("/api/status/sources/policies", json=policy_payload).status_code == 200

    # 2026-04-22 02:00 UTC = 10:00 Asia/Shanghai (weekday business hour), but custom holiday => suppress
    api_module.service._record_source_health(  # noqa: SLF001
        source_id="macro:fred",
        source_kind="macro",
        provider="fred",
        status="degraded",
        last_checked_at=datetime(2026, 4, 22, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        last_failure_at=datetime(2026, 4, 22, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        fallback_used=True,
        error_message="tz holiday degraded test",
    )
    actions_holiday = api_module.service.run_source_health_policies(
        now=datetime(2026, 4, 22, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        trigger="manual",
    )
    assert any(
        item.get("policy_id") == "source-policy-tz-holiday-1"
        and item.get("status") == "skipped"
        and item.get("reason") == "outside_policy_window"
        for item in actions_holiday
    )

    # Next day same UTC hour => local weekday business hour and not holiday => trigger
    api_module.service._record_source_health(  # noqa: SLF001
        source_id="macro:fred",
        source_kind="macro",
        provider="fred",
        status="degraded",
        last_checked_at=datetime(2026, 4, 23, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        last_failure_at=datetime(2026, 4, 23, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        fallback_used=True,
        error_message="tz non-holiday degraded test",
    )
    actions_open = api_module.service.run_source_health_policies(
        now=datetime(2026, 4, 23, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        trigger="manual",
    )
    assert any(
        item.get("policy_id") == "source-policy-tz-holiday-1"
        and item.get("status") == "triggered"
        for item in actions_open
    )
    deliveries = client.get(
        "/api/notifications/deliveries",
        params={"channel_id": "source-policy-tz-holiday-drop", "event_type": "manual"},
    )
    assert deliveries.status_code == 200
    assert len(deliveries.json()) >= 1
