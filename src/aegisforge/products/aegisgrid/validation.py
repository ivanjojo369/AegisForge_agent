"""Independent replay of AegisGrid's *reported* energy accounting.

Checks arithmetic, declarations and conservation of the supplied trace, not
meteorology, clinical safety, economic optimality, or source authenticity.
No mutation, filesystem, subprocess, or network effects.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any

_TOL = 1e-6
_COMPONENTS = ("grid_import_kwh", "solar_direct_kwh", "solar_to_battery_kwh",
               "solar_curtailed_kwh", "battery_to_load_kwh",
               "critical_unserved_kwh", "other_unserved_kwh")


def _digest(data: Any) -> str:
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=_TOL)


def _finite_nonnegative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= -_TOL


def verify_trace(result: Any) -> dict[str, Any]:
    """Recalculate each energy flow from the trace; reject tampering or drift.

    A passing check is not an independent physical measurement. It establishes
    only consistency with the disclosed hypothetical scenario and trace.
    """
    checks = {
        "result_digest": False, "scenario_digest": False,
        "trace_length_and_time": False, "outage_schedule": False,
        "step_energy_balances": False, "battery_dynamics": False,
        "daily_totals": False, "declared_no_physical_control": False,
    }
    if type(result) is not dict:
        return {"passed": False, "checks": checks, "scope": "reported_trace_only"}
    try:
        scenario = result["scenario"]
        a = result["assumptions"]
        checks["result_digest"] = (result.get("result_sha256") ==
                                   _digest({k: v for k, v in result.items() if k != "result_sha256"}))
        checks["scenario_digest"] = result["scenario_sha256"] == _digest(scenario)
        checks["declared_no_physical_control"] = (a.get("operates_physical_equipment") is False
                                                  and a.get("is_optimization") is False)
        if (result.get("schema") != "aegisforge.aegisgrid.simulation.v1" or
                a.get("time_step_minutes") != 30 or
                type(scenario) is not dict or
                not all(_finite_nonnegative(scenario[x]) for x in (
                    "demand_kwh", "critical_kwh", "solar_kwh", "battery_kwh",
                    "outage_hours", "tariff")) or
                scenario["critical_kwh"] > scenario["demand_kwh"] or
                scenario["outage_hours"] > 24):
            return {"passed": False, "checks": checks, "scope": "reported_trace_only"}
        c_eff = a["battery_charge_efficiency"]
        d_eff = a["battery_discharge_efficiency"]
        if not (type(c_eff) in (float, int) and type(d_eff) in (float, int)
                and 0 < c_eff <= 1 and 0 < d_eff <= 1):
            return {"passed": False, "checks": checks, "scope": "reported_trace_only"}
        sample_demand = scenario["demand_kwh"] / 48
        # Separate the advertised expected load from energy actually delivered.
        critical_per_step = scenario["critical_kwh"] / 48
        all_good = True
        time_good = True
        outage_good = True
        battery_good = True
        daily_good = True
        for key, candidate in (("reference", False), ("solar_battery", True)):
            record = result[key]
            rows = record["trace"]
            totals = record["totals"]
            if type(rows) is not list or len(rows) != 48 or type(totals) is not dict:
                return {"passed": False, "checks": checks, "scope": "reported_trace_only"}
            cap = scenario["battery_kwh"] if candidate else 0.0
            soc = cap * a["battery_initial_soc_fraction"]
            for idx, row in enumerate(rows):
                if type(row) is not dict or any(not _finite_nonnegative(row[f]) for f in
                    (*_COMPONENTS, "pv_generation_kwh", "battery_soc_kwh")):
                    return {"passed": False, "checks": checks, "scope": "reported_trace_only"}
                hour = idx * 0.5
                outage = ((hour - a["outage_start_hour"]) % 24) < scenario["outage_hours"]
                time_good &= row["hour"] == hour
                outage_good &= type(row["grid_outage"]) is bool and row["grid_outage"] == outage
                pv = row["pv_generation_kwh"]
                pv_direct = row["solar_direct_kwh"]
                pv_charge = row["solar_to_battery_kwh"]
                battery_out = row["battery_to_load_kwh"]
                import_kwh = row["grid_import_kwh"]
                unserved_c = row["critical_unserved_kwh"]
                unserved_o = row["other_unserved_kwh"]
                all_good &= (_close(import_kwh + pv_direct + battery_out + unserved_c + unserved_o, sample_demand)
                             and _close(pv_direct + pv_charge + row["solar_curtailed_kwh"], pv)
                             and unserved_c <= critical_per_step + _TOL
                             and (not outage or _close(import_kwh, 0)))
                battery_good &= (_close(soc + pv_charge * c_eff - battery_out / d_eff,
                                        row["battery_soc_kwh"])
                                 and -_TOL <= row["battery_soc_kwh"] <= cap + _TOL
                                 and pv_charge <= cap * 0.125 + _TOL
                                 and battery_out <= cap * 0.125 + _TOL)
                soc = row["battery_soc_kwh"]
                if not candidate:
                    battery_good &= _close(soc, 0) and _close(pv, 0)
            for field in _COMPONENTS:
                daily_good &= _close(sum(row[field] for row in rows), totals[field])
            daily_good &= (_close(sum(row["pv_generation_kwh"] for row in rows),
                                   scenario["solar_kwh"] if candidate else 0)
                           and _close(totals["grid_import_kwh"] * scenario["tariff"], totals["grid_cost"])
                           and _close(totals["critical_unserved_kwh"] + totals["other_unserved_kwh"],
                                      totals["total_unserved_kwh"])
                           and _close(soc, totals["final_battery_soc_kwh"])
                           and _close(totals["initial_battery_soc_kwh"], cap * a["battery_initial_soc_fraction"]))
            expected_pct = (100 if scenario["critical_kwh"] == 0 else
                            100 * (1 - totals["critical_unserved_kwh"] / scenario["critical_kwh"]))
            daily_good &= _close(totals["critical_served_percent"], expected_pct)
        checks["trace_length_and_time"] = time_good
        checks["outage_schedule"] = outage_good
        checks["step_energy_balances"] = all_good
        checks["battery_dynamics"] = battery_good
        checks["daily_totals"] = daily_good
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        pass
    return {"passed": all(checks.values()), "checks": checks,
            "scope": "independent_replay_of_reported_synthetic_trace_not_external_validation"}
