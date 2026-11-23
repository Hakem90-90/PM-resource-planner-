import datetime
from urllib.parse import quote

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt


# ----------------------------------------------------------------------
# 1. Page Configuration & Central Data Sources
# ----------------------------------------------------------------------
st.set_page_config(page_title="ACKD Engineering Resource Planner", layout="wide")
st.title("📊 Dynamic Engineering Resource Planner - ACKD")

MASTER_SHEET_ID = "1ksIh1yt0_zeOWszW1fk5zQdZSL5ytaKJ1-IMODmtjBY"

PM_SHEETS = {
    "Dr. Hatem Al-Sayed": {
        "sheet_id": "1N-FtQn7a0lWQqoru0dZauugW-afe78l5rH0HW6MW1L4",
        "pm_label": "Dr. Hatem Al-Sayed",
    },
    "Eng. Ali Athamneh": {
        "sheet_id": "1eK-aTlLHhsDWGJv4Afelc-A3IYcLuKy53QXaAoxcUlE",
        "pm_label": "Eng. Ali Athamneh",
    },
    "Eng. Mohamed Gamal": {
        "sheet_id": "1UdKXCiEFStLYBHHxfXfCHWF_4eDuCe-K8x-HMt2DuOA",
        "pm_label": "Eng. Mohamed Gamal",
    },
    "Eng. Saed Abu Taha": {
        "sheet_id": "1H8eOnYL_okvUmzouvSTm1hMJMdikrHJbX77-l-sKc7E",
        "pm_label": "Eng. Saed Abu Taha",
    },
    "Eng. Hakem Al-Omoush": {
        "sheet_id": "1MYYCSMOoWIjutD6wso3K4KiwQrI_bF6aE9BGwzBw6Cs",
        "pm_label": "Eng. Hakem Al-Omoush",
    },
}

# Working week: Sunday to Thursday (Friday & Saturday are the weekend)
NP_WEEKMASK = "1111001"
PD_WEEKMASK = "Sun Mon Tue Wed Thu"
DEFAULT_HOURS_PER_DAY = 8.0
DEFAULT_EFFICIENCY_PCT = 80.0


def build_sheet_csv_url(sheet_id: str, sheet_name: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={quote(sheet_name)}"
    )


def build_edit_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def parse_dates(series: pd.Series) -> pd.Series:
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(series, errors="coerce")
    return parsed.dt.date


def compute_workdays(s_date, e_date) -> int:
    if pd.isnull(s_date) or pd.isnull(e_date):
        return 0
    if isinstance(s_date, str):
        s_date = pd.to_datetime(s_date, errors="coerce").date()
    if isinstance(e_date, str):
        e_date = pd.to_datetime(e_date, errors="coerce").date()
    if not s_date or not e_date or e_date < s_date:
        return 0
    wd = int(np.busday_count(s_date, e_date + datetime.timedelta(days=1), weekmask=NP_WEEKMASK))
    return max(wd, 1) if s_date == e_date else wd


