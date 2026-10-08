"""Clean the raw daily operations extract.

Raw data is what arrived. This script checks it, repairs records that have a
clear interpretation, quarantines records that do not, and writes a dataset
later analysis can trust.

The cleaner uses rules, not a list of known bad dates. Rerunning it after the
generator changes will still apply the same checks.

Repair rules:

- Exact duplicate plant-days (same Plant_ID and Date) keep one copy.
- Conflicting duplicates, where the same plant-day disagrees with itself,
  are quarantined.
- A negative maintenance cost is treated as a sign error. The magnitude is
  kept and the row is flagged.
- Availability is recalculated from downtime hours. Hours are the source
  measurement; the raw percent is a derived field and can be wrong.
- Missing actual generation is filled from that day's expected generation
  and flagged. A plant-day with no actual and no expected generation is
  quarantined, because there is no basis for the energy value.
- Capacity factor is recalculated from actual generation and nameplate
  capacity so it cannot drift from the formula.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "data" / "raw" / "plant_operations_daily.csv"
CLEAN_PATH = ROOT / "data" / "processed" / "plant_operations_daily_clean.csv"
REJECTED_PATH = ROOT / "data" / "processed" / "rejected_records.csv"

KEY_COLUMNS = ["Plant_ID", "Date"]
GENERATION_TYPES = ("Solar", "Wind", "Natural Gas", "Battery Energy Storage")

NUMERIC_COLUMNS = [
    "Capacity_MW",
    "Actual_Generation_MWh",
    "Expected_Generation_MWh",
    "Capacity_Factor",
    "Availability_Percent",
    "Planned_Downtime_Hours",
    "Unplanned_Downtime_Hours",
    "Maintenance_Cost",
    "Maintenance_Events",
    "Safety_Incidents",
]

CLEAN_COLUMNS = [
    "Date",
    "Plant_ID",
    "Plant_Name",
    "Generation_Type",
    "Region",
    "Capacity_MW",
    "Actual_Generation_MWh",
    "Expected_Generation_MWh",
    "Generation_Variance_MWh",
    "Generation_Variance_Pct",
    "Capacity_Factor",
    "Availability_Percent",
    "Available_Hours",
    "Planned_Downtime_Hours",
    "Unplanned_Downtime_Hours",
    "Total_Downtime_Hours",
    "Maintenance_Cost",
    "Maintenance_Events",
    "Safety_Incidents",
    "Generation_Imputed",
    "Maintenance_Cost_Corrected",
    "Year",
    "Month",
    "Month_Name",
]


def load_raw(path: Path) -> pd.DataFrame:
    """Read the extract and coerce types. Invalid dates and numbers become null."""
    frame = pd.read_csv(path)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def print_raw_profile(frame: pd.DataFrame) -> None:
    """Report data quality before any value is changed."""
    print(f"Loaded {RAW_PATH}")
    print(f"Rows: {len(frame):,}")
    print(f"Columns: {len(frame.columns)}")
    print()
    print("Missing values:")
    missing = frame.isna().sum()
    missing = missing[missing > 0]
    if missing.empty:
        print("  none")
    else:
        for column, count in missing.items():
            print(f"  {column}: {int(count)}")

    duplicate_rows = int(frame.duplicated(KEY_COLUMNS).sum())
    print()
    print(f"Duplicate plant-days (Plant_ID + Date): {duplicate_rows}")

    print()
    print("Range checks:")
    checks = {
        "Availability_Percent outside 0-100": _outside(frame["Availability_Percent"], 0, 100),
        "Capacity_Factor outside 0-1": _outside(frame["Capacity_Factor"], 0, 1),
        "Maintenance_Cost below 0": frame["Maintenance_Cost"].lt(0),
        "Actual_Generation_MWh below 0": frame["Actual_Generation_MWh"].lt(0),
        "Expected_Generation_MWh below 0": frame["Expected_Generation_MWh"].lt(0),
        "Capacity_MW at or below 0": frame["Capacity_MW"].le(0),
        "Planned downtime outside 0-24": _outside(frame["Planned_Downtime_Hours"], 0, 24),
        "Unplanned downtime outside 0-24": _outside(frame["Unplanned_Downtime_Hours"], 0, 24),
        "Downtime hours sum above 24": frame["Planned_Downtime_Hours"].add(frame["Unplanned_Downtime_Hours"]).gt(24),
        "Maintenance_Events below 0": frame["Maintenance_Events"].lt(0),
        "Safety_Incidents below 0": frame["Safety_Incidents"].lt(0),
    }
    for label, mask in checks.items():
        print(f"  {label}: {int(mask.fillna(False).sum())}")


def _outside(series: pd.Series, low: float, high: float) -> pd.Series:
    return series.lt(low) | series.gt(high)


def split_duplicates(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Keep one copy of exact duplicates. Quarantine plant-days that disagree."""
    conflicting_indexes: list[int] = []
    duplicate_keys = frame.duplicated(KEY_COLUMNS, keep=False)
    grouped = frame.loc[duplicate_keys].groupby(KEY_COLUMNS, dropna=False)
    value_columns = [column for column in frame.columns if column not in KEY_COLUMNS]
    for _, group in grouped:
        if group[value_columns].nunique(dropna=False).max() > 1:
            conflicting_indexes.extend(group.index.tolist())

    conflicting = frame.loc[conflicting_indexes].copy()
    if not conflicting.empty:
        conflicting["Rejection_Reason"] = "Conflicting duplicate plant-day"

    remaining = frame.drop(index=conflicting_indexes)
    exact_duplicate_mask = remaining.duplicated(KEY_COLUMNS, keep="first")
    deduped = remaining.loc[~exact_duplicate_mask].copy()
    stats = {
        "exact_duplicates_removed": int(exact_duplicate_mask.sum()),
        "conflicting_plant_days_quarantined": int(len(conflicting)),
    }
    return deduped, conflicting, stats


