"""
STEP B - Pydantic v2 models for every Fabric stream contract.

These names are the Eventstream / Eventhouse table contract:
  generation_telemetry, grid_telemetry, meter_telemetry, crm_context,
  erp_assets, weather_observations, predictions, activator_alerts
"""
from __future__ import annotations

from typing import Optional, Literal
from pydantic import BaseModel, Field

AssetType = Literal["wind_turbine", "solar_farm", "gas_peaker", "hydro", "bess", "biomass"]
GenStatus = Literal["running", "derated", "offline", "starting"]
ElementType = Literal["substation", "feeder", "tie_line"]


class GenerationTelemetry(BaseModel):
    event_id: str
    event_time_utc: str
    event_time_local: str
    asset_id: str
    asset_type: AssetType
    farm_or_plant_id: str
    region_id: str
    substation_id: str
    active_power_mw: float
    reactive_power_mvar: float
    voltage_kv: float
    frequency_hz: float
    availability_pct: float
    status: GenStatus
    wind_speed_ms: Optional[float] = None
    irradiance_wm2: Optional[float] = None
    soc_pct: Optional[float] = None
    curtailment_mw: float = 0.0
    setpoint_mw: float


class GridTelemetry(BaseModel):
    event_id: str
    event_time_utc: str
    event_time_local: str
    element_id: str
    element_type: ElementType
    region_id: str
    load_mw: float
    generation_in_mw: float
    loading_pct: float
    voltage_kv: float
    frequency_hz: float
    unbalance_pct: float
    status: str


class MeterTelemetry(BaseModel):
    event_id: str
    event_time_utc: str
    event_time_local: str
    meter_id: str
    feeder_id: str
    substation_id: str
    region_id: str
    active_power_kw: float
    voltage_v: float
    current_a: float
    power_factor: float
    energy_kwh_delta: float
    voltage_violation: bool = False
    last_gasp: bool = False
    restoration: bool = False
    quality_flag: str = "good"


class CrmContext(BaseModel):
    event_time_utc: str
    meter_id: str
    customer_id: str
    address: str
    postal_code: str
    lat: float
    lon: float
    rate_class: Literal["RES", "COM", "IND"]
    medical_baseline: bool
    critical_facility: bool
    feeder_id: str
    account_status: str = "active"


class ErpAssets(BaseModel):
    as_of_utc: str
    asset_id: str
    asset_type: AssetType
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


class WeatherObservation(BaseModel):
    event_time_utc: str
    region_id: str
    temp_c: float
    wind_speed_ms: float
    wind_dir_deg: float
    ghi_wm2: float
    cloud_cover_pct: float
    precip_mm_h: float
    alert: Optional[str] = None


class Prediction(BaseModel):
    event_time_utc: str
    horizon_minutes: int
    scope_type: Literal["grid", "region", "substation", "feeder"]
    scope_id: str
    demand_mw_p50: float
    generation_mw_p50: float
    deficit_mw: float
    deficit_pct: float
    reserve_margin_pct: float
    spike_probability: float
    model_version: str = "heuristic-v1"


class ActivatorAlert(BaseModel):
    alert_id: str
    event_time_utc: str
    rule_id: str
    severity: Literal["info", "warning", "critical"]
    scope_type: Literal["grid", "region", "substation", "feeder", "asset"]
    scope_id: str
    metric: str
    value: float
    threshold: float
    message: str
    recommended_action: str
    human_approval_required: bool = True
    status: Literal["open", "approved", "dismissed"] = "open"
