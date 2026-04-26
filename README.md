# Modular Macro Research Platform

OpenBB-first research platform scaffold for macro analysis, cross-asset monitoring, and investment strategy workflows.

## What is implemented

- Canonical domain model for series, observations, prices, screens, and dashboards
- Source adapters for FRED, World Bank, BLS, ECB, IMF, OECD, and optional OpenBB/Yahoo market data
- Explicit connector data-state visibility for real, cached, and demo-fallback data
- Optional OpenBB-backed market data provider with deterministic demo fallback
- FastAPI app exposing catalog, observations, prices, screens, scenarios, portfolios, reports, and dashboards
- Streamlit UI with V1 analyst workspace, global monitor, cross-country, cross-asset, regime, research-library, portfolio-lab, and report-studio views
- V1 Macro Brief generation path for standardized prototype outputs (markdown/xlsx by default)
- Raw snapshot archival helpers
- Database-backed normalized observations, market prices, dashboards, watchlists, saved screens, scenarios, model portfolios, and report templates/snapshots
- Snapshot exports in markdown, JSON, zipped CSV bundles, XLSX workbooks, and PPTX decks
- Scheduled report jobs with manual and due-run execution paths
- Report scheduler worker support, poll controls, and persisted run history
- Change monitor for ranked macro and cross-asset deltas, including report-section support
- Alert rules and persisted alert events for thresholded change detection and alert-driven report sections
- Persisted notification channels and delivery logs for alert events and report jobs, including test-send and retry flows
- Channel-level routing policy for event types, minimum alert significance, and digest-vs-immediate alert delivery
- Persisted notification digests for batched alert distribution
- Backup-channel fallback for failed deliveries and severity-based escalation fan-out for alert events
- Cooldown windows, duplicate suppression, and retry backoff/max-attempt governance for notification channels
- Channel pause/resume controls with paused-until enforcement and per-channel delivery health summaries
- Automatic channel auto-pause based on failure-rate and consecutive-failure thresholds
- Persisted notification routing audit trail for delivered/failed/suppressed/paused/inactive decisions
- Automatic recovery probes that can auto-resume previously auto-paused channels
- Configurable recovery probe profiles (`minimal`, `standard`, `verbose`) with optional JSON payload overrides
- Recovery probe throttling with cooldown and max-probes-per-hour safeguards
- Policy-based ops escalation on repeated failed/suppressed/paused routing decisions
- Ops incident lifecycle (`open`, `ack`, `resolved`) with API/UI triage workflows
- Ops incident ownership, priority, SLA due-times, overdue filtering, and summary metrics
- Persistent source-health registry with degraded/down fallback visibility and stale-source indicators
- Source-health policy engine with per-source stale-threshold overrides, cooldowns, and channel-based alerting
- Scheduled source-health policy execution in scheduler poll loop with persisted policy-run history
- Source-health reason-level severity routing, per-reason subject templates, and escalation tiers by failure threshold
- Policy scheduling windows (active weekdays/hours) with optional critical `down` bypass outside schedule
- Per-policy timezone and holiday controls (built-in regional calendars + custom holiday date overrides)
- Soft-delete policy lifecycle with archive/restore controls while preserving policy-run history
- Policy version history with per-change snapshots/diffs for create, update, archive, restore, rollback, and version comparison actions, with saved filter presets, default preset support, preset lifecycle controls, filtering, and JSON/CSV export in the UI
- Tests for catalog shape, analytics, and API behavior

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

Run the API:

```bash
uvicorn macro_platform.api.app:app --reload
```

Run the Streamlit app:

```bash
streamlit run src/macro_platform/ui/app.py
```

Optional live connector smoke test:

```bash
python scripts/live_connector_smoke.py --allow-fail
```

Bootstrap and refresh V1 workspace:

```bash
python scripts/bootstrap_v1_workspace.py
python scripts/bootstrap_v1_workspace.py --run-macro-brief-job
```

Generate a V1 Macro Brief via API:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/reports/macro-brief"
```

Bootstrap and run the scheduled V1 Macro Brief job:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/reports/macro-brief/job/bootstrap"
curl -X POST "http://127.0.0.1:8000/api/v1/reports/macro-brief/job/run"
```

Complete missing exports for an existing V1 Macro Brief snapshot:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/reports/macro-brief/snapshots/<snapshot_id>/complete-exports"
```

## Environment

Copy `.env.example` to `.env` and adjust values if needed.

Key defaults:

- API runs offline-friendly with source-health visibility when public APIs or OpenBB are unavailable
- Local development defaults to SQLite via `DATABASE_URL=sqlite:///./data/macro_platform.db`
- Docker Compose runs against Postgres/Timescale via the `db` service
- FRED API key is optional for public series
- BLS API key can be set with `BLS_API_KEY` to avoid unauthenticated request limits
- Raw snapshots are archived to `data/raw/`
- Notification drops are written under `data/notifications/`
- Optional background scheduler can be enabled with `ENABLE_REPORT_SCHEDULER=true`

## Architecture

- `src/macro_platform/catalog`: tracked macro series and default dashboards
- `src/macro_platform/providers`: source adapters
- `src/macro_platform/services`: orchestration and business logic
- `src/macro_platform/api`: FastAPI surface
- `src/macro_platform/ui`: Streamlit research UI
- `tests`: analytics, catalog, and API verification