# ----------------------------------------------------------------------
# 2. Auto-Sync Data Loader (Master + All PM Sheets)
# ----------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner="Syncing Projects, Teams, and PM Task Sheets...")
def load_all_synced_data(master_id: str, pm_sheets_map: dict):
    sync_status = []

    # 1. Projects
    projects_df = pd.read_csv(build_sheet_csv_url(master_id, "Projects"))
    projects_df.columns = projects_df.columns.astype(str).str.strip()
    projects_df = projects_df.dropna(how="all").reset_index(drop=True)
    if "Target_Delivery_Date" in projects_df.columns:
        projects_df["Target_Delivery_Date"] = parse_dates(projects_df["Target_Delivery_Date"])

    # 2. Team Capacity
    teams_df = pd.read_csv(build_sheet_csv_url(master_id, "Team_Capacity"))
    teams_df.columns = teams_df.columns.astype(str).str.strip()
    teams_df = teams_df.dropna(how="all").reset_index(drop=True)
    for col, default_val in [
        ("Engineers_Count", 1),
        ("Hours_Per_Day", DEFAULT_HOURS_PER_DAY),
        ("Efficiency_Pct", DEFAULT_EFFICIENCY_PCT),
    ]:
        if col in teams_df.columns:
            teams_df[col] = pd.to_numeric(teams_df[col], errors="coerce").fillna(default_val)

    # 3. Tasks from each PM Sheet
    all_task_frames = []
    for pm_key, cfg in pm_sheets_map.items():
        pm_id = cfg["sheet_id"]
        pm_name = cfg["pm_label"]
        try:
            df_pm = pd.read_csv(build_sheet_csv_url(pm_id, "Tasks"))
            df_pm.columns = df_pm.columns.astype(str).str.strip()
            key_cols = [c for c in ["Project_Code", "Task_Name", "Team_Name"] if c in df_pm.columns]
            if key_cols:
                df_pm = df_pm.dropna(subset=key_cols, how="all").reset_index(drop=True)
            else:
                df_pm = df_pm.dropna(how="all").reset_index(drop=True)

            df_pm["PM_Name"] = pm_name
            all_task_frames.append(df_pm)
            sync_status.append({"PM": pm_name, "Tasks Loaded": len(df_pm), "Status": "Synced"})
        except Exception as exc:
            sync_status.append({"PM": pm_name, "Tasks Loaded": 0, "Status": f"Error: {exc}"})

    if all_task_frames:
        tasks_df = pd.concat(all_task_frames, ignore_index=True)
    else:
        tasks_df = pd.DataFrame(
            columns=[
                "Task_ID",
                "Project_Code",
                "Project_Name",
                "PM_Name",
                "Project_Deadline",
                "Task_Name",
                "Team_Name",
                "Start_Date",
                "End_Date",
                "Engineers_Assigned",
                "Allocated_Hours",
                "Coordination_Status",
                "PM_Notes",
                "Team_Notes",
            ]
        )

    for d_col in ["Start_Date", "End_Date", "Project_Deadline"]:
        if d_col in tasks_df.columns:
            tasks_df[d_col] = parse_dates(tasks_df[d_col])

    for n_col in ["Engineers_Assigned", "Allocated_Hours"]:
        if n_col in tasks_df.columns:
            tasks_df[n_col] = pd.to_numeric(tasks_df[n_col], errors="coerce")

    return projects_df, teams_df, tasks_df, pd.DataFrame(sync_status)


def prepare_tasks_df(
    raw_tasks: pd.DataFrame,
    projects_df: pd.DataFrame,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
    efficiency: float = 0.80,
) -> pd.DataFrame:
    df = raw_tasks.copy()
    if df.empty:
        return df

    if not projects_df.empty and "Project_Code" in df.columns and "Project_Code" in projects_df.columns:
        proj_lookup = projects_df.drop_duplicates(subset=["Project_Code"]).set_index("Project_Code")
        for target_col, src_col in [
            ("Project_Name", "Project_Name"),
            ("Project_Deadline", "Target_Delivery_Date"),
        ]:
            if src_col in proj_lookup.columns:
                mapped = df["Project_Code"].map(proj_lookup[src_col])
                if target_col not in df.columns:
                    df[target_col] = mapped
                else:
                    df[target_col] = df[target_col].fillna(mapped)

    df["Start_Date"] = parse_dates(df["Start_Date"])
    df["End_Date"] = parse_dates(df["End_Date"])
    df["End_Date"] = df["End_Date"].fillna(df["Start_Date"])
    df["Start_Date"] = df["Start_Date"].fillna(df["End_Date"])

    df["WorkDays"] = [compute_workdays(s, e) for s, e in zip(df["Start_Date"], df["End_Date"])]

    calc_hours = []
    for _, row in df.iterrows():
        hrs = row.get("Allocated_Hours")
        eng = row.get("Engineers_Assigned")
        wd = row.get("WorkDays", 0)
        if pd.notnull(hrs) and float(hrs) > 0:
            calc_hours.append(float(hrs))
        elif pd.notnull(eng) and float(eng) > 0 and wd > 0:
            calc_hours.append(round(float(eng) * wd * hours_per_day * efficiency, 1))
        else:
            calc_hours.append(0.0)
    df["Effective_Hours"] = calc_hours

    today = datetime.date.today()
    health_flags = []
    for _, row in df.iterrows():
        status_str = str(row.get("Coordination_Status", "")).lower()
        e_date = row.get("End_Date")
        deadline = row.get("Project_Deadline")
        if "completed" in status_str or "مكتمل" in status_str:
            health_flags.append("✅ Completed")
        elif pd.notnull(deadline) and pd.notnull(e_date) and e_date > deadline:
            health_flags.append("🚨 Exceeds Project Delivery")
        elif pd.notnull(e_date) and e_date < today:
            health_flags.append("⚠️ Past Due")
        else:
            health_flags.append("🟢 On Track")
    df["Schedule_Status"] = health_flags

    return df


