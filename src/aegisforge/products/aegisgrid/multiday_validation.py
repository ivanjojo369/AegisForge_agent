"""Read-only independent arithmetic inspection of a reported multiday trace.

Does not call the simulator or check real-world authenticity. An integrity
hash alone is never a source signature, measurement, or safety approval.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any

_TOL = 1e-6
_POLICIES = ("grid_only", "solar_balanced", "solar_critical_reserve", "solar_grid_recharge")
_FLOWS = ("grid_import_kwh", "grid_to_battery_kwh", "solar_direct_kwh",
          "solar_to_battery_kwh", "solar_curtailed_kwh", "battery_to_load_kwh",
          "critical_unserved_kwh", "other_unserved_kwh")


def _digest(data: Any) -> str:
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=_TOL, rel_tol=1e-9)


def _num(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= -_TOL


def verify_multiday(data: Any) -> dict[str, Any]:
    checks = {k: False for k in (
        "result_digest", "scenario_digest", "bounded_trace_shape",
        "outage_and_solar_schedule", "interval_energy_conservation",
        "battery_continuity_and_power", "daily_and_total_metrics",
        "strategy_selection_and_no_control",
    )}
    try:
        if type(data) is not dict or data.get("schema") != "aegisforge.aegisgrid.multiday.v1":
            raise ValueError("schema")
        config = data["scenario"]
        base = config["scenario"]
        days = config["days"]
        if type(days) is not int or days not in (1, 3, 7):
            raise ValueError("days")
        if config["weather"] not in ("stable", "variable", "low_solar") or config[
                "outage_schedule"] not in ("fixed", "shifted"):
            raise ValueError("schedule")
        for key in ("demand_kwh", "critical_kwh", "solar_kwh", "battery_kwh", "tariff",
                    "outage_hours"):
            if not _num(base[key]):
                raise ValueError("inputs")
        if not (base["demand_kwh"] > 0 and base["critical_kwh"] <= base["demand_kwh"]
                and base["outage_hours"] <= 24):
            raise ValueError("constraints")
        a = data["assumptions"]
        if (a.get("time_step_minutes") != 30 or
                a.get("battery_charge_efficiency") != 0.95 or
                a.get("battery_discharge_efficiency") != 0.95 or
                a.get("max_battery_c_rate") != 0.25 or
                a.get("initial_soc_fraction") != 0.5):
            raise ValueError("assumptions")
        checks["result_digest"] = (data["result_sha256"] ==
                                   _digest({k: v for k, v in data.items() if k != "result_sha256"}))
        checks["scenario_digest"] = data["scenario_sha256"] == _digest(config)
        strategies = data["strategies"]
        if type(strategies) is not dict or set(strategies) != set(_POLICIES):
            raise ValueError("strategies")
        shape, schedule_ok, energy_ok, battery_ok, metrics_ok = True, True, True, True, True
        solar_weights = [max(0., math.sin(math.pi * ((i + 0.5) / 2 - 6) / 12)) for i in range(48)]
        normalizer = sum(solar_weights)
        for policy in _POLICIES:
            trial = strategies[policy]
            rows, daily, totals = trial["trace"], trial["daily"], trial["totals"]
            shape &= type(rows) is list and len(rows) == days * 48 and type(daily) is list and len(daily) == days
            if not shape or type(totals) is not dict:
                raise ValueError("shape")
            capacity = 0. if policy == "grid_only" else base["battery_kwh"]
            soc = capacity * .5
            accum = {key: 0. for key in _FLOWS}
            for day in range(days):
                if config["weather"] == "stable":
                    factor = 1.
                elif config["weather"] == "variable":
                    factor = (1., .35, .75, .5, 1.15, .2, .9)[day % 7]
                else:
                    factor = (.3, .15, .45, .2, .1, .35, .25)[day % 7]
                onset = 18. if config["outage_schedule"] == "fixed" else (
                    18., 14., 20., 9., 17., 6., 21.)[day % 7]
                start = soc
                daytime = {key: 0. for key in _FLOWS}
                for i in range(48):
                    row = rows[day * 48 + i]
                    if type(row) is not dict or any(not _num(row.get(k)) for k in
                        (*_FLOWS, "pv_generation_kwh", "battery_soc_kwh")):
                        raise ValueError("invalid trace")
                    h = i / 2
                    outage = ((h - onset) % 24) < base["outage_hours"]
                    pv = 0 if policy == "grid_only" else (
                        base["solar_kwh"] * factor * solar_weights[i] / normalizer)
                    schedule_ok &= (row["day"] == day + 1 and row["hour"] == h and
                                    type(row["grid_outage"]) is bool and
                                    row["grid_outage"] == outage and
                                    _close(row["pv_generation_kwh"], pv))
                    delivered_grid = row["grid_import_kwh"] - row["grid_to_battery_kwh"]
                    energy_ok &= (_close(delivered_grid + row["solar_direct_kwh"] +
                                         row["battery_to_load_kwh"] +
                                         row["critical_unserved_kwh"] + row["other_unserved_kwh"],
                                         base["demand_kwh"] / 48)
                                  and _close(row["solar_direct_kwh"] +
                                             row["solar_to_battery_kwh"] +
                                             row["solar_curtailed_kwh"], pv)
                                  and delivered_grid >= -_TOL
                                  and row["critical_unserved_kwh"] <= base["critical_kwh"] / 48 + _TOL
                                  and (not outage or _close(row["grid_import_kwh"], 0)))
                    battery_ok &= (_close(soc + .95 * (row["solar_to_battery_kwh"] +
                                                          row["grid_to_battery_kwh"]) -
                                              row["battery_to_load_kwh"] / .95,
                                              row["battery_soc_kwh"])
                                   and row["battery_soc_kwh"] <= capacity + _TOL
                                   and row["solar_to_battery_kwh"] + row["grid_to_battery_kwh"]
                                   <= capacity * .125 + _TOL
                                   and row["battery_to_load_kwh"] <= capacity * .125 + _TOL
                                   and (policy == "solar_grid_recharge" or
                                        _close(row["grid_to_battery_kwh"], 0))
                                   and (policy != "grid_only" or
                                        _close(row["battery_to_load_kwh"], 0)))
                    soc = row["battery_soc_kwh"]
                    for key in _FLOWS:
                        daytime[key] += row[key]
                        accum[key] += row[key]
                report = daily[day]
                metrics_ok &= (report["day"] == day + 1 and
                               _close(report["solar_factor"], factor) and
                               _close(report["outage_start_hour"], onset) and
                               _close(report["initial_soc_kwh"], start) and
                               _close(report["final_soc_kwh"], soc) and
                               _close(report["critical_unserved_kwh"], daytime["critical_unserved_kwh"]) and
                               _close(report["total_unserved_kwh"], daytime["critical_unserved_kwh"] +
                                      daytime["other_unserved_kwh"]) and
                               _close(report["grid_cost"], daytime["grid_import_kwh"] * base["tariff"]))
            for key in _FLOWS:
                metrics_ok &= _close(totals[key], accum[key])
            critical_pct = 100. if base["critical_kwh"] == 0 else (
                100. * (1 - accum["critical_unserved_kwh"] / (base["critical_kwh"] * days)))
            metrics_ok &= (_close(totals["critical_served_percent"], critical_pct) and
                           _close(totals["initial_battery_soc_kwh"], capacity * .5) and
                           _close(totals["final_battery_soc_kwh"], soc) and
                           _close(totals["grid_cost"], accum["grid_import_kwh"] * base["tariff"]) and
                           _close(totals["total_unserved_kwh"], accum["critical_unserved_kwh"] +
                                  accum["other_unserved_kwh"]))
        checks["bounded_trace_shape"] = shape
        checks["outage_and_solar_schedule"] = schedule_ok
        checks["interval_energy_conservation"] = energy_ok
        checks["battery_continuity_and_power"] = battery_ok
        checks["daily_and_total_metrics"] = metrics_ok
        comparison = data["comparison"]
        candidates = _POLICIES[1:]
        winner = min(candidates, key=lambda p: (
            round(strategies[p]["totals"]["critical_unserved_kwh"], 7),
            round(strategies[p]["totals"]["total_unserved_kwh"], 7),
            round(strategies[p]["totals"]["grid_cost"], 7), p))
        checks["strategy_selection_and_no_control"] = (
            comparison["selected_policy"] == winner and
            comparison["candidate_policies"] == list(candidates) and
            comparison["is_global_optimum"] is False and
            a.get("operates_physical_equipment") is False and
            a.get("is_global_optimization") is False and
            a.get("excludes_capex_and_maintenance") is True and
            data["verification"]["passed"] is True)
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError, IndexError):
        pass
    return {"passed": all(checks.values()), "checks": checks,
            "scope": "replay_of_synthetic_multiday_accounting_not_external_measurement"}
