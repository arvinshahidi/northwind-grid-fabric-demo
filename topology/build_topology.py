"""
STEP A - Northwind Grid topology + master data generator.

Deterministic (SEED=42) generation of:
  - 3 regions (Coastal, Metro, Inland) inside a Pacific Northwest bounding box
  - 12 substations (4 per region)
  - 36 feeders (3 per substation)
  - 40 generation units (18 wind / 8 solar / 6 gas peaker / 4 hydro / 2 BESS / 2 biomass)
  - 2,400 smart meters (200 per substation) with mixed residential/commercial/industrial
  - 8 field crews

Writes:
  data/seed/topology.json
  data/seed/asset_master.csv / .parquet
  data/seed/customer_master.csv / .parquet
  data/seed/substations.csv, feeders.csv, generators.csv, meters.csv, crews.csv
"""
from __future__ import annotations

import csv
import json
import os
import random
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

SEED = 42
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")

# Pacific Northwest coastal->inland bounding box (WA/OR style slice)
LAT_MIN, LAT_MAX = 45.2, 48.8
LON_MIN, LON_MAX = -124.6, -121.0

REGIONS = [
    {"region_id": "REG-COASTAL", "name": "Coastal", "lon_span": (LON_MIN, -123.4)},
    {"region_id": "REG-METRO", "name": "Metro", "lon_span": (-123.4, -122.2)},
    {"region_id": "REG-INLAND", "name": "Inland", "lon_span": (-122.2, LON_MAX)},
]

RATE_CLASSES = ["RES", "RES", "RES", "RES", "COM", "COM", "IND"]  # weighted mix
FEEDER_VOLTAGE_KV = 13.8
SUBSTATION_VOLTAGE_KV = 115.0

rng = random.Random(SEED)


def _rand_latlon(lon_span):
    lat = round(rng.uniform(LAT_MIN, LAT_MAX), 5)
    lon = round(rng.uniform(*lon_span), 5)
    return lat, lon


@dataclass
class Substation:
    substation_id: str
    name: str
    region_id: str
    lat: float
    lon: float
    voltage_kv: float = SUBSTATION_VOLTAGE_KV


@dataclass
class Feeder:
    feeder_id: str
    name: str
    substation_id: str
    region_id: str
    capacity_mva: float
    voltage_kv: float = FEEDER_VOLTAGE_KV
    voltage_band_pu: tuple = (0.95, 1.05)
    mix: dict = field(default_factory=dict)  # customer class diversity mix for this feeder


@dataclass
class Generator:
    asset_id: str
    asset_type: str  # wind_turbine | solar_farm | gas_peaker | hydro | bess | biomass
    farm_or_plant_id: str
    name: str
    nameplate_mw: float
    voltage_kv: float
    region_id: str
    substation_id: str
    feeder_id: str
    lat: float
    lon: float
    install_year: int
    last_maintenance: str
    next_maintenance: str
    status: str
    oem: str


@dataclass
class Meter:
    meter_id: str
    feeder_id: str
    substation_id: str
    region_id: str
    rate_class: str
    critical_facility: bool
    medical_baseline: bool


@dataclass
class Customer:
    customer_id: str
    meter_id: str
    address: str
    postal_code: str
    lat: float
    lon: float
    rate_class: str
    medical_baseline: bool
    critical_facility: bool
    feeder_id: str
    billed_kwh_last_month: float
    account_status: str


@dataclass
class Crew:
    crew_id: str
    name: str
    home_substation_id: str
    region_id: str
    status: str  # available | dispatched | on_break


OEMS = ["Vestas", "GE Renewables", "Siemens Gamesa", "First Solar", "Mitsubishi Power",
        "Andritz Hydro", "Wartsila", "Tesla Energy"]


def build_regions():
    return REGIONS


def build_substations():
    subs = []
    for region in REGIONS:
        for i in range(4):
            idx = len(subs) + 1
            lat, lon = _rand_latlon(region["lon_span"])
            subs.append(Substation(
                substation_id=f"SUB-{idx:02d}",
                name=f"{region['name']} Substation {i + 1}",
                region_id=region["region_id"],
                lat=lat, lon=lon,
            ))
    return subs


def build_feeders(substations):
    feeders = []
    for sub in substations:
        for i in range(3):
            idx = len(feeders) + 1
            # feeder capacity mix so 3 feeders per substation sum to plausible substation capacity
            capacity = round(rng.uniform(18, 34), 1)
            mix = {
                "residential_pct": round(rng.uniform(0.4, 0.8), 2),
                "commercial_pct": 0.0,
                "industrial_pct": 0.0,
            }
            remaining = 1 - mix["residential_pct"]
            mix["commercial_pct"] = round(remaining * rng.uniform(0.4, 0.8), 2)
            mix["industrial_pct"] = round(1 - mix["residential_pct"] - mix["commercial_pct"], 2)
            feeders.append(Feeder(
                feeder_id=f"FDR-{idx:03d}",
                name=f"{sub.name} Feeder {i + 1}",
                substation_id=sub.substation_id,
                region_id=sub.region_id,
                capacity_mva=capacity,
                mix=mix,
            ))
    return feeders


