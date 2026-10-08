"""Calculate operational KPIs from the clean daily extract.

The question these figures answer is: how effectively did the fleet perform
in 2025, and where should operations look next?

Definitions used here:

- Total generation is the sum of Actual_Generation_MWh. For battery rows
  that field is energy discharged, so the report also shows generation
  excluding storage.
- Installed capacity is the sum of nameplate MW across plants. Capacity is
  repeated on every daily row, so it is taken once per plant.
- Average available capacity is the average, across days, of nameplate MW
  that was online. Capacity-weighted availability is that same result
  expressed as a percent of installed capacity.
- Capacity factor is total actual MWh divided by total nameplate MWh
  (capacity x 24 x days). A simple average of daily factors would give a
  50 MW battery the same weight as a 540 MW plant.
- Unplanned capacity MWh is nameplate MW times unplanned hours. It measures
  how much full-power energy those hours represented. Solar, wind, and
  peaker plants would not have produced at full power the whole time, so
  the figure is an exposure ranking, not a metered loss.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CLEAN_PATH = ROOT / "data" / "processed" / "plant_operations_daily_clean.csv"
OUTPUT_DIR = ROOT / "data" / "processed"

PLANT_KEYS = ["Plant_ID", "Plant_Name", "Generation_Type", "Region"]
MONTH_KEYS = ["Month", "Month_Name"]


def load_clean(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["Date"])
    frame["Generation_Imputed"] = frame["Generation_Imputed"].map(lambda value: str(value).strip().lower() == "true")
    frame["Maintenance_Cost_Corrected"] = frame["Maintenance_Cost_Corrected"].map(
        lambda value: str(value).strip().lower() == "true"
    )
    frame["Available_MW"] = frame["Capacity_MW"] * frame["Availability_Percent"] / 100
    frame["Nameplate_MWh"] = frame["Capacity_MW"] * 24
    frame["Unplanned_Capacity_MWh"] = frame["Capacity_MW"] * frame["Unplanned_Downtime_Hours"]
    return frame


def _metrics(frame: pd.DataFrame) -> dict[str, float]:
    actual = float(frame["Actual_Generation_MWh"].sum())
    expected = float(frame["Expected_Generation_MWh"].sum())
    nameplate_mwh = float(frame["Nameplate_MWh"].sum())
    capacity_mw = float(frame["Capacity_MW"].sum())
    return {
        "Actual_Generation_MWh": actual,
        "Expected_Generation_MWh": expected,
        "Generation_Variance_MWh": actual - expected,
        "Generation_Variance_Pct": (actual - expected) / expected * 100 if expected else 0.0,
        "Capacity_Factor": actual / nameplate_mwh * 100 if nameplate_mwh else 0.0,
        "Availability_Percent": float(frame["Available_MW"].sum() / capacity_mw * 100) if capacity_mw else 0.0,
        "Planned_Downtime_Hours": float(frame["Planned_Downtime_Hours"].sum()),
        "Unplanned_Downtime_Hours": float(frame["Unplanned_Downtime_Hours"].sum()),
        "Unplanned_Capacity_MWh": float(frame["Unplanned_Capacity_MWh"].sum()),
        "Maintenance_Cost": float(frame["Maintenance_Cost"].sum()),
        "Maintenance_Events": float(frame["Maintenance_Events"].sum()),
        "Safety_Incidents": float(frame["Safety_Incidents"].sum()),
    }


def fleet_summary(frame: pd.DataFrame) -> dict[str, float]:
    plants = frame.drop_duplicates(PLANT_KEYS)
    installed_mw = float(plants["Capacity_MW"].sum())
    daily_available = frame.groupby("Date")["Available_MW"].sum()
    storage = frame["Generation_Type"].eq("Battery Energy Storage")
    measured = frame.loc[~frame["Generation_Imputed"]]
    measured_actual = float(measured["Actual_Generation_MWh"].sum())
    measured_expected = float(measured["Expected_Generation_MWh"].sum())
    summary = _metrics(frame)
    summary.update(
        {
            "Installed_Capacity_MW": installed_mw,
            "Average_Available_Capacity_MW": float(daily_available.mean()),
            "Minimum_Available_Capacity_MW": float(daily_available.min()),
            "Minimum_Available_Capacity_Date": daily_available.idxmin().strftime("%Y-%m-%d"),
            "Battery_Discharge_MWh": float(frame.loc[storage, "Actual_Generation_MWh"].sum()),
            "Generation_Excluding_Storage_MWh": float(frame.loc[~storage, "Actual_Generation_MWh"].sum()),
            "Measured_Generation_Variance_Pct": (measured_actual - measured_expected) / measured_expected * 100,
            "Imputed_Days": float(frame["Generation_Imputed"].sum()),
            "Plant_Count": float(plants["Plant_ID"].nunique()),
            "Day_Count": float(frame["Date"].nunique()),
        }
    )
    return summary


def grouped_summary(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(keys, sort=False):
        key_values = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, key_values))
        row.update(_metrics(group))
        if "Capacity_MW" not in keys:
            row["Capacity_MW"] = float(group.drop_duplicates("Plant_ID")["Capacity_MW"].sum())
        rows.append(row)
    summary = pd.DataFrame(rows)
    return summary.sort_values("Actual_Generation_MWh", ascending=False).reset_index(drop=True)


def monthly_by_technology(frame: pd.DataFrame) -> pd.DataFrame:
    summary = grouped_summary(frame, MONTH_KEYS + ["Generation_Type"])
    return summary.sort_values(["Month", "Actual_Generation_MWh"], ascending=[True, False]).reset_index(drop=True)


def build_observations(
    frame: pd.DataFrame,
    fleet: dict[str, float],
    facilities: pd.DataFrame,
    technologies: pd.DataFrame,
    monthly: pd.DataFrame,
) -> list[str]:
    """Turn the tables into a short list of things operations would investigate."""
    facilities = facilities.set_index("Plant_ID")
    riverbend = facilities.loc["GAS-01"]
    pinecrest = facilities.loc["GAS-02"]
    horizon = facilities.loc["SOL-01"]
    cedar = facilities.loc["SOL-02"]
    red_mesa = facilities.loc["WND-02"]
    gas_cost_share = technologies.set_index("Generation_Type").loc["Natural Gas", "Maintenance_Cost"] / fleet["Maintenance_Cost"] * 100
    riverbend_outage = frame[(frame["Plant_ID"] == "GAS-01") & (frame["Planned_Downtime_Hours"] == 24)]
    pinecrest_outage = frame[(frame["Plant_ID"] == "GAS-02") & (frame["Planned_Downtime_Hours"] == 24)]
    april = monthly.loc[monthly["Month"] == 4].iloc[0]
    peak_month = monthly.loc[monthly["Actual_Generation_MWh"].idxmax()]

    normal_riverbend = frame[(frame["Plant_ID"] == "GAS-01") & (frame["Unplanned_Downtime_Hours"] == 0)]
    riverbend_online_mw = normal_riverbend["Actual_Generation_MWh"].sum() / normal_riverbend["Available_Hours"].sum()
    riverbend_unplanned_energy = riverbend_online_mw * riverbend["Unplanned_Downtime_Hours"]

    unplanned_hours = facilities.sort_values("Unplanned_Downtime_Hours", ascending=False)
    unplanned_exposure = facilities.sort_values("Unplanned_Capacity_MWh", ascending=False)
    cedar_exposure_rank = int(
        unplanned_exposure["Unplanned_Capacity_MWh"].rank(ascending=False, method="min").loc["SOL-02"]
    )
    busy_month = monthly.sort_values("Unplanned_Downtime_Hours", ascending=False).iloc[0]
    busy_month_plants = (
        frame.loc[frame["Month"] == busy_month["Month"]]
        .groupby("Plant_Name")["Unplanned_Downtime_Hours"]
        .sum()
        .sort_values(ascending=False)
    )
    incidents = frame.loc[frame["Safety_Incidents"] > 0]
    incident_plants = incidents.groupby("Plant_Name")["Safety_Incidents"].sum().sort_values(ascending=False)

    measured = frame.loc[(~frame["Generation_Imputed"]) & (frame["Expected_Generation_MWh"] > 0)].copy()
    measured["Absolute_Variance_Pct"] = (
        (measured["Actual_Generation_MWh"] - measured["Expected_Generation_MWh"]).abs()
        / measured["Expected_Generation_MWh"]
        * 100
    )
    fleet_direction = "below" if fleet["Generation_Variance_Pct"] < 0 else "above"
    measured_direction = "below" if fleet["Measured_Generation_Variance_Pct"] < 0 else "above"
    median_miss = measured.groupby("Plant_Name")["Absolute_Variance_Pct"].median()

    return [
        (
            f"The fleet finished {abs(fleet['Generation_Variance_Pct']):.2f}% {fleet_direction} its expected generation "
            f"({fleet['Generation_Variance_MWh']:,.0f} MWh). Leaving out the {int(fleet['Imputed_Days'])} imputed days "
            f"leaves the gap at {abs(fleet['Measured_Generation_Variance_Pct']):.2f}% {measured_direction}. The annual plan was met. "
            f"The useful questions are inside the fleet: which plants missed, and whether the miss was weather, "
            f"a forecast, or equipment."
        ),
        (
            f"Capacity factor has to be read against the job of the asset. Riverbend, the combined-cycle unit, "
            f"delivered a {riverbend['Capacity_Factor']:.1f}% factor. Pinecrest, the peaker, delivered "
            f"{pinecrest['Capacity_Factor']:.1f}% and finished {abs(pinecrest['Generation_Variance_Pct']):.1f}% from its own plan. "
            f"The battery sites sit near {facilities.loc['BAT-01', 'Capacity_Factor']:.1f}% because they discharge for a few hours, "
            f"while availability there is {facilities.loc['BAT-01', 'Availability_Percent']:.1f}% and "
            f"{facilities.loc['BAT-02', 'Availability_Percent']:.1f}%. Horizon Flats produced a {horizon['Capacity_Factor']:.1f}% "
            f"solar factor and Cedar Bend produced {cedar['Capacity_Factor']:.1f}%, which fits the stronger desert resource "
            f"at Horizon Flats."
        ),
        (
            f"Riverbend's planned outage is the maintenance and energy event of the year. "
            f"{len(riverbend_outage)} full-outage days cost ${riverbend_outage['Maintenance_Cost'].sum():,.0f}, "
            f"and Riverbend alone is {riverbend['Maintenance_Cost'] / fleet['Maintenance_Cost'] * 100:.0f}% of fleet maintenance spend. "
            f"Natural gas as a whole is {gas_cost_share:.0f}%. April, the outage month, is the low month for fleet generation "
            f"at {april['Actual_Generation_MWh']:,.0f} MWh, against {peak_month['Month_Name']}'s "
            f"{peak_month['Actual_Generation_MWh']:,.0f} MWh. Available capacity bottomed at "
            f"{fleet['Minimum_Available_Capacity_MW']:,.0f} MW on {fleet['Minimum_Available_Capacity_Date']}, "
            f"inside that outage. Pinecrest's {len(pinecrest_outage)}-day outage cost "
            f"${pinecrest_outage['Maintenance_Cost'].sum():,.0f} and moves fleet energy much less, because a peaker "
            f"was not going to run many of those hours. Review the Riverbend outage scope, duration, and cost first."
        ),
        (
            f"Unplanned hours and unplanned exposure tell different stories. "
            f"{unplanned_hours.iloc[0]['Plant_Name']} leads both rankings: "
            f"{riverbend['Unplanned_Downtime_Hours']:.0f} unplanned hours and "
            f"{riverbend['Unplanned_Capacity_MWh']:,.0f} MWh of nameplate exposure. "
            f"Cedar Bend is second in hours ({cedar['Unplanned_Downtime_Hours']:.0f}) and "
            f"{cedar_exposure_rank}th in nameplate exposure "
            f"({cedar['Unplanned_Capacity_MWh']:,.0f} MWh). "
            f"Horizon Flats, the other solar plant, had only {horizon['Unplanned_Downtime_Hours']:.0f} unplanned hours. "
            f"Riverbend's annual shortfall of {abs(riverbend['Generation_Variance_MWh']):,.0f} MWh lines up with its "
            f"unplanned hours: on days without a forced outage it averaged {riverbend_online_mw:.0f} MW, and "
            f"{riverbend['Unplanned_Downtime_Hours']:.0f} hours at that rate is about {riverbend_unplanned_energy:,.0f} MWh. "
            f"The planned outage was already in the expected generation, so the gap versus plan is the forced outage, "
            f"not day-to-day dispatch. {busy_month['Month_Name']} had the most unplanned hours "
            f"({busy_month['Unplanned_Downtime_Hours']:.0f}), led by {busy_month_plants.index[0]} "
            f"({busy_month_plants.iloc[0]:.0f} hours)."
        ),
        (
            f"Wind is where actual generation wanders from the plan. Red Mesa's median daily miss is "
            f"{median_miss['Red Mesa Wind']:.0f}% of expected, and Gale Point's is {median_miss['Gale Point Wind']:.0f}%. "
            f"Riverbend and Pinecrest are under 1% on a typical day. Red Mesa still finished the year "
            f"{red_mesa['Generation_Variance_Pct']:.1f}% above its expected generation, so the annual total hides a forecast "
            f"that was noisy and biased low. That is a forecasting and resource question. Cedar Bend's solar miss "
            f"is {median_miss['Cedar Bend Solar']:.0f}% on a typical day, against {median_miss['Horizon Flats Solar']:.0f}% at Horizon Flats."
        ),
        (
            f"The year recorded {int(fleet['Safety_Incidents'])} safety incidents: "
            + ", ".join(f"{plant} ({int(count)})" for plant, count in incident_plants.items())
            + ". None of those days had unplanned downtime. The counts are too small to call a trend, "
            f"and Cedar Bend is the plant that combines two incidents with the solar fleet's unplanned hours."
        ),
    ]


def _round_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    rounded = frame.copy()
    for column in rounded.columns:
        if column.endswith("_Pct") or column in {"Capacity_Factor", "Availability_Percent"}:
            rounded[column] = rounded[column].round(2)
        elif column.endswith("_MWh") or column.endswith("_Hours") or column.endswith("_Cost"):
            rounded[column] = rounded[column].round(1)
        elif column in {"Maintenance_Events", "Safety_Incidents", "Capacity_MW"}:
            rounded[column] = rounded[column].round(0).astype(int)
    return rounded


def write_tables(
    fleet: dict[str, float],
    technologies: pd.DataFrame,
    facilities: pd.DataFrame,
    monthly: pd.DataFrame,
    monthly_technology: pd.DataFrame,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fleet_rows = pd.DataFrame(
        [
            ("Total_Generation_MWh", fleet["Actual_Generation_MWh"], "MWh", "Sum of actual generation, including battery discharge"),
            ("Generation_Excluding_Storage_MWh", fleet["Generation_Excluding_Storage_MWh"], "MWh", "Actual generation from solar, wind, and natural gas"),
            ("Battery_Discharge_MWh", fleet["Battery_Discharge_MWh"], "MWh", "Energy discharged by battery facilities"),
            ("Installed_Capacity_MW", fleet["Installed_Capacity_MW"], "MW", "Sum of nameplate capacity, counted once per plant"),
            ("Average_Available_Capacity_MW", fleet["Average_Available_Capacity_MW"], "MW", "Average online nameplate capacity across days"),
            ("Average_Availability_Percent", fleet["Availability_Percent"], "Percent", "Capacity-weighted availability"),
            ("Total_Planned_Downtime_Hours", fleet["Planned_Downtime_Hours"], "Hours", "Sum of planned downtime hours"),
            ("Total_Unplanned_Downtime_Hours", fleet["Unplanned_Downtime_Hours"], "Hours", "Sum of unplanned downtime hours"),
            ("Total_Maintenance_Cost", fleet["Maintenance_Cost"], "USD", "Sum of maintenance cost"),
            ("Average_Capacity_Factor_Percent", fleet["Capacity_Factor"], "Percent", "Total actual MWh divided by total nameplate MWh"),
            ("Generation_Variance_MWh", fleet["Generation_Variance_MWh"], "MWh", "Actual generation minus expected generation"),
            ("Generation_Variance_Pct", fleet["Generation_Variance_Pct"], "Percent", "Variance divided by expected generation"),
        ],
        columns=["Metric", "Value", "Unit", "Definition"],
    )
    fleet_rows.to_csv(OUTPUT_DIR / "kpi_fleet.csv", index=False)
    _round_metrics(technologies).to_csv(OUTPUT_DIR / "kpi_by_technology.csv", index=False)
    _round_metrics(facilities).to_csv(OUTPUT_DIR / "kpi_by_facility.csv", index=False)
    _round_metrics(monthly).to_csv(OUTPUT_DIR / "kpi_monthly.csv", index=False)
    _round_metrics(monthly_technology).to_csv(OUTPUT_DIR / "kpi_monthly_by_technology.csv", index=False)


def _print_table(title: str, frame: pd.DataFrame, columns: list[str]) -> None:
    print()
    print(title)
    display = frame[columns].copy()
    for column in display.columns:
        if pd.api.types.is_numeric_dtype(display[column]):
            display[column] = display[column].map(lambda value: f"{value:,.1f}")
    print(display.to_string(index=False))


def print_report(
    fleet: dict[str, float],
    technologies: pd.DataFrame,
    facilities: pd.DataFrame,
    monthly: pd.DataFrame,
    monthly_technology: pd.DataFrame,
    observations: list[str],
) -> None:
    print(f"Source: {CLEAN_PATH}")
    print(f"Plants: {int(fleet['Plant_Count'])}    Days: {int(fleet['Day_Count'])}")
    print()
    print("Fleet KPIs")
    print(f"  Total generation: {fleet['Actual_Generation_MWh']:,.0f} MWh")
    print(f"    Generation excluding storage: {fleet['Generation_Excluding_Storage_MWh']:,.0f} MWh")
    print(f"    Battery discharge: {fleet['Battery_Discharge_MWh']:,.0f} MWh")
    print(f"  Installed capacity: {fleet['Installed_Capacity_MW']:,.0f} MW")
    print(f"  Average available capacity: {fleet['Average_Available_Capacity_MW']:,.0f} MW")
    print(f"  Average availability (capacity-weighted): {fleet['Availability_Percent']:.1f}%")
    print(f"  Total planned downtime: {fleet['Planned_Downtime_Hours']:,.0f} hours")
    print(f"  Total unplanned downtime: {fleet['Unplanned_Downtime_Hours']:,.0f} hours")
    print(f"  Total maintenance cost: ${fleet['Maintenance_Cost']:,.0f}")
    print(f"  Average capacity factor: {fleet['Capacity_Factor']:.1f}%")
    print(
        f"  Generation variance: {fleet['Generation_Variance_MWh']:,.0f} MWh "
        f"({fleet['Generation_Variance_Pct']:.2f}%)"
    )

    tech_columns = [
        "Generation_Type",
        "Capacity_MW",
        "Actual_Generation_MWh",
        "Capacity_Factor",
        "Availability_Percent",
        "Generation_Variance_Pct",
        "Planned_Downtime_Hours",
        "Unplanned_Downtime_Hours",
        "Maintenance_Cost",
    ]
    facility_columns = [
        "Plant_Name",
        "Generation_Type",
        "Capacity_MW",
        "Actual_Generation_MWh",
        "Capacity_Factor",
        "Availability_Percent",
        "Generation_Variance_Pct",
    ]
    downtime_columns = [
        "Plant_Name",
        "Planned_Downtime_Hours",
        "Unplanned_Downtime_Hours",
        "Unplanned_Capacity_MWh",
        "Maintenance_Cost",
        "Safety_Incidents",
    ]
    month_columns = [
        "Month_Name",
        "Actual_Generation_MWh",
        "Expected_Generation_MWh",
        "Generation_Variance_MWh",
        "Planned_Downtime_Hours",
        "Unplanned_Downtime_Hours",
        "Maintenance_Cost",
    ]
    _print_table("Generation by technology", technologies, tech_columns)
    _print_table("Generation by facility", facilities, facility_columns)
    _print_table(
        "Downtime and maintenance cost by facility",
        facilities.sort_values("Unplanned_Downtime_Hours", ascending=False),
        downtime_columns,
    )
    _print_table("Monthly generation trend", monthly.sort_values("Month"), month_columns)
    trend = monthly_technology.pivot(index="Month_Name", columns="Generation_Type", values="Actual_Generation_MWh")
    trend = trend.reindex(monthly.sort_values("Month")["Month_Name"])
    print()
    print("Monthly generation by technology (MWh)")
    print(trend.map(lambda value: f"{value:,.0f}").to_string())

    print()
    print("Observations")
    for number, observation in enumerate(observations, start=1):
        print()
        print(f"{number}. {observation}")

    print()
    print("Wrote:")
    for name in [
        "kpi_fleet.csv",
        "kpi_by_technology.csv",
        "kpi_by_facility.csv",
        "kpi_monthly.csv",
        "kpi_monthly_by_technology.csv",
    ]:
        print(f"  {OUTPUT_DIR / name}")


def main() -> None:
    frame = load_clean(CLEAN_PATH)
    fleet = fleet_summary(frame)
    technologies = grouped_summary(frame, ["Generation_Type"])
    facilities = grouped_summary(frame, PLANT_KEYS)
    facilities.insert(4, "Capacity_MW", facilities.pop("Capacity_MW"))
    monthly = grouped_summary(frame, MONTH_KEYS).sort_values("Month").reset_index(drop=True)
    monthly_technology = monthly_by_technology(frame)
    observations = build_observations(frame, fleet, facilities, technologies, monthly)
    write_tables(fleet, technologies, facilities, monthly, monthly_technology)
    print_report(fleet, technologies, facilities, monthly, monthly_technology, observations)


if __name__ == "__main__":
    main()
