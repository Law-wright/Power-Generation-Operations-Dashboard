"""Split the clean extract into a star schema Power BI can import.

The clean file is one wide table: plant attributes repeat on every daily row.
A star schema stores those attributes once, in dimension tables, and keeps the
daily measurements in a fact table. Power BI filters flow from the dimensions
into the fact.

Grain of FactOperations: one row per plant per day.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CLEAN_PATH = ROOT / "data" / "processed" / "plant_operations_daily_clean.csv"
MODEL_DIR = ROOT / "data" / "processed" / "model"

GENERATION_TYPE_ORDER = [
    "Natural Gas",
    "Wind",
    "Solar",
    "Battery Energy Storage",
]


def load_clean(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["Date"])
    for column in ["Generation_Imputed", "Maintenance_Cost_Corrected"]:
        frame[column] = frame[column].map(lambda value: str(value).strip().lower() == "true")
    return frame


def build_fact(frame: pd.DataFrame) -> pd.DataFrame:
    """Daily measurements only. Ratios such as capacity factor are left for DAX."""
    fact = frame[
        [
            "Date",
            "Plant_ID",
            "Generation_Type",
            "Actual_Generation_MWh",
            "Expected_Generation_MWh",
            "Available_Hours",
            "Planned_Downtime_Hours",
            "Unplanned_Downtime_Hours",
            "Maintenance_Cost",
            "Maintenance_Events",
            "Safety_Incidents",
            "Generation_Imputed",
            "Maintenance_Cost_Corrected",
        ]
    ].copy()
    fact["Date"] = fact["Date"].dt.strftime("%Y-%m-%d")
    fact["Generation_Imputed"] = fact["Generation_Imputed"].astype(int)
    fact["Maintenance_Cost_Corrected"] = fact["Maintenance_Cost_Corrected"].astype(int)
    return fact.sort_values(["Date", "Plant_ID"]).reset_index(drop=True)


def build_dim_plant(frame: pd.DataFrame) -> pd.DataFrame:
    plants = frame.drop_duplicates("Plant_ID")[
        ["Plant_ID", "Plant_Name", "Region", "Capacity_MW", "Generation_Type"]
    ]
    return plants.sort_values("Plant_ID").reset_index(drop=True)


def build_dim_generation_type() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Generation_Type": GENERATION_TYPE_ORDER,
            "Sort_Order": range(1, len(GENERATION_TYPE_ORDER) + 1),
        }
    )


def build_dim_date() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", "2025-12-31", freq="D")
    calendar = pd.DataFrame({"Date": dates})
    calendar["Year"] = calendar["Date"].dt.year
    calendar["Quarter"] = calendar["Date"].dt.quarter.map(lambda quarter: f"Q{quarter}")
    calendar["Month"] = calendar["Date"].dt.month
    calendar["Month_Name"] = calendar["Date"].dt.strftime("%B")
    calendar["Year_Month"] = calendar["Date"].dt.strftime("%Y-%m")
    calendar["Month_Year_Label"] = calendar["Date"].dt.strftime("%b %Y")
    calendar["Weekday_Number"] = calendar["Date"].dt.dayofweek + 1
    calendar["Day_Name"] = calendar["Date"].dt.day_name()
    calendar["Date"] = calendar["Date"].dt.strftime("%Y-%m-%d")
    return calendar


def main() -> None:
    frame = load_clean(CLEAN_PATH)
    tables = {
        "FactOperations.csv": build_fact(frame),
        "DimPlant.csv": build_dim_plant(frame),
        "DimDate.csv": build_dim_date(),
        "DimGenerationType.csv": build_dim_generation_type(),
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Source: {CLEAN_PATH}")
    for name, table in tables.items():
        path = MODEL_DIR / name
        table.to_csv(path, index=False)
        print(f"Wrote {path} ({len(table):,} rows, {len(table.columns)} columns)")


if __name__ == "__main__":
    main()
