from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pandas as pd
import plotly.express as px
import streamlit as st

from macro_platform.domain.models import (
    ChangeAlertRule,
    ModelPortfolio,
    NotificationChannel,
    ObservationQuery,
    PortfolioHolding,
    ReportJob,
    ReportTemplate,
    ReportTemplateSection,
    SavedScreen,
    ScenarioDefinition,
    ScenarioShock,
    ScreenFilter,
    ScreenSpec,
    Watchlist,
)
from macro_platform.services.platform import PlatformService

st.set_page_config(page_title="Macro Platform", layout="wide")
service = PlatformService()


def render_timeseries(series_id: str, title: str) -> None:
    observations = service.query_observations(
        ObservationQuery(series_id=series_id, start_date=date.today() - timedelta(days=365 * 5))
    )
    frame = pd.DataFrame([item.model_dump(mode="json") for item in observations])
    if frame.empty:
        st.warning(f"No data available for {series_id}")
        return
    frame["date"] = pd.to_datetime(frame["date"])
    figure = px.line(frame, x="date", y="value", title=title)
    st.plotly_chart(figure, use_container_width=True)


st.title("Modular Macro Research Platform")
view = st.sidebar.selectbox(
    "Workspace",
    [
        "Global Macro Monitor",
        "Country Dashboard",
        "Cross Asset Monitor",
        "Regime Monitor",
        "Release Calendar",
        "Change Monitor",
        "Alert Center",
        "Notification Center",
        "Screening Lab",
        "Research Library",
        "Portfolio Lab",
        "Report Studio",
    ],
)

if view == "Global Macro Monitor":
    st.subheader("Global Macro Monitor")
    monitor = pd.DataFrame(service.get_global_macro_monitor())
    st.dataframe(monitor, use_container_width=True)
    left, right = st.columns(2)
    with left:
        render_timeseries("fred:CPIAUCSL", "US CPI All Items")
    with right:
        render_timeseries("fred:UNRATE", "US Unemployment Rate")

elif view == "Country Dashboard":
    country = st.sidebar.selectbox("Country", ["US", "CN", "EA", "JP", "GB", "CA"])
    st.subheader(f"{country} Dashboard")
    snapshot = pd.DataFrame(service.get_country_snapshot(country))
    st.dataframe(snapshot, use_container_width=True)
    series_map = {
        "US": "world_bank:USA:NY.GDP.MKTP.CD",
        "CN": "world_bank:CHN:NY.GDP.MKTP.CD",
        "EA": "world_bank:EMU:NY.GDP.MKTP.CD",
        "JP": "world_bank:JPN:NY.GDP.MKTP.CD",
        "GB": "world_bank:GBR:NY.GDP.MKTP.CD",
        "CA": "world_bank:CAN:NY.GDP.MKTP.CD",
    }
    render_timeseries(series_map[country], f"{country} GDP")

elif view == "Cross Asset Monitor":
    st.subheader("Cross Asset Monitor")
    monitor = pd.DataFrame(service.get_cross_asset_monitor())
    st.dataframe(monitor, use_container_width=True)
    ticker = st.selectbox("Ticker", list(service.market_universe.keys()))
    prices = pd.DataFrame([item.model_dump(mode="json") for item in service.get_prices(ticker)])
    prices["date"] = pd.to_datetime(prices["date"])
    st.plotly_chart(px.line(prices, x="date", y="close", title=f"{ticker} Price"), use_container_width=True)

elif view == "Regime Monitor":
    st.subheader("Regime Monitor")
    regime = service.get_regime_snapshot()
    cols = st.columns(4)
    cols[0].metric("Inflation YoY", f"{regime['inflation_yoy']}%")
    cols[1].metric("Unemployment", f"{regime['unemployment_rate']}%")
    cols[2].metric("10Y-2Y", f"{regime['yield_curve_slope']} pts")
    cols[3].metric("Current Regime", str(regime["regime"]))
    left, right = st.columns(2)
    with left:
        render_timeseries("fred:DGS10", "US 10Y Yield")
    with right:
        render_timeseries("fred:DGS2", "US 2Y Yield")

