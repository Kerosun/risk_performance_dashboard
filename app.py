import pandas as pd
import streamlit as st
import altair as alt

def format_duration(seconds):
    """Convert seconds into a readable duration."""
    if pd.isna(seconds):
        return "N/A"

    seconds = max(0, int(round(seconds)))

    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes:02d}m {remaining_seconds:02d}s"

    if minutes > 0:
        return f"{minutes}m {remaining_seconds:02d}s"

    return f"{remaining_seconds}s"

# 1. Configure the page
st.set_page_config(
    page_title="Risk Incident Dashboard",
    layout="wide"
)

st.title("Incident Workflow Performance Dashboard")
st.write("This dashboard reviews a random sample of completed incidents to help managers and analysts understand workflow speed, SLA performance, consistency, data quality and opportunities for process improvement.")

# 2. Upload the CSV files
st.sidebar.header("Upload data")

incidents_file = st.sidebar.file_uploader(
    "Upload incidents CSV",
    type=["csv"]
)

notes_file = st.sidebar.file_uploader(
    "Upload notes CSV",
    type=["csv"]
)

if incidents_file is None or notes_file is None:
    st.info("Upload both the incidents CSV and notes CSV to begin.")
    st.stop()

try:
    incidents = pd.read_csv(incidents_file)
    notes = pd.read_csv(notes_file)
except Exception as error:
    st.error(f"Could not read the uploaded CSV files: {error}")
    st.stop()

# Remove accidental spaces from column headings
incidents.columns = incidents.columns.str.strip()
notes.columns = notes.columns.str.strip()

# The real sample uses severity_at_ud
if "severity_at_ud" in incidents.columns:
    incidents = incidents.rename(
        columns={"severity_at_ud": "severity"}
    )

# 3. Check required columns
required_incident_columns = {
    "incident_id",
    "detection_at",
    "ud_at",
    "closed_at",
    "severity",
    "incident_type",
    "country",
    "state_province",
    "city"
}

required_note_columns = {
    "incident_id",
    "note_at",
    "note_type"
}

missing_incident_columns = (
    required_incident_columns - set(incidents.columns)
)

missing_note_columns = (
    required_note_columns - set(notes.columns)
)

if missing_incident_columns:
    st.error(
        "The incidents CSV is missing these columns: "
        + ", ".join(sorted(missing_incident_columns))
    )
    st.write("Columns found:", incidents.columns.tolist())
    st.stop()

if missing_note_columns:
    st.error(
        "The notes CSV is missing these columns: "
        + ", ".join(sorted(missing_note_columns))
    )
    st.write("Columns found:", notes.columns.tolist())
    st.stop()

# 4. Convert timestamps to Melbourne time
def to_melbourne_time(series):
    return (
        pd.to_datetime(
            series,
            utc=True,
            errors="coerce"
        )
        .dt.tz_convert("Australia/Melbourne")
    )

for column in ["detection_at", "ud_at", "closed_at"]:
    incidents[column] = to_melbourne_time(
        incidents[column]
    )

notes["note_at"] = to_melbourne_time(
    notes["note_at"]
)

if incidents["detection_at"].dropna().empty:
    st.error("No valid detection_at timestamps were found.")
    st.stop()

# 5. Find the first RTC alert
notes["note_type"] = (
    notes["note_type"]
    .astype(str)
    .str.strip()
    .str.lower()
)

first_notes = (
    notes[notes["note_type"] == "first_note"]
    .sort_values("note_at")
    .groupby("incident_id", as_index=False)
    .first()
    [["incident_id", "note_at"]]
    .rename(columns={"note_at": "first_note_at"})
)

# 6. Merge the datasets
workflow = incidents.merge(
    first_notes,
    on="incident_id",
    how="left"
)


# 7. Calculate workflow metrics

workflow["detection_to_ud_seconds"] = (
    workflow["ud_at"] - workflow["detection_at"]
).dt.total_seconds()

workflow["first_note_seconds"] = (
    workflow["first_note_at"] - workflow["ud_at"]
).dt.total_seconds()

workflow["incident_duration_seconds"] = (
    workflow["closed_at"] - workflow["ud_at"]
).dt.total_seconds()


# Classify first-alert timing

# A timestamp is genuinely missing
missing_first_note_timestamp = (
    workflow["first_note_at"].isna()
    | workflow["first_note_seconds"].isna()
)

# A calculated time of exactly zero is treated as missing
zero_first_note_time = (
    workflow["first_note_seconds"].eq(0)
)

# A negative time is an invalid timestamp order
negative_first_note_time = (
    workflow["first_note_seconds"].lt(0)
)

