"""AegisGrid: deterministic, educational 24-hour energy simulation.

No network, equipment commands, external effects, or optimization claims.
All measurements are synthetic assumptions provided by the operator.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any

SCHEMA = "aegisforge.aegisgrid.simulation.v1"
_KEYS = frozenset({
    "site", "currency", "demand_kwh", "critical_kwh", "solar_kwh",
    "battery_kwh", "outage_hours", "tariff",
})
_SITES = frozenset({"clínica comunitaria", "escuela", "hogar", "microred comunitaria"})
_CURRENCIES = frozenset({"MXN", "USD", "EUR", "GBP", "OTRA"})
_STEPS = 48
_STEP_HOURS = 0.5
_CHARGE_EFF = 0.95
_DISCHARGE_EFF = 0.95
_EPS = 1e-7


class AegisGridInputError(ValueError):
    """Unsupported or invalid hypothetical scenario."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _number(data: dict[str, Any], name: str, low: float, high: float) -> float:
    value = data.get(name)
    if type(value) not in (int, float):
        raise AegisGridInputError("invalid " + name)
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise AegisGridInputError("invalid " + name)
    return float(value)


def _validate(data: Any) -> dict[str, Any]:
    if type(data) is not dict or set(data) != _KEYS:
        raise AegisGridInputError("unexpected scenario fields")
    if (type(data["site"]) is not str or type(data["currency"]) is not str
            or data["site"] not in _SITES or data["currency"] not in _CURRENCIES):
        raise AegisGridInputError("unsupported site or currency")
    demand = _number(data, "demand_kwh", 0.1, 1_000_000)
    critical = _number(data, "critical_kwh", 0, demand)
    outage = _number(data, "outage_hours", 0, 24)
    if abs(outage * 2 - round(outage * 2)) > _EPS:
        raise AegisGridInputError("outage_hours must use half-hour increments")
    return {
        "site": data["site"], "currency": data["currency"],
        "demand_kwh": demand, "critical_kwh": critical,
        "solar_kwh": _number(data, "solar_kwh", 0, 1_000_000),
        "battery_kwh": _number(data, "battery_kwh", 0, 1_000_000),
        "outage_hours": outage,
        "tariff": _number(data, "tariff", 0, 10_000),
    }