# ----------------------------------------------------------------------
# 3. Gantt Timeline Chart
# ----------------------------------------------------------------------
def render_gantt_chart(df: pd.DataFrame, color_by: str = "Team_Name", hide_completed: bool = False):
    if df.empty:
        st.info("No tasks match the current selection.")
        return

    gantt_df = df.copy()
    gantt_df["Start"] = pd.to_datetime(gantt_df["Start_Date"], errors="coerce")
    gantt_df["End"] = pd.to_datetime(gantt_df["End_Date"], errors="coerce")
    gantt_df = gantt_df.dropna(subset=["Start", "End"])
    gantt_df = gantt_df[gantt_df["End"] >= gantt_df["Start"]]

    if hide_completed and "Coordination_Status" in gantt_df.columns:
        mask_comp = gantt_df["Coordination_Status"].astype(str).str.lower().str.contains("completed|مكتمل", na=False)
        gantt_df = gantt_df[~mask_comp]

    if gantt_df.empty:
        st.info("No active or upcoming tasks with valid dates to display.")
        return

    gantt_df["RowLabel"] = (
        gantt_df["Project_Code"].fillna("").astype(str)
        + " — "
        + gantt_df["Task_Name"].fillna("Task").astype(str)
        + " ["
        + gantt_df["Team_Name"].fillna("").astype(str)
        + "]"
    )

    if gantt_df["RowLabel"].duplicated().any():
        seen = {}
        unique_labels = []
        for lbl in gantt_df["RowLabel"]:
            cnt = seen.get(lbl, 0) + 1
            seen[lbl] = cnt
            unique_labels.append(f"{lbl} ({cnt})" if cnt > 1 else lbl)
        gantt_df["RowLabel"] = unique_labels

    gantt_df["EndPlus"] = gantt_df["End"] + pd.Timedelta(days=1)
    gantt_df = gantt_df.sort_values(["Start", "End"])
    gantt_df["DateRange"] = [
        f"{s:%d-%b} → {e:%d-%b-%Y}"
        for s, e in zip(gantt_df["Start"], gantt_df["End"])
    ]
    row_order = gantt_df["RowLabel"].tolist()

    today = pd.Timestamp.today().normalize()
    x_min = gantt_df["Start"].min() - pd.Timedelta(days=2)
    span_days = max(1, (gantt_df["EndPlus"].max() - gantt_df["Start"].min()).days)
    x_max = gantt_df["EndPlus"].max() + pd.Timedelta(days=max(10, int(span_days * 0.22)))
    x_scale = alt.Scale(domain=[x_min.isoformat(), x_max.isoformat()])

    tooltips = [
        alt.Tooltip("Project_Code:N", title="Project Code"),
        alt.Tooltip("Project_Name:N", title="Project Name"),
        alt.Tooltip("PM_Name:N", title="Project Manager"),
        alt.Tooltip("Task_Name:N", title="Task"),
        alt.Tooltip("Team_Name:N", title="Team"),
        alt.Tooltip("Start:T", title="Start Date", format="%a %d-%b-%Y"),
        alt.Tooltip("End:T", title="Delivery Date", format="%a %d-%b-%Y"),
        alt.Tooltip("Effective_Hours:Q", title="Allocated Hours", format=",.1f"),
        alt.Tooltip("Coordination_Status:N", title="Status"),
    ]

    bars = alt.Chart(gantt_df).mark_bar(cornerRadius=3).encode(
        y=alt.Y("RowLabel:N", sort=row_order, title=None, axis=alt.Axis(labelLimit=340)),
        x=alt.X("Start:T", scale=x_scale, title="Timeline", axis=alt.Axis(format="%d-%b", labelAngle=-45)),
        x2="EndPlus:T",
        color=alt.Color(f"{color_by}:N", title="Team" if color_by == "Team_Name" else "Project Manager"),
        tooltip=tooltips,
    )

    labels = alt.Chart(gantt_df).mark_text(
        align="left", baseline="middle", dx=6, fontSize=11
    ).encode(
        y=alt.Y("RowLabel:N", sort=row_order),
        x=alt.X("EndPlus:T", scale=x_scale),
        text="DateRange:N",
    )

    layers = [bars, labels]
    if gantt_df["Start"].min() <= today <= gantt_df["EndPlus"].max():
        today_rule = alt.Chart(pd.DataFrame({"Today": [today]})).mark_rule(
            color="red", strokeDash=[5, 4], strokeWidth=2
        ).encode(x=alt.X("Today:T", scale=x_scale))
        layers.append(today_rule)

    chart = alt.layer(*layers).properties(height=max(180, 34 * len(gantt_df) + 60))
    st.altair_chart(chart, use_container_width=True)