def _pick_feeder(feeders, substation_id):
    candidates = [f for f in feeders if f.substation_id == substation_id]
    return rng.choice(candidates).feeder_id


def build_generators(substations, feeders):
    gens = []
    coastal_subs = [s for s in substations if s.region_id == "REG-COASTAL"]
    metro_subs = [s for s in substations if s.region_id == "REG-METRO"]
    inland_subs = [s for s in substations if s.region_id == "REG-INLAND"]
    all_subs = substations

    def add(asset_type, count, home_subs, rating_range, farm_size=1, name_prefix="", oem_pool=None):
        made = 0
        farm_idx = 0
        while made < count:
            farm_idx += 1
            this_farm = min(farm_size, count - made)
            sub = rng.choice(home_subs)
            farm_id = f"{name_prefix}-FARM-{farm_idx:02d}" if farm_size > 1 else None
            base_lat, base_lon = _rand_latlon(
                next(r["lon_span"] for r in REGIONS if r["region_id"] == sub.region_id)
            )
            for k in range(this_farm):
                idx = len(gens) + 1
                rating = round(rng.uniform(*rating_range), 2)
                install_year = rng.randint(2005, 2023)
                gens.append(Generator(
                    asset_id=f"GEN-{idx:03d}",
                    asset_type=asset_type,
                    farm_or_plant_id=farm_id or f"{name_prefix}-{idx:03d}",
                    name=f"{name_prefix} Unit {idx}",
                    nameplate_mw=rating,
                    voltage_kv=34.5 if asset_type in ("wind_turbine", "solar_farm") else 115.0,
                    region_id=sub.region_id,
                    substation_id=sub.substation_id,
                    feeder_id=_pick_feeder(feeders, sub.substation_id),
                    lat=round(base_lat + rng.uniform(-0.05, 0.05), 5),
                    lon=round(base_lon + rng.uniform(-0.05, 0.05), 5),
                    install_year=install_year,
                    last_maintenance=f"{install_year + rng.randint(1, 5)}-0{rng.randint(1,9)}-15",
                    next_maintenance="2025-11-01",
                    status="running",
                    oem=rng.choice(oem_pool or OEMS),
                ))
            made += this_farm
        return gens

    add("wind_turbine", 18, coastal_subs, (2.5, 4.5), farm_size=6, name_prefix="WIND", oem_pool=["Vestas", "GE Renewables", "Siemens Gamesa"])
    add("solar_farm", 8, all_subs, (15, 25), farm_size=1, name_prefix="SOLAR", oem_pool=["First Solar", "GE Renewables"])
    add("gas_peaker", 6, metro_subs, (80, 120), farm_size=1, name_prefix="GASPK", oem_pool=["GE Renewables", "Siemens Gamesa"])
    add("hydro", 4, inland_subs, (45, 75), farm_size=1, name_prefix="HYDRO", oem_pool=["Andritz Hydro"])
    add("bess", 2, [coastal_subs[0], metro_subs[0]], (40, 60), farm_size=1, name_prefix="BESS", oem_pool=["Tesla Energy", "Wartsila"])
    add("biomass", 2, inland_subs, (12, 18), farm_size=1, name_prefix="BIOM", oem_pool=["Wartsila"])
    return gens


def build_meters_and_customers(substations, feeders):
    meters, customers = [], []
    m_idx = 0
    for sub in substations:
        sub_feeders = [f for f in feeders if f.substation_id == sub.substation_id]
        # split 200 meters across 3 feeders unevenly but deterministically
        splits = [70, 65, 65]
        rng.shuffle(splits)
        for feeder, count in zip(sub_feeders, splits):
            for _ in range(count):
                m_idx += 1
                meter_id = f"MTR-{m_idx:05d}"
                rate_class = rng.choice(RATE_CLASSES)
                critical = rng.random() < 0.015  # ~1.5% critical facilities (hospitals, water plants)
                medical = (not critical) and rng.random() < 0.02
                meters.append(Meter(
                    meter_id=meter_id, feeder_id=feeder.feeder_id,
                    substation_id=sub.substation_id, region_id=sub.region_id,
                    rate_class=rate_class, critical_facility=critical, medical_baseline=medical,
                ))
                lat, lon = _rand_latlon(next(r["lon_span"] for r in REGIONS if r["region_id"] == sub.region_id))
                billed = {"RES": rng.uniform(400, 1200), "COM": rng.uniform(2000, 9000),
                          "IND": rng.uniform(15000, 60000)}[rate_class]
                customers.append(Customer(
                    customer_id=f"CUST-{m_idx:05d}", meter_id=meter_id,
                    address=f"{rng.randint(100, 9999)} {rng.choice(['Cedar','Pine','Harbor','Summit','River','Alder'])} "
                            f"{rng.choice(['St','Ave','Way','Rd'])}",
                    postal_code=f"9{rng.randint(8000, 8999)}",
                    lat=lat, lon=lon, rate_class=rate_class,
                    medical_baseline=medical, critical_facility=critical,
                    feeder_id=feeder.feeder_id, billed_kwh_last_month=round(billed, 1),
                    account_status="active",
                ))
    return meters, customers


