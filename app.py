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

# 10. Calculate and display KPI cards

# Total number of rows in the original incidents CSV
# This represents the total number of uploaded incident records
all_incidents = len(incidents)

# Number of unique incidents matching the current sidebar filters
filtered_incidents = (
    filtered_workflow["incident_id"]
    .dropna()
    .nunique()
)

# Display as: filtered (total)
incident_count_text = (
    f"{filtered_incidents} ({all_incidents})"
)


# Keep only valid detection-to-U&D times
valid_detection_to_ud = (
    filtered_workflow.loc[
        filtered_workflow["detection_to_ud_seconds"].ge(0),
        "detection_to_ud_seconds"
    ]
    .dropna()
)

# Keep only valid first-alert response times
valid_first_note_times = (
    filtered_workflow.loc[
        filtered_workflow["first_note_status"].isin(
            ["SLA met", "Late first alert"]
        ),
        "first_note_seconds"
    ]
    .dropna()
)


# Calculate timing KPIs
median_detection_to_ud = (
    valid_detection_to_ud.median()
)

median_first_note = (
    valid_first_note_times.median()
)

p90_first_note = (
    valid_first_note_times.quantile(0.90)
)


# Calculate SLA compliance using valid first-alert records only
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


# Create five KPI columns
column1, column2, column3, column4, column5 = (
    st.columns(5)
)


column1.metric(
    label="Incidents shown (total)",
    value=incident_count_text,
    help=(
        "The first number is the number of unique incidents "
        "matching the current filters. The number in brackets "
        "is the total number of incident rows in the uploaded "
        "incidents CSV."
    )
)

column2.metric(
    label="Median detection → U&D",
    value=format_duration(
        median_detection_to_ud
    )
)

column3.metric(
    label="Median U&D → first alert",
    value=format_duration(
        median_first_note
    )
)

column4.metric(
    label="P90 U&D → first alert",
    value=format_duration(
        p90_first_note
    ),
    help=(
        "About 90% of incidents with a valid first-alert time "
        "received their first alert within this duration. "
        "The slowest 10% took longer."
    )
)

column5.metric(
    label="First-alert SLA compliance",
    value=sla_rate_text,
    help=(
        "The percentage of incidents with a valid positive "
        "first-alert response time that met the 120-second target. "
        "Missing, zero and invalid timings are excluded."
    )
)

# 11. Workflow performance table

st.subheader("Workflow performance")

st.caption(
    "All timestamps are in AEST."
)

# Columns displayed in the table
workflow_display_columns = [
    "incident_id",
    "event_label",
    "incident_type",
    "country",
    "detection_at",
    "ud_at",
    "first_note_at",
    "detection_to_ud_seconds",
    "first_note_seconds",
    "first_note_status",
    "first_note_sla_met",
    "closure_method",
    "data_quality_flag",
]

# Keep only columns available in the filtered dataset
workflow_display_columns = [
    column
    for column in workflow_display_columns
    if column in filtered_workflow.columns
]

# Prepare the display table without changing the underlying data
workflow_display = filtered_workflow[
    workflow_display_columns
].copy()

