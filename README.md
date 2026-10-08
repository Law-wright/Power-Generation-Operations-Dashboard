# Power Generation Operations Dashboard

A portfolio project that simulates daily operations for a fictional electric utility fleet. The goal is to practice working with operational data: generate a realistic extract, and later clean it, measure performance, and design a Power BI dashboard.

The project generates a raw extract, cleans it, calculates operational KPIs, and prepares a star schema plus a Power BI build guide. Building the report itself is the step that happens in Power BI Desktop on Windows.

## Purpose

Generation operations teams need a clear view of how assets are performing: how much energy they produced, whether that matched the plan, how often they were available, and where maintenance time and cost are concentrating.

This project builds a small, interview-ready version of that workflow using completely synthetic data.

## Business problem

Imagine an operations analyst supporting a mixed fleet of solar, wind, natural gas, and battery storage. Each morning they need to answer:

**How effectively are our generation assets performing, and where should operations teams focus their attention?**

The dataset is shaped so that question can be explored later in Power BI, without requiring Power BI to generate or inspect the data.

## Synthetic data disclaimer

Every plant, region, and operating value in this project is fictional. The generator does not use real utility operating data, and the numbers do not represent the performance of any company. They are teaching data with realistic seasonal and technology patterns.

## Technologies

- Python 3
- pandas
- NumPy
- matplotlib, in the exploratory notebook
- CSV files for a direct Power BI import later

Power BI is the planned visualization tool. It is not required to run this project.

## Architecture

An operations dashboard starts with a repeatable data pipeline, not with charts.

```text
Fictional plant catalog + 2025 calendar
        |
        v
Technology models (solar, wind, gas, battery)
        |
        v
data/raw/plant_operations_daily.csv
        |
        v
scripts/clean_data.py
        |
        v
data/processed/plant_operations_daily_clean.csv
        |
        v
scripts/analyze_data.py
        |
        v
KPI tables in data/processed/
        |
        v
scripts/build_star_schema.py
        |
        v
Fact and dimension CSVs in data/processed/model/
```

- **Raw data** is the extract as it arrives: one row per plant per day, stored in `data/raw/`. A few documented defects are included so cleaning has real work to do.
- **A fixed random seed** (`42`) makes the file reproducible. Rerunning the generator produces the same numbers.
- **Clean data** is the version analysis should use. Derived fields are recalculated, invalid values are repaired or quarantined, and a few calculated columns are added.
- **KPI tables** summarize the clean file at fleet, technology, facility, and month level. `scripts/analyze_data.py` rebuilds them from the clean CSV.
- **The star schema** splits that clean file into `FactOperations`, `DimPlant`, `DimDate`, and `DimGenerationType` so Power BI can filter plants, dates, and technologies without double-counting capacity. The build guide is [docs/dashboard_plan.md](docs/dashboard_plan.md).

### Project layout

```text
Power-Generation-Operations-Dashboard/
├── data/
│   ├── raw/                  # generated daily extract
│   └── processed/            # cleaned extract, KPI tables, star schema
├── scripts/
│   ├── generate_data.py      # synthetic data generator
│   ├── clean_data.py         # data quality checks and repairs
│   ├── analyze_data.py       # operational KPIs and observations
│   └── build_star_schema.py  # fact and dimension CSVs for Power BI
├── notebooks/
│   └── exploratory_analysis.ipynb
├── docs/
│   └── dashboard_plan.md     # Power BI page, model, and DAX
├── requirements.txt
└── README.md
```

## Dataset

The generator builds daily records for **1 January 2025 through 31 December 2025** for eight fictional facilities:

| Plant | Technology | Region | Capacity |
| --- | --- | --- | --- |
| Horizon Flats Solar | Solar | High Desert | 180 MW |
| Cedar Bend Solar | Solar | Coastal Plain | 95 MW |
| Gale Point Wind | Wind | Coastal Plain | 210 MW |
| Red Mesa Wind | Wind | High Desert | 140 MW |
| Riverbend Combined Cycle | Natural Gas | River Valley | 540 MW |
| Pinecrest Peakers | Natural Gas | North Ridge | 200 MW |
| Summit Storage | Battery Energy Storage | North Ridge | 75 MW |
| Harbor Storage | Battery Energy Storage | Coastal Plain | 50 MW |

Regions (High Desert, Coastal Plain, River Valley, North Ridge) are fictional operating areas.

