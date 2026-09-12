"""Independent accounting and physical checks; never imports the optimizer.

Every quantity except price is in bus-side kWh, with E in internal kWh.
Planning approximations are not interpreted as executed scenario policies here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

ETA = float(np.sqrt(0.9))
CAP = 5000.0 / 6.0
TOLERANCE = 1e-5
EVENT_TOLERANCE = 1e-7
REVISION_SLOTS = (36, 72, 108)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def emergency_events(u_day, day_id: str, tolerance: float = EVENT_TOLERANCE) -> list[dict]:
    """Merge positive adjacent slots; calls for separate days split midnight."""
    u = np.asarray(u_day, dtype=float)
    _check(u.shape == (144,), "emergency array must contain 144 slots")
    _check(np.isfinite(u).all(), "non-finite emergency energy")
    mask = u > tolerance
    edges = np.diff(np.r_[False, mask, False].astype(int))
    events = []
    for index, (start, end) in enumerate(zip(np.flatnonzero(edges == 1),
                                             np.flatnonzero(edges == -1)), 1):
        event = {"event_index": index, "start_minute": int(start * 10),
                 "end_minute": int(end * 10), "value": float(u[start:end].sum())}
        if day_id != "typical_day":
            midnight = datetime.fromisoformat(day_id)
            event["interval_start"] = (midnight + timedelta(minutes=int(start * 10))).isoformat()
            event["interval_end"] = (midnight + timedelta(minutes=int(end * 10))).isoformat()
        else:
            event.update(interval_start="", interval_end="")
        events.append(event)
    return events


def _ledger(trace: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return overwritten effective plan, absolute revision fee, signed delta."""
    current = np.asarray(trace["q0"], dtype=float).copy()
    price = np.asarray(trace["price"], dtype=float)
    versions = np.asarray(trace["versions"], dtype=float)
    fee = np.zeros(len(current))
    delta_cost = np.zeros(len(current))
    for version in range(3):
        adopted = np.isfinite(versions[:, version, :])
        delta = np.where(adopted, versions[:, version, :] - current, 0.0)
        fee += 0.5 * np.sum(price * np.abs(delta), axis=1)
        delta_cost += np.sum(price * (delta + 0.5 * np.abs(delta)), axis=1)
        current[adopted] = versions[:, version, :][adopted]
    return current, fee, delta_cost


def summarize(trace: dict) -> list[dict[str, Any]]:
    """Realized daily costs; auxiliary terminal values never enter electricity bills."""
    effective, fees, deltas = _ledger(trace)
    price = np.asarray(trace["price"], dtype=float)
    q0 = np.asarray(trace["q0"], dtype=float)
    u = np.asarray(trace["u"], dtype=float)
    energy = np.asarray(trace["E"], dtype=float)
    output = []
    for i, day_id in enumerate(trace["dates"]):
        initial_cost = float(np.dot(price[i], q0[i]))
        settled = float(np.dot(price[i], effective[i]) + fees[i])
        emergency_cost = float(5.0 * np.dot(price[i], u[i]))
        output.append({
            "day_id": str(day_id), "planned_kwh": float(q0[i].sum()),
            "planned_cost": initial_cost, "effective_kwh": float(effective[i].sum()),
            "adjustment_fee": float(fees[i]), "revision_delta": float(deltas[i]),
            "planned_settled": settled, "emergency_kwh": float(u[i].sum()),
            "emergency_cost": emergency_cost, "total_cost": settled + emergency_cost,
            "curtailment_kwh": float(np.asarray(trace["r"])[i].sum()),
            "charge_kwh": float(np.asarray(trace["c"])[i].sum()),
            "discharge_kwh": float(np.asarray(trace["d"])[i].sum()),
            "initial_energy": float(energy[i, 0]), "final_energy": float(energy[i, -1]),
            "events": emergency_events(u[i], str(day_id)),
        })
    return output


