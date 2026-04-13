# Modular Macro Research Platform

OpenBB-first research platform scaffold for macro analysis, cross-asset monitoring, and investment strategy workflows.

## What is implemented

- Canonical domain model for series, observations, prices, screens, and dashboards
- Source adapters for FRED and World Bank
- Optional OpenBB-backed market data provider with deterministic demo fallback
- FastAPI app exposing catalog, observations, prices, screens, scenarios, portfolios, reports, and dashboards
- Streamlit UI with global monitor, cross-country, cross-asset, regime, research-library, portfolio-lab, and report-studio views
- Raw snapshot archival helpers
- Database-backed normalized observations, market prices, dashboards, watchlists, saved screens, scenarios, model portfolios, and report templates/snapshots
- Snapshot exports in markdown, JSON, zipped CSV bundles, XLSX workbooks, and PPTX decks
- Scheduled report jobs with manual and due-run execution paths
- Report scheduler worker support, poll controls, and persisted run history
- Change monitor for ranked macro and cross-asset deltas, including report-section support
- Alert rules and persisted alert events for thresholded change detection and alert-driven report sections
- Persisted notification channels and delivery logs for alert events and report jobs
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

## Environment

Copy `.env.example` to `.env` and adjust values if needed.

Key defaults:

- API runs offline-friendly with demo market data when OpenBB is not installed
- Local development defaults to SQLite via `DATABASE_URL=sqlite:///./data/macro_platform.db`
- Docker Compose runs against Postgres/Timescale via the `db` service
- FRED API key is optional for public series
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