# Only positive response times are valid for SLA calculations
valid_first_note_time = (
    workflow["first_note_seconds"].gt(0)
)


# Create one clear status for every incident
workflow["first_note_status"] = "Invalid timing"

workflow.loc[
    missing_first_note_timestamp | zero_first_note_time,
    "first_note_status"
] = "Missing first alert"

workflow.loc[
    valid_first_note_time
    & workflow["first_note_seconds"].le(120),
    "first_note_status"
] = "SLA met"

workflow.loc[
    valid_first_note_time
    & workflow["first_note_seconds"].gt(120),
    "first_note_status"
] = "Late first alert"


# Use a nullable Boolean column
# True  = valid alert within 120 seconds
# False = valid alert taking more than 120 seconds
# NA    = missing, zero or invalid timing
workflow["first_note_sla_met"] = pd.Series(
    pd.NA,
    index=workflow.index,
    dtype="boolean"
)

workflow.loc[
    valid_first_note_time,
    "first_note_sla_met"
] = (
    workflow.loc[
        valid_first_note_time,
        "first_note_seconds"
    ]
    .le(120)
)


# Add data-quality flags

if "data_quality_flag" not in workflow.columns:
    workflow["data_quality_flag"] = pd.NA


def add_quality_flag(existing_flag, new_flag):
    """Add a flag without deleting an existing flag."""

    if (
        pd.isna(existing_flag)
        or str(existing_flag).strip() in {"", "no_issue"}
    ):
        return new_flag

    existing_flags = [
        flag.strip()
        for flag in str(existing_flag).split(";")
    ]

    if new_flag in existing_flags:
        return "; ".join(existing_flags)

    return f"{'; '.join(existing_flags)}; {new_flag}"


# Flag zero-second records
workflow.loc[
    zero_first_note_time,
    "data_quality_flag"
] = (
    workflow.loc[
        zero_first_note_time,
        "data_quality_flag"
    ]
    .apply(
        lambda value: add_quality_flag(
            value,
            "zero_first_note_time"
        )
    )
)

# Flag genuinely missing first alerts
workflow.loc[
    missing_first_note_timestamp,
    "data_quality_flag"
] = (
    workflow.loc[
        missing_first_note_timestamp,
        "data_quality_flag"
    ]
    .apply(
        lambda value: add_quality_flag(
            value,
            "missing_first_alert"
        )
    )
)

# Flag negative timing
workflow.loc[
    negative_first_note_time,
    "data_quality_flag"
] = (
    workflow.loc[
        negative_first_note_time,
        "data_quality_flag"
    ]
    .apply(
        lambda value: add_quality_flag(
            value,
            "negative_first_note_time"
        )
    )
)

workflow["data_quality_flag"] = (
    workflow["data_quality_flag"]
    .fillna("no_issue")
)

# 8. Create sidebar filters

# Incident type filter
incident_type_options = sorted(
    workflow["incident_type"]
    .dropna()
    .astype(str)
    .unique()
)

selected_incident_types = st.sidebar.multiselect(
    "Incident type",
    options=incident_type_options,
    default=incident_type_options
)


# Country filter
country_options = sorted(
    workflow["country"]
    .dropna()
    .astype(str)
    .unique()
)

selected_countries = st.sidebar.multiselect(
    "Country",
    options=country_options,
    default=country_options
)


# Time filter
minimum_date = workflow["detection_at"].min().date()
maximum_date = workflow["detection_at"].max().date()

start_date = st.sidebar.date_input(
    "Start date",
    value=minimum_date,
    min_value=minimum_date,
    max_value=maximum_date,
    format="DD/MM/YYYY"
)

end_date = st.sidebar.date_input(
    "End date",
    value=maximum_date,
    min_value=minimum_date,
    max_value=maximum_date,
    format="DD/MM/YYYY"
)

if start_date > end_date:
    st.warning("The start date must be on or before the end date.")
    st.stop()


# 9. Apply the three filters

filtered_workflow = workflow[
    (
        workflow["incident_type"]
        .astype(str)
        .isin(selected_incident_types)
    )
    & (
        workflow["country"]
        .astype(str)
        .isin(selected_countries)
    )
    & (
        workflow["detection_at"]
        .dt.date
        .between(start_date, end_date)
    )
].copy()

if filtered_workflow.empty:
    st.warning("No incidents match the selected filters.")
    st.stop()


# 10. KPI cards

# 10. Calculate and display KPI cards

total_incidents = filtered_workflow["incident_id"].nunique()

# Keep only valid response times
valid_detection_to_ud = (
    filtered_workflow.loc[
        filtered_workflow["detection_to_ud_seconds"] >= 0,
        "detection_to_ud_seconds"
    ]
    .dropna()
)

