import datetime
from urllib.parse import quote

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt


# ----------------------------------------------------------------------
# 1. Page Configuration & Central Google Sheet Setup
# ----------------------------------------------------------------------
st.set_page_config(page_title="ACKD Engineering Resource Planner", layout="wide")
st.title("📊 Dynamic Engineering Resource Planner - ACKD")

# Central Google Sheet ID (ACKD_Resource_Planner_DB)
SPREADSHEET_ID = "1ksIh1yt0_zeOWszW1fk5zQdZSL5ytaKJ1-IMODmtjBY"
SHEET_EDIT_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"

# Working week: Sunday to Thursday (Friday & Saturday are the weekend)
NP_WEEKMASK = "1111001"
PD_WEEKMASK = "Sun Mon Tue Wed Thu"
DEFAULT_HOURS_PER_DAY = 8.0
DEFAULT_EFFICIENCY_PCT = 80.0


def build_sheet_csv_url(sheet_id: str, sheet_name: str) -> str:
    """Build public CSV export URL for a specific Google Sheet tab."""
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={quote(sheet_name)}"
    )


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse mixed date formats safely into python date objects."""
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(series, errors="coerce")
    return parsed.dt.date


# ----------------------------------------------------------------------
# 2. Data Loaders (No API Required)
# ----------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner="Loading Projects, Teams, and Tasks from Google Sheets...")
def load_planner_data(sheet_id: str):
    projects_url = build_sheet_csv_url(sheet_id, "Projects")
    teams_url = build_sheet_csv_url(sheet_id, "Team_Capacity")
    tasks_url = build_sheet_csv_url(sheet_id, "Tasks")

    # 1. Projects
    projects_df = pd.read_csv(projects_url)
    projects_df.columns = projects_df.columns.astype(str).str.strip()
    projects_df = projects_df.dropna(how="all").reset_index(drop=True)
    if "Target_Delivery_Date" in projects_df.columns:
        projects_df["Target_Delivery_Date"] = parse_dates(projects_df["Target_Delivery_Date"])

    # 2. Team Capacity
    teams_df = pd.read_csv(teams_url)
    teams_df.columns = teams_df.columns.astype(str).str.strip()
    teams_df = teams_df.dropna(how="all").reset_index(drop=True)
    for num_col, default_val in [
        ("Engineers_Count", 1),
        ("Hours_Per_Day", DEFAULT_HOURS_PER_DAY),
        ("Efficiency_Pct", DEFAULT_EFFICIENCY_PCT),
    ]:
        if num_col in teams_df.columns:
            teams_df[num_col] = pd.to_numeric(teams_df[num_col], errors="coerce").fillna(default_val)

    # 3. Tasks
    tasks_df = pd.read_csv(tasks_url)
    tasks_df.columns = tasks_df.columns.astype(str).str.strip()
    # Keep only rows that actually have a Project_Code or Task_Name
    key_cols = [c for c in ["Project_Code", "Task_Name", "Team_Name"] if c in tasks_df.columns]
    if key_cols:
        tasks_df = tasks_df.dropna(subset=key_cols, how="all").reset_index(drop=True)
    else:
        tasks_df = tasks_df.dropna(how="all").reset_index(drop=True)

    for d_col in ["Start_Date", "End_Date", "Project_Deadline"]:
        if d_col in tasks_df.columns:
            tasks_df[d_col] = parse_dates(tasks_df[d_col])

    for n_col in ["Engineers_Assigned", "Allocated_Hours"]:
        if n_col in tasks_df.columns:
            tasks_df[n_col] = pd.to_numeric(tasks_df[n_col], errors="coerce")

    return projects_df, teams_df, tasks_df


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


def prepare_tasks_df(
    raw_tasks: pd.DataFrame,
    projects_df: pd.DataFrame,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
    efficiency: float = 0.80,
) -> pd.DataFrame:
    df = raw_tasks.copy()
    if df.empty:
        return df

    # Enrich missing Project_Name / PM_Name / Project_Deadline from Projects master if needed
    if not projects_df.empty and "Project_Code" in df.columns and "Project_Code" in projects_df.columns:
        proj_lookup = projects_df.drop_duplicates(subset=["Project_Code"]).set_index("Project_Code")
        for target_col, src_col in [
            ("Project_Name", "Project_Name"),
            ("PM_Name", "PM_Name"),
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

    # Calculate Allocated_Hours if missing/zero and Engineers_Assigned is entered (for live What-If edits)
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

    # Schedule risk flag against Project_Deadline
    today = datetime.date.today()
    status_flags = []
    for _, row in df.iterrows():
        status_str = str(row.get("Coordination_Status", ""))
        e_date = row.get("End_Date")
        deadline = row.get("Project_Deadline")
        if "مكتمل" in status_str:
            status_flags.append("✅ مكتمل")
        elif pd.notnull(deadline) and pd.notnull(e_date) and e_date > deadline:
            status_flags.append("🚨 يتجاوز تسليم المشروع")
        elif pd.notnull(e_date) and e_date < today:
            status_flags.append("⚠️ متأخر عن اليوم")
        else:
            status_flags.append("🟢 ضمن الجدول")
    df["Schedule_Health"] = status_flags

    return df


# ----------------------------------------------------------------------
# 3. Gantt Chart Renderer
# ----------------------------------------------------------------------
def render_gantt_chart(df: pd.DataFrame, label_mode: str = "project_task", hide_completed: bool = False):
    if df.empty:
        st.info("لا توجد مهام مطابقة لمعايير التصفية الحالية.")
        return

    gantt_df = df.copy()
    gantt_df["Start"] = pd.to_datetime(gantt_df["Start_Date"], errors="coerce")
    gantt_df["End"] = pd.to_datetime(gantt_df["End_Date"], errors="coerce")
    gantt_df = gantt_df.dropna(subset=["Start", "End"])
    gantt_df = gantt_df[gantt_df["End"] >= gantt_df["Start"]]

    if hide_completed and "Coordination_Status" in gantt_df.columns:
        gantt_df = gantt_df[~gantt_df["Coordination_Status"].astype(str).str.contains("مكتمل", na=False)]

    if gantt_df.empty:
        st.info("لا توجد مهام نشطة أو قادمة بتواريخ صالحة للعرض.")
        return

    if label_mode == "team_task":
        gantt_df["RowLabel"] = (
            gantt_df["Project_Code"].fillna("").astype(str)
            + " | "
            + gantt_df["Task_Name"].fillna("Unnamed Task").astype(str)
        )
        color_col = "PM_Name"
    else:
        gantt_df["RowLabel"] = (
            gantt_df["Project_Code"].fillna("").astype(str)
            + " — "
            + gantt_df["Task_Name"].fillna("Unnamed Task").astype(str)
            + " ("
            + gantt_df["Team_Name"].fillna("").astype(str)
            + ")"
        )
        color_col = "Team_Name"

    # Ensure unique labels on Y-axis
    if gantt_df["RowLabel"].duplicated().any():
        seen = {}
        unique_labels = []
        for lbl in gantt_df["RowLabel"]:
            cnt = seen.get(lbl, 0) + 1
            seen[lbl] = cnt
            unique_labels.append(f"{lbl} #{cnt}" if cnt > 1 else lbl)
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
        alt.Tooltip("PM_Name:N", title="PM"),
        alt.Tooltip("Task_Name:N", title="Task"),
        alt.Tooltip("Team_Name:N", title="Team Lead"),
        alt.Tooltip("Start:T", title="Start", format="%a %d-%b-%Y"),
        alt.Tooltip("End:T", title="End", format="%a %d-%b-%Y"),
        alt.Tooltip("Effective_Hours:Q", title="Hours", format=",.1f"),
        alt.Tooltip("Coordination_Status:N", title="Status"),
    ]

    bars = alt.Chart(gantt_df).mark_bar(cornerRadius=3).encode(
        y=alt.Y("RowLabel:N", sort=row_order, title=None, axis=alt.Axis(labelLimit=320)),
        x=alt.X("Start:T", scale=x_scale, title="Date", axis=alt.Axis(format="%d-%b", labelAngle=-45)),
        x2="EndPlus:T",
        color=alt.Color(f"{color_col}:N", title="Legend"),
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
# 4. Daily Capacity & Bottleneck Engine
# ----------------------------------------------------------------------
def compute_daily_team_load(
    tasks_df: pd.DataFrame,
    teams_df: pd.DataFrame,
    team_name: str,
    start_timeline: datetime.date,
    num_weeks: int,
    override_engineers: int,
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
        if "مكتمل" in str(row.get("Coordination_Status", "")):
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

    daily_cap = round(override_engineers * hours_per_day * efficiency, 1)
    dem_list = [round(x, 1) for x in daily_demand]
    cap_list = [daily_cap] * num_days
    var_list = [round(daily_cap - d, 1) for d in dem_list]

    return days, day_labels, dem_list, cap_list, var_list


# ----------------------------------------------------------------------
# 5. Load Live Data & Top Action Bar
# ----------------------------------------------------------------------
try:
    projects_df, teams_df, raw_tasks_df = load_planner_data(SPREADSHEET_ID)
except Exception as e:
    st.error(
        f"⚠️ تعذر قراءة ملف Google Sheet. تأكد من أن الملف مشارك بصلاحية العرض (Anyone with the link: Viewer).\n\nالتفاصيل: {e}"
    )
    st.stop()

top_c1, top_c2, top_c3 = st.columns([1.2, 1.8, 3])
with top_c1:
    if st.button("🔄 تحديث البيانات (Refresh)", use_container_width=True):
        load_planner_data.clear()
        st.rerun()
with top_c2:
    st.link_button("📝 فتح Google Sheet لإدخال/تعديل المهام", SHEET_EDIT_URL, use_container_width=True)
with top_c3:
    st.caption(
        f"🟢 متصل بـ ACKD_Resource_Planner_DB | المشاريع: {len(projects_df)} | الأفرقة: {len(teams_df)} | المهام المسجلة: {len(raw_tasks_df)}"
    )

# Sidebar Global Settings
st.sidebar.header("⚙️ إعدادات السعة والتحليل")
hours_per_day = st.sidebar.number_input(
    "ساعات العمل اليومية للمهندس", min_value=1.0, max_value=12.0, value=DEFAULT_HOURS_PER_DAY, step=0.5
)
efficiency = st.sidebar.slider("معامل الكفاءة (%)", 50, 100, int(DEFAULT_EFFICIENCY_PCT)) / 100.0
start_timeline = st.sidebar.date_input("بداية أفق التحليل الزمني", value=datetime.date.today())
num_weeks = st.sidebar.slider("عدد أسابيع التحليل (الأحد–الخميس)", 1, 12, 4)

# Prepare enriched tasks
tasks_df = prepare_tasks_df(raw_tasks_df, projects_df, hours_per_day, efficiency)

# Navigation Modes
view_mode = st.radio(
    "اختر طريقة العرض:",
    [
        "🏢 نظرة تنفيذية شاملة (Executive Overview)",
        "📁 متابعة المشاريع ومدراء المشاريع (PM & Deadlines)",
        "👥 القدرة الاستيعابية للأفرقة (Team Capacity & Bottlenecks)",
    ],
    horizontal=True,
)
st.markdown("---")


# ======================================================================
# VIEW 1: Executive Overview
# ======================================================================
if view_mode.startswith("🏢"):
    st.subheader("🏢 ملخص حالة المشاريع، التنسيق، والقدرة الاستيعابية")

    total_tasks = len(tasks_df)
    active_tasks = len(tasks_df[~tasks_df["Coordination_Status"].astype(str).str.contains("مكتمل", na=False)]) if total_tasks else 0
    pending_coord = len(tasks_df[tasks_df["Coordination_Status"].astype(str).str.contains("مقترح|مراجعة", na=False)]) if total_tasks else 0
    overdue_risk = len(tasks_df[tasks_df["Schedule_Health"].astype(str).str.contains("🚨|⚠️", na=False)]) if total_tasks else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📁 إجمالي المشاريع", len(projects_df))
    m2.metric("📌 المهام النشطة", active_tasks)
    m3.metric("🤝 بانتظار اعتماد مدير الفريق", pending_coord)
    m4.metric("🚨 تنبيهات المواعيد والتسليم", overdue_risk)

    st.markdown("### 📊 ملخص القدرة الاستيعابية للأفرقة العشرة خلال فترة التحليل")
    summary_rows = []
    for _, t_row in teams_df.iterrows():
        t_name = t_row["Team_Name"]
        eng_cnt = int(t_row["Engineers_Count"])
        _, _, dem_l, cap_l, var_l = compute_daily_team_load(
            tasks_df, teams_df, t_name, start_timeline, num_weeks, eng_cnt, hours_per_day, efficiency
        )
        total_dem = sum(dem_l)
        total_cap = sum(cap_l)
        util_pct = round((total_dem / total_cap) * 100, 1) if total_cap > 0 else 0.0
        bottleneck_days = sum(1 for v in var_l if v < 0)

        summary_rows.append({
            "الفريق / مدير الفريق": t_name,
            "الفرع": t_row.get("Location", ""),
            "عدد المهندسين": eng_cnt,
            "السعة اليومية (ساعة)": cap_l[0] if cap_l else 0,
            "إجمالي الطلب بالفترة (ساعة)": round(total_dem, 1),
            "إجمالي السعة بالفترة (ساعة)": round(total_cap, 1),
            "نسبة الإشغال (%)": util_pct,
            "أيام العجز (Bottlenecks)": bottleneck_days,
        })

    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    st.markdown("### 🗓️ المخطط الزمني لكافة المهام النشطة")
    hide_comp_exec = st.checkbox("إخفاء المهام المكتملة", value=True, key="exec_hide_comp")
    render_gantt_chart(tasks_df, label_mode="project_task", hide_completed=hide_comp_exec)


# ======================================================================
# VIEW 2: Project Manager & Deadlines View
# ======================================================================
elif view_mode.startswith("📁"):
    st.subheader("📁 متابعة مواعيد التسليم حسب مدير المشروع (PM) والمشروع")

    pm_list = ["الكل"] + sorted(projects_df["PM_Name"].dropna().unique().tolist())
    f_col1, f_col2, f_col3 = st.columns([1.2, 1.8, 1])
    with f_col1:
        selected_pm = st.selectbox("👤 اختر مدير المشروع (PM):", pm_list)
    with f_col2:
        if selected_pm == "الكل":
            avail_projects = projects_df
        else:
            avail_projects = projects_df[projects_df["PM_Name"] == selected_pm]

        proj_options = ["كافة المشاريع"] + [
            f"{r['Project_Code']} — {r['Project_Name']}" for _, r in avail_projects.iterrows()
        ]
        selected_proj_label = st.selectbox("🏗️ اختر المشروع:", proj_options)
    with f_col3:
        hide_comp_pm = st.checkbox("إخفاء المكتمل", value=False, key="pm_hide_comp")

    filtered_tasks = tasks_df.copy()
    if selected_pm != "الكل":
        filtered_tasks = filtered_tasks[filtered_tasks["PM_Name"] == selected_pm]
    if selected_proj_label != "كافة المشاريع":
        sel_code = selected_proj_label.split(" — ")[0].strip()
        filtered_tasks = filtered_tasks[filtered_tasks["Project_Code"] == sel_code]

    st.markdown("#### 📋 سجل مهام المشاريع (قابل للتعديل المؤقت للمحاكاة)")
    edited_pm_df = st.data_editor(
        filtered_tasks,
        key="pm_task_editor",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )
    sim_pm_tasks = prepare_tasks_df(edited_pm_df, projects_df, hours_per_day, efficiency)

    st.markdown("#### 🗓️ الجدول الزمني للمهام ومواعيد التسليم")
    render_gantt_chart(sim_pm_tasks, label_mode="project_task", hide_completed=hide_comp_pm)


# ======================================================================
# VIEW 3: Team Capacity & Bottlenecks View
# ======================================================================
elif view_mode.startswith("👥"):
    st.subheader("👥 تحليل القدرة الاستيعابية والاختناقات لكل فريق")

    team_names = teams_df["Team_Name"].dropna().tolist()
    selected_team = st.selectbox("اختر الفريق / مدير الفريق:", team_names)

    team_meta = teams_df[teams_df["Team_Name"] == selected_team].iloc[0]
    default_eng = int(team_meta["Engineers_Count"])

    c1, c2, c3 = st.columns([1.2, 1.2, 2.6])
    with c1:
        override_eng = st.number_input(
            "عدد المهندسين المتاحين (محاكاة):",
            min_value=0,
            max_value=200,
            value=default_eng,
            key=f"eng_input_{selected_team}",
        )
    with c2:
        daily_cap_val = round(override_eng * hours_per_day * efficiency, 1)
        st.metric("السعة اليومية المتاحة", f"{daily_cap_val} ساعة/يوم")
    with c3:
        st.info(
            f"📍 **الفرع:** {team_meta.get('Location', '-')} | **المجموعة:** {team_meta.get('Department_Group', '-')} | **العدد الفعلي في الشيت:** {default_eng} مهندس"
        )

    team_tasks_raw = tasks_df[tasks_df["Team_Name"] == selected_team].copy()
    st.markdown("#### 📌 المهام المسندة لهذا الفريق من مدراء المشاريع (يمكن التعديل المؤقت للمحاكاة)")
    edited_team_tasks = st.data_editor(
        team_tasks_raw,
        key=f"team_editor_{selected_team}",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )
    sim_team_tasks = prepare_tasks_df(edited_team_tasks, projects_df, hours_per_day, efficiency)

    # Calculate daily load
    days, day_labels, dem_list, cap_list, var_list = compute_daily_team_load(
        sim_team_tasks,
        teams_df,
        selected_team,
        start_timeline,
        num_weeks,
        override_eng,
        hours_per_day,
        efficiency,
    )

    short_days = [(day_labels[i], v) for i, v in enumerate(var_list) if v < 0]
    if short_days:
        worst = min(short_days, key=lambda x: x[1])
        st.warning(
            f"⚠️ يوجد عجز في السعة خلال **{len(short_days)}** يوم عمل من أصل {len(days)}. أسوأ يوم: **{worst[0]}** ({worst[1]:.1f} ساعة)."
        )
    else:
        st.success("✅ السعة الاستيعابية كافية ولا توجد اختناقات خلال الفترة المحددة.")

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

    st.markdown("#### 📉 مخطط الفائض والعجز اليومي (Daily Variance)")
    var_chart = alt.Chart(chart_data).mark_bar().encode(
        x=alt.X("Day:N", sort=day_labels, title="اليوم", axis=alt.Axis(labelAngle=-90)),
        y=alt.Y("Variance:Q", title="الفائض / العجز (ساعة)"),
        color=alt.Color("Color:N", scale=None),
        tooltip=["Day", "Variance", "Demand", "Capacity"],
    )
    st.altair_chart(var_chart, use_container_width=True)

    st.markdown("#### 🗓️ الجدول الزمني لمهام الفريق حسب المشاريع")
    render_gantt_chart(sim_team_tasks, label_mode="team_task", hide_completed=False)
