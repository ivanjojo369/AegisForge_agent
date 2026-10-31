"""Bounded synthetic multiday scenarios and finite-policy comparison for AegisGrid.

No weather forecast, infrastructure control, economic investment model or
claim of globally optimal operation. The checkpoint-backed mission is owned
by the caller, never by this pure computation module.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any

from .simulator import _validate, _CHARGE_EFF, _DISCHARGE_EFF

SCHEMA = "aegisforge.aegisgrid.multiday.v1"
_POLICIES = ("grid_only", "solar_balanced", "solar_critical_reserve", "solar_grid_recharge")
_DAYS = (1, 3, 7)
_WEATHER = ("stable", "variable", "low_solar")
_OUTAGES = ("fixed", "shifted")
_STEPS = 48
_DT = 0.5
_WEIGHTS = tuple(max(0.0, math.sin(math.pi * ((i + 0.5) * _DT - 6) / 12))
                 for i in range(_STEPS))
_WEIGHT_SUM = sum(_WEIGHTS)
_KEYS = frozenset(("scenario", "days", "weather", "outage_schedule"))
_FLOW_FIELDS = ("grid_import_kwh", "grid_to_battery_kwh", "solar_direct_kwh",
                "solar_to_battery_kwh", "solar_curtailed_kwh", "battery_to_load_kwh",
                "critical_unserved_kwh", "other_unserved_kwh")


def _digest(data: Any) -> str:
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def validate_request(data: Any) -> dict[str, Any]:
    if type(data) is not dict or set(data) != _KEYS:
        raise ValueError("invalid multiday fields")
    days, weather, schedule = data["days"], data["weather"], data["outage_schedule"]
    if type(days) is not int or days not in _DAYS or weather not in _WEATHER or schedule not in _OUTAGES:
        raise ValueError("unsupported multiday configuration")
    scenario = _validate(data["scenario"])
    return {"scenario": scenario, "days": days, "weather": weather, "outage_schedule": schedule}


def day_conditions(day: int, weather: str, outage_schedule: str) -> tuple[float, float]:
    # Fixed, published synthetic patterns; never inferred from local weather.
    factor = {"stable": (1.0,), "variable": (1.0, 0.35, 0.75, 0.5, 1.15, 0.2, 0.9),
              "low_solar": (0.3, 0.15, 0.45, 0.2, 0.1, 0.35, 0.25)}[weather][day %
              (1 if weather == "stable" else 7)]
    onset = 18.0 if outage_schedule == "fixed" else (18.0, 14.0, 20.0, 9.0, 17.0, 6.0, 21.0)[day % 7]
    return factor, onset


def _run(config: dict[str, Any], policy: str) -> dict[str, Any]:
    base, days = config["scenario"], config["days"]
    capacity = 0.0 if policy == "grid_only" else base["battery_kwh"]
    soc = initial_soc = capacity * 0.5
    slot_power_limit = capacity * 0.25 * _DT  # 0.25 C, kWh per interval
    demand_slot = base["demand_kwh"] / _STEPS
    critical_slot = base["critical_kwh"] / _STEPS
    other_slot = demand_slot - critical_slot
    totals = {field: 0.0 for field in _FLOW_FIELDS}
    rows: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    balances_passed = True

    for day in range(days):
        factor, onset = day_conditions(day, config["weather"], config["outage_schedule"])
        day_totals = {field: 0.0 for field in _FLOW_FIELDS}
        start_soc = soc
        for step in range(_STEPS):
            hour = step * _DT
            outage = ((hour - onset) % 24) < base["outage_hours"]
            solar = (base["solar_kwh"] * factor * _WEIGHTS[step] / _WEIGHT_SUM
                     if policy != "grid_only" else 0.0)
            soc_before = soc
            direct_critical = min(critical_slot, solar)
            direct_other = min(other_slot, solar - direct_critical)
            direct = direct_critical + direct_other
            surplus = max(0.0, solar - direct)
            solar_charge = min(surplus, slot_power_limit, (capacity - soc) / _CHARGE_EFF)
            soc += solar_charge * _CHARGE_EFF
            curtailed = surplus - solar_charge
            remaining_critical = critical_slot - direct_critical
            remaining_other = other_slot - direct_other
            grid_charge = 0.0
            discharge = 0.0
            if outage:
                available = min(slot_power_limit, soc * _DISCHARGE_EFF,
                                remaining_critical + remaining_other)
                to_critical = min(remaining_critical, available)
                to_other = min(remaining_other, available - to_critical)
                if policy in ("solar_critical_reserve", "solar_grid_recharge"):
                    # Reserve enough AC energy for critical demand later in this day's
                    # remaining outage slots; conservative, never an external guarantee.
                    future = sum(critical_slot for j in range(step + 1, _STEPS)
                                 if ((j * _DT - onset) % 24) < base["outage_hours"])
                    spare = max(0.0, (soc - to_critical / _DISCHARGE_EFF)
                                * _DISCHARGE_EFF - future)
                    to_other = min(to_other, spare)
                discharge = to_critical + to_other
                soc -= discharge / _DISCHARGE_EFF
                grid_import = 0.0
                critical_unserved = remaining_critical - to_critical
                other_unserved = remaining_other - to_other
            else:
                # Candidate policy: locally simulated grid top-up to 90% SOC.
                # Grid energy to battery is distinct from energy delivered to load.
                if policy == "solar_grid_recharge":
                    grid_charge = min(max(0.0, slot_power_limit - solar_charge),
                                      max(0.0, (capacity * 0.9 - soc) / _CHARGE_EFF))
                    soc += grid_charge * _CHARGE_EFF
                grid_import = remaining_critical + remaining_other + grid_charge
                critical_unserved = other_unserved = 0.0
            soc = max(0.0, min(capacity, soc))
            row = {
                "day": day + 1, "hour": hour, "grid_outage": outage,
                "pv_generation_kwh": solar, "grid_import_kwh": grid_import,
                "grid_to_battery_kwh": grid_charge, "solar_direct_kwh": direct,
                "solar_to_battery_kwh": solar_charge,
                "solar_curtailed_kwh": curtailed, "battery_to_load_kwh": discharge,
                "battery_soc_kwh": soc, "critical_unserved_kwh": critical_unserved,
                "other_unserved_kwh": other_unserved,
            }
            flow_in_to_load = (grid_import - grid_charge + direct + discharge +
                               critical_unserved + other_unserved)
            balances_passed &= (abs(flow_in_to_load - demand_slot) < 1e-6 and
                                abs(solar - direct - solar_charge - curtailed) < 1e-6 and
                                abs(soc - soc_before - (solar_charge + grid_charge) * _CHARGE_EFF
                                    + discharge / _DISCHARGE_EFF) < 1e-6 and
                                -1e-8 <= soc <= capacity + 1e-8)
            for name in _FLOW_FIELDS:
                day_totals[name] += row[name]
                totals[name] += row[name]
            rows.append(row)
        daily.append({"day": day + 1, "solar_factor": factor, "outage_start_hour": onset,
                      "initial_soc_kwh": start_soc, "final_soc_kwh": soc,
                      "critical_unserved_kwh": day_totals["critical_unserved_kwh"],
                      "total_unserved_kwh": (day_totals["critical_unserved_kwh"] +
                                             day_totals["other_unserved_kwh"]),
                      "grid_cost": day_totals["grid_import_kwh"] * base["tariff"]})

    totals.update({
        "total_unserved_kwh": totals["critical_unserved_kwh"] + totals["other_unserved_kwh"],
        "grid_cost": totals["grid_import_kwh"] * base["tariff"],
        "critical_served_percent": (100.0 if base["critical_kwh"] == 0 else 100.0 *
                                     (1 - totals["critical_unserved_kwh"] /
                                      (base["critical_kwh"] * days))),
        "initial_battery_soc_kwh": initial_soc, "final_battery_soc_kwh": soc,
    })
    return {"totals": totals, "daily": daily, "trace": rows,
            "balance_checks_passed": bool(balances_passed)}


def simulate_multiday(data: Any) -> dict[str, Any]:
    config = validate_request(data)
    strategies = {policy: _run(config, policy) for policy in _POLICIES}
    # Finite enumeration, not mathematical/global optimization or system sizing.
    candidates = _POLICIES[1:]
    selected = min(candidates, key=lambda name: (
        round(strategies[name]["totals"]["critical_unserved_kwh"], 7),
        round(strategies[name]["totals"]["total_unserved_kwh"], 7),
        round(strategies[name]["totals"]["grid_cost"], 7), name))
    payload = {
        "schema": SCHEMA, "scenario": config, "scenario_sha256": _digest(config),
        "assumptions": {"time_step_minutes": 30, "battery_charge_efficiency": _CHARGE_EFF,
                        "battery_discharge_efficiency": _DISCHARGE_EFF,
                        "max_battery_c_rate": 0.25, "initial_soc_fraction": 0.5,
                        "grid_top_up_target_fraction": 0.9,
                        "solar_profile": "synthetic_sine_06_18",
                        "solar_factors": "fixed_synthetic_patterns_not_weather_forecast",
                        "load_profile": "uniform_every_half_hour",
                        "solar_export": False, "operates_physical_equipment": False,
                        "is_global_optimization": False,
                        "excludes_capex_and_maintenance": True},
        "strategies": strategies,
        "comparison": {"selected_policy": selected,
                       "selection_rule": "min_critical_unserved_then_total_unserved_then_grid_cost",
                       "candidate_policies": list(candidates),
                       "is_global_optimum": False},
        "verification": {"method": "internal_step_energy_conservation",
                         "passed": all(x["balance_checks_passed"] for x in strategies.values()),
                         "scope": "hypothetical_accounting_only_not_external_validation"},
    }
    payload["result_sha256"] = _digest(payload)
    return payload