def _simulate_one(scenario: dict[str, Any], *, candidate: bool) -> dict[str, Any]:
    demand = scenario["demand_kwh"]
    critical = scenario["critical_kwh"]
    capacity = scenario["battery_kwh"] if candidate else 0.0
    solar_total = scenario["solar_kwh"] if candidate else 0.0
    soc = capacity * 0.5  # Explicit initial battery assumption; not a forecast.
    initial_soc = soc
    max_slot_energy = (capacity / 4) * _STEP_HOURS  # C/4 assumed power limit.
    critical_slot = critical / _STEPS
    other_slot = (demand - critical) / _STEPS

    # Illustrative bell-shaped daylight curve 06:00–18:00, no weather forecast.
    daylight_weights = [max(0.0, math.sin(math.pi * ((i + 0.5) * _STEP_HOURS - 6) / 12))
                        for i in range(_STEPS)]
    normalizer = sum(daylight_weights)
    totals = {key: 0.0 for key in (
        "grid_import_kwh", "solar_direct_kwh", "solar_to_battery_kwh",
        "solar_curtailed_kwh", "battery_to_load_kwh",
        "critical_unserved_kwh", "other_unserved_kwh",
    )}
    trace: list[dict[str, Any]] = []
    checks_ok = True
    for i in range(_STEPS):
        hour = i * _STEP_HOURS
        # Fixed illustrative outage starts at 18:00 and wraps over midnight.
        outage = ((hour - 18) % 24) < scenario["outage_hours"]
        pv = solar_total * daylight_weights[i] / normalizer
        soc_before = soc
        pv_crit = min(critical_slot, pv)
        pv_other = min(other_slot, pv - pv_crit)
        pv_direct = pv_crit + pv_other
        surplus = max(0.0, pv - pv_direct)
        pv_charge = min(surplus, max_slot_energy, max(0.0, (capacity - soc) / _CHARGE_EFF))
        soc += pv_charge * _CHARGE_EFF
        pv_curtail = max(0.0, surplus - pv_charge)
        remaining_crit = critical_slot - pv_crit
        remaining_other = other_slot - pv_other
        if outage:
            available_ac = min(remaining_crit + remaining_other, max_slot_energy, soc * _DISCHARGE_EFF)
            discharge_crit = min(remaining_crit, available_ac)
            soc_after_critical = soc - discharge_crit / _DISCHARGE_EFF
            # Reserve stored energy for future critical outage demand first.
            future_critical = sum(
                critical_slot for j in range(i + 1, _STEPS)
                if (((j * _STEP_HOURS - 18) % 24) < scenario["outage_hours"])
            )
            spare_ac = max(0.0, soc_after_critical * _DISCHARGE_EFF - future_critical)
            discharge_other = min(remaining_other, available_ac - discharge_crit, spare_ac)
            battery_ac = discharge_crit + discharge_other
            soc -= battery_ac / _DISCHARGE_EFF
            grid = 0.0
            unserved_crit = remaining_crit - discharge_crit
            unserved_other = remaining_other - discharge_other
        else:
            battery_ac = 0.0
            grid = remaining_crit + remaining_other
            unserved_crit = 0.0
            unserved_other = 0.0
        soc = max(0.0, min(capacity, soc))
        row = {
            "hour": hour, "grid_outage": outage, "pv_generation_kwh": pv,
            "grid_import_kwh": grid, "solar_direct_kwh": pv_direct,
            "solar_to_battery_kwh": pv_charge, "solar_curtailed_kwh": pv_curtail,
            "battery_to_load_kwh": battery_ac, "battery_soc_kwh": soc,
            "critical_unserved_kwh": unserved_crit, "other_unserved_kwh": unserved_other,
        }
        demand_balance = grid + pv_direct + battery_ac + unserved_crit + unserved_other
        pv_balance = pv_direct + pv_charge + pv_curtail
        soc_balance = soc_before + pv_charge * _CHARGE_EFF - battery_ac / _DISCHARGE_EFF
        checks_ok &= (
            abs(demand_balance - (critical_slot + other_slot)) < _EPS
            and abs(pv_balance - pv) < _EPS
            and abs(soc_balance - soc) < _EPS
            and -_EPS <= soc <= capacity + _EPS
            and all(v >= -_EPS for v in (grid, pv_charge, battery_ac, unserved_crit, unserved_other))
        )
        for key in totals:
            totals[key] += row[key]
        trace.append(row)

    totals["total_unserved_kwh"] = totals["critical_unserved_kwh"] + totals["other_unserved_kwh"]
    totals["grid_cost"] = totals["grid_import_kwh"] * scenario["tariff"]
    totals["critical_served_percent"] = (
        100.0 if critical == 0 else max(0.0, min(100.0,
            100.0 * (1 - totals["critical_unserved_kwh"] / critical)))
    )
    totals["initial_battery_soc_kwh"] = initial_soc
    totals["final_battery_soc_kwh"] = soc
    checks_ok &= (
        abs(sum(x["pv_generation_kwh"] for x in trace) - solar_total) < _EPS
        and abs(totals["grid_import_kwh"] + totals["solar_direct_kwh"]
                + totals["battery_to_load_kwh"] + totals["total_unserved_kwh"] - demand) < _EPS
        and 0 <= totals["critical_served_percent"] <= 100 + _EPS
    )
    return {"totals": totals, "trace": trace, "balance_checks_passed": bool(checks_ok)}


def simulate(data: dict[str, Any]) -> dict[str, Any]:
    """Compare grid-only vs a simple solar+battery dispatch, not an optimizer."""
    scenario = _validate(data)
    reference = _simulate_one(scenario, candidate=False)
    candidate = _simulate_one(scenario, candidate=True)
    result = {
        "schema": SCHEMA, "scenario": scenario, "scenario_sha256": _digest(scenario),
        "assumptions": {
            "source": "hypothetical_user_inputs_not_measured",
            "time_step_minutes": 30, "load_profile": "constant_hourly_load",
            "solar_profile": "idealized_daylight_06_18",
            "outage_start_hour": 18, "battery_initial_soc_fraction": 0.5,
            "battery_max_charge_discharge_c_rate": 0.25,
            "battery_charge_efficiency": _CHARGE_EFF,
            "battery_discharge_efficiency": _DISCHARGE_EFF,
            "battery_dispatch": "outage_only_critical_priority_no_grid_charging",
            "solar_export": False, "operates_physical_equipment": False,
            "is_optimization": False,
        },
        "reference": reference, "solar_battery": candidate,
        "verification": {
            "method": "internal_arithmetic_balances_each_step_and_day",
            "passed": reference["balance_checks_passed"] and candidate["balance_checks_passed"],
            "scope": "energy_accounting_only_not_real_world_validation",
        },
    }
    result["result_sha256"] = _digest(result)
    return result