def _verify_audits(audits: list[dict]) -> dict:
    counts = {"decisions": len(audits), "forecast_issue_checks": 0,
              "historical_day_checks": 0, "weight_checks": 0,
              "reveal_checks": 0, "solver_checks": 0}
    for audit in audits:
        if audit.get("decision_time") in (None, "", "typical_day"):
            _check(audit.get("kind") in ("deterministic", "q1"),
                   "causal audit missing decision_time")
            continue
        decision = datetime.fromisoformat(str(audit["decision_time"]))
        issues = list(audit.get("issue_times", []))
        if audit.get("issue_time"):
            issues.append(audit["issue_time"])
        if audit.get("forecast_issue_time"):
            issues.append(audit["forecast_issue_time"])
        for issue in issues:
            _check(datetime.fromisoformat(str(issue)) <= decision,
                   f"future forecast release {issue} used at {decision}")
            counts["forecast_issue_checks"] += 1
        historical = list(audit.get("historical_days", []))
        for key in ("historical_day", "history_max_day", "training_max_day", "residual_max_day"):
            if audit.get(key):
                historical.append(audit[key])
        for old_day in historical:
            _check(date.fromisoformat(str(old_day)[:10]) < decision.date(),
                   f"incomplete/future historical day {old_day} used at {decision}")
            counts["historical_day_checks"] += 1
        if "weights" in audit:
            weights = np.asarray(audit["weights"], dtype=float)
            _check(weights.size > 0 and np.isfinite(weights).all() and
                   weights.min() >= 0 and abs(weights.sum() - 1.0) <= 1e-9,
                   "invalid scenario probabilities")
            counts["weight_checks"] += 1
        for key in ("reveal_time", "max_actual_time", "max_price_reveal_time"):
            if audit.get(key):
                _check(datetime.fromisoformat(str(audit[key])) <= decision,
                       f"future observation in {key}")
                counts["reveal_checks"] += 1
        if "actual_slot" in audit and "decision_slot" in audit:
            _check(int(audit["actual_slot"]) <= int(audit["decision_slot"]),
                   "future actual slot accessed")
            counts["reveal_checks"] += 1
        if "available_until_slot" in audit and "decision_slot" in audit:
            _check(int(audit["available_until_slot"]) <= int(audit["decision_slot"]),
                   "forecast accessed observations beyond its decision slot")
            counts["reveal_checks"] += 1
        if "solver_success" in audit:
            _check(bool(audit["solver_success"]), "failed solve used for execution")
            counts["solver_checks"] += 1
        if "solver_status" in audit:
            _check(str(audit["solver_status"]).lower() in ("0", "optimal", "success"),
                   f"nonoptimal solver status used: {audit['solver_status']}")
            counts["solver_checks"] += 1
    return counts