Each row includes date, plant identity, technology, region, capacity, actual and expected generation, capacity factor, availability, planned and unplanned downtime, maintenance cost, maintenance events, and safety incidents.

Technology behavior in the generator:

- **Solar** follows a seasonal irradiance curve that peaks near the summer solstice, with a stronger resource in the High Desert than on the Coastal Plain, plus day-to-day cloud noise.
- **Wind** uses an autoregressive daily wind index so windy and calm periods cluster, and actual output wanders farther from the expected profile than gas does.
- **Natural gas** is dispatchable. Riverbend runs a fairly steady combined-cycle schedule. Pinecrest is a peaker. Each gas plant has one multi-day planned outage in a shoulder month.
- **Battery storage** is not fuel generation. `Actual_Generation_MWh` means energy discharged. Daily discharge is capped like a multi-hour storage asset, so the capacity factor looks low even when the battery is used as intended.

`Capacity_Factor` is actual generation divided by nameplate capacity times 24 hours. `Availability_Percent` is hours available that day divided by 24.

## Data cleaning

`scripts/clean_data.py` reads the raw CSV, prints a data-quality report, and writes `data/processed/plant_operations_daily_clean.csv`.

The checks run before any value is changed. On the current extract they find:

- 4 missing actual-generation values, and the same 4 missing capacity factors
- 2 duplicate plant-days
- 1 availability value above 100%
- 1 negative maintenance cost

What the script does with each problem:

- **Exact duplicates** drop the extra copy. The grain of this table is one row per plant per day, so two rows for the same plant and date would double-count generation and cost.
- **Conflicting duplicates**, if the two copies disagreed, would be quarantined. These two copies match, so one is kept.
- **Availability** is recalculated from planned and unplanned hours. Hours are the measurement. The percent is derived, which is why a stored value of 112.5% can be replaced with 100% when both downtime fields are zero.
- **Capacity factor** is recalculated from actual generation and nameplate capacity.
- **Negative maintenance cost** is treated as a sign error. Riverbend’s 10 April 2025 outage day keeps its cost magnitude ($86,783.29) and is flagged with `Maintenance_Cost_Corrected`.
- **Missing actual generation** is filled from that day’s expected generation and flagged with `Generation_Imputed`. The rest of those four plant-days is still usable. On an imputed day, generation variance is zero because actual was set equal to the plan. Filter `Generation_Imputed` off when a variance figure needs to ignore those fills.
- **Rows that still fail** a physical check are written to `data/processed/rejected_records.csv` and left out of the clean file. The current extract quarantines nothing.

The clean file also adds `Generation_Variance_MWh`, `Generation_Variance_Pct`, `Available_Hours`, `Total_Downtime_Hours`, `Year`, `Month`, and `Month_Name`. It has 2,920 rows: 8 plants × 365 days.

## KPIs

`scripts/analyze_data.py` reads the clean file and writes five summary tables:

- `data/processed/kpi_fleet.csv`
- `data/processed/kpi_by_technology.csv`
- `data/processed/kpi_by_facility.csv`
- `data/processed/kpi_monthly.csv`
- `data/processed/kpi_monthly_by_technology.csv`

The script also prints the figures and six observations. Headline results for 2025:

| KPI | Result |
| --- | --- |
| Total generation | 5,592,439 MWh, including 150,436 MWh of battery discharge |
| Installed capacity | 1,490 MW |
| Average available capacity | 1,452 MW |
| Average availability | 97.5%, weighted by capacity |
| Planned downtime | 804 hours |
| Unplanned downtime | 290 hours |
| Maintenance cost | $3,023,745 |
| Capacity factor | 42.8% |
| Generation variance | 10,668 MWh below plan, or 0.19% |

How those KPIs are defined:

- **Total generation** sums actual MWh. Battery rows are energy discharged, so the report also shows generation excluding storage.
- **Installed capacity** sums nameplate MW once per plant. Summing the daily column would count each plant 365 times.
- **Average available capacity** is the average, across days, of nameplate MW that was online. **Capacity-weighted availability** is that same result as a percent of the 1,490 MW fleet. A simple average of daily availability percents would give a 50 MW battery the same vote as a 540 MW plant.
- **Capacity factor** is total actual MWh divided by total nameplate MWh (capacity × 24 × days).
- **Unplanned capacity MWh**, used in the facility ranking, is nameplate MW times unplanned hours. It shows which outages remove the most capacity. It is an exposure figure: a solar plant would not have produced at full power for every one of those hours.