valid_first_note_times = (
    filtered_workflow.loc[
        filtered_workflow["first_note_status"].isin(
            ["SLA met", "Late first alert"]
        ),
        "first_note_seconds"
    ]
    .dropna()
)

median_detection_to_ud = valid_detection_to_ud.median()

median_first_note = valid_first_note_times.median()

p90_first_note = valid_first_note_times.quantile(0.90)

valid_sla_results = (
    filtered_workflow["first_note_sla_met"]
    .dropna()
)

if valid_sla_results.empty:
    sla_rate_text = "N/A"
else:
    sla_rate = (
        valid_sla_results
        .astype(float)
        .mean()
        * 100
    )

    sla_rate_text = f"{sla_rate:.1f}%"

column1, column2, column3, column4, column5 = st.columns(5)

column1.metric(
    "Total incidents",
    total_incidents
)

column2.metric(
    "Median detection → U&D",
    format_duration(median_detection_to_ud)
)

column3.metric(
    "Median U&D → first alert",
    format_duration(median_first_note)
)

column4.metric(
    "P90 U&D → first alert",
    format_duration(p90_first_note),
    help=(
        "90% of incidents received their first RTC Alert "
        "within this amount of time."
    )
)

column5.metric(
    "First-alert SLA compliance",
    sla_rate_text,
    help=(
        "Calculated only from incidents with a valid positive "
        "first-alert response time. Missing, zero and invalid "
        "timings are excluded."
    )
)

# 11. Workflow performance table

st.subheader("Workflow performance")

workflow_display_columns = [
    "incident_id",
    "event_label",
    "severity",
    "incident_type",
    "country",
    "state_province",
    "city",
    "detection_at",
    "ud_at",
    "first_note_at",
    "detection_to_ud_seconds",
    "first_note_seconds",
    "first_note_status",
    "first_note_sla_met",
    "closure_method",
    "data_quality_flag"
]

# Keep only columns that exist
workflow_display_columns = [
    column
    for column in workflow_display_columns
    if column in filtered_workflow.columns
]

# Keep all incidents
workflow_display = filtered_workflow[
    workflow_display_columns
]

st.dataframe(
    workflow_display,
    width="stretch",
    height=245,
    hide_index=True
)

# 12. First-note SLA exceptions and summary

st.subheader("First-note SLA exceptions")

# Identify exception types
missing_first_alert_mask = (
    filtered_workflow["first_note_status"]
    .eq("Missing first alert")
)

late_first_alert_mask = (
    filtered_workflow["first_note_status"]
    .eq("Late first alert")
)

invalid_timing_mask = (
    filtered_workflow["first_note_status"]
    .eq("Invalid timing")
)

# Count incidents
total_incidents = filtered_workflow["incident_id"].nunique()

late_first_alert_count = (
    filtered_workflow.loc[
        late_first_alert_mask,
        "incident_id"
    ]
    .nunique()
)

missing_first_alert_count = (
    filtered_workflow.loc[
        missing_first_alert_mask,
        "incident_id"
    ]
    .nunique()
)

# Calculate percentages
if total_incidents > 0:
    late_first_alert_percentage = (
        late_first_alert_count
        / total_incidents
        * 100
    )

    missing_first_alert_percentage = (
        missing_first_alert_count
        / total_incidents
        * 100
    )
else:
    late_first_alert_percentage = 0
    missing_first_alert_percentage = 0


# Create the exception table
sla_exceptions = (
    filtered_workflow[
        late_first_alert_mask
        | missing_first_alert_mask
        | invalid_timing_mask
    ]
    .sort_values(
        "first_note_seconds",
        ascending=False,
        na_position="last"
    )
)

exception_columns = [
    "incident_id",
    "event_label",
    "incident_type",
    "first_note_seconds",
    "first_note_status",
    "data_quality_flag"
]

# Keep only columns that exist
exception_columns = [
    column
    for column in exception_columns
    if column in sla_exceptions.columns
]

exception_display = (
    sla_exceptions[exception_columns]
    .rename(
        columns={
            "incident_id": "Incident ID",
            "event_label": "Incident",
            "incident_type": "Incident type",
            "first_note_seconds": "First alert (seconds)",
            "first_note_status": "Status",
            "data_quality_flag": "Data-quality flag"
        }
    )
)

# Exception table and KPI summary

table_column, summary_column = st.columns(
    [3.7, 1.3],
    gap="large",
    vertical_alignment="top"
)

# Both content areas use exactly the same height
section_height = 290