def build_crews(substations):
    crews = []
    chosen = rng.sample(substations, k=8)
    for i, sub in enumerate(chosen, start=1):
        crews.append(Crew(
            crew_id=f"CREW-{i:02d}", name=f"Field Crew {i}",
            home_substation_id=sub.substation_id, region_id=sub.region_id, status="available",
        ))
    return crews


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    substations = build_substations()
    feeders = build_feeders(substations)
    generators = build_generators(substations, feeders)
    meters, customers = build_meters_and_customers(substations, feeders)
    crews = build_crews(substations)

    _write_csv(os.path.join(DATA_DIR, "substations.csv"), substations)
    _write_csv(os.path.join(DATA_DIR, "feeders.csv"), feeders)
    _write_csv(os.path.join(DATA_DIR, "generators.csv"), generators)
    _write_csv(os.path.join(DATA_DIR, "meters.csv"), meters)
    _write_csv(os.path.join(DATA_DIR, "crews.csv"), crews)

    # asset_master (ERP) = generators, as_of snapshot
    as_of = datetime.now(timezone.utc).isoformat()
    asset_rows = []
    for g in generators:
        asset_rows.append({
            "as_of_utc": as_of, "asset_id": g.asset_id, "asset_type": g.asset_type,
            "name": g.name, "nameplate_mw": g.nameplate_mw, "voltage_kv": g.voltage_kv,
            "region_id": g.region_id, "substation_id": g.substation_id, "feeder_id": g.feeder_id,
            "lat": g.lat, "lon": g.lon, "install_year": g.install_year,
            "last_maintenance": g.last_maintenance, "next_maintenance": g.next_maintenance,
            "status": g.status, "oem": g.oem,
        })
    with open(os.path.join(DATA_DIR, "asset_master.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asset_rows[0].keys()))
        writer.writeheader()
        writer.writerows(asset_rows)

    # customer_master (CRM)
    cust_rows = []
    for c in customers:
        cust_rows.append({
            "event_time_utc": as_of, **asdict(c),
        })
    with open(os.path.join(DATA_DIR, "customer_master.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(cust_rows[0].keys()))
        writer.writeheader()
        writer.writerows(cust_rows)

    # topology.json - static graph
    topology = {
        "utility": "Northwind Grid",
        "seed": SEED,
        "bounding_box": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lon_min": LON_MIN, "lon_max": LON_MAX},
        "regions": REGIONS,
        "substations": [asdict(s) for s in substations],
        "feeders": [asdict(f) for f in feeders],
        "generators": [asdict(g) for g in generators],
        "meters": [asdict(m) for m in meters],
        "crews": [asdict(c) for c in crews],
    }
    with open(os.path.join(DATA_DIR, "topology.json"), "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=2)

    # try parquet too (optional dependency)
    try:
        import pandas as pd
        pd.DataFrame(asset_rows).to_parquet(os.path.join(DATA_DIR, "asset_master.parquet"))
        pd.DataFrame(cust_rows).to_parquet(os.path.join(DATA_DIR, "customer_master.parquet"))
    except Exception as e:  # pragma: no cover
        print(f"[warn] parquet export skipped: {e}")

    total_nameplate = sum(g.nameplate_mw for g in generators)
    by_type = {}
    for g in generators:
        by_type[g.asset_type] = by_type.get(g.asset_type, 0) + g.nameplate_mw

    print("=== STEP A: Northwind Grid topology + masters ===")
    print(f"Regions: {len(REGIONS)}  Substations: {len(substations)}  Feeders: {len(feeders)}")
    print(f"Generators: {len(generators)}  Meters: {len(meters)}  Customers: {len(customers)}  Crews: {len(crews)}")
    print(f"Total nameplate generation: {total_nameplate:.1f} MW (target ~1200 MW)")
    for t, mw in sorted(by_type.items()):
        print(f"  - {t:14s}: {mw:7.1f} MW")
    print("Expected peak demand target: 850-950 MW")
    print(f"Files written to: {DATA_DIR}")


if __name__ == "__main__":
    main()