elif view == "Release Calendar":
    st.subheader("Release Calendar And Freshness")
    country_filter = st.sidebar.selectbox("Country filter", ["All", "US", "EA", "CN", "JP", "GB", "CA"])
    country = None if country_filter == "All" else country_filter
    horizon = st.sidebar.slider("Days ahead", min_value=14, max_value=180, value=60, step=7)
    calendar = pd.DataFrame([item.model_dump(mode="json") for item in service.get_release_calendar(country=country, days=horizon)])
    freshness = pd.DataFrame([item.model_dump(mode="json") for item in service.get_freshness_status(country=country)])
    left, right = st.columns(2)
    with left:
        st.caption("Expected next releases")
        st.dataframe(calendar, use_container_width=True)
    with right:
        st.caption("Freshness status")
        st.dataframe(freshness, use_container_width=True)

elif view == "Change Monitor":
    st.subheader("Change Monitor")
    country_filter = st.sidebar.selectbox("Country filter", ["All", "US", "EA", "CN", "JP", "GB", "CA"], key="change_country")
    topic_filter = st.sidebar.selectbox("Topic filter", ["All", "inflation", "labor", "policy", "rates", "fx", "growth"], key="change_topic")
    asset_filter = st.sidebar.selectbox("Asset class filter", ["All", "equities", "rates", "commodities", "fx", "crypto"], key="change_asset")
    limit = st.sidebar.slider("Signals", min_value=5, max_value=50, value=20, step=5, key="change_limit")
    rows = pd.DataFrame(
        [
            item.model_dump(mode="json")
            for item in service.get_change_monitor(
                country=None if country_filter == "All" else country_filter,
                topic=None if topic_filter == "All" else topic_filter,
                asset_class=None if asset_filter == "All" else asset_filter,
                limit=limit,
            )
        ]
    )
    st.dataframe(rows, use_container_width=True)
    if not rows.empty:
        scatter = px.scatter(
            rows,
            x="percent_change",
            y="absolute_change",
            color="significance",
            hover_name="title",
            symbol="entity_type",
            title="Latest Ranked Change Signals",
        )
        st.plotly_chart(scatter, use_container_width=True)

elif view == "Alert Center":
    st.subheader("Alert Center")
    left, right = st.columns(2)
    with left:
        st.caption("Create alert rule")
        rule_name = st.text_input("Rule name", value="")
        entity_type = st.selectbox("Entity type", ["any", "series", "asset"])
        topic = st.selectbox("Topic", ["all", "inflation", "labor", "policy", "rates", "fx", "growth", "markets"])
        country = st.selectbox("Country", ["all", "US", "EA", "CN", "JP", "GB", "CA"])
        asset_class = st.selectbox("Asset class", ["all", "equities", "rates", "commodities", "fx", "crypto"])
        saved_watchlists = service.list_watchlists()
        watchlist_options = ["none"] + [item.id for item in saved_watchlists]
        watchlist_id = st.selectbox(
            "Watchlist filter",
            watchlist_options,
            format_func=lambda x: "No watchlist" if x == "none" else next(item.name for item in saved_watchlists if item.id == x),
        )
        min_significance = st.selectbox("Minimum significance", ["high", "medium", "low"], index=1)
        min_abs_change = st.number_input("Minimum absolute change", min_value=0.0, value=0.0, step=0.1)
        min_pct_change = st.number_input("Minimum percent change", min_value=0.0, value=0.0, step=0.5)
        channels = service.list_notification_channels(active_only=True)
        selected_channels = st.multiselect(
            "Notification channels",
            options=[item.id for item in channels],
            format_func=lambda x: next(item.name for item in channels if item.id == x),
        )
        active = st.checkbox("Active", value=True, key="alert_active")
        if st.button("Save alert rule") and rule_name.strip():
            rule = ChangeAlertRule(
                id=f"alert-rule-{uuid4().hex[:8]}",
                name=rule_name.strip(),
                entity_type=entity_type,
                topic=None if topic == "all" else topic,
                country=None if country == "all" else country,
                asset_class=None if asset_class == "all" else asset_class,
                watchlist_id=None if watchlist_id == "none" else watchlist_id,
                min_significance=min_significance,
                min_absolute_change=min_abs_change or None,
                min_percent_change=min_pct_change or None,
                notification_channel_ids=selected_channels,
                active=active,
            )
            service.save_change_alert_rule(rule)
            st.success(f"Saved alert rule: {rule.name}")
        rules = pd.DataFrame([item.model_dump(mode="json") for item in service.list_change_alert_rules()])
        st.dataframe(rules, use_container_width=True)
    with right:
        st.caption("Alert events")
        rules = service.list_change_alert_rules()
        selected_rule = st.selectbox(
            "Scan rule",
            ["all"] + [item.id for item in rules],
            format_func=lambda x: "All rules" if x == "all" else next(item.name for item in rules if item.id == x),
        )
        if st.button("Run alert scan"):
            created = service.run_change_alert_scan(rule_id=None if selected_rule == "all" else selected_rule)
            st.success(f"Created {len(created)} new alert event(s)")
        status_filter = st.selectbox("Event status", ["all", "new", "published", "dismissed"])
        events = service.list_change_alert_events(status=None if status_filter == "all" else status_filter, limit=50)
        event_frame = pd.DataFrame(
            [
                {
                    "id": event.id,
                    "rule_name": event.rule_name,
                    "key": event.signal.key,
                    "title": event.signal.title,
                    "significance": event.signal.significance,
                    "status": event.status,
                    "observation_date": event.signal.observation_date,
                    "percent_change": event.signal.percent_change,
                }
                for event in events
            ]
        )
        st.dataframe(event_frame, use_container_width=True)
        if events:
            event_id = st.selectbox("Update event", [item.id for item in events])
            next_status = st.selectbox("Set status", ["new", "published", "dismissed"])
            if st.button("Update event status"):
                event = service.update_change_alert_event_status(event_id, next_status)
                st.success(f"Updated {event.id} to {event.status}")

