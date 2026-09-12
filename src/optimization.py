"""Storage dispatch and scenario procurement, in bus-side kWh.

The scenario LP has a common procurement vector and scenario-specific perfect
information recourse. It is a planning approximation, NOT a nonanticipative
multistage policy. Only ``execute_step``/``greedy_step`` produce operating
actions; their inputs contain current observations and a previously computed
value function, never future actual observations.

The project interprets 90% efficiency as round-trip efficiency, split equally
between charging and discharging. Unrestricted, free disposal is assumed.
Under precisely these constraints simultaneous LP charging/discharging can be
removed without changing inventory, procurement, emergencies or objective:
remove eps from c and eta_c*eta_d*eps from d, add (1-eta_c*eta_d)*eps to disposal.
No charge/discharge lower bounds, disposal caps or disposal costs are present.
The repaired solution is checked, with MILP fallback if the check fails.
"""
from __future__ import annotations

from functools import lru_cache
import time
import warnings
from typing import Any

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, OptimizeWarning, linprog, milp as scipy_milp


ETA_C = float(np.sqrt(0.9))
ETA_D = float(np.sqrt(0.9))
E_MIN = 1200.0
E_MAX = 10800.0
P_MAX = 5000.0 / 6.0
FEASIBILITY_TOLERANCE = 2e-5


def _inputs(net, prices, weights):
    net = np.asarray(net, dtype=float)
    if net.ndim == 1:
        net = net[None, :]
    if net.ndim != 2 or not net.size:
        raise ValueError("net must be a nonempty J by H array")
    prices = np.broadcast_to(np.asarray(prices, dtype=float), net.shape)
    weights = np.asarray(weights, dtype=float)
    if weights.shape != (net.shape[0],):
        raise ValueError("weights must have one entry per scenario")
    if (not np.all(np.isfinite(net)) or not np.all(np.isfinite(prices))
            or not np.all(np.isfinite(weights))):
        raise ValueError("scenario arrays must be finite")
    if np.any(prices <= 0):
        raise ValueError("this model requires strictly positive prices")
    if np.any(weights < 0) or not np.isclose(weights.sum(), 1., atol=1e-9):
        raise ValueError("scenario probabilities must be nonnegative and sum to one")
    return net, prices, weights


def eliminate_simultaneous(c, d, r, eta_c=ETA_C, eta_d=ETA_D):
    """Exact free-disposal LP-to-exclusive-flow transformation.

    Returns new arrays and leaves its inputs unchanged. The additional disposal
    has no penalty and no upper bound in this project's model.
    """
    c, d, r = (np.asarray(x, dtype=float) for x in (c, d, r))
    eps = np.minimum(np.maximum(c, 0.), np.maximum(d, 0.) / (eta_c * eta_d))
    return (np.maximum(c - eps, 0.),
            np.maximum(d - eta_c * eta_d * eps, 0.),
            np.maximum(r + (1. - eta_c * eta_d) * eps, 0.))


