"""Generate a synthetic daily operations extract for a fictional power fleet.

Every plant, region, and value is invented. This script does not use real
utility operating data, and the output does not represent any company's
performance.

The file is one row per plant per day for calendar year 2025. A fixed NumPy
seed (42) makes the extract reproducible: rerunning the script rewrites the
same CSV.

Raw-file defects are intentional, so a later cleaning step has real work to
do. They are applied after the physical model and are listed in
``inject_raw_defects``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
YEAR = 2025
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "raw" / "plant_operations_daily.csv"

# Nameplate duration used only for battery discharge math. The published
# schema stores power capacity (MW), not energy capacity (MWh).
BATTERY_DURATION_HOURS = 4

COLUMNS = [
    "Date",
    "Plant_ID",
    "Plant_Name",
    "Generation_Type",
    "Region",
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

# Fictional fleet. Two plants of each technology so later charts can compare
# sites that share a technology but not a resource or a duty cycle.
PLANTS = [
    {
        "plant_id": "SOL-01",
        "plant_name": "Horizon Flats Solar",
        "generation_type": "Solar",
        "region": "High Desert",
        "capacity_mw": 180,
        "solar_peak": 0.42,
        "cloud_alpha": 16.0,
        "cloud_beta": 1.8,
    },
    {
        "plant_id": "SOL-02",
        "plant_name": "Cedar Bend Solar",
        "generation_type": "Solar",
        "region": "Coastal Plain",
        "capacity_mw": 95,
        "solar_peak": 0.40,
        "cloud_alpha": 5.5,
        "cloud_beta": 1.55,
    },
    {
        "plant_id": "WND-01",
        "plant_name": "Gale Point Wind",
        "generation_type": "Wind",
        "region": "Coastal Plain",
        "capacity_mw": 210,
        "wind_mean_ms": 8.25,
        "wind_sigma_ms": 1.5,
        "wind_phi": 0.72,
    },
    {
        "plant_id": "WND-02",
        "plant_name": "Red Mesa Wind",
        "generation_type": "Wind",
        "region": "High Desert",
        "capacity_mw": 140,
        "wind_mean_ms": 7.4,
        "wind_sigma_ms": 2.2,
        "wind_phi": 0.58,
    },
    {
        "plant_id": "GAS-01",
        "plant_name": "Riverbend Combined Cycle",
        "generation_type": "Natural Gas",
        "region": "River Valley",
        "capacity_mw": 540,
        "gas_role": "combined_cycle",
        "outage_start": "2025-04-07",
        "outage_days": 12,
    },
    {
        "plant_id": "GAS-02",
        "plant_name": "Pinecrest Peakers",
        "generation_type": "Natural Gas",
        "region": "North Ridge",
        "capacity_mw": 200,
        "gas_role": "peaker",
        "outage_start": "2025-10-06",
        "outage_days": 8,
    },
    {
        "plant_id": "BAT-01",
        "plant_name": "Summit Storage",
        "generation_type": "Battery Energy Storage",
        "region": "North Ridge",
        "capacity_mw": 75,
    },
    {
        "plant_id": "BAT-02",
        "plant_name": "Harbor Storage",
        "generation_type": "Battery Energy Storage",
        "region": "Coastal Plain",
        "capacity_mw": 50,
    },
]

# Routine work and forced-outage rates. Gas major outages are scheduled
# separately as a consecutive block in a shoulder month.
ROUTINE = {
    "Solar": {"planned_days": 6, "planned_hours": (5, 10), "unplanned_p": 0.012, "unplanned_hours": (3, 12)},
    "Wind": {"planned_days": 7, "planned_hours": (6, 12), "unplanned_p": 0.022, "unplanned_hours": (4, 18)},
    "Natural Gas": {"planned_days": 3, "planned_hours": (6, 14), "unplanned_p": 0.010, "unplanned_hours": (6, 18)},
    "Battery Energy Storage": {"planned_days": 4, "planned_hours": (4, 8), "unplanned_p": 0.004, "unplanned_hours": (2, 8)},
}

# Dollars per downtime hour when a crew is on site, before the size factor.
COST_PER_HOUR = {
    "Solar": 900.0,
    "Wind": 1600.0,
    "Natural Gas": 2800.0,
    "Battery Energy Storage": 700.0,
}


def solar_season(day_of_year: np.ndarray) -> np.ndarray:
    """Seasonal daylight-energy index, 1.0 near the summer solstice.

    Daily solar energy is modeled for the whole day, not hour by hour. The
    low capacity factor already includes the hours after sunset: a clear
    summer day still uses only part of the 24-hour denominator.
    """
    angle = 2 * np.pi * (day_of_year - 172) / 365.25
    daylight = 0.5 + 0.5 * np.cos(angle)
    return 0.45 + 0.55 * daylight


def wind_season(day_of_year: np.ndarray) -> np.ndarray:
    """Modest winter/spring increase in mean wind speed."""
    angle = 2 * np.pi * (day_of_year - 20) / 365.25
    return 1.0 + 0.10 * np.cos(angle)


def wind_power_pu(wind_speed_ms: np.ndarray) -> np.ndarray:
    """Approximate a modern turbine curve as a fraction of rated power.

    Cut-in is 3 m/s, rated output is reached near 12 m/s, and output falls
    to zero above a 25 m/s cut-out. The exponent is chosen so a day near
    8 m/s lands around 45% of rated power.
    """
    speed = np.asarray(wind_speed_ms, dtype=float)
    spanned = np.clip((speed - 3.0) / 9.0, 0.0, 1.0) ** 1.35
    powered = np.where(speed < 3.0, 0.0, spanned)
    return np.where(speed > 25.0, 0.0, powered)


def ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    """Unit-variance autoregressive series. Nearby days stay correlated."""
    shocks = rng.normal(0.0, 1.0, n)
    series = np.zeros(n)
    series[0] = shocks[0]
    carry = np.sqrt(1.0 - phi**2)
    for t in range(1, n):
        series[t] = phi * series[t - 1] + carry * shocks[t]
    return series


def days_from(day_of_year: np.ndarray, center: float) -> np.ndarray:
    """Shortest distance on the calendar, so late December meets early January."""
    delta = np.abs(day_of_year - center)
    return np.minimum(delta, 365.25 - delta)


def storage_shape(day_of_year: np.ndarray) -> np.ndarray:
    """Higher battery discharge in summer and winter peak seasons."""
    summer = np.exp(-0.5 * (days_from(day_of_year, 205) / 50) ** 2)
    winter = np.exp(-0.5 * (days_from(day_of_year, 15) / 42) ** 2)
    return 0.45 + 0.85 * summer + 0.65 * winter


def choose_days(n: int, k: int, blocked: set[int], rng: np.random.Generator) -> np.ndarray:
    open_days = np.array([i for i in range(n) if i not in blocked])
    return rng.choice(open_days, size=k, replace=False)


def build_downtime(plant: dict, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Planned hours, then rare unplanned hours, never totaling more than 24."""
    planned = np.zeros(n, dtype=int)
    blocked: set[int] = set()

    if plant["generation_type"] == "Natural Gas":
        start = (pd.Timestamp(plant["outage_start"]) - pd.Timestamp(f"{YEAR}-01-01")).days
        for offset in range(plant["outage_days"]):
            planned[start + offset] = 24
            blocked.add(start + offset)

    routine = ROUTINE[plant["generation_type"]]
    partial_days = choose_days(n, routine["planned_days"], blocked, rng)
    low, high = routine["planned_hours"]
    planned[partial_days] = rng.integers(low, high + 1, size=routine["planned_days"])

    unplanned = np.zeros(n, dtype=int)
    hits = (rng.random(n) < routine["unplanned_p"]) & (planned < 24)
    low, high = routine["unplanned_hours"]
    drawn = rng.integers(low, high + 1, size=n)
    unplanned[hits] = drawn[hits]
    unplanned = np.minimum(unplanned, 24 - planned)
    return planned, unplanned