elif view == "Notification Center":
    st.subheader("Notification Center")
    left, right = st.columns(2)
    with left:
        st.caption("Create notification channel")
        channel_name = st.text_input("Channel name", value="")
        channel_kind = st.selectbox("Channel kind", ["file", "email", "webhook", "slack"])
        target_help = {
            "file": "Folder or label for file drops",
            "email": "Recipient email address",
            "webhook": "Webhook URL",
            "slack": "Slack webhook URL",
        }
        channel_target = st.text_input("Target", value="", help=target_help[channel_kind])
        channel_notes = st.text_area("Channel notes", value="")
        channel_active = st.checkbox("Channel active", value=True)
        if st.button("Save notification channel") and channel_name.strip() and channel_target.strip():
            channel = NotificationChannel(
                id=f"notification-channel-{uuid4().hex[:8]}",
                name=channel_name.strip(),
                kind=channel_kind,
                target=channel_target.strip(),
                notes=channel_notes or None,
                active=channel_active,
            )
            service.save_notification_channel(channel)
            st.success(f"Saved notification channel: {channel.name}")
        channels_frame = pd.DataFrame([item.model_dump(mode="json") for item in service.list_notification_channels()])
        st.dataframe(channels_frame, use_container_width=True)
    with right:
        st.caption("Delivery history")
        channels = service.list_notification_channels()
        channel_filter = st.selectbox(
            "Channel filter",
            ["all"] + [item.id for item in channels],
            format_func=lambda x: "All channels" if x == "all" else next(item.name for item in channels if item.id == x),
        )
        event_type_filter = st.selectbox("Event type", ["all", "alert_event", "report_job", "manual"])
        status_filter = st.selectbox("Delivery status", ["all", "success", "failed"], key="delivery_status")
        deliveries = pd.DataFrame(
            [
                item.model_dump(mode="json")
                for item in service.list_notification_deliveries(
                    channel_id=None if channel_filter == "all" else channel_filter,
                    event_type=None if event_type_filter == "all" else event_type_filter,
                    status=None if status_filter == "all" else status_filter,
                    limit=100,
                )
            ]
        )
        st.dataframe(deliveries, use_container_width=True)
        if channels:
            test_channel_id = st.selectbox(
                "Send test notification",
                [item.id for item in channels],
                key="test_notification_channel",
                format_func=lambda x: next(item.name for item in channels if item.id == x),
            )
            test_subject = st.text_input("Test subject", value="")
            if st.button("Send test notification"):
                delivery = service.send_test_notification(test_channel_id, subject=test_subject or None)
                st.success(f"Sent test notification via {delivery.channel_name}")
        delivery_rows = service.list_notification_deliveries(
            channel_id=None if channel_filter == "all" else channel_filter,
            event_type=None if event_type_filter == "all" else event_type_filter,
            status=None if status_filter == "all" else status_filter,
            limit=100,
        )
        if delivery_rows:
            retry_delivery_id = st.selectbox("Retry delivery", [item.id for item in delivery_rows])
            if st.button("Retry selected delivery"):
                delivery = service.retry_notification_delivery(retry_delivery_id)
                st.success(f"Retried delivery {delivery.id} via {delivery.channel_name}")