# ----------------------------------------------------------------------
# 4. Daily Team Capacity & Bottleneck Engine
# ----------------------------------------------------------------------
def compute_daily_team_load(
    tasks_df: pd.DataFrame,
    team_name: str,
    start_timeline: datetime.date,
    num_weeks: int,
    engineers_count: int,
    hours_per_day: float,
    efficiency: float,
):
    num_days = num_weeks * 5
    days = [
        d.date()
        for d in pd.bdate_range(start=start_timeline, periods=num_days, freq="C", weekmask=PD_WEEKMASK)
    ]
    day_labels = [d.strftime("%a %d-%b") for d in days]
    daily_demand = [0.0] * num_days

    team_tasks = tasks_df[tasks_df["Team_Name"] == team_name].copy()
    for _, row in team_tasks.iterrows():
        status_str = str(row.get("Coordination_Status", "")).lower()
        if "completed" in status_str or "مكتمل" in status_str:
            continue
        s_date = row.get("Start_Date")
        e_date = row.get("End_Date")
        wd = row.get("WorkDays", 0)
        hrs = row.get("Effective_Hours", 0.0)
        if pd.isnull(s_date) or pd.isnull(e_date) or wd <= 0 or hrs <= 0:
            continue

        daily_rate = hrs / wd
        for i, day in enumerate(days):
            if s_date <= day <= e_date:
                daily_demand[i] += daily_rate

    daily_cap = round(engineers_count * hours_per_day * efficiency, 1)
    dem_list = [round(x, 1) for x in daily_demand]
    cap_list = [daily_cap] * num_days
    var_list = [round(daily_cap - d, 1) for d in dem_list]

    return days, day_labels, dem_list, cap_list, var_list


# ----------------------------------------------------------------------
# 5. Load & Sync All Data
# ----------------------------------------------------------------------
try:
    projects_df, teams_df, raw_tasks_df, sync_df = load_all_synced_data(MASTER_SHEET_ID, PM_SHEETS)
except Exception as e:
    st.error(f"Failed to load data from Google Sheets. Ensure view access is enabled. Details: {e}")
    st.stop()

top_c1, top_c2, top_c3 = st.columns([1.3, 1.7, 3])
with top_c1:
    if st.button("🔄 Refresh & Sync All", use_container_width=True):
        load_all_synced_data.clear()
        st.rerun()
with top_c2:
    st.link_button("🗂️ Open Master Database", build_edit_url(MASTER_SHEET_ID), use_container_width=True)
with top_c3:
    st.caption(
        f"Synced {len(PM_SHEETS)} PM sheets | Projects: {len(projects_df)} | Teams: {len(teams_df)} | Total Tasks: {len(raw_tasks_df)}"
    )

with st.expander("🔗 PM Sheets Sync Links & Status"):
    st.dataframe(sync_df, use_container_width=True, hide_index=True)
    link_cols = st.columns(len(PM_SHEETS))
    for idx, (pm_label, cfg) in enumerate(PM_SHEETS.items()):
        with link_cols[idx]:
            st.link_button(f"📝 {pm_label}", build_edit_url(cfg["sheet_id"]), use_container_width=True)