def solar_generation(
    plant: dict,
    day_of_year: np.ndarray,
    planned: np.ndarray,
    available: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Seasonal expectation, with cloud noise on the actual day.

    Expected generation is the seasonal plan and already excludes planned
    downtime. Actual generation also loses unplanned hours and varies with
    that day's cloud cover. High Desert draws a tighter, clearer cloud
    distribution than Coastal Plain.
    """
    season = solar_season(day_of_year)
    mean_cloud = plant["cloud_alpha"] / (plant["cloud_alpha"] + plant["cloud_beta"])
    expected_resource = plant["solar_peak"] * season * mean_cloud
    clouds = np.clip(rng.beta(plant["cloud_alpha"], plant["cloud_beta"], size=len(day_of_year)), 0.15, 1.0)
    actual_resource = np.clip(plant["solar_peak"] * season * clouds, 0.0, 1.0)

    capacity = plant["capacity_mw"]
    expected = capacity * (24 - planned) * expected_resource
    actual = capacity * available * actual_resource
    return actual, expected


def wind_generation(
    plant: dict,
    day_of_year: np.ndarray,
    planned: np.ndarray,
    available: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Clustered wind days, with a seasonal forecast that only partly tracks them.

    The autoregressive index keeps windy and calm stretches together. Expected
    generation blends that realized wind with the seasonal mean, so the miss
    versus actual is wider than it is for dispatchable gas.
    """
    seasonal_mean = plant["wind_mean_ms"] * wind_season(day_of_year)
    realized = np.clip(seasonal_mean + plant["wind_sigma_ms"] * ar1(len(day_of_year), plant["wind_phi"], rng), 0.0, 32.0)
    forecast = np.clip(0.35 * realized + 0.65 * seasonal_mean + rng.normal(0.0, 0.35, len(realized)), 0.0, 32.0)

    capacity = plant["capacity_mw"]
    expected = capacity * (24 - planned) * wind_power_pu(forecast)
    actual = capacity * available * wind_power_pu(realized)
    return actual, expected


def gas_generation(
    plant: dict,
    dates: pd.DatetimeIndex,
    planned: np.ndarray,
    available: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Dispatchable output. Actual stays close to the schedule.

    Riverbend is a combined-cycle unit with a steady utilization. Pinecrest
    is a peaker: low most days, and high when a summer or winter peak is drawn.
    Unplanned hours cut the schedule. Planned hours are already out of it.
    """
    n = len(dates)
    day_of_year = dates.dayofyear.to_numpy()
    if plant["gas_role"] == "combined_cycle":
        utilization = np.clip(rng.normal(0.68, 0.035, n), 0.48, 0.92)
    else:
        base = np.clip(rng.normal(0.10, 0.025, n), 0.02, 0.22)
        heat = np.exp(-0.5 * ((day_of_year - 200) / 28) ** 2)
        cold = np.exp(-0.5 * ((day_of_year - 15) / 24) ** 2)
        peak_draw = np.maximum(heat, cold)
        is_peak = rng.random(n) < (0.06 + 0.40 * peak_draw)
        utilization = np.where(is_peak, rng.uniform(0.55, 0.95, n), base)

    capacity = plant["capacity_mw"]
    expected = capacity * (24 - planned) * utilization
    scheduled_hours = np.maximum(24 - planned, 1)
    online_ratio = np.where(planned >= 24, 0.0, available / scheduled_hours)
    actual = expected * online_ratio * rng.normal(1.0, 0.012, n)
    return np.clip(actual, 0.0, None), expected


def battery_generation(
    plant: dict,
    dates: pd.DatetimeIndex,
    planned: np.ndarray,
    available: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Energy discharged, not fuel burned.

    A four-hour battery can move about ``capacity_mw * 4`` MWh in one cycle.
    Discharge rises on weekdays and in the summer and winter peaks, and is
    capped near one and a half cycles. Capacity factor still divides by a
    full 24 hours, so it stays low even on a busy day.
    """
    day_of_year = dates.dayofyear.to_numpy()
    weekend = np.where(dates.dayofweek >= 5, 0.62, 1.0)
    cycles_expected = np.clip(storage_shape(day_of_year) * weekend, 0.12, 1.25)
    cycles_actual = np.clip(cycles_expected * rng.normal(1.0, 0.06, len(dates)), 0.05, 1.50)

    capacity = plant["capacity_mw"]
    expected = capacity * BATTERY_DURATION_HOURS * cycles_expected * ((24 - planned) / 24)
    actual = capacity * BATTERY_DURATION_HOURS * cycles_actual * (available / 24)
    actual = np.minimum(actual, capacity * available)
    expected = np.minimum(expected, capacity * (24 - planned))
    return actual, expected


def maintenance_and_safety(
    plant: dict,
    planned: np.ndarray,
    unplanned: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cost and event counts follow downtime. Quiet days stay at zero.

    A major gas outage (24 planned hours) is the expensive block. Unplanned
    hours add a crew and a higher rate. Safety incidents stay rare, with a
    slightly higher chance on forced-outage days.
    """
    n = len(planned)
    events = np.zeros(n, dtype=int)
    cost = np.zeros(n, dtype=float)
    rate = COST_PER_HOUR[plant["generation_type"]]
    size = 0.65 + 0.35 * (plant["capacity_mw"] / 200.0)

    partial = (planned > 0) & (planned < 24)
    events[partial] = 1
    cost[partial] = planned[partial] * rate * size * rng.uniform(0.85, 1.15, n)[partial]

    major = planned == 24
    if major.any():
        events[major] = rng.integers(2, 5, size=int(major.sum()))
        cost[major] = plant["capacity_mw"] * rng.uniform(130, 190, size=int(major.sum()))

    forced = unplanned > 0
    if forced.any():
        events[forced] += 1 + (unplanned[forced] >= 12).astype(int)
        cost[forced] += unplanned[forced] * rate * size * 1.8

    incident_p = np.where(unplanned > 0, 0.045, 0.0012)
    incidents = (rng.random(n) < incident_p).astype(int)
    return events, np.round(cost, 2), incidents


def generate_plant(plant: dict, dates: pd.DatetimeIndex, rng: np.random.Generator) -> pd.DataFrame:
    n = len(dates)
    planned, unplanned = build_downtime(plant, n, rng)
    available = 24 - planned - unplanned
    tech = plant["generation_type"]

    if tech == "Solar":
        actual, expected = solar_generation(plant, dates.dayofyear.to_numpy(), planned, available, rng)
    elif tech == "Wind":
        actual, expected = wind_generation(plant, dates.dayofyear.to_numpy(), planned, available, rng)
    elif tech == "Natural Gas":
        actual, expected = gas_generation(plant, dates, planned, available, rng)
    else:
        actual, expected = battery_generation(plant, dates, planned, available, rng)

    actual = np.clip(actual, 0.0, plant["capacity_mw"] * available)
    expected = np.clip(expected, 0.0, None)
    events, cost, incidents = maintenance_and_safety(plant, planned, unplanned, rng)
    capacity_factor = actual / (plant["capacity_mw"] * 24)
    availability = available / 24 * 100

    return pd.DataFrame(
        {
            "Date": dates,
            "Plant_ID": plant["plant_id"],
            "Plant_Name": plant["plant_name"],
            "Generation_Type": tech,
            "Region": plant["region"],
            "Capacity_MW": plant["capacity_mw"],
            "Actual_Generation_MWh": np.round(actual, 1),
            "Expected_Generation_MWh": np.round(expected, 1),
            "Capacity_Factor": np.round(capacity_factor, 4),
            "Availability_Percent": np.round(availability, 1),
            "Planned_Downtime_Hours": planned,
            "Unplanned_Downtime_Hours": unplanned,
            "Maintenance_Cost": cost,
            "Maintenance_Events": events,
            "Safety_Incidents": incidents,
        }
    )


def inject_raw_defects(frame: pd.DataFrame) -> pd.DataFrame:
    """Spoil a few cells on purpose.

    - Four plant-days are missing actual generation and the capacity factor
      derived from it.
    - Two plant-days are duplicated exactly.
    - One outage day has a negative maintenance cost.
    - One healthy day reports availability above 100%.
    """
    raw = frame.copy()

    missing = [
        ("SOL-01", "2025-03-12"),
        ("WND-02", "2025-07-19"),
        ("GAS-02", "2025-01-22"),
        ("BAT-01", "2025-11-02"),
    ]
    for plant_id, date in missing:
        mask = (raw["Plant_ID"] == plant_id) & (raw["Date"] == pd.Timestamp(date))
        raw.loc[mask, ["Actual_Generation_MWh", "Capacity_Factor"]] = np.nan

    negative = (raw["Plant_ID"] == "GAS-01") & (raw["Date"] == pd.Timestamp("2025-04-10"))
    raw.loc[negative, "Maintenance_Cost"] = -abs(raw.loc[negative, "Maintenance_Cost"])

    too_high = (raw["Plant_ID"] == "SOL-01") & (raw["Date"] == pd.Timestamp("2025-06-15"))
    raw.loc[too_high, "Availability_Percent"] = 112.5

    duplicates = [
        ("SOL-02", "2025-05-05"),
        ("WND-01", "2025-09-14"),
    ]
    copies = []
    for plant_id, date in duplicates:
        mask = (raw["Plant_ID"] == plant_id) & (raw["Date"] == pd.Timestamp(date))
        copies.append(raw.loc[mask])
    raw = pd.concat([raw, *copies], ignore_index=True)
    return raw


def build_dataset(rng: np.random.Generator) -> pd.DataFrame:
    dates = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D")
    frames = [generate_plant(plant, dates, rng) for plant in PLANTS]
    dataset = pd.concat(frames, ignore_index=True)
    dataset = inject_raw_defects(dataset)
    dataset["Date"] = dataset["Date"].dt.strftime("%Y-%m-%d")
    return dataset[COLUMNS]


def print_summary(dataset: pd.DataFrame, path: Path) -> None:
    unique_days = dataset.drop_duplicates(["Plant_ID", "Date"])
    print(f"Wrote {path}")
    print(f"Rows: {len(dataset):,} (unique plant-days: {len(unique_days):,})")
    print(f"Dates: {dataset['Date'].min()} through {dataset['Date'].max()}")
    print(f"Plants: {dataset['Plant_ID'].nunique()}")
    print(f"Columns ({len(dataset.columns)}): {', '.join(dataset.columns)}")
    print()
    print("Mean capacity factor by technology (nulls skipped):")
    means = (
        dataset.dropna(subset=["Capacity_Factor"])
        .groupby("Generation_Type")["Capacity_Factor"]
        .mean()
        .mul(100)
        .round(1)
    )
    for tech, value in means.items():
        print(f"  {tech}: {value:.1f}%")
    print()
    print(
        "Intentional raw defects: "
        f"{int(dataset['Actual_Generation_MWh'].isna().sum())} missing generation values, "
        f"{len(dataset) - len(unique_days)} duplicate rows, "
        f"{int((dataset['Maintenance_Cost'] < 0).sum())} negative maintenance cost, "
        f"{int((dataset['Availability_Percent'] > 100).sum())} availability above 100%."
    )


def main() -> None:
    dataset = build_dataset(np.random.default_rng(SEED))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(OUTPUT_PATH, index=False)
    print_summary(dataset, OUTPUT_PATH)


if __name__ == "__main__":
    main()