st.dataframe(
    workflow_display,
    width="stretch",
    height=245,
    hide_index=True,
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

# Number of unique incidents currently included by the filters
selected_incident_count = (
    filtered_workflow["incident_id"]
    .dropna()
    .nunique()
)

late_first_alert_count = (
    filtered_workflow.loc[
        late_first_alert_mask,
        "incident_id"
    ]
    .dropna()
    .nunique()
)

missing_first_alert_count = (
    filtered_workflow.loc[
        missing_first_alert_mask,
        "incident_id"
    ]
    .dropna()
    .nunique()
)

invalid_timing_count = (
    filtered_workflow.loc[
        invalid_timing_mask,
        "incident_id"
    ]
    .dropna()
    .nunique()
)


# Calculate percentages based on selected incidents

if selected_incident_count > 0:
    late_first_alert_percentage = (
        late_first_alert_count
        / selected_incident_count
        * 100
    )

    missing_first_alert_percentage = (
        missing_first_alert_count
        / selected_incident_count
        * 100
    )

    invalid_timing_percentage = (
        invalid_timing_count
        / selected_incident_count
        * 100
    )
else:
    late_first_alert_percentage = 0.0
    missing_first_alert_percentage = 0.0
    invalid_timing_percentage = 0.0


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
    "country",
    "state_province",
    "city",
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
            "country": "Country",
            "state_province": "State / Province",
            "city": "City",
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

section_height = 320


# Left side: exception table

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
            "Country": st.column_config.TextColumn(
                width="small"
            ),
            "State / Province": st.column_config.TextColumn(
                width="small"
            ),
            "City": st.column_config.TextColumn(
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


# Right side: exception summary

with summary_column:
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
            )
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
                "Includes incidents with a missing or "
                "zero-second first-alert time."
            )
        )

        st.caption(
            f"{missing_first_alert_percentage:.1f}% "
            "of selected incidents"
        )

        st.divider()

        st.metric(
            label="Invalid timings",
            value=invalid_timing_count,
            help=(
                "Incidents where the first-alert timestamp "
                "appears before the U&D timestamp."
            )
        )

        st.caption(
            f"{invalid_timing_percentage:.1f}% "
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


# Keep only the columns required for the chart
sla_outcomes = filtered_workflow[
    [
        "incident_id",
        "incident_type",
        "first_note_status",
    ]
].copy()


# Clean incident-type labels
sla_outcomes["incident_type"] = (
    sla_outcomes["incident_type"]
    .fillna("Unknown incident type")
    .astype(str)
    .str.strip()
    .str.lower()
    .replace("", "unknown incident type")
)


# Rename labels for display without changing the original CSV
incident_type_display_names = {
    "explosion/fire": "Explosion/fire",
    "protest": "Protest",
    "shooting": "Shooting",
    "standoff": "Standoff",
    "weather": "Weather",
    "bomb threat": "Bomb threat",
    "others": "Others",
    "unknown incident type": "Unknown incident type",
}

sla_outcomes["incident_type_display"] = (
    sla_outcomes["incident_type"]
    .replace(incident_type_display_names)
)


# Clean first-alert outcome labels
sla_outcomes["first_note_status"] = (
    sla_outcomes["first_note_status"]
    .fillna("Invalid timing")
    .astype(str)
    .str.strip()
    .replace("", "Invalid timing")
)


# Fixed outcome order and colours
status_order = [
    "SLA met",
    "Late first alert",
    "Missing first alert",
    "Invalid timing",
]

status_colours = [
    "#5B7CFA",
    "#E6AC55",
    "#D98675",
    "#A8ADB7",
]

status_rank = {
    status: rank
    for rank, status in enumerate(status_order, start=1)
}

sla_outcomes["status_rank"] = (
    sla_outcomes["first_note_status"]
    .map(status_rank)
    .fillna(len(status_order) + 1)
    .astype(int)
)


# Count unique incidents by incident type and first-alert outcome
sla_by_incident_type = (
    sla_outcomes
    .groupby(
        [
            "incident_type_display",
            "first_note_status",
            "status_rank",
        ],
        as_index=False,
        observed=True,
    )
    .agg(
        incident_count=(
            "incident_id",
            "nunique",
        )
    )
)


if sla_by_incident_type.empty:
    st.info("No incidents match the selected filters.")

else:
    # Calculate total incidents for each incident type
    incident_type_totals = (
        sla_by_incident_type
        .groupby(
            "incident_type_display",
            as_index=False,
            observed=True,
        )
        .agg(
            total_incidents=(
                "incident_count",
                "sum",
            )
        )
    )


    # Add totals to the chart data
    sla_by_incident_type = (
        sla_by_incident_type
        .merge(
            incident_type_totals,
            on="incident_type_display",
            how="left",
        )
    )


    # Calculate percentages for the tooltip
    sla_by_incident_type["outcome_percentage"] = (
        sla_by_incident_type["incident_count"]
        .div(sla_by_incident_type["total_incidents"])
        .mul(100)
    )

    sla_by_incident_type["outcome_percentage_text"] = (
        sla_by_incident_type["outcome_percentage"]
        .map(lambda value: f"{value:.1f}%")
    )


    # Keep incident types in a stable order as the dataset grows
    preferred_incident_type_order = [
        "Explosion/fire",
        "Protest",
        "Shooting",
        "Standoff",
        "Weather",
        "Bomb threat",
        "Others",
        "Unknown incident type",
    ]

    present_incident_types = set(
        incident_type_totals["incident_type_display"]
    )

    incident_type_order = [
        incident_type
        for incident_type in preferred_incident_type_order
        if incident_type in present_incident_types
    ]

    # Preserve any unexpected categories instead of dropping them
    unexpected_incident_types = sorted(
        present_incident_types
        - set(preferred_incident_type_order)
    )

    incident_type_order.extend(unexpected_incident_types)


    # Give every visible category consistent vertical space
    number_of_categories = len(incident_type_order)
    space_per_category = 50
    minimum_chart_height = 120

    chart_height = max(
        minimum_chart_height,
        number_of_categories * space_per_category,
    )


    # Reserve horizontal space for the total labels
    maximum_total = int(
        incident_type_totals["total_incidents"].max()
    )

    x_axis_maximum = max(
        2,
        int(maximum_total * 1.15) + 1,
    )


    # Create horizontal stacked bars
    bars = (
        alt.Chart(sla_by_incident_type)
        .mark_bar(
            size=30,
            cornerRadiusEnd=4,
            opacity=0.92,
        )
        .encode(
            y=alt.Y(
                "incident_type_display:N",
                title=None,
                sort=incident_type_order,
                axis=alt.Axis(
                    labelLimit=260,
                    labelPadding=12,
                    labelFontSize=13,
                    domain=False,
                    tickSize=0,
                ),
            ),

            x=alt.X(
                "incident_count:Q",
                title="Number of incidents",
                stack="zero",
                scale=alt.Scale(
                    domain=[0, x_axis_maximum],
                    nice=True,
                ),
                axis=alt.Axis(
                    tickMinStep=1,
                    titlePadding=14,
                    domain=False,
                    tickSize=0,
                    grid=True,
                ),
            ),

            color=alt.Color(
                "first_note_status:N",
                title=None,
                scale=alt.Scale(
                    domain=status_order,
                    range=status_colours,
                ),
                legend=alt.Legend(
                    orient="top",
                    direction="horizontal",
                    symbolType="square",
                    symbolSize=120,
                    labelLimit=190,
                    offset=14,
                ),
            ),

            order=alt.Order(
                "status_rank:Q",
                sort="ascending",
            ),

            tooltip=[
                alt.Tooltip(
                    "incident_type_display:N",
                    title="Incident type",
                ),
                alt.Tooltip(
                    "first_note_status:N",
                    title="First-alert outcome",
                ),
                alt.Tooltip(
                    "incident_count:Q",
                    title="Incidents",
                    format=".0f",
                ),
                alt.Tooltip(
                    "outcome_percentage_text:N",
                    title="Share of category",
                ),
                alt.Tooltip(
                    "total_incidents:Q",
                    title="Category total",
                    format=".0f",
                ),
            ],
        )
    )


    # Add the total count at the end of each bar
    total_labels = (
        alt.Chart(incident_type_totals)
        .mark_text(
            dx=10,
            align="left",
            baseline="middle",
            fontSize=13,
            fontWeight=600,
            color="#2C2C2E",
        )
        .encode(
            y=alt.Y(
                "incident_type_display:N",
                sort=incident_type_order,
            ),

            x=alt.X(
                "total_incidents:Q",
                scale=alt.Scale(
                    domain=[0, x_axis_maximum],
                    nice=True,
                ),
            ),

            text=alt.Text(
                "total_incidents:Q",
                format=".0f",
            ),
        )
    )


    # Combine and style the chart
    sla_chart = (
        (bars + total_labels)
        .properties(
            height=chart_height,
        )
        .configure_view(
            stroke=None,
            fill="#FBFCFE",
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
            titleFontWeight=500,
        )
        .configure_legend(
            labelColor="#48484A",
            labelFont="Arial",
            labelFontSize=12,
            symbolStrokeWidth=0,
        )
    )


    st.altair_chart(
        sla_chart,
        width="stretch",
        theme=None,
    )


with st.expander("Review incidents classified as Others"):
    others_review = filtered_workflow.copy()

    others_review["incident_type_normalized"] = (
        others_review["incident_type"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    others_review = others_review.loc[
        others_review["incident_type_normalized"].eq("others")
    ].copy()

    review_column_candidates = [
        "incident_id",
        "event_label",
        "city",
        "state_province",
        "country",
        "incident_type",
    ]

    review_columns = [
        column
        for column in review_column_candidates
        if column in others_review.columns
    ]

    others_review = (
        others_review[review_columns]
        .drop_duplicates()
        .sort_values("incident_id")
    )

    if others_review.empty:
        st.info(
            "No incidents classified as Others match the selected filters."
        )
    else:
        st.caption(
            "Review these incidents to decide whether any should be "
            "reclassified into a more specific incident type."
        )

        st.dataframe(
            others_review,
            width="stretch",
            hide_index=True,
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