# Left side
with table_column:

    st.dataframe(
        exception_display,
        width="stretch",
        height=section_height,
        row_height=42,
        hide_index=True,
        column_config={
            "Incident ID": st.column_config.TextColumn(
                width="small"
            ),
            "Incident": st.column_config.TextColumn(
                width="medium"
            ),
            "Incident type": st.column_config.TextColumn(
                width="small"
            ),
            "First alert (seconds)":
                st.column_config.NumberColumn(
                    width="small",
                    format="%d"
                ),
            "Status": st.column_config.TextColumn(
                width="medium"
            ),
            "Data-quality flag":
                st.column_config.TextColumn(
                    width="large"
                )
        }
    )


# Right side
with summary_column:

    # Same fixed height as the table
    with st.container(
        border=True,
        height=section_height,
        vertical_alignment="distribute",
        gap="xxsmall"
    ):
        st.metric(
            label="Late first alerts",
            value=late_first_alert_count,
            help=(
                "Valid first alerts issued more than "
                "120 seconds after U&D."
            ),
            height="content"
        )

        st.caption(
            f"{late_first_alert_percentage:.1f}% "
            "of selected incidents"
        )

        st.divider()

        st.metric(
            label="Missing first alerts",
            value=missing_first_alert_count,
            help=(
                "Includes missing or zero-second "
                "first-alert times."
            ),
            height="content"
        )

        st.caption(
            f"{missing_first_alert_percentage:.1f}% "
            "of selected incidents"
        )


# 13. SLA compliance by severity — currently hidden
if False:
    st.subheader("SLA compliance by severity")

    sla_by_severity = (
        filtered_workflow
        .groupby("severity")["first_note_sla_met"]
        .mean()
        .mul(100)
        .reset_index()
    )

    st.bar_chart(
        sla_by_severity,
        x="severity",
        y="first_note_sla_met"
    )


# 14. First-alert outcomes by incident type

st.subheader("First-alert outcomes by incident type")

# Keep only the columns required for this chart
sla_outcomes = filtered_workflow[
    [
        "incident_id",
        "incident_type",
        "first_note_status"
    ]
].copy()

# Clean incident-type labels
sla_outcomes["incident_type"] = (
    sla_outcomes["incident_type"]
    .fillna("Unknown")
    .astype(str)
    .str.strip()
    .replace("", "Unknown")
)

# first_note_status should already exist,
# but this prevents unexpected missing values
sla_outcomes["first_note_status"] = (
    sla_outcomes["first_note_status"]
    .fillna("Invalid timing")
)

# Fixed display order for stacked bars and legend
status_order = [
    "SLA met",
    "Late first alert",
    "Missing first alert",
    "Invalid timing"
]

# Coordinated Apple-inspired technology palette
status_colours = [
    "#5B7CFA",  # SLA met — soft Apple blue
    "#E6AC55",  # Late first alert — muted amber
    "#D98675",  # Missing first alert — soft coral
    "#A8ADB7"   # Invalid timing — cool grey
]

# Numeric order helps Altair stack the outcomes consistently
status_rank = {
    status: rank
    for rank, status in enumerate(
        status_order,
        start=1
    )
}

sla_outcomes["status_rank"] = (
    sla_outcomes["first_note_status"]
    .map(status_rank)
    .fillna(len(status_order) + 1)
    .astype(int)
)

# Count unique incidents by incident type and SLA outcome
sla_by_incident_type = (
    sla_outcomes
    .groupby(
        [
            "incident_type",
            "first_note_status",
            "status_rank"
        ],
        as_index=False,
        observed=True
    )
    .agg(
        incident_count=(
            "incident_id",
            "nunique"
        )
    )
)

# Calculate the total number of incidents for each incident type
incident_type_totals = (
    sla_by_incident_type
    .groupby(
        "incident_type",
        as_index=False,
        observed=True
    )
    .agg(
        total_incidents=(
            "incident_count",
            "sum"
        )
    )
    .sort_values(
        [
            "total_incidents",
            "incident_type"
        ],
        ascending=[
            False,
            True
        ]
    )
)

# Add totals to the chart dataset
sla_by_incident_type = (
    sla_by_incident_type
    .merge(
        incident_type_totals,
        on="incident_type",
        how="left"
    )
)

# Calculate each outcome's percentage within its incident type
sla_by_incident_type["outcome_percentage"] = (
    sla_by_incident_type["incident_count"]
    .div(
        sla_by_incident_type["total_incidents"]
    )
    .mul(100)
)

# Pre-format the percentage for the tooltip
sla_by_incident_type["outcome_percentage_text"] = (
    sla_by_incident_type["outcome_percentage"]
    .map(lambda value: f"{value:.1f}%")
)