@lru_cache(maxsize=32)
def _matrices(j, h, revision, binary, terminal_pieces):
    """Cache only structural matrices; scenario values are never cached here."""
    count = j * h
    q = np.arange(h)
    offsets = {name: h + k * count for k, name in enumerate(("c", "d", "u", "r"))}
    e_start = h + 4 * count
    e = np.arange(e_start, e_start + j * (h + 1)).reshape(j, h + 1)
    nvars = e_start + j * (h + 1)
    a = np.arange(nvars, nvars + h) if revision else np.empty(0, dtype=int)
    nvars += len(a)
    z = np.arange(nvars, nvars + count) if binary else np.empty(0, dtype=int)
    nvars += len(z)
    v = np.arange(nvars, nvars + j) if terminal_pieces else np.empty(0, dtype=int)
    nvars += len(v)
    idx = {name: np.arange(start, start + count) for name, start in offsets.items()}
    row = np.arange(count)
    eq_rows = np.concatenate([row] * 5 + [row + count] * 4)
    eq_cols = np.concatenate([
        np.tile(q, j), idx["u"], idx["d"], idx["c"], idx["r"],
        e[:, 1:].ravel(), e[:, :-1].ravel(), idx["c"], idx["d"],
    ])
    eq_values = np.concatenate([
        np.full(count, val) for val in (1., 1., 1., -1., -1., 1., -1., -ETA_C, 1. / ETA_D)
    ])
    ae = sparse.coo_matrix((eq_values, (eq_rows, eq_cols)), shape=(2 * count, nvars)).tocsr()
    rows, cols, vals = [], [], []
    nrows = 0
    if revision:
        ar = np.arange(h)
        rows.extend([ar, ar, ar + h, ar + h])
        cols.extend([q, a, q, a])
        vals.extend([np.ones(h), -np.ones(h), -np.ones(h), -np.ones(h)])
        nrows += 2 * h
    if binary:
        br = np.arange(count) + nrows
        rows.extend([br, br, br + count, br + count])
        cols.extend([idx["c"], z, idx["d"], z])
        vals.extend([np.ones(count), np.full(count, -P_MAX), np.ones(count), np.full(count, P_MAX)])
        nrows += 2 * count
    if rows:
        au = sparse.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                               shape=(nrows, nvars)).tocsr()
    else:
        au = sparse.csr_matrix((0, nvars))
    return ae, au, {**idx, "q": q, "E": e, "a": a, "z": z, "v": v}, nvars


def _terminal_values(terminal_value, grid):
    """Numeric means marginal salvage price; dict means convex cost samples."""
    if isinstance(terminal_value, dict):
        x = np.asarray(terminal_value["grid"], dtype=float)
        y = np.asarray(terminal_value["value"], dtype=float)
        if (x.ndim != 1 or y.shape != x.shape or len(x) < 2
                or np.any(np.diff(x) <= 0) or x[0] > E_MIN or x[-1] < E_MAX
                or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y))):
            raise ValueError("terminal grid/value must cover the storage domain")
        slopes = np.diff(y) / np.diff(x)
        if np.any(np.diff(slopes) < -1e-9):
            raise ValueError("terminal PWL cost must be convex")
        return np.interp(grid, x, y)
    value = float(terminal_value)
    if not np.isfinite(value) or value < 0:
        raise ValueError("terminal marginal value must be nonnegative and finite")
    return -value * np.asarray(grid)