def repair_records(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply repairs that have a single defensible interpretation."""
    work = frame.copy()
    negative_cost = work["Maintenance_Cost"].lt(0).fillna(False)
    work["Maintenance_Cost_Corrected"] = negative_cost
    work.loc[negative_cost, "Maintenance_Cost"] = work.loc[negative_cost, "Maintenance_Cost"].abs()

    downtime = work["Planned_Downtime_Hours"].add(work["Unplanned_Downtime_Hours"])
    downtime_is_usable = (
        work["Planned_Downtime_Hours"].between(0, 24)
        & work["Unplanned_Downtime_Hours"].between(0, 24)
        & downtime.le(24)
    )
    recalculated_availability = (24 - downtime) / 24 * 100
    availability_changed = downtime_is_usable & (
        work["Availability_Percent"].sub(recalculated_availability).abs().gt(0.15)
        | work["Availability_Percent"].isna()
    )
    work.loc[downtime_is_usable, "Availability_Percent"] = recalculated_availability.round(1)

    missing_actual = work["Actual_Generation_MWh"].isna()
    can_impute = missing_actual & work["Expected_Generation_MWh"].notna() & work["Expected_Generation_MWh"].ge(0)
    work["Generation_Imputed"] = False
    work.loc[can_impute, "Actual_Generation_MWh"] = work.loc[can_impute, "Expected_Generation_MWh"]
    work.loc[can_impute, "Generation_Imputed"] = True

    can_recalculate_factor = work["Actual_Generation_MWh"].notna() & work["Capacity_MW"].gt(0)
    work.loc[can_recalculate_factor, "Capacity_Factor"] = (
        work.loc[can_recalculate_factor, "Actual_Generation_MWh"]
        / (work.loc[can_recalculate_factor, "Capacity_MW"] * 24)
    ).round(4)

    stats = {
        "negative_costs_corrected": int(negative_cost.sum()),
        "availability_values_replaced": int(availability_changed.sum()),
        "generation_values_imputed": int(can_impute.sum()),
    }
    return work, stats


def rejection_reasons(frame: pd.DataFrame) -> pd.Series:
    """Build a semicolon-separated reason for every row that is still unsafe to keep."""
    collected: list[list[str]] = [[] for _ in range(len(frame))]

    def add(mask: pd.Series, reason: str) -> None:
        for position in np.flatnonzero(mask.fillna(False).to_numpy()):
            collected[position].append(reason)

    add(frame["Date"].isna(), "Unparseable date")
    add(frame["Plant_ID"].isna() | frame["Plant_ID"].astype(str).str.strip().eq(""), "Missing Plant_ID")
    add(frame["Plant_Name"].isna() | frame["Plant_Name"].astype(str).str.strip().eq(""), "Missing Plant_Name")
    add(frame["Region"].isna() | frame["Region"].astype(str).str.strip().eq(""), "Missing Region")
    add(~frame["Generation_Type"].isin(GENERATION_TYPES), "Unknown Generation_Type")
    add(frame["Capacity_MW"].isna() | frame["Capacity_MW"].le(0), "Capacity_MW missing or not positive")
    add(
        frame["Planned_Downtime_Hours"].isna()
        | frame["Unplanned_Downtime_Hours"].isna()
        | ~frame["Planned_Downtime_Hours"].between(0, 24)
        | ~frame["Unplanned_Downtime_Hours"].between(0, 24)
        | frame["Planned_Downtime_Hours"].add(frame["Unplanned_Downtime_Hours"]).gt(24),
        "Downtime hours invalid",
    )
    add(
        frame["Expected_Generation_MWh"].isna() | frame["Expected_Generation_MWh"].lt(0),
        "Expected generation missing or negative",
    )
    add(
        frame["Actual_Generation_MWh"].isna() | frame["Actual_Generation_MWh"].lt(0),
        "Actual generation missing or negative",
    )
    add(
        frame["Actual_Generation_MWh"].gt(frame["Capacity_MW"] * 24 + 0.05),
        "Actual generation above nameplate energy for the day",
    )
    add(
        frame["Maintenance_Cost"].isna() | frame["Maintenance_Cost"].lt(0),
        "Maintenance cost missing or negative",
    )
    add(
        frame["Maintenance_Events"].isna() | frame["Maintenance_Events"].lt(0),
        "Maintenance events missing or negative",
    )
    add(
        frame["Safety_Incidents"].isna() | frame["Safety_Incidents"].lt(0),
        "Safety incidents missing or negative",
    )
    return pd.Series(["; ".join(items) for items in collected], index=frame.index)


def add_calculated_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add fields the raw extract does not store, all derived from cleaned inputs."""
    work = frame.copy()
    work["Generation_Variance_MWh"] = (work["Actual_Generation_MWh"] - work["Expected_Generation_MWh"]).round(1)
    matched_zero_plan = work["Expected_Generation_MWh"].eq(0) & work["Actual_Generation_MWh"].eq(0)
    variance_pct = np.where(
        work["Expected_Generation_MWh"].gt(0),
        work["Generation_Variance_MWh"] / work["Expected_Generation_MWh"] * 100,
        np.where(matched_zero_plan, 0.0, np.nan),
    )
    work["Generation_Variance_Pct"] = pd.Series(variance_pct, index=work.index).round(1)
    work["Total_Downtime_Hours"] = work["Planned_Downtime_Hours"] + work["Unplanned_Downtime_Hours"]
    work["Available_Hours"] = 24 - work["Total_Downtime_Hours"]
    work["Year"] = work["Date"].dt.year
    work["Month"] = work["Date"].dt.month
    work["Month_Name"] = work["Date"].dt.strftime("%B")
    return work


def format_for_export(frame: pd.DataFrame) -> pd.DataFrame:
    """Stable column order and types for a Power BI CSV import."""
    export = frame.sort_values(KEY_COLUMNS).reset_index(drop=True)
    export["Date"] = export["Date"].dt.strftime("%Y-%m-%d")
    export["Capacity_MW"] = export["Capacity_MW"].round().astype(int)
    for column in [
        "Planned_Downtime_Hours",
        "Unplanned_Downtime_Hours",
        "Total_Downtime_Hours",
        "Available_Hours",
        "Maintenance_Events",
        "Safety_Incidents",
        "Year",
        "Month",
    ]:
        export[column] = export[column].round().astype(int)
    export["Maintenance_Cost"] = export["Maintenance_Cost"].round(2)
    export["Actual_Generation_MWh"] = export["Actual_Generation_MWh"].round(1)
    export["Expected_Generation_MWh"] = export["Expected_Generation_MWh"].round(1)
    export["Generation_Imputed"] = export["Generation_Imputed"].astype(bool)
    export["Maintenance_Cost_Corrected"] = export["Maintenance_Cost_Corrected"].astype(bool)
    return export[CLEAN_COLUMNS]


def assert_clean(frame: pd.DataFrame) -> None:
    """Refuse to publish a file that still fails the rules analysis will rely on."""
    problems: list[str] = []
    if frame.duplicated(KEY_COLUMNS).any():
        problems.append("duplicate plant-days remain")
    if frame["Actual_Generation_MWh"].isna().any() or frame["Actual_Generation_MWh"].lt(0).any():
        problems.append("actual generation is missing or negative")
    if frame["Maintenance_Cost"].lt(0).any():
        problems.append("maintenance cost is negative")
    if _outside(frame["Availability_Percent"], 0, 100).any():
        problems.append("availability is outside 0-100")
    if _outside(frame["Capacity_Factor"], 0, 1).any():
        problems.append("capacity factor is outside 0-1")
    if frame["Planned_Downtime_Hours"].add(frame["Unplanned_Downtime_Hours"]).gt(24).any():
        problems.append("downtime hours exceed 24")
    expected_factor = (frame["Actual_Generation_MWh"] / (frame["Capacity_MW"] * 24)).round(4)
    if not np.allclose(frame["Capacity_Factor"], expected_factor, atol=0.0001):
        problems.append("capacity factor does not match actual generation")
    if problems:
        raise SystemExit("Clean file failed validation: " + "; ".join(problems))


def write_outputs(clean: pd.DataFrame, rejected: pd.DataFrame) -> None:
    CLEAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(CLEAN_PATH, index=False)
    if rejected.empty:
        REJECTED_PATH.unlink(missing_ok=True)
        return
    rejected = rejected.copy()
    rejected["Date"] = rejected["Date"].dt.strftime("%Y-%m-%d")
    rejected.to_csv(REJECTED_PATH, index=False)


def print_actions(raw_rows: int, stats: dict[str, int], clean: pd.DataFrame, rejected: pd.DataFrame) -> None:
    print()
    print("Cleaning actions:")
    print(f"  Exact duplicate rows removed: {stats['exact_duplicates_removed']}")
    print(f"  Conflicting plant-days quarantined: {stats['conflicting_plant_days_quarantined']}")
    print(f"  Negative maintenance costs corrected: {stats['negative_costs_corrected']}")
    print(f"  Availability values replaced from downtime hours: {stats['availability_values_replaced']}")
    print(f"  Missing actual generation imputed from expected: {stats['generation_values_imputed']}")
    print(f"  Other invalid rows quarantined: {len(rejected) - stats['conflicting_plant_days_quarantined']}")
    print()
    print("Calculated columns:")
    print("  Generation_Variance_MWh, Generation_Variance_Pct")
    print("  Available_Hours, Total_Downtime_Hours")
    print("  Year, Month, Month_Name")
    print("  Generation_Imputed, Maintenance_Cost_Corrected")
    print()
    print(f"Wrote {CLEAN_PATH}")
    print(f"Clean rows: {len(clean):,} (started with {raw_rows:,})")
    print(f"Plants: {clean['Plant_ID'].nunique()}")
    print(f"Dates: {clean['Date'].min()} through {clean['Date'].max()}")
    print(f"Missing values in the clean file: {int(clean.isna().sum().sum())}")
    if rejected.empty:
        print("Quarantined rows: 0")
    else:
        print(f"Quarantined rows: {len(rejected):,} -> {REJECTED_PATH}")


def main() -> None:
    raw = load_raw(RAW_PATH)
    print_raw_profile(raw)
    deduped, conflicting, duplicate_stats = split_duplicates(raw)
    repaired, repair_stats = repair_records(deduped)
    reasons = rejection_reasons(repaired)
    invalid = repaired.loc[reasons.ne("")].copy()
    invalid["Rejection_Reason"] = reasons.loc[reasons.ne("")]
    kept = repaired.loc[reasons.eq("")].copy()
    clean = format_for_export(add_calculated_columns(kept))
    rejected = pd.concat([conflicting, invalid], ignore_index=True)
    assert_clean(clean)
    write_outputs(clean, rejected)
    print_actions(len(raw), {**duplicate_stats, **repair_stats}, clean, rejected)


if __name__ == "__main__":
    main()