elif view == "Screening Lab":
    st.subheader("Screening Lab")
    saved_watchlists = service.list_watchlists()
    watchlist_options = {"All tracked assets": list(service.market_universe.keys())}
    for watchlist in saved_watchlists:
        watchlist_options[watchlist.name] = watchlist.tickers
    selected_watchlist = st.selectbox("Universe", list(watchlist_options.keys()))
    min_return = st.slider("Minimum 63D return", min_value=-20.0, max_value=20.0, value=0.0, step=0.5)
    max_drawdown = st.slider("Maximum drawdown", min_value=-30.0, max_value=0.0, value=-10.0, step=0.5)
    spec = ScreenSpec(
        universe=watchlist_options[selected_watchlist],
        filters=[
            ScreenFilter(field="return_63d", operator="gte", value=min_return),
            ScreenFilter(field="drawdown", operator="gte", value=max_drawdown / 100),
        ]
    )
    saved_screen_name = st.text_input("Save current screen as", value="")
    if st.button("Save screen") and saved_screen_name.strip():
        payload = SavedScreen(id=f"screen-{uuid4().hex[:8]}", name=saved_screen_name.strip(), spec=spec)
        service.save_saved_screen(payload)
        st.success(f"Saved screen: {payload.name}")
    results = pd.DataFrame(service.run_screen(spec))
    st.dataframe(results, use_container_width=True)

elif view == "Research Library":
    st.subheader("Research Library")
    left, right = st.columns(2)
    with left:
        st.caption("Watchlists")
        name = st.text_input("Watchlist name", value="")
        tickers = st.multiselect("Tickers", options=list(service.market_universe.keys()))
        notes = st.text_area("Notes", value="")
        if st.button("Save watchlist") and name.strip() and tickers:
            watchlist = Watchlist(id=f"watchlist-{uuid4().hex[:8]}", name=name.strip(), tickers=tickers, notes=notes or None)
            service.save_watchlist(watchlist)
            st.success(f"Saved watchlist: {watchlist.name}")
        watchlists = pd.DataFrame([item.model_dump(mode="json") for item in service.list_watchlists()])
        st.dataframe(watchlists, use_container_width=True)
    with right:
        st.caption("Saved screens")
        saved_screens = pd.DataFrame([item.model_dump(mode="json") for item in service.list_saved_screens()])
        st.dataframe(saved_screens, use_container_width=True)

elif view == "Portfolio Lab":
    st.subheader("Portfolio Lab")
    left, right = st.columns(2)
    with left:
        st.caption("Scenario Builder")
        scenario_name = st.text_input("Scenario name", value="")
        shock_target = st.selectbox("Shock target", ["asset_class", "ticker"])
        if shock_target == "asset_class":
            asset_class = st.selectbox("Asset class", sorted(set(service.market_universe.values())))
            ticker = None
        else:
            ticker = st.selectbox("Ticker", list(service.market_universe.keys()))
            asset_class = None
        shock_pct = st.slider("Shock pct", min_value=-25.0, max_value=25.0, value=-5.0, step=0.5)
        if st.button("Save scenario") and scenario_name.strip():
            scenario = ScenarioDefinition(
                id=f"scenario-{uuid4().hex[:8]}",
                name=scenario_name.strip(),
                shocks=[ScenarioShock(label="Primary shock", asset_class=asset_class, ticker=ticker, shock_pct=shock_pct)],
            )
            service.save_scenario(scenario)
            st.success(f"Saved scenario: {scenario.name}")
        scenarios = pd.DataFrame([item.model_dump(mode="json") for item in service.list_scenarios()])
        st.dataframe(scenarios, use_container_width=True)
    with right:
        st.caption("Model Portfolio")
        portfolio_name = st.text_input("Portfolio name", value="")
        selected_tickers = st.multiselect("Holdings", options=list(service.market_universe.keys()))
        default_weight = round(100 / len(selected_tickers), 2) if selected_tickers else 0.0
        if st.button("Save portfolio") and portfolio_name.strip() and selected_tickers:
            holdings = [
                PortfolioHolding(ticker=ticker, weight=default_weight)
                for ticker in selected_tickers
            ]
            portfolio = ModelPortfolio(id=f"portfolio-{uuid4().hex[:8]}", name=portfolio_name.strip(), holdings=holdings)
            service.save_model_portfolio(portfolio)
            st.success(f"Saved portfolio: {portfolio.name}")
        portfolios = service.list_model_portfolios()
        portfolio_frame = pd.DataFrame([item.model_dump(mode="json") for item in portfolios])
        st.dataframe(portfolio_frame, use_container_width=True)

    st.caption("Portfolio Summary")
    portfolios = service.list_model_portfolios()
    scenarios = service.list_scenarios()
    if portfolios:
        selected_portfolio = st.selectbox("Saved portfolio", [item.id for item in portfolios], format_func=lambda x: next(item.name for item in portfolios if item.id == x))
        scenario_options = ["none"] + [item.id for item in scenarios]
        selected_scenario = st.selectbox("Scenario", scenario_options, format_func=lambda x: "No scenario" if x == "none" else next(item.name for item in scenarios if item.id == x))
        summary = pd.DataFrame(
            service.get_portfolio_summary(
                selected_portfolio,
                scenario_id=None if selected_scenario == "none" else selected_scenario,
            )
        )
        st.dataframe(summary, use_container_width=True)