def solve_plan(net, prices, weights, e0, old_plan=None, terminal_value=0.0,
               fixed_plan=None, milp=False, cyclic=False, allow_emergency=True,
               storage=True, time_limit=120.0):
    """Optimize common procurement with a perfect-information recourse LP.

    ``old_plan`` prices deviations by 0.5 * E[p] * abs(q-old_plan), in
    addition to the current complete plan's E[p]*q. Historic deviation charges
    are sunk and must be retained once in the external settlement ledger.
    ``fixed_plan`` fixes q for a comparable recourse-value assessment.
    ``cyclic=True, allow_emergency=False, milp=True`` is the Q1 benchmark.
    ``storage=False`` is useful only for the one-period theory unit tests.
    A scalar terminal_value is CNY per internal kWh of terminal inventory;
    a dict {"grid": ..., "value": ...} gives a convex piecewise-linear cost.
    Neither form is an actual electricity charge.
    """
    start = time.perf_counter()
    net, prices, weights = _inputs(net, prices, weights)
    j, h = net.shape
    if not np.isfinite(e0) or not E_MIN - 1e-8 <= e0 <= E_MAX + 1e-8:
        raise ValueError("initial storage is outside physical bounds")
    e0 = float(np.clip(e0, E_MIN, E_MAX))
    old = None if old_plan is None else np.asarray(old_plan, dtype=float)
    fixed = None if fixed_plan is None else np.asarray(fixed_plan, dtype=float)
    for name, arr in (("old_plan", old), ("fixed_plan", fixed)):
        if arr is not None and (arr.shape != (h,) or np.any(arr < -1e-8) or not np.all(np.isfinite(arr))):
            raise ValueError(f"{name} must be a nonnegative length-H vector")
    pwl = isinstance(terminal_value, dict)
    _terminal_values(terminal_value, np.array([E_MIN, E_MAX]))
    ae, au, idx, nvars = _matrices(j, h, old is not None, bool(milp), pwl)
    lb = np.zeros(nvars)
    ub = np.full(nvars, np.inf)
    for name in ("c", "d"):
        ub[idx[name]] = P_MAX if storage else 0.
    if not allow_emergency:
        ub[idx["u"]] = 0.
    lb[idx["E"]] = E_MIN
    ub[idx["E"]] = E_MAX
    lb[idx["E"][:, 0]] = ub[idx["E"][:, 0]] = e0
    if cyclic:
        lb[idx["E"][:, -1]] = ub[idx["E"][:, -1]] = e0
    if fixed is not None:
        lb[idx["q"]] = ub[idx["q"]] = np.maximum(fixed, 0.)
    if milp:
        ub[idx["z"]] = 1.
    if pwl:
        lb[idx["v"]] = -np.inf
    objective = np.zeros(nvars)
    mean_price = weights @ prices
    objective[idx["q"]] = mean_price
    objective[idx["u"]] = (5. * weights[:, None] * prices).ravel()
    if old is not None:
        objective[idx["a"]] = 0.5 * mean_price
    if pwl:
        objective[idx["v"]] = weights
    else:
        objective[idx["E"][:, -1]] = -float(terminal_value) * weights
    be = np.concatenate([net.ravel(), np.zeros(j * h)])
    bu = []
    if old is not None:
        bu.extend([old, -old])
    if milp:
        bu.extend([np.zeros(j * h), np.full(j * h, P_MAX)])
    bu = np.concatenate(bu) if bu else np.empty(0)
    if pwl:
        tx = np.asarray(terminal_value["grid"], dtype=float)
        ty = np.asarray(terminal_value["value"], dtype=float)
        slopes = np.diff(ty) / np.diff(tx)
        intercepts = ty[:-1] - slopes * tx[:-1]
        m = len(slopes)
        pr = np.arange(j * m)
        pc = np.concatenate([np.repeat(idx["E"][:, -1], m), np.repeat(idx["v"], m)])
        pv = np.concatenate([np.tile(slopes, j), -np.ones(j * m)])
        extra = sparse.coo_matrix((pv, (np.tile(pr, 2), pc)), shape=(j * m, nvars)).tocsr()
        au = sparse.vstack([au, extra], format="csr")
        bu = np.concatenate([bu, -np.tile(intercepts, j)])
    if milp:
        integrality = np.zeros(nvars, dtype=np.uint8)
        integrality[idx["z"]] = 1
        matrix = sparse.vstack([ae, au], format="csc")
        lower = np.concatenate([be, np.full(len(bu), -np.inf)])
        upper = np.concatenate([be, bu])
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unrecognized options detected.*threads.*", category=RuntimeWarning)
            result = scipy_milp(objective, integrality=integrality,
                                bounds=Bounds(lb, ub), constraints=LinearConstraint(matrix, lower, upper),
                                options={"time_limit": float(time_limit), "mip_rel_gap": 1e-8, "threads": 1})
    else:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unrecognized options detected.*threads.*", category=OptimizeWarning)
            result = linprog(objective, A_ub=au if au.shape[0] else None,
                             b_ub=bu if len(bu) else None, A_eq=ae, b_eq=be,
                             bounds=np.column_stack([lb, ub]), method="highs",
                             options={"time_limit": float(time_limit), "dual_feasibility_tolerance": 1e-7,
                                      "primal_feasibility_tolerance": 1e-7, "threads": 1})
    if not result.success:
        raise RuntimeError(f"{'MILP' if milp else 'LP'} failed: {result.status}: {result.message}")
    q = np.maximum(result.x[idx["q"]], 0.)
    solution = {name: np.maximum(result.x[idx[name]].reshape(j, h), 0.) for name in ("c", "d", "u", "r")}
    c_raw, d_raw = solution["c"].copy(), solution["d"].copy()
    solution["c"], solution["d"], solution["r"] = eliminate_simultaneous(
        solution["c"], solution["d"], solution["r"])
    energy = result.x[idx["E"]].copy()
    balance_error = float(np.max(np.abs(q[None, :] + solution["u"] + solution["d"] - net - solution["c"] - solution["r"])))
    state_error = float(np.max(np.abs(np.diff(energy, axis=1) - ETA_C * solution["c"] + solution["d"] / ETA_D)))
    physical_error = max(balance_error, state_error,
                         float(np.max(E_MIN - energy)), float(np.max(energy - E_MAX)),
                         float(np.max(solution["c"] - P_MAX)), float(np.max(solution["d"] - P_MAX)),
                         float(np.max(np.minimum(solution["c"], solution["d"]))))
    if physical_error > FEASIBILITY_TOLERANCE:
        if not milp:
            fallback = solve_plan(net, prices, weights, e0, old, terminal_value, fixed,
                                  True, cyclic, allow_emergency, storage, time_limit)
            fallback["lp_repair_fallback"] = True
            return fallback
        raise RuntimeError(f"MILP physical verification failed: {physical_error}")
    expected_plan_cost = float(mean_price @ q)
    expected_adjustment_cost = float(0.5 * mean_price @ np.abs(q - old)) if old is not None else 0.
    expected_emergency_cost = float(np.sum(weights[:, None] * 5. * prices * solution["u"]))
    terminal_cost = float(weights @ _terminal_values(terminal_value, energy[:, -1]))
    recomputed = expected_plan_cost + expected_adjustment_cost + expected_emergency_cost + terminal_cost
    if abs(recomputed - result.fun) > max(1e-4, 1e-7 * abs(result.fun)):
        raise RuntimeError("independently recomputed planner objective differs from solver objective")
    return {"q": q, **solution, "E": energy, "objective": recomputed,
            "expected_cost": expected_plan_cost + expected_adjustment_cost + expected_emergency_cost,
            "expected_plan_cost": expected_plan_cost, "expected_adjustment_cost": expected_adjustment_cost,
            "expected_emergency_cost": expected_emergency_cost, "terminal_cost": terminal_cost,
            "status": "optimal", "solver": "scipy.optimize.milp/HiGHS" if milp else "scipy.optimize.linprog/HiGHS",
            "solver_status": int(result.status), "solver_message": str(result.message),
            "mip_gap": float(getattr(result, "mip_gap", 0.) or 0.),
            "runtime_seconds": time.perf_counter() - start,
            "maximum_physical_error": physical_error,
            "simultaneous_flow_removed_kwh": float(np.sum(c_raw - solution["c"]) + np.sum(d_raw - solution["d"])),
            "lp_repair_fallback": False,
            "recourse_interpretation": "perfect-information scenario planning approximation"}