Observations worth investigating:

1. The fleet met its annual plan. The 0.19% shortfall is unchanged when the four imputed days are left out. The follow-up work is inside individual plants.
2. Capacity factor follows the job of the asset. Riverbend runs at 64.8%. Pinecrest, a peaker, runs at 21.0% and stays on its own plan. The batteries sit near 13.7% with availability above 99%. Horizon Flats outproduces Cedar Bend on capacity factor because the desert resource is stronger.
3. Riverbend’s 12-day April outage is the maintenance event of the year: about $1.03 million, and Riverbend is 62% of fleet maintenance spend. April is also the low month for fleet generation, and available capacity bottoms at 845 MW on 18 April. Pinecrest’s October outage costs about $258,000 and moves fleet energy less, because a peaker was not scheduled to run many of those hours.
4. Riverbend also leads unplanned downtime, with 92 hours and about 49,680 MWh of nameplate exposure. That exposure lines up with Riverbend’s 33,904 MWh shortfall versus plan, because the planned outage was already in the expected generation. Cedar Bend is second in unplanned hours (54) but 5th in exposure. Horizon Flats, the other solar plant, has 3 unplanned hours. March is the month with the most unplanned hours, led by Riverbend.
5. Wind is where actual generation wanders from the plan. A typical day at Red Mesa misses expected generation by about 27%, and Gale Point by about 18%. The gas plants miss by under 1% on a typical day. Red Mesa still finishes the year 7.2% above its expected total, so the annual beat hides a noisy forecast.
6. Three safety incidents are on record, two at Cedar Bend and one at Summit Storage. None fall on an unplanned-downtime day. The counts are small. Cedar Bend is the plant that combines those incidents with the solar fleet’s unplanned hours.

## Planned Power BI dashboard

The dashboard is specified in [docs/dashboard_plan.md](docs/dashboard_plan.md). It is a single page, **Fleet performance**, aimed at the same question as the KPI analysis.

Power BI Desktop is a Windows application. This repository prepares the import files and the build steps. It does not require Power BI to generate or check the data.

The page has five cards across the top: total generation, available capacity, average availability, unplanned downtime, and maintenance cost. Below them are generation by technology, actual versus expected by plant, a monthly trend with one line per technology, availability by facility, planned versus unplanned downtime, maintenance cost by facility, and a facility performance table. Slicers cover date, facility, generation type, and region.

The DAX measures repeat the Python definitions. Capacity factor and availability are ratios of sums, so a large plant keeps its weight when a slicer changes. The full-year cards should match the KPI table above: 5,592,439 MWh, 1,452 MW available, 97.5% availability, 290 unplanned hours, and $3,023,745 of maintenance cost.

## How to run

From the repository root in Cursor’s terminal on macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_data.py
python scripts/clean_data.py
python scripts/analyze_data.py
python scripts/build_star_schema.py
```

`generate_data.py` writes `data/raw/plant_operations_daily.csv` and prints row count, date span, plant count, columns, and mean capacity factor by technology.

`clean_data.py` writes `data/processed/plant_operations_daily_clean.csv` and prints the quality checks, the repairs it made, and the clean row count.

`analyze_data.py` prints the KPI report and writes the summary CSVs listed above.

`build_star_schema.py` writes `FactOperations.csv`, `DimPlant.csv`, `DimDate.csv`, and `DimGenerationType.csv` to `data/processed/model/`. Import those four files in Power BI and follow [docs/dashboard_plan.md](docs/dashboard_plan.md).

Run the scripts in this order. Each step reads the file produced by the step before it.

Open `notebooks/exploratory_analysis.ipynb` in Cursor and choose the `.venv` kernel. The notebook reads the clean CSV and charts monthly generation, capacity factor, variance versus plan, downtime, and maintenance cost. Run the cleaner before the notebook.

## Future improvements

- Build the report in Power BI Desktop on Windows and check the cards against `kpi_fleet.csv`.
- Add a second year so the date table can support year-over-year comparisons.
- Split battery charge and discharge into separate measures. This model records discharge only.
- Add an outage-cause field so unplanned hours can be grouped by equipment rather than by plant alone.
- Move from daily energy to hourly profiles when the question is about solar shape within the day or battery state of charge.
