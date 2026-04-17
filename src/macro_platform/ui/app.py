from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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
    SourceHealthPolicy,
    SourceHealthPolicyVersionPresetImportRequest,
    SourceHealthPolicyVersionPreset,
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
        "Data Quality",
        "Change Monitor",
        "Alert Center",
        "Notification Center",
        "Ops Incidents",
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

elif view == "Data Quality":
    st.subheader("Data Quality And Source Health")
    summary = service.get_source_health_summary()
    metric_total, metric_healthy, metric_degraded, metric_stale = st.columns(4)
    metric_total.metric("Tracked Sources", summary.get("total", 0))
    metric_healthy.metric("Healthy", summary.get("healthy", 0))
    metric_degraded.metric("Degraded", summary.get("degraded", 0) + summary.get("down", 0))
    metric_stale.metric("Stale", summary.get("stale", 0))
    kind_filter = st.selectbox("Source kind", ["all", "macro", "market"])
    status_filter = st.selectbox("Status", ["all", "healthy", "degraded", "down", "unknown"])
    rows = pd.DataFrame(
        [
            item.model_dump(mode="json")
            for item in service.list_source_health(
                source_kind=None if kind_filter == "all" else kind_filter,
                status=None if status_filter == "all" else status_filter,
                limit=200,
            )
        ]
    )
    st.dataframe(rows, use_container_width=True)
    all_sources = service.list_source_health(limit=500)
    left, right = st.columns(2)
    with left:
        st.caption("Source threshold override")
        source_options = [item.id for item in all_sources]
        if source_options:
            selected_source_id = st.selectbox("Source", source_options)
            selected_source = next(item for item in all_sources if item.id == selected_source_id)
            threshold_minutes = st.number_input(
                "Stale threshold (minutes)",
                min_value=1,
                value=int(selected_source.stale_threshold_minutes),
                step=30,
            )
            if st.button("Update source threshold"):
                updated = service.set_source_stale_threshold(selected_source_id, int(threshold_minutes))
                st.success(f"Updated {updated.id} stale threshold to {updated.stale_threshold_minutes} minutes")
        else:
            st.info("No tracked sources yet. Query data first to initialize source records.")
    with right:
        st.caption("Source health policy")
        policy_catalog = service.list_source_health_policies(include_archived=True, limit=200)
        policy_selector_options = ["Create new"] + [item.id for item in policy_catalog]
        selected_policy_ref = st.selectbox(
            "Policy profile",
            policy_selector_options,
            format_func=lambda x: "Create new policy" if x == "Create new" else next(
                f"{item.name} ({'active' if item.active else 'inactive'})"
                for item in policy_catalog
                if item.id == x
            ),
        )
        editing_policy = None if selected_policy_ref == "Create new" else next(
            item for item in policy_catalog if item.id == selected_policy_ref
        )
        state_key = "new" if editing_policy is None else editing_policy.id

        policy_name = st.text_input(
            "Policy name",
            value="" if editing_policy is None else editing_policy.name,
            key=f"source_policy_name_{state_key}",
        )
        policy_kind_default = "all" if editing_policy is None or editing_policy.source_kind is None else editing_policy.source_kind
        policy_kind = st.selectbox(
            "Policy source kind",
            ["all", "macro", "market"],
            index=["all", "macro", "market"].index(policy_kind_default),
            key=f"source_policy_kind_{state_key}",
        )
        policy_source_scope_default = (
            "single source"
            if editing_policy is not None and editing_policy.source_id
            else "all"
        )
        policy_source_scope = st.selectbox(
            "Policy source scope",
            ["all", "single source"],
            index=["all", "single source"].index(policy_source_scope_default),
            key=f"source_policy_scope_{state_key}",
        )
        policy_source_id = None
        if policy_source_scope == "single source" and source_options:
            source_index = 0
            if editing_policy is not None and editing_policy.source_id in source_options:
                source_index = source_options.index(editing_policy.source_id)
            policy_source_id = st.selectbox(
                "Policy source",
                source_options,
                index=source_index,
                key=f"source_policy_source_{state_key}",
            )
        trigger_degraded = st.checkbox(
            "Trigger on degraded",
            value=True if editing_policy is None else editing_policy.trigger_on_degraded,
            key=f"source_policy_trigger_degraded_{state_key}",
        )
        trigger_down = st.checkbox(
            "Trigger on down",
            value=True if editing_policy is None else editing_policy.trigger_on_down,
            key=f"source_policy_trigger_down_{state_key}",
        )
        trigger_stale = st.checkbox(
            "Trigger on stale",
            value=True if editing_policy is None else editing_policy.trigger_on_stale,
            key=f"source_policy_trigger_stale_{state_key}",
        )
        min_failures = st.number_input(
            "Min consecutive failures",
            min_value=1,
            value=1 if editing_policy is None else int(editing_policy.min_consecutive_failures),
            step=1,
            key=f"source_policy_min_failures_{state_key}",
        )
        policy_stale_threshold = st.number_input(
            "Override stale threshold (minutes, optional)",
            min_value=0,
            value=0 if editing_policy is None or editing_policy.stale_threshold_minutes is None else int(editing_policy.stale_threshold_minutes),
            step=30,
            key=f"source_policy_stale_threshold_{state_key}",
        )
        policy_cooldown = st.number_input(
            "Policy cooldown (minutes)",
            min_value=0,
            value=60 if editing_policy is None else int(editing_policy.cooldown_minutes),
            step=5,
            key=f"source_policy_cooldown_{state_key}",
        )
        channels = service.list_notification_channels()
        selected_channels = st.multiselect(
            "Notification channels",
            options=[item.id for item in channels],
            default=[] if editing_policy is None else editing_policy.notification_channel_ids,
            format_func=lambda x: next(item.name for item in channels if item.id == x),
            key=f"source_policy_channels_{state_key}",
        )
        severity_down_default = "high" if editing_policy is None else editing_policy.reason_severity.get("down", "high")
        severity_degraded_default = "medium" if editing_policy is None else editing_policy.reason_severity.get("degraded", "medium")
        severity_stale_default = "low" if editing_policy is None else editing_policy.reason_severity.get("stale", "low")
        severity_down = st.selectbox("Severity for down", ["high", "medium", "low"], index=["high", "medium", "low"].index(severity_down_default), key=f"source_policy_severity_down_{state_key}")
        severity_degraded = st.selectbox("Severity for degraded", ["high", "medium", "low"], index=["high", "medium", "low"].index(severity_degraded_default), key=f"source_policy_severity_degraded_{state_key}")
        severity_stale = st.selectbox("Severity for stale", ["high", "medium", "low"], index=["high", "medium", "low"].index(severity_stale_default), key=f"source_policy_severity_stale_{state_key}")
        subject_template_down = st.text_input(
            "Subject template (down)",
            value="[{severity}] {source_id} is {status} ({reason})" if editing_policy is None else editing_policy.reason_subject_templates.get("down", "[{severity}] {source_id} is {status} ({reason})"),
            help="Use placeholders: {source_id}, {source_kind}, {provider}, {reason}, {status}, {severity}",
            key=f"source_policy_subject_down_{state_key}",
        )
        subject_template_degraded = st.text_input(
            "Subject template (degraded)",
            value="[{severity}] {source_id} is {status} ({reason})" if editing_policy is None else editing_policy.reason_subject_templates.get("degraded", "[{severity}] {source_id} is {status} ({reason})"),
            key=f"source_policy_subject_degraded_{state_key}",
        )
        subject_template_stale = st.text_input(
            "Subject template (stale)",
            value="[{severity}] {source_id} is {status} ({reason})" if editing_policy is None else editing_policy.reason_subject_templates.get("stale", "[{severity}] {source_id} is {status} ({reason})"),
            key=f"source_policy_subject_stale_{state_key}",
        )
        override_down = st.multiselect(
            "Reason channels: down",
            options=[item.id for item in channels],
            default=[] if editing_policy is None else editing_policy.reason_channel_overrides.get("down", []),
            format_func=lambda x: next(item.name for item in channels if item.id == x),
            key=f"source_policy_channels_down_{state_key}",
        )
        override_degraded = st.multiselect(
            "Reason channels: degraded",
            options=[item.id for item in channels],
            default=[] if editing_policy is None else editing_policy.reason_channel_overrides.get("degraded", []),
            format_func=lambda x: next(item.name for item in channels if item.id == x),
            key=f"source_policy_channels_degraded_{state_key}",
        )
        override_stale = st.multiselect(
            "Reason channels: stale",
            options=[item.id for item in channels],
            default=[] if editing_policy is None else editing_policy.reason_channel_overrides.get("stale", []),
            format_func=lambda x: next(item.name for item in channels if item.id == x),
            key=f"source_policy_channels_stale_{state_key}",
        )
        escalation_channels = st.multiselect(
            "Escalation channels",
            options=[item.id for item in channels],
            default=[] if editing_policy is None else editing_policy.escalation_channel_ids,
            format_func=lambda x: next(item.name for item in channels if item.id == x),
            key=f"source_policy_escalation_channels_{state_key}",
        )
        escalation_threshold = st.number_input(
            "Escalation failure threshold",
            min_value=1,
            value=3 if editing_policy is None else int(editing_policy.escalation_failure_threshold),
            step=1,
            key=f"source_policy_escalation_threshold_{state_key}",
        )
        active_weekdays = st.multiselect(
            "Active weekdays",
            options=list(range(7)),
            default=[0, 1, 2, 3, 4, 5, 6] if editing_policy is None else editing_policy.active_weekdays,
            format_func=lambda x: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][x],
            key=f"source_policy_active_weekdays_{state_key}",
        )
        policy_timezone = st.text_input("Policy timezone", value="Asia/Shanghai" if editing_policy is None else editing_policy.timezone, key=f"source_policy_timezone_{state_key}")
        holiday_calendar_default = "none" if editing_policy is None else editing_policy.holiday_calendar
        holiday_calendar = st.selectbox(
            "Holiday calendar",
            ["none", "us", "uk", "eu", "jp", "cn"],
            index=["none", "us", "uk", "eu", "jp", "cn"].index(holiday_calendar_default),
            key=f"source_policy_holiday_calendar_{state_key}",
        )
        custom_holidays_text = st.text_input(
            "Custom holidays (YYYY-MM-DD, comma-separated)",
            value="" if editing_policy is None else ", ".join(item.isoformat() for item in editing_policy.holiday_dates),
            key=f"source_policy_custom_holidays_{state_key}",
        )
        active_hour_start = st.number_input("Active hour start", min_value=0, max_value=23, value=0 if editing_policy is None else int(editing_policy.active_hour_start), step=1, key=f"source_policy_active_hour_start_{state_key}")
        active_hour_end = st.number_input("Active hour end", min_value=1, max_value=24, value=24 if editing_policy is None else int(editing_policy.active_hour_end), step=1, key=f"source_policy_active_hour_end_{state_key}")
        allow_down_outside_schedule = st.checkbox(
            "Allow down alerts outside schedule",
            value=True if editing_policy is None else editing_policy.allow_down_outside_schedule,
            key=f"source_policy_allow_down_outside_{state_key}",
        )
        policy_active = st.checkbox(
            "Policy active",
            value=True if editing_policy is None else editing_policy.active,
            key=f"source_policy_active_{state_key}",
        )
        save_label = "Save source health policy" if editing_policy is None else "Update source health policy"
        if st.button(save_label) and policy_name.strip():
            custom_holidays = []
            parse_ok = True
            if custom_holidays_text.strip():
                try:
                    custom_holidays = [
                        date.fromisoformat(part.strip())
                        for part in custom_holidays_text.split(",")
                        if part.strip()
                    ]
                except ValueError:
                    st.error("Invalid custom holiday format. Use YYYY-MM-DD, comma-separated.")
                    parse_ok = False
            if not parse_ok:
                st.stop()
            policy = SourceHealthPolicy(
                id=f"source-health-policy-{uuid4().hex[:8]}" if editing_policy is None else editing_policy.id,
                name=policy_name.strip(),
                source_kind=None if policy_kind == "all" else policy_kind,
                source_id=policy_source_id,
                trigger_on_degraded=trigger_degraded,
                trigger_on_down=trigger_down,
                trigger_on_stale=trigger_stale,
                min_consecutive_failures=int(min_failures),
                stale_threshold_minutes=int(policy_stale_threshold) if policy_stale_threshold > 0 else None,
                cooldown_minutes=int(policy_cooldown),
                notification_channel_ids=selected_channels,
                reason_channel_overrides={
                    "down": override_down,
                    "degraded": override_degraded,
                    "stale": override_stale,
                },
                reason_severity={
                    "down": severity_down,
                    "degraded": severity_degraded,
                    "stale": severity_stale,
                },
                reason_subject_templates={
                    "down": subject_template_down or "Source Health Alert [{severity}]: {source_id} ({reason})",
                    "degraded": subject_template_degraded or "Source Health Alert [{severity}]: {source_id} ({reason})",
                    "stale": subject_template_stale or "Source Health Alert [{severity}]: {source_id} ({reason})",
                },
                escalation_channel_ids=escalation_channels,
                escalation_failure_threshold=int(escalation_threshold),
                active_weekdays=active_weekdays,
                active_hour_start=int(active_hour_start),
                active_hour_end=int(active_hour_end),
                allow_down_outside_schedule=allow_down_outside_schedule,
                timezone=policy_timezone.strip() or "UTC",
                holiday_calendar=holiday_calendar,
                holiday_dates=custom_holidays,
                active=policy_active,
                last_triggered_at=None if editing_policy is None else editing_policy.last_triggered_at,
            )
            service.save_source_health_policy(policy)
            st.success(f"Saved policy: {policy.name} ({'active' if policy.active else 'inactive'})")
        if editing_policy is not None:
            lifecycle_left, lifecycle_right = st.columns(2)
            with lifecycle_left:
                if editing_policy.archived_at is None:
                    archive_reason = st.text_input(
                        "Archive reason",
                        value="",
                        key=f"source_policy_archive_reason_{state_key}",
                    )
                    if st.button("Archive policy", key=f"source_policy_archive_button_{state_key}"):
                        updated = service.archive_source_health_policy(editing_policy.id, reason=archive_reason or None)
                        st.success(f"Archived policy: {updated.name}")
                else:
                    st.caption(f"Archived at: {editing_policy.archived_at}")
                    if editing_policy.archived_reason:
                        st.caption(f"Reason: {editing_policy.archived_reason}")
            with lifecycle_right:
                if editing_policy.archived_at is not None:
                    if st.button("Restore policy", key=f"source_policy_restore_button_{state_key}"):
                        updated = service.restore_source_health_policy(editing_policy.id)
                        st.success(f"Restored policy: {updated.name}")
            version_presets = service.list_source_health_policy_version_presets(editing_policy.id, limit=50)
            preset_options = ["Custom"] + [item.id for item in version_presets]
            default_preset = next((item for item in version_presets if item.is_default), None)
            preset_select_key = f"source_policy_version_preset_select_{state_key}"
            if preset_select_key not in st.session_state:
                st.session_state[preset_select_key] = default_preset.id if default_preset is not None else "Custom"
            if st.session_state[preset_select_key] not in preset_options:
                st.session_state[preset_select_key] = "Custom"
            selected_preset = None
            preset_selector = st.selectbox(
                "Version preset",
                preset_options,
                format_func=lambda x: "Custom filters" if x == "Custom" else next(
                    f"{item.name}{' [default]' if item.is_default else ''} "
                    f"(action={item.action_filter or 'all'}, query={item.query or 'blank'})"
                    for item in version_presets
                    if item.id == x
                ),
                key=preset_select_key,
            )
            if preset_selector != "Custom":
                selected_preset = next(item for item in version_presets if item.id == preset_selector)
            preview_key = f"source_policy_version_preset_preview_rows_{state_key}"
            if preview_key not in st.session_state:
                st.session_state[preview_key] = []
            load_left, load_right = st.columns(2)
            with load_left:
                if selected_preset is not None and st.button("Load preset", key=f"source_policy_load_version_preset_{state_key}"):
                    st.session_state[f"source_policy_version_action_{state_key}"] = selected_preset.action_filter or "all"
                    st.session_state[f"source_policy_version_query_{state_key}"] = selected_preset.query or ""
                    st.session_state[f"source_policy_version_limit_{state_key}"] = int(selected_preset.limit)
                    st.rerun()
                if selected_preset is not None and st.button(
                    "Preview selected preset",
                    key=f"source_policy_preview_version_preset_{state_key}",
                ):
                    st.session_state[preview_key] = [
                        item.model_dump(mode="json")
                        for item in service.list_source_health_policy_versions_by_preset(selected_preset.id)
                    ]
            version_action_filter = st.selectbox(
                "Version action filter",
                ["all", "create", "update", "archive", "restore", "rollback"],
                key=f"source_policy_version_action_{state_key}",
            )
            version_query = st.text_input(
                "Version search",
                value="",
                placeholder="Field, summary, or changed field",
                key=f"source_policy_version_query_{state_key}",
            )
            version_limit = st.number_input(
                "Version history limit",
                min_value=1,
                max_value=200,
                value=20,
                step=5,
                key=f"source_policy_version_limit_{state_key}",
            )
            with load_right:
                preset_is_default = st.checkbox(
                    "Mark saved preset as default",
                    value=False if selected_preset is None else selected_preset.is_default,
                    key=f"source_policy_version_is_default_{state_key}",
                )
                preset_name = st.text_input(
                    "Save preset as",
                    value="",
                    key=f"source_policy_version_preset_name_{state_key}",
                )
                if st.button("Save preset", key=f"source_policy_save_version_preset_{state_key}") and preset_name.strip():
                    preset = SourceHealthPolicyVersionPreset(
                        id=f"source-policy-version-preset-{uuid4().hex[:8]}",
                        policy_id=editing_policy.id,
                        name=preset_name.strip(),
                        action_filter=None if version_action_filter == "all" else version_action_filter,
                        query=version_query or None,
                        limit=int(version_limit),
                        is_default=bool(preset_is_default),
                        owner_scope="shared",
                    )
                    service.save_source_health_policy_version_preset(preset)
                    st.success(f"Saved version preset: {preset.name}")
                    st.rerun()
                if selected_preset is not None:
                    st.caption(f"Selected preset: {selected_preset.name}{' [default]' if selected_preset.is_default else ''}")
                    rename_name = st.text_input(
                        "Rename selected preset",
                        value=selected_preset.name,
                        key=f"source_policy_rename_version_preset_name_{state_key}",
                    )
                    if st.button("Rename selected preset", key=f"source_policy_rename_version_preset_{state_key}"):
                        updated = service.rename_source_health_policy_version_preset(selected_preset.id, rename_name)
                        st.success(f"Renamed version preset: {updated.name}")
                        st.rerun()
                    if st.button("Update selected preset", key=f"source_policy_update_version_preset_{state_key}"):
                        updated_preset = SourceHealthPolicyVersionPreset(
                            id=selected_preset.id,
                            policy_id=editing_policy.id,
                            name=selected_preset.name,
                            action_filter=None if version_action_filter == "all" else version_action_filter,
                            query=version_query or None,
                            limit=int(version_limit),
                            is_default=bool(preset_is_default),
                            owner_scope=selected_preset.owner_scope,
                        )
                        service.save_source_health_policy_version_preset(updated_preset)
                        st.success(f"Updated version preset: {selected_preset.name}")
                        st.rerun()
                    if st.button("Set selected as default", key=f"source_policy_set_default_version_preset_{state_key}"):
                        updated = service.set_default_source_health_policy_version_preset(selected_preset.id)
                        st.success(f"Default preset set: {updated.name}")
                        st.rerun()
                    clone_name = st.text_input(
                        "Clone as",
                        value=f"{selected_preset.name} copy",
                        key=f"source_policy_clone_version_preset_name_{state_key}",
                    )
                    if st.button("Clone selected preset", key=f"source_policy_clone_version_preset_{state_key}"):
                        clone = service.clone_source_health_policy_version_preset(selected_preset.id, name=clone_name)
                        st.success(f"Cloned version preset: {clone.name}")
                        st.rerun()
                    if st.button("Delete selected preset", key=f"source_policy_delete_version_preset_{state_key}"):
                        service.delete_source_health_policy_version_preset(selected_preset.id)
                        st.success(f"Deleted version preset: {selected_preset.name}")
                        st.rerun()
                st.caption("Preset bundle import/export")
                export_text_key = f"source_policy_version_preset_bundle_{state_key}"
                if export_text_key not in st.session_state:
                    st.session_state[export_text_key] = service.export_source_health_policy_version_presets(
                        editing_policy.id
                    ).model_dump_json(indent=2)
                refresh_export_key = f"source_policy_refresh_version_preset_bundle_{state_key}"
                if st.button("Refresh preset bundle JSON", key=refresh_export_key):
                    st.session_state[export_text_key] = service.export_source_health_policy_version_presets(
                        editing_policy.id
                    ).model_dump_json(indent=2)
                bundle_json = st.text_area(
                    "Preset bundle JSON",
                    height=220,
                    key=export_text_key,
                )
                import_mode = st.selectbox(
                    "Import mode",
                    ["append", "replace"],
                    index=0,
                    key=f"source_policy_import_version_preset_mode_{state_key}",
                )
                if st.button("Import preset bundle", key=f"source_policy_import_version_preset_{state_key}"):
                    try:
                        payload = json.loads(bundle_json)
                        preset_rows: list[dict[str, object]]
                        if isinstance(payload, dict) and isinstance(payload.get("presets"), list):
                            preset_rows = payload["presets"]
                        elif isinstance(payload, list):
                            preset_rows = payload
                        else:
                            raise ValueError("Preset bundle must be a JSON object with 'presets' or a JSON list.")
                        import_request = SourceHealthPolicyVersionPresetImportRequest.model_validate(
                            {"mode": import_mode, "presets": preset_rows}
                        )
                        imported = service.import_source_health_policy_version_presets(
                            policy_id=editing_policy.id,
                            request=import_request,
                        )
                        st.success(f"Imported {len(imported)} preset(s) in {import_mode} mode.")
                        st.session_state[export_text_key] = service.export_source_health_policy_version_presets(
                            editing_policy.id
                        ).model_dump_json(indent=2)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Import failed: {exc}")
            preview_rows = st.session_state.get(preview_key, [])
            if preview_rows:
                st.caption("Preset preview result")
                st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)
            version_rows = service.list_source_health_policy_versions(
                editing_policy.id,
                limit=int(version_limit),
                action=None if version_action_filter == "all" else version_action_filter,
                query=version_query or None,
            )
            versions = pd.DataFrame([item.model_dump(mode="json") for item in version_rows])
            st.caption("Policy versions")
            st.caption(f"{len(version_rows)} version(s) matched the current filters")
            st.dataframe(versions, use_container_width=True)
            if not versions.empty:
                rollback_version = st.selectbox(
                    "Rollback to version",
                    version_rows,
                    format_func=lambda item: f"v{item.version_number} - {item.action} - {item.changed_at}",
                    key=f"source_policy_rollback_version_{state_key}",
                )
                if st.button("Rollback to selected version", key=f"source_policy_rollback_button_{state_key}"):
                    restored = service.rollback_source_health_policy_version(rollback_version.id)
                    st.success(f"Rolled back policy: {restored.name}")
                compare_left, compare_right = st.columns(2)
                with compare_left:
                    left_version = st.selectbox(
                        "Compare left version",
                        version_rows,
                        format_func=lambda item: f"v{item.version_number} - {item.action}",
                        key=f"source_policy_compare_left_{state_key}",
                    )
                with compare_right:
                    right_version = st.selectbox(
                        "Compare right version",
                        version_rows,
                        format_func=lambda item: f"v{item.version_number} - {item.action}",
                        key=f"source_policy_compare_right_{state_key}",
                    )
                if st.button("Compare selected versions", key=f"source_policy_compare_button_{state_key}"):
                    st.session_state[f"source_policy_compare_result_{state_key}"] = service.compare_source_health_policy_versions(
                        left_version.id,
                        right_version.id,
                    )
                compare_result = st.session_state.get(f"source_policy_compare_result_{state_key}")
                if compare_result:
                    st.caption(
                        f"Comparing v{compare_result['left_version_number']} ({compare_result['left_action']}) "
                        f"to v{compare_result['right_version_number']} ({compare_result['right_action']})"
                    )
                    diff_frame = pd.DataFrame(compare_result["diffs"])
                    st.dataframe(diff_frame, use_container_width=True)
                    json_payload = json.dumps(compare_result, indent=2, default=str)
                    csv_payload = diff_frame.to_csv(index=False) if not diff_frame.empty else "field,left_value,right_value\n"
                    download_left, download_right = st.columns(2)
                    with download_left:
                        st.download_button(
                            "Download compare JSON",
                            data=json_payload,
                            file_name=f"source-policy-compare-{state_key}.json",
                            mime="application/json",
                            key=f"source_policy_compare_json_{state_key}",
                        )
                    with download_right:
                        st.download_button(
                            "Download compare CSV",
                            data=csv_payload,
                            file_name=f"source-policy-compare-{state_key}.csv",
                            mime="text/csv",
                            key=f"source_policy_compare_csv_{state_key}",
                        )
        if st.button("Run source health policies"):
            actions = service.run_source_health_policies()
            st.success(f"Executed source health policies: {len(actions)} action(s)")
            st.dataframe(pd.DataFrame(actions), use_container_width=True)
        policies = pd.DataFrame([item.model_dump(mode="json") for item in service.list_source_health_policies(include_archived=True, limit=200)])
        st.dataframe(policies, use_container_width=True)
        policy_runs = pd.DataFrame([item.model_dump(mode="json") for item in service.list_source_health_policy_runs(limit=50)])
        st.caption("Recent policy runs")
        st.dataframe(policy_runs, use_container_width=True)
    stale_rows = pd.DataFrame(
        [
            item.model_dump(mode="json")
            for item in service.list_source_health(limit=200)
            if item.is_stale or item.status in {"degraded", "down"}
        ]
    )
    st.caption("Attention required")
    st.dataframe(stale_rows, use_container_width=True)

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
        existing_channels = service.list_notification_channels()
        channel_name = st.text_input("Channel name", value="")
        channel_kind = st.selectbox("Channel kind", ["file", "email", "webhook", "slack"])
        channel_event_types = st.multiselect(
            "Route event types",
            options=["alert_event", "report_job", "manual"],
            default=["alert_event", "report_job", "manual"],
        )
        channel_min_significance = st.selectbox("Minimum alert significance", ["high", "medium", "low"], index=2)
        channel_delivery_mode = st.selectbox("Alert delivery mode", ["immediate", "digest"])
        fallback_channel_ids = st.multiselect(
            "Fallback channels",
            options=[item.id for item in existing_channels],
            format_func=lambda x: next(item.name for item in existing_channels if item.id == x),
        )
        escalation_channel_ids = st.multiselect(
            "Escalation channels",
            options=[item.id for item in existing_channels],
            format_func=lambda x: next(item.name for item in existing_channels if item.id == x),
        )
        escalation_min_significance = st.selectbox("Escalation threshold", ["high", "medium", "low"], index=0)
        ops_escalation_enabled = st.checkbox("Enable ops escalation policy", value=False)
        ops_escalation_channel_ids = st.multiselect(
            "Ops escalation targets",
            options=[item.id for item in existing_channels],
            format_func=lambda x: next(item.name for item in existing_channels if item.id == x),
        )
        ops_escalation_window_hours = st.slider(
            "Ops escalation window (hours)",
            min_value=1,
            max_value=168,
            value=6,
            step=1,
        )
        ops_escalation_threshold = st.slider(
            "Ops escalation adverse decision threshold",
            min_value=1,
            max_value=100,
            value=5,
            step=1,
        )
        ops_escalation_cooldown_minutes = st.slider(
            "Ops escalation cooldown (minutes)",
            min_value=0,
            max_value=1440,
            value=60,
            step=5,
        )
        cooldown_minutes = st.slider("Cooldown minutes", min_value=0, max_value=1440, value=0, step=5)
        duplicate_window_minutes = st.slider("Duplicate suppression window", min_value=0, max_value=1440, value=60, step=5)
        retry_backoff_minutes = st.slider("Retry backoff minutes", min_value=0, max_value=240, value=15, step=5)
        max_retry_attempts = st.slider("Max retry attempts", min_value=1, max_value=10, value=3, step=1)
        auto_pause_enabled = st.checkbox("Enable auto-pause on failures", value=False)
        auto_pause_window_hours = st.slider("Auto-pause lookback (hours)", min_value=1, max_value=168, value=24, step=1)
        auto_pause_error_rate_threshold = st.slider(
            "Auto-pause error-rate threshold",
            min_value=0.05,
            max_value=1.0,
            value=0.5,
            step=0.05,
        )
        auto_pause_consecutive_failures = st.slider(
            "Auto-pause consecutive failures",
            min_value=1,
            max_value=20,
            value=3,
            step=1,
        )
        auto_pause_minutes = st.slider("Auto-pause duration (minutes)", min_value=5, max_value=1440, value=60, step=5)
        auto_resume_enabled = st.checkbox("Enable auto-resume after auto-pause", value=False)
        recovery_probe_profile = st.selectbox("Recovery probe profile", ["minimal", "standard", "verbose"], index=1)
        recovery_probe_payload_text = st.text_area("Recovery probe payload override (JSON)", value="")
        recovery_probe_cooldown_minutes = st.slider(
            "Recovery probe cooldown (minutes)",
            min_value=0,
            max_value=240,
            value=30,
            step=5,
        )
        recovery_probe_max_per_hour = st.slider(
            "Recovery probe max per hour",
            min_value=1,
            max_value=20,
            value=2,
            step=1,
        )
        recovery_probe_payload = {}
        if recovery_probe_payload_text.strip():
            try:
                parsed_payload = json.loads(recovery_probe_payload_text)
                if isinstance(parsed_payload, dict):
                    recovery_probe_payload = parsed_payload
                else:
                    st.error("Recovery probe payload must be a JSON object.")
            except json.JSONDecodeError:
                st.error("Recovery probe payload JSON is invalid.")
        pause_minutes = st.slider("Pause for minutes (optional)", min_value=0, max_value=1440, value=0, step=15)
        pause_reason = st.text_input("Pause reason", value="")
        digest_hour_local = st.slider("Digest hour (local)", min_value=0, max_value=23, value=8)
        digest_limit = st.slider("Default digest rows", min_value=1, max_value=50, value=25, step=1)
        digest_status_filter = st.selectbox("Default digest status", ["new", "published", "dismissed"])
        digest_publish_included = st.checkbox("Publish digest events after send", value=False)
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
                event_types=channel_event_types or ["manual"],
                min_significance=channel_min_significance,
                delivery_mode=channel_delivery_mode,
                fallback_channel_ids=fallback_channel_ids,
                escalation_channel_ids=escalation_channel_ids,
                escalation_min_significance=escalation_min_significance,
                ops_escalation_enabled=ops_escalation_enabled,
                ops_escalation_channel_ids=ops_escalation_channel_ids,
                ops_escalation_window_hours=ops_escalation_window_hours,
                ops_escalation_threshold=ops_escalation_threshold,
                ops_escalation_cooldown_minutes=ops_escalation_cooldown_minutes,
                cooldown_minutes=cooldown_minutes,
                duplicate_window_minutes=duplicate_window_minutes,
                retry_backoff_minutes=retry_backoff_minutes,
                max_retry_attempts=max_retry_attempts,
                auto_pause_enabled=auto_pause_enabled,
                auto_pause_window_hours=auto_pause_window_hours,
                auto_pause_error_rate_threshold=auto_pause_error_rate_threshold,
                auto_pause_consecutive_failures=auto_pause_consecutive_failures,
                auto_pause_minutes=auto_pause_minutes,
                auto_resume_enabled=auto_resume_enabled,
                recovery_probe_profile=recovery_probe_profile,
                recovery_probe_payload=recovery_probe_payload,
                recovery_probe_cooldown_minutes=recovery_probe_cooldown_minutes,
                recovery_probe_max_per_hour=recovery_probe_max_per_hour,
                paused_until=(datetime.now() + timedelta(minutes=pause_minutes)) if pause_minutes > 0 else None,
                pause_reason=pause_reason.strip() or None,
                digest_hour_local=digest_hour_local,
                digest_limit=digest_limit,
                digest_status_filter=digest_status_filter,
                digest_publish_included=digest_publish_included,
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
        health_window_hours = st.slider("Health window (hours)", min_value=1, max_value=168, value=24, step=1)
        health = pd.DataFrame(
            [
                item.model_dump(mode="json")
                for item in service.list_notification_channel_health(window_hours=health_window_hours)
            ]
        )
        st.caption("Channel health")
        st.dataframe(health, use_container_width=True)
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
        route_decision_filter = st.selectbox(
            "Routing decision",
            ["all", "delivered", "failed", "suppressed", "paused", "inactive", "rejected", "digest_deferred"],
        )
        routing_rows = pd.DataFrame(
            [
                item.model_dump(mode="json")
                for item in service.list_notification_routing_audits(
                    channel_id=None if channel_filter == "all" else channel_filter,
                    event_type=None if event_type_filter == "all" else event_type_filter,
                    decision=None if route_decision_filter == "all" else route_decision_filter,
                    limit=200,
                )
            ]
        )
        st.caption("Routing audit")
        st.dataframe(routing_rows, use_container_width=True)
        summary_window = st.slider("Routing summary window (hours)", min_value=1, max_value=168, value=24, step=1)
        routing_summary = pd.DataFrame(
            service.get_notification_routing_summary(
                channel_id=None if channel_filter == "all" else channel_filter,
                event_type=None if event_type_filter == "all" else event_type_filter,
                window_hours=summary_window,
            )
        )
        st.caption("Routing summary")
        st.dataframe(routing_summary, use_container_width=True)
        export_format = st.selectbox("Routing export format", ["csv", "json"])
        if st.button("Export routing audit"):
            result = service.export_notification_routing_audits(
                format=export_format,
                channel_id=None if channel_filter == "all" else channel_filter,
                event_type=None if event_type_filter == "all" else event_type_filter,
                decision=None if route_decision_filter == "all" else route_decision_filter,
                limit=5000,
            )
            st.success(f"Exported {result['count']} rows to {result['path']}")
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
            pause_channel_id = st.selectbox(
                "Pause or resume channel",
                [item.id for item in channels],
                key="pause_channel_id",
                format_func=lambda x: next(item.name for item in channels if item.id == x),
            )
            pause_minutes_action = st.slider("Pause duration (minutes)", min_value=5, max_value=1440, value=60, step=5)
            pause_reason_action = st.text_input("Pause reason (action)", value="", key="pause_reason_action")
            pause_col, resume_col = st.columns(2)
            with pause_col:
                if st.button("Pause channel"):
                    channel = service.pause_notification_channel(
                        pause_channel_id,
                        minutes=pause_minutes_action,
                        reason=pause_reason_action.strip() or None,
                    )
                    st.success(f"Paused {channel.name} until {channel.paused_until}")
            with resume_col:
                if st.button("Resume channel"):
                    channel = service.resume_notification_channel(pause_channel_id)
                    st.success(f"Resumed {channel.name}")
            if st.button("Run recovery checks"):
                recoveries = service.run_notification_channel_recovery()
                st.success(f"Recovery checks completed: {len(recoveries)} channel action(s)")
            digest_channel_options = [item.id for item in channels if "alert_event" in item.event_types]
            if digest_channel_options:
                digest_channel_id = st.selectbox(
                    "Send digest",
                    digest_channel_options,
                    key="digest_notification_channel",
                    format_func=lambda x: next(item.name for item in channels if item.id == x),
                )
                digest_status = st.selectbox("Digest event status", ["new", "published", "dismissed"])
                digest_limit = st.slider("Digest rows", min_value=1, max_value=50, value=10, step=1)
                publish_digest_events = st.checkbox("Mark digest events published", value=False)
                if st.button("Send alert digest"):
                    try:
                        digest = service.send_notification_digest(
                            digest_channel_id,
                            status=digest_status,
                            limit=digest_limit,
                            publish_included=publish_digest_events,
                        )
                        st.success(f"Sent digest {digest.id} with {digest.event_count} event(s)")
                    except ValueError as exc:
                        st.error(str(exc))
                if st.button("Run due digests"):
                    completed = service.run_due_notification_digests()
                    st.success(f"Ran {len(completed)} due digest channel(s)")
        delivery_rows = service.list_notification_deliveries(
            channel_id=None if channel_filter == "all" else channel_filter,
            event_type=None if event_type_filter == "all" else event_type_filter,
            status=None if status_filter == "all" else status_filter,
            limit=100,
        )
        if delivery_rows:
            retry_delivery_id = st.selectbox("Retry delivery", [item.id for item in delivery_rows])
            if st.button("Retry selected delivery"):
                try:
                    delivery = service.retry_notification_delivery(retry_delivery_id)
                    st.success(f"Retried delivery {delivery.id} via {delivery.channel_name}")
                except ValueError as exc:
                    st.error(str(exc))
        digests = pd.DataFrame(
            [
                item.model_dump(mode="json")
                for item in service.list_notification_digests(
                    channel_id=None if channel_filter == "all" else channel_filter,
                    limit=50,
                )
            ]
        )
        st.caption("Digest history")
        st.dataframe(digests, use_container_width=True)