elif view == "Report Studio":
    st.subheader("Report Studio")
    left, right = st.columns(2)
    with left:
        st.caption("Create report template")
        template_name = st.text_input("Template name", value="")
        section_kind = st.selectbox(
            "Section type",
            ["global_monitor", "cross_asset_monitor", "change_monitor", "alert_monitor", "release_calendar", "saved_screen", "portfolio_summary", "dashboard_summary"],
        )
        section_title = st.text_input("Section title", value="")
        ref_options = {
            "saved_screen": [item.id for item in service.list_saved_screens()],
            "portfolio_summary": [item.id for item in service.list_model_portfolios()],
            "dashboard_summary": [item.id for item in service.list_dashboards()],
        }
        ref_id = None
        if section_kind in ref_options:
            options = ref_options[section_kind]
            ref_id = st.selectbox("Reference", options if options else [""])
            if ref_id == "":
                ref_id = None
        days = None
        scenario_ref = None
        if section_kind == "release_calendar":
            days = st.slider("Release horizon days", min_value=14, max_value=180, value=60, step=7)
        change_topic = None
        change_asset = None
        change_limit = None
        alert_rule_id = None
        alert_status = None
        alert_publish = None
        if section_kind == "change_monitor":
            change_topic = st.selectbox("Change topic", ["all", "inflation", "labor", "policy", "rates", "fx", "growth"])
            change_asset = st.selectbox("Change asset class", ["all", "equities", "rates", "commodities", "fx", "crypto"])
            change_limit = st.slider("Change rows", min_value=5, max_value=25, value=10, step=5)
        if section_kind == "alert_monitor":
            saved_rules = service.list_change_alert_rules()
            alert_rule_options = ["all"] + [item.id for item in saved_rules]
            alert_rule_id = st.selectbox(
                "Alert rule",
                alert_rule_options,
                format_func=lambda x: "All active rules" if x == "all" else next(item.name for item in saved_rules if item.id == x),
            )
            alert_status = st.selectbox("Alert status", ["new", "published", "dismissed"])
            change_limit = st.slider("Alert rows", min_value=5, max_value=25, value=10, step=5, key="alert_rows")
            alert_publish = st.checkbox("Mark included alerts published", value=False)
        if section_kind == "portfolio_summary":
            scenario_options = ["none"] + [item.id for item in service.list_scenarios()]
            scenario_ref = st.selectbox("Scenario overlay", scenario_options)
        if st.button("Save report template") and template_name.strip() and section_title.strip():
            params = {}
            if days is not None:
                params["days"] = days
            if change_topic and change_topic != "all":
                params["topic"] = change_topic
            if change_asset and change_asset != "all":
                params["asset_class"] = change_asset
            if change_limit is not None:
                params["limit"] = change_limit
            if alert_rule_id and alert_rule_id != "all":
                params["rule_id"] = alert_rule_id
            if alert_status:
                params["status"] = alert_status
            if alert_publish is not None:
                params["publish_included"] = alert_publish
            if scenario_ref and scenario_ref != "none":
                params["scenario_id"] = scenario_ref
            template = ReportTemplate(
                id=f"report-template-{uuid4().hex[:8]}",
                name=template_name.strip(),
                sections=[ReportTemplateSection(kind=section_kind, title=section_title.strip(), ref_id=ref_id, params=params)],
            )
            service.save_report_template(template)
            st.success(f"Saved report template: {template.name}")
        templates = pd.DataFrame([item.model_dump(mode="json") for item in service.list_report_templates()])
        st.dataframe(templates, use_container_width=True)
    with right:
        st.caption("Generate report snapshot")
        templates = service.list_report_templates()
        if templates:
            selected_template = st.selectbox(
                "Template",
                [item.id for item in templates],
                format_func=lambda x: next(item.name for item in templates if item.id == x),
            )
            snapshot_name = st.text_input("Snapshot name", value="")
            if st.button("Generate report"):
                snapshot = service.generate_report_snapshot(selected_template, name_override=snapshot_name or None)
                st.success(f"Generated report: {snapshot.name}")
        snapshots = pd.DataFrame([item.model_dump(mode="json") for item in service.list_report_snapshots()])
        st.dataframe(snapshots, use_container_width=True)
        if templates and not snapshots.empty:
            snapshot_id = st.selectbox("Snapshot details", list(snapshots["id"]))
            snapshot = service.get_report_snapshot(snapshot_id)
            st.write(snapshot.summary)
            if snapshot.output_path:
                st.code(snapshot.output_path)
            export_format = st.selectbox("Export format", ["markdown", "json", "csv_zip", "xlsx", "pptx"])
            if st.button("Export snapshot"):
                snapshot = service.export_report_snapshot(snapshot_id, export_format)
                st.success(f"Exported {export_format}: {snapshot.export_paths.get(export_format)}")
            if snapshot.export_paths:
                st.caption("Available exports")
                export_frame = pd.DataFrame(
                    [{"format": key, "path": value} for key, value in snapshot.export_paths.items()]
                )
                st.dataframe(export_frame, use_container_width=True)

    st.divider()
    st.caption("Scheduled report jobs")
    jobs_left, jobs_right = st.columns(2)
    templates = service.list_report_templates()
    with jobs_left:
        if templates:
            job_name = st.text_input("Job name", value="")
            selected_template_for_job = st.selectbox(
                "Template for job",
                [item.id for item in templates],
                key="job_template_id",
                format_func=lambda x: next(item.name for item in templates if item.id == x),
            )
            cadence = st.selectbox("Cadence", ["manual", "daily", "weekly"])
            run_hour = st.slider("Run hour (local)", min_value=0, max_value=23, value=8)
            run_day = None
            if cadence == "weekly":
                run_day = st.selectbox(
                    "Run day",
                    options=list(range(7)),
                    format_func=lambda x: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][x],
                )
            export_formats = st.multiselect(
                "Export formats",
                options=["markdown", "json", "csv_zip", "xlsx", "pptx"],
                default=["markdown", "xlsx"],
            )
            channels = service.list_notification_channels(active_only=True)
            selected_job_channels = st.multiselect(
                "Notification channels",
                options=[item.id for item in channels],
                key="job_notification_channels",
                format_func=lambda x: next(item.name for item in channels if item.id == x),
            )
            active = st.checkbox("Active", value=True)
            if st.button("Save report job") and job_name.strip() and export_formats:
                job = ReportJob(
                    id=f"report-job-{uuid4().hex[:8]}",
                    name=job_name.strip(),
                    template_id=selected_template_for_job,
                    cadence=cadence,
                    run_hour_local=run_hour,
                    run_day_of_week=run_day,
                    export_formats=export_formats,
                    notification_channel_ids=selected_job_channels,
                    active=active,
                )
                service.save_report_job(job)
                st.success(f"Saved report job: {job.name}")
        else:
            st.info("Create a report template first.")
    with jobs_right:
        jobs = service.list_report_jobs()
        jobs_frame = pd.DataFrame([item.model_dump(mode="json") for item in jobs])
        st.dataframe(jobs_frame, use_container_width=True)
        if jobs:
            selected_job = st.selectbox(
                "Run saved job",
                [item.id for item in jobs],
                format_func=lambda x: next(item.name for item in jobs if item.id == x),
            )
            if st.button("Run selected job now"):
                completed_job = service.run_report_job(selected_job)
                st.success(f"Ran job: {completed_job.name}")
            if st.button("Run due jobs"):
                completed = service.run_due_report_jobs()
                st.success(f"Ran {len(completed)} due jobs")
            if st.button("Poll scheduler once"):
                completed = service.run_due_report_jobs(trigger="worker")
                succeeded = sum(1 for item in completed if item.last_run_status == "success")
                failed = sum(1 for item in completed if item.last_run_status == "failed")
                st.success(f"Scheduler polled {len(completed)} jobs: {succeeded} succeeded, {failed} failed")
            runs = pd.DataFrame([item.model_dump(mode="json") for item in service.list_report_job_runs(limit=20)])
            st.caption("Recent run history")
            st.dataframe(runs, use_container_width=True)