def value_policy(net, prices, weights, fixed_plan, terminal_value=0.0, grid_size=41):
    """Backward expected-cost DP using only decision-time scenario information.

    The Bellman approximation uses the forecast ensemble's weighted joint
    (net load, price) marginal at each future slot. It retains their same-slot
    dependence but does not model a posterior over the full path; the common
    forecast/state information is updated externally at authorized decisions.
    After observing the current net load and price, the controller minimizes
    actual emergency expense plus this common continuation value.

    Emergency charging is forbidden in this causal policy: charging can use
    only currently available plan/PV surplus. Discharging into disposal is
    also omitted because free disposal and nonnegative salvage make it weakly
    dominated. Every current-stage action is mutually exclusive by construction.
    """
    net, prices, weights = _inputs(net, prices, weights)
    j, h = net.shape
    q = np.asarray(fixed_plan, dtype=float)
    if q.shape != (h,) or np.any(q < -1e-8) or not np.all(np.isfinite(q)):
        raise ValueError("fixed_plan must be a finite nonnegative length-H vector")
    if int(grid_size) != grid_size or grid_size < 3:
        raise ValueError("storage value grid requires at least three points")
    grid = np.linspace(E_MIN, E_MAX, int(grid_size))
    step = grid[1] - grid[0]
    values = np.empty((h + 1, len(grid)))
    values[h] = _terminal_values(terminal_value, grid)
    # Only neighboring breakpoints can be reached under the power constraint.
    reach = int(np.ceil(max(P_MAX * ETA_C, P_MAX / ETA_D) / step)) + 1
    targets = grid[np.clip(np.arange(len(grid))[:, None] + np.arange(-reach, reach + 1)[None, :], 0, len(grid) - 1)]
    energy = grid[None, :, None]
    for t in range(h - 1, -1, -1):
        residual = (net[:, t] - q[t])[:, None, None]
        lower = np.maximum(E_MIN, energy - np.minimum(np.maximum(residual, 0.), P_MAX) / ETA_D)
        upper = np.minimum(E_MAX, energy + np.minimum(np.maximum(-residual, 0.), P_MAX) * ETA_C)
        candidate = np.clip(targets[None, :, :], lower, upper)
        bus_discharge = np.maximum(energy - candidate, 0.) * ETA_D
        emergency = np.maximum(residual - bus_discharge, 0.)
        floating_index = np.clip((candidate - E_MIN) / step, 0., len(grid) - 1.)
        left = np.minimum(floating_index.astype(np.int64), len(grid) - 2)
        fraction = floating_index - left
        continuation = values[t + 1, left] * (1. - fraction) + values[t + 1, left + 1] * fraction
        costs = 5. * prices[:, t, None, None] * emergency + continuation
        values[t] = weights @ costs.min(axis=2)
    return {"grid": grid, "V": values, "grid_size": len(grid),
            "approximation": "weighted stage-marginal Bellman value; action observes current net load and price"}


