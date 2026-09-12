"""Chronological simulation. Only this module exposes one actual interval to control.

All planner recourse is a two-stage perfect-information approximation. It is never
copied into an executed trace: execution uses a common causal value policy.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .forecasting import Data, Forecaster
from .optimization import solve_plan, value_policy, execute_step, greedy_step


ARRAY_KEYS = ('q0', 'versions', 'q', 'c', 'd', 'u', 'r', 'E', 'load', 'pv', 'price')


def json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def save_trace(trace, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(folder / 'trace.npz', **{k: trace[k] for k in ARRAY_KEYS})
    meta = {k: v for k, v in trace.items() if k not in ARRAY_KEYS}
    (folder / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False,
        indent=2, default=json_default), encoding='utf-8')


def load_trace(folder):
    folder = Path(folder)
    trace = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    with np.load(folder / 'trace.npz') as arrays:
        trace.update({k: arrays[k] for k in arrays.files})
    return trace


def allocate(data, days):
    trace = {k: np.zeros((days, 144)) for k in ('q0', 'q', 'c', 'd', 'u', 'r')}
    trace.update(versions=np.full((days, 3, 144), np.nan), E=np.zeros((days, 145)),
                 dates=list(data.dates[:days]), load=data.load[:days].copy(),
                 pv=data.pv[:days].copy(), price=data.price[:days].copy(), audits=[])
    return trace


def q1(data):
    start = time.perf_counter()
    net = (data.typical_load - data.typical_pv)[None, :]
    price = data.tariff[None, :]
    kwargs = dict(net=net, prices=price, weights=np.ones(1), e0=6000,
                  cyclic=True, allow_emergency=False)
    lp = solve_plan(**kwargs)
    mip = solve_plan(**kwargs, milp=True)
    assert abs(lp['objective'] - mip['objective']) < 1e-3
    trace = allocate(data, 1)
    trace.update(dates=['typical_day'], load=data.typical_load[None, :],
                 pv=data.typical_pv[None, :], price=price)
    for k in ('c', 'd', 'u', 'r', 'E'):
        trace[k] = np.asarray(lp[k]).reshape(1, -1)
    trace['q0'][0] = trace['q'][0] = lp['q']
    trace['metadata'] = {'result_id': 'result1', 'runtime_seconds': time.perf_counter()-start,
                         'lp_objective': lp['objective'], 'milp_objective': mip['objective'],
                         'lp_status': lp['status'], 'milp_status': mip['status']}
    return trace


def _decision_audit(data, day, slot, scenario, **extra):
    source = scenario.get('source_days', [])
    source = [data.dates[int(x)] if isinstance(x, (int, np.integer)) else str(x) for x in source]
    audit = dict(decision_time=f'{data.dates[day]}T{slot//6:02d}:00:00',
                 decision_slot=slot, available_until_slot=slot,
                 historical_days=source, weights=np.asarray(scenario['weights']).tolist())
    issue = scenario.get('forecast_issue_time', scenario.get('issue_time'))
    if issue is not None and str(issue) not in ('', 'None', 'NaT'):
        audit['issue_time'] = str(issue)
    if source:
        audit['history_max_day'] = max(source)
    audit.update(extra)
    return audit


def simulate(data, config, variant, days=365, calibration=False, progress=None):
    """A policy selected after January never influences the January warmup."""
    begin = time.perf_counter()
    trace = allocate(data, days)
    random_price = variant.get('random_price', False)
    use_a3 = variant.get('use_a3', False)
    revisions = variant.get('revisions', False)
    if not random_price:
        trace['price'][:] = data.tariff
    mode = variant.get('mode', config['mode'])
    forecasters = {m: Forecaster(data, mode=m, use_a3=use_a3, use_price=random_price,
                              seed=config['seed'], history_window_days=config['history_window_days'])
                  for m in {mode, 'weekday'}}
    energy = 6000.0
    solver_count = 0
    adopted_count = 0
    for day in range(days):
        trace['E'][day, 0] = energy
        activate = day >= (15 if calibration else 31)
        active_mode = mode if activate else 'weekday'
        h = config['bandwidth'] if activate else 1.0
        allow_revisions = revisions if activate else variant.get('warmup_revisions', revisions)
        f = forecasters[active_mode]
        active_q = np.zeros(144)
        for slot in (0, 36, 72, 108):
            sc = f.scenarios(day, start=slot, count=config['scenario_count'],
                weekly=variant.get('weekly', True) if activate else True,
                conditional=variant.get('conditional', True) if activate else True,
                independent=variant.get('independent', False) if activate else False,
                independent_price=variant.get('independent_price', False) if activate else False,
                bandwidth=h, issue_cap=0 if activate and variant.get('midnight_only', False) else None)
            net, weights = np.asarray(sc['net']), np.asarray(sc['weights'])
            prices = np.asarray(sc['prices']) if random_price else np.broadcast_to(data.tariff[slot:], net.shape)
            mean_p = weights @ prices
            terminal = config['terminal_factor'] * float(np.min(mean_p)) / np.sqrt(0.9)
            adoption, gain = False, 0.0
            if slot == 0:
                plan = solve_plan(net, prices, weights, energy, terminal_value=terminal)
                solver_count += 1
                active_q[:] = plan['q']
                trace['q0'][day] = active_q
                policy = value_policy(net, prices, weights, active_q, terminal_value=terminal,
                                      grid_size=config['grid_size'])
            else:
                policy = value_policy(net, prices, weights, active_q[slot:], terminal_value=terminal,
                                      grid_size=config['grid_size'])
                if allow_revisions:
                    old = active_q[slot:].copy()
                    plan = solve_plan(net, prices, weights, energy, old_plan=old, terminal_value=terminal)
                    solver_count += 1
                    candidate = np.asarray(plan['q'])
                    candidate_policy = value_policy(net, prices, weights, candidate,
                        terminal_value=terminal, grid_size=config['grid_size'])
                    old_score = mean_p @ old + np.interp(energy, policy['grid'], policy['V'][0])
                    new_score = (mean_p @ (candidate + .5*np.abs(candidate-old)) +
                                 np.interp(energy, candidate_policy['grid'], candidate_policy['V'][0]))
                    gain = float(old_score-new_score)
                    if gain > config['revision_tolerance_cny']:
                        adoption = True
                        adopted_count += 1
                        active_q[slot:] = candidate
                        trace['versions'][day, slot//36-1, slot:] = candidate
                        policy = candidate_policy
            trace['audits'].append(_decision_audit(data, day, slot, sc,
                adopted=adoption, estimated_improvement_cny=gain,
                terminal_value_per_internal_kwh=terminal, solver_status=str(plan['status']),
                solver_success=True,
                policy='greedy' if activate and variant.get('greedy', False) else 'causal_storage_grid_DP'))
            for t in range(slot, slot+36):
                # These are the only current actuals passed to the executor.
                actual_net = float(data.load[day, t]-data.pv[day, t])
                actual_price = float(trace['price'][day, t])
                if activate and variant.get('greedy', False):
                    action = greedy_step(actual_net, actual_price, active_q[t], energy)
                else:
                    action = execute_step(actual_net, actual_price, active_q[t], energy,
                                          policy['V'][t-slot+1], policy['grid'])
                for k in ('c', 'd', 'u', 'r'):
                    trace[k][day, t] = action[k]
                energy = float(action['E_next'])
                trace['E'][day, t+1] = energy
                trace['q'][day, t] = active_q[t]
        if progress and (day % 30 == 0 or day == days-1):
            progress(f"{variant['name']}: {data.dates[day]}, E={energy:.3f}, elapsed={time.perf_counter()-begin:.1f}s")
    trace['metadata'] = dict(variant=variant, config=config, runtime_seconds=time.perf_counter()-begin,
        solver_count=solver_count, adopted_count=adopted_count, calibration=calibration,
        controller_information='current interval actual net load and price; value function computed at latest six-hour decision',
        scenario_recourse='two-stage perfect-information planning approximation; not an executable scenario trajectory')
    return trace