# Sort incident types from highest to lowest volume
incident_type_order = (
    incident_type_totals["incident_type"]
    .tolist()
)

# Create horizontal stacked bars
bars = (
    alt.Chart(sla_by_incident_type)
    .mark_bar(
        size=24,
        opacity=0.9
    )
    .encode(
        y=alt.Y(
            "incident_type:N",
            title=None,
            sort=incident_type_order,
            axis=alt.Axis(
                labelLimit=220,
                labelPadding=10,
                domain=False,
                tickSize=0
            )
        ),

        x=alt.X(
            "incident_count:Q",
            title="Number of incidents",
            stack="zero",
            axis=alt.Axis(
                tickMinStep=1,
                titlePadding=12,
                domain=False,
                tickSize=0,
                grid=True
            )
        ),

        color=alt.Color(
            "first_note_status:N",
            title=None,
            scale=alt.Scale(
                domain=[
                    "SLA met",
                    "Late first alert",
                    "Missing first alert",
                    "Invalid timing"
                ],
                range=[
                    "#5B7CFA",
                    "#E6AC55",
                    "#D98675",
                    "#A8ADB7"
                ]
            ),
            legend=alt.Legend(
                orient="top",
                direction="horizontal",
                symbolType="square",
                symbolSize=110,
                labelLimit=180,
                offset=12
            )
        ),  # This comma was missing

        order=alt.Order(
            "status_rank:Q",
            sort="ascending"
        ),

        tooltip=[
            alt.Tooltip(
                "incident_type:N",
                title="Incident type"
            ),
            alt.Tooltip(
                "first_note_status:N",
                title="Outcome"
            ),
            alt.Tooltip(
                "incident_count:Q",
                title="Incidents",
                format=".0f"
            ),
            alt.Tooltip(
                "outcome_percentage_text:N",
                title="Share of incident type"
            )
        ]
    )
)


# Add the total count at the end of each bar
total_labels = (
    alt.Chart(incident_type_totals)
    .mark_text(
        dx=8,
        align="left",
        baseline="middle",
        fontSize=12,
        fontWeight=600,
        color="#2C2C2E"
    )
    .encode(
        y=alt.Y(
            "incident_type:N",
            sort=incident_type_order
        ),

        x=alt.X(
            "total_incidents:Q"
        ),

        text=alt.Text(
            "total_incidents:Q",
            format=".0f"
        )
    )
)


# Combine the stacked bars and total labels
sla_chart = (
    (bars + total_labels)  # Use total_labels, not totals
    .properties(
        height=340
    )
    .configure_view(
        stroke=None,
        fill="#FBFCFE"
    )
    .configure_axis(
        gridColor="#E6EAF0",
        gridOpacity=0.8,
        domainColor="#D1D6DE",
        tickColor="#D1D6DE",
        labelColor="#48484A",
        titleColor="#2C2C2E",
        labelFont="Arial",
        titleFont="Arial",
        labelFontSize=12,
        titleFontSize=13,
        titleFontWeight=500
    )
    .configure_legend(
        labelColor="#48484A",
        labelFont="Arial",
        labelFontSize=12,
        symbolStrokeWidth=0
    )
)

st.altair_chart(
    sla_chart,
    width="stretch",
    theme=None
)

# 15. Data-quality checks

st.subheader("Data quality checks")

missing_first_notes = (
    filtered_workflow["first_note_status"]
    .eq("Missing first alert")
    .sum()
)

zero_second_first_notes = (
    filtered_workflow["first_note_seconds"]
    .eq(0)
    .sum()
)

ud_before_detection = (
    filtered_workflow["detection_to_ud_seconds"] < 0
).sum()

closed_before_first_note = (
    filtered_workflow["closed_at"]
    < filtered_workflow["first_note_at"]
).sum()

(
    quality_column1,
    quality_column2,
    quality_column3,
    quality_column4
) = st.columns(4)

quality_column1.metric(
    "Missing first alerts",
    int(missing_first_notes),
    help="Includes records with a missing or zero-second first alert."
)

quality_column2.metric(
    "Zero-second alert records",
    int(zero_second_first_notes),
    help=(
        "Records where the calculated U&D-to-first-alert time "
        "is exactly zero seconds."
    )
)

quality_column3.metric(
    "U&D before detection",
    int(ud_before_detection)
)

quality_column4.metric(
    "Closed before first note",
    int(closed_before_first_note)
)


# 16.  Dashboard disclaimer

st.divider()

st.caption(
    "Disclaimer: Results are based on a random sample of incidents "
    "and should be interpreted as workflow indicators rather than "
    "a complete measure of team performance."
)