def execute_step(n, p, q, E, Vnext, grid):
    """Choose one feasible action using ONLY this slot's revealed n and p."""
    n, p, q, E = map(float, (n, p, q, E))
    grid, Vnext = np.asarray(grid, dtype=float), np.asarray(Vnext, dtype=float)
    if (not np.all(np.isfinite([n, p, q, E])) or p <= 0 or q < -1e-8
            or E < E_MIN - 1e-6 or E > E_MAX + 1e-6
            or grid.shape != Vnext.shape or grid.ndim != 1 or len(grid) < 2
            or np.any(np.diff(grid) <= 0) or grid[0] > E_MIN or grid[-1] < E_MAX
            or not np.all(np.isfinite(Vnext))):
        raise ValueError("invalid causal execution inputs")
    E = float(np.clip(E, E_MIN, E_MAX))
    residual = n - q
    lower = max(E_MIN, E - min(max(residual, 0.), P_MAX) / ETA_D)
    upper = min(E_MAX, E + min(max(-residual, 0.), P_MAX) * ETA_C)
    candidates = np.unique(np.r_[np.clip(grid, lower, upper), E])
    c = np.maximum(candidates - E, 0.) / ETA_C
    d = np.maximum(E - candidates, 0.) * ETA_D
    u = np.maximum(residual + c - d, 0.)
    r = np.maximum(-residual - c + d, 0.)
    objective = 5. * p * u + np.interp(candidates, grid, Vnext)
    minimum = objective.min()
    # Among numerically tied actions preserve the most inventory.
    chosen = np.flatnonzero(objective <= minimum + 1e-9)[-1]
    return {"c": float(c[chosen]), "d": float(d[chosen]), "u": float(u[chosen]),
            "r": float(r[chosen]), "E_next": float(candidates[chosen]),
            "emergency_cost": float(5. * p * u[chosen]),
            "continuation_value": float(np.interp(candidates[chosen], grid, Vnext)),
            "decision_objective": float(objective[chosen])}


def greedy_step(n, p, q, E):
    """Feasible deficit-first comparator, with the same surplus charging rule."""
    residual = float(n) - float(q)
    if residual >= 0:
        c = 0.
        d = min(residual, P_MAX, max(float(E) - E_MIN, 0.) * ETA_D)
    else:
        c = min(-residual, P_MAX, max(E_MAX - float(E), 0.) / ETA_C)
        d = 0.
    u = max(residual + c - d, 0.)
    r = max(-residual - c + d, 0.)
    return {"c": c, "d": d, "u": u, "r": r,
            "E_next": float(E) + ETA_C * c - d / ETA_D,
            "emergency_cost": 5. * float(p) * u,
            "continuation_value": 0., "decision_objective": 5. * float(p) * u}