# Sidebar Controls
st.sidebar.header("⚙️ Capacity & Horizon Settings")
hours_per_day = st.sidebar.number_input(
    "Working Hours / Day", min_value=1.0, max_value=12.0, value=DEFAULT_HOURS_PER_DAY, step=0.5
)
efficiency = st.sidebar.slider("Efficiency Factor (%)", 50, 100, int(DEFAULT_EFFICIENCY_PCT)) / 100.0
start_timeline = st.sidebar.date_input("Analysis Start Date", value=datetime.date.today())
num_weeks = st.sidebar.slider("Analysis Horizon (Weeks, Sun–Thu)", 1, 12, 4)

tasks_df = prepare_tasks_df(raw_tasks_df, projects_df, hours_per_day, efficiency)

# Navigation
view_mode = st.radio(
    "Select View:",
    [
        "🏢 Executive Overview",
        "📁 Projects & Delivery Schedule",
        "👥 Teams Capacity & Bottlenecks",
    ],
    horizontal=True,
)
st.markdown("---")


# ======================================================================
# VIEW 1: Executive Overview
# ======================================================================
if view_mode.startswith("🏢"):
    st.subheader("🏢 Executive Overview — Delivery & Teams Capacity")

    total_tasks = len(tasks_df)
    active_df = (
        tasks_df[~tasks_df["Coordination_Status"].astype(str).str.lower().str.contains("completed|مكتمل", na=False)]
        if total_tasks
        else tasks_df
    )
    risk_df = (
        tasks_df[tasks_df["Schedule_Status"].astype(str).str.contains("🚨|⚠️", na=False)]
        if total_tasks
        else tasks_df
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Projects", len(projects_df))
    m2.metric("Active Tasks", len(active_df))
    m3.metric("Engineering Teams", len(teams_df))
    m4.metric("Schedule Alerts", len(risk_df))

    st.markdown("### 📊 Teams Capacity Summary")
    summary_rows = []
    for _, t_row in teams_df.iterrows():
        t_name = t_row["Team_Name"]
        eng_cnt = int(t_row["Engineers_Count"])
        _, _, dem_l, cap_l, var_l = compute_daily_team_load(
            tasks_df, t_name, start_timeline, num_weeks, eng_cnt, hours_per_day, efficiency
        )
        total_dem = sum(dem_l)
        total_cap = sum(cap_l)
        util_pct = round((total_dem / total_cap) * 100, 1) if total_cap > 0 else 0.0
        bottleneck_days = sum(1 for v in var_l if v < 0)

        summary_rows.append({
            "Team": t_name,
            "Location": t_row.get("Location", ""),
            "Engineers": eng_cnt,
            "Daily Capacity (Hrs)": cap_l[0] if cap_l else 0.0,
            "Period Demand (Hrs)": round(total_dem, 1),
            "Period Capacity (Hrs)": round(total_cap, 1),
            "Utilization (%)": util_pct,
            "Bottleneck Days": bottleneck_days,
        })

    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    st.markdown("### 🗓️ Master Tasks Timeline")
    hide_comp_exec = st.checkbox("Hide completed tasks", value=True, key="exec_hide_comp")
    render_gantt_chart(tasks_df, color_by="Team_Name", hide_completed=hide_comp_exec)


# ======================================================================
# VIEW 2: Projects & Delivery Schedule
# ======================================================================
elif view_mode.startswith("📁"):
    st.subheader("📁 Projects & Task Delivery Schedule")

    pm_options = ["All"] + list(PM_SHEETS.keys())
    c1, c2, c3 = st.columns([1.3, 1.7, 1])
    with c1:
        sel_pm = st.selectbox("Project Manager:", pm_options)
    with c2:
        if sel_pm == "All":
            proj_pool = projects_df
        else:
            proj_pool = projects_df[projects_df["PM_Name"] == sel_pm]
        proj_choices = ["All Projects"] + [
            f"{r['Project_Code']} — {r['Project_Name']}" for _, r in proj_pool.iterrows()
        ]
        sel_proj = st.selectbox("Project:", proj_choices)
    with c3:
        hide_comp_pm = st.checkbox("Hide completed", value=False, key="pm_hide_comp")

    filtered = tasks_df.copy()
    if sel_pm != "All" and "PM_Name" in filtered.columns:
        filtered = filtered[filtered["PM_Name"] == sel_pm]
    if sel_proj != "All Projects" and "Project_Code" in filtered.columns:
        p_code = sel_proj.split(" — ")[0].strip()
        filtered = filtered[filtered["Project_Code"] == p_code]

    st.markdown("#### 📋 Task Schedule Register (What-If Simulation)")
    edited_pm = st.data_editor(
        filtered,
        key="pm_editor",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )
    sim_pm = prepare_tasks_df(edited_pm, projects_df, hours_per_day, efficiency)

    st.markdown("#### 🗓️ Task Delivery Gantt Chart")
    render_gantt_chart(sim_pm, color_by="Team_Name", hide_completed=hide_comp_pm)


# ======================================================================
# VIEW 3: Teams Capacity & Bottlenecks
# ======================================================================
elif view_mode.startswith("👥"):
    st.subheader("👥 Teams Capacity & Daily Bottleneck Analysis")

    team_list = teams_df["Team_Name"].dropna().tolist()
    sel_team = st.selectbox("Select Team:", team_list)

    t_meta = teams_df[teams_df["Team_Name"] == sel_team].iloc[0]
    default_eng = int(t_meta["Engineers_Count"])

    c1, c2, c3 = st.columns([1.2, 1.2, 2.6])
    with c1:
        eng_val = st.number_input(
            "Available Engineers (What-If):",
            min_value=0,
            max_value=200,
            value=default_eng,
            key=f"eng_cnt_{sel_team}",
        )
    with c2:
        daily_cap_val = round(eng_val * hours_per_day * efficiency, 1)
        st.metric("Daily Team Capacity", f"{daily_cap_val:.1f} hrs/day")
    with c3:
        st.info(
            f"**Team:** {sel_team} | **Location:** {t_meta.get('Location', '-')} | **Group:** {t_meta.get('Department_Group', '-')} | **Sheet Headcount:** {default_eng}"
        )

    team_tasks_raw = tasks_df[tasks_df["Team_Name"] == sel_team].copy()
    st.markdown("#### 📌 Assigned Team Tasks (What-If Simulation)")
    edited_team = st.data_editor(
        team_tasks_raw,
        key=f"team_editor_{sel_team}",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )
    sim_team = prepare_tasks_df(edited_team, projects_df, hours_per_day, efficiency)

    days, day_labels, dem_list, cap_list, var_list = compute_daily_team_load(
        sim_team, sel_team, start_timeline, num_weeks, eng_val, hours_per_day, efficiency
    )

    short_days = [(day_labels[i], v) for i, v in enumerate(var_list) if v < 0]
    if short_days:
        worst = min(short_days, key=lambda x: x[1])
        st.warning(
            f"⚠️ {len(short_days)} bottleneck day(s) out of {len(days)} working days. Worst day: **{worst[0]}** ({worst[1]:.1f} hrs)."
        )
    else:
        st.success("✅ No capacity bottlenecks detected in the selected period.")

    results_df = pd.DataFrame({"Metric": ["Demand (Hrs)", "Capacity (Hrs)", "Variance (Surplus/Shortage)"]})
    for i, lbl in enumerate(day_labels):
        results_df[lbl] = [dem_list[i], cap_list[i], var_list[i]]

    def highlight_bottlenecks(val):
        if isinstance(val, (int, float)) and val < 0:
            return "background-color: #ffcccc; color: #900000; font-weight: bold"
        return ""

    st.dataframe(
        results_df.style.map(highlight_bottlenecks, subset=day_labels).format("{:.1f}", subset=day_labels),
        use_container_width=True,
        hide_index=True,
    )

    chart_data = pd.DataFrame({
        "Day": day_labels,
        "Variance": var_list,
        "Demand": dem_list,
        "Capacity": cap_list,
    })
    chart_data["Color"] = np.where(chart_data["Variance"] >= 0, "#2ca02c", "#d62728")

    st.markdown("#### 📉 Daily Variance Chart")
    var_chart = alt.Chart(chart_data).mark_bar().encode(
        x=alt.X("Day:N", sort=day_labels, title="Day", axis=alt.Axis(labelAngle=-90)),
        y=alt.Y("Variance:Q", title="Variance (Hrs)"),
        color=alt.Color("Color:N", scale=None),
        tooltip=["Day", "Variance", "Demand", "Capacity"],
    )
    st.altair_chart(var_chart, use_container_width=True)

    st.markdown("#### 🗓️ Team Task Schedule")
    render_gantt_chart(sim_team, color_by="PM_Name", hide_completed=False)