def verify_trace(trace: dict, result_id: str, tolerance: float = TOLERANCE) -> dict:
    """Raise on failure; reconstruct physics, revisions, accounting and information times.

    Small contiguous traces are supported for smoke tests. The publication writer
    additionally requires the complete year and nonempty decision audit records.
    """
    dates = [str(x) for x in trace["dates"]]
    count = len(dates)
    _check(count > 0 and len(set(dates)) == count, "empty/duplicate trace dates")
    arrays = {}
    for key in ("q0", "q", "c", "d", "u", "r", "load", "pv", "price"):
        arrays[key] = np.asarray(trace[key], dtype=float)
        _check(arrays[key].shape == (count, 144), f"incorrect {key} shape")
        _check(np.isfinite(arrays[key]).all(), f"non-finite {key}")
        _check(arrays[key].min() >= -tolerance, f"negative {key}")
    versions = np.asarray(trace["versions"], dtype=float)
    _check(versions.shape == (count, 3, 144), "incorrect versions shape")
    _check(not np.isinf(versions).any(), "infinite plan version")
    finite_versions = versions[np.isfinite(versions)]
    _check(finite_versions.size == 0 or finite_versions.min() >= -tolerance,
           "negative revised plan")
    for v, start in enumerate(REVISION_SLOTS):
        _check(np.isnan(versions[:, v, :start]).all(), "revision changes an executed slot")
        for row in versions[:, v, start:]:
            _check(np.isfinite(row).all() or np.isnan(row).all(),
                   "adopted revision must cover all remaining slots")
    if result_id in ("result1", "result2", "result4-2"):
        _check(np.isnan(versions).all(), f"{result_id} cannot revise its purchase plan")
    energy = np.asarray(trace["E"], dtype=float)
    _check(energy.shape == (count, 145) and np.isfinite(energy).all(), "invalid E array")
    _check(energy.min() >= 1200.0 - tolerance, "battery below 1200 kWh")
    _check(energy.max() <= 10800.0 + tolerance, "battery above 10800 kWh")
    _check(np.max(arrays["c"]) <= CAP + tolerance, "charge power exceeds 5000 kW")
    _check(np.max(arrays["d"]) <= CAP + tolerance, "discharge power exceeds 5000 kW")
    simultaneous = float(np.minimum(arrays["c"], arrays["d"]).max())
    _check(simultaneous <= tolerance, "simultaneous charge and discharge")
    balance = arrays["q"] + arrays["u"] + arrays["pv"] + arrays["d"] - arrays["load"] - arrays["c"] - arrays["r"]
    recursion = np.diff(energy, axis=1) - ETA * arrays["c"] + arrays["d"] / ETA
    balance_error = float(np.abs(balance).max())
    storage_error = float(np.abs(recursion).max())
    _check(balance_error <= tolerance, f"bus balance error {balance_error:g}")
    _check(storage_error <= tolerance, f"storage recursion error {storage_error:g}")
    continuity_error = float(np.max(np.abs(energy[1:, 0] - energy[:-1, -1]))) if count > 1 else 0.0
    _check(continuity_error <= tolerance, "battery reset/discontinuity at midnight")
    if result_id == "result1":
        _check(dates == ["typical_day"], "question 1 must use typical_day")
        _check(abs(energy[0, -1] - energy[0, 0]) <= tolerance, "question 1 not cyclic")
        _check(np.max(arrays["u"]) <= tolerance, "emergency purchase in question 1")
    else:
        parsed = [date.fromisoformat(x) for x in dates]
        _check(all(b - a == timedelta(days=1) for a, b in zip(parsed, parsed[1:])),
               "trace dates must be contiguous and ordered")
        if dates[0] == "2025-01-01":
            _check(abs(energy[0, 0] - 6000.0) <= tolerance, "January 1 energy must be 6000 kWh")
    effective, fees, delta_cost = _ledger(trace)
    plan_error = float(np.max(np.abs(effective - arrays["q"])))
    _check(plan_error <= tolerance, "effective plan differs from overwritten versions")
    # Two independently arranged settlement identities detect full-version double billing.
    initial_cost = np.sum(arrays["q0"] * arrays["price"], axis=1)
    settled_cost = np.sum(arrays["q"] * arrays["price"], axis=1) + fees
    ledger_error = float(np.max(np.abs(initial_cost + delta_cost - settled_cost)))
    _check(ledger_error <= max(tolerance, 1e-10 * float(np.max(np.abs(settled_cost)))),
           "revision ledger does not telescope")
    daily = summarize(trace)
    event_error = max(abs(sum(e["value"] for e in x["events"]) - x["emergency_kwh"]) for x in daily)
    _check(event_error <= 144 * EVENT_TOLERANCE + tolerance, "event total mismatch")
    information = _verify_audits(trace.get("audits", []))
    return {"result_id": result_id, "passed": True, "days": count,
            "first_day": dates[0], "last_day": dates[-1], "tolerance_kwh": tolerance,
            "max_balance_error_kwh": balance_error, "max_storage_error_kwh": storage_error,
            "max_continuity_error_kwh": continuity_error, "max_simultaneous_kwh": simultaneous,
            "max_version_error_kwh": plan_error, "max_ledger_error_cny": ledger_error,
            "max_event_error_kwh": float(event_error), "information_audit": information,
            "total_cost_all_days": float(sum(x["total_cost"] for x in daily)),
            "independent_of_optimizer_objective": True}