elif view == "Ops Incidents":
    st.subheader("Ops Incidents")
    summary = service.get_ops_incident_summary()
    metric_total, metric_open, metric_ack, metric_overdue = st.columns(4)
    metric_total.metric("Total", summary.get("total", 0))
    metric_open.metric("Open", summary.get("open", 0))
    metric_ack.metric("Ack", summary.get("ack", 0))
    metric_overdue.metric("Overdue Active", summary.get("overdue_active", 0))

    all_incidents = service.list_ops_incidents(limit=500)
    source_options = ["all"] + sorted({item.source_channel_id for item in all_incidents})
    status_filter = st.selectbox("Incident status", ["all", "open", "ack", "resolved"])
    source_filter = st.selectbox("Source channel", source_options)
    overdue_only = st.checkbox("Overdue only", value=False)
    incidents = service.list_ops_incidents(
        status=None if status_filter == "all" else status_filter,
        source_channel_id=None if source_filter == "all" else source_filter,
        overdue_only=overdue_only,
        limit=200,
    )
    incident_frame = pd.DataFrame([item.model_dump(mode="json") for item in incidents])
    st.dataframe(incident_frame, use_container_width=True)
    if incidents:
        selected_incident = st.selectbox(
            "Incident",
            [item.id for item in incidents],
            format_func=lambda x: next(
                f"{item.source_channel_name} ({item.status}, {item.priority})"
                for item in incidents
                if item.id == x
            ),
        )
        selected = next(item for item in incidents if item.id == selected_incident)
        next_status = st.selectbox("Set status", ["open", "ack", "resolved"], index=["open", "ack", "resolved"].index(selected.status))
        owner_value = st.text_input("Owner", value=selected.owner or "")
        next_priority = st.selectbox(
            "Priority",
            ["low", "medium", "high"],
            index=["low", "medium", "high"].index(selected.priority),
        )
        sla_minutes = st.number_input("SLA (minutes)", min_value=0, value=int(selected.sla_minutes), step=15)
        incident_notes = st.text_input("Incident notes", value=selected.notes or "")
        if st.button("Update incident"):
            updated = service.update_ops_incident(
                incident_id=selected_incident,
                status=next_status,
                owner=owner_value,
                priority=next_priority,
                sla_minutes=int(sla_minutes),
                notes=incident_notes or None,
            )
            st.success(
                f"Updated {updated.id}: status={updated.status}, owner={updated.owner or '-'}, "
                f"priority={updated.priority}, due={updated.due_at}"
            )

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
