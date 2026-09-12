#!/home/jasper/miniconda3/envs/tslib/bin/python
"""Reproducible entry point; all derived files are cache/reports except final CSV."""
from __future__ import annotations

import os
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from src.forecasting import Data, write_audit_reports
from src.simulation import simulate, q1, save_trace, load_trace, json_default
from src.verification import verify_trace, summarize
from src.results import write_results


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=json_default), encoding='utf-8')


def worker(config, variant, days, folder, calibration=False):
    def progress(message):
        print(message, flush=True)
    data = Data(ROOT)
    trace = simulate(data, config, variant, days=days, calibration=calibration, progress=progress)
    audit = verify_trace(trace, variant.get('result_id', 'result4-3' if variant.get('random_price') and variant.get('use_a3') else
        'result3' if variant.get('use_a3') else 'result4-2' if variant.get('random_price') else 'result2'))
    save_trace(trace, folder)
    write_json(Path(folder)/'verification.json', audit)
    daily = summarize(trace)
    write_json(Path(folder)/'daily.json', daily)
    return variant['name'], audit


def jobs_run(jobs, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, *job): job[1]['name'] for job in jobs}
        for future in as_completed(futures):
            name, audit = future.result()
            print(f'VERIFIED {name}: {audit}', flush=True)


def main_variants():
    return [dict(name='result2', result_id='result2'),
            dict(name='result3', result_id='result3', use_a3=True, revisions=True),
            dict(name='result4-2', result_id='result4-2', random_price=True),
            dict(name='result4-3', result_id='result4-3', random_price=True, use_a3=True, revisions=True)]


def ablation_variants():
    return [dict(name='q2_yesterday', mode='yesterday'), dict(name='q2_lastweek', mode='lastweek'),
            dict(name='q2_independent_errors', independent=True, conditional=False),
            dict(name='q2_no_week_alignment', weekly=False),
            dict(name='q2_static_weights', conditional=False),
            dict(name='q2_greedy_storage', greedy=True),
            dict(name='q3_midnight_forecast', use_a3=True, midnight_only=True, warmup_revisions=True),
            dict(name='q3_new_forecast_fixed_plan', use_a3=True, warmup_revisions=True),
            dict(name='q4_static_weights', random_price=True, conditional=False),
            dict(name='q4_independent_price', random_price=True, independent_price=True, conditional=False)]


def calibrate(config, workers):
    jobs = []
    for random_price in (False, True):
        family = 'q4' if random_price else 'q2'
        for i, candidate in enumerate(config['candidates']):
            cfg = {**config, **candidate}
            variant = dict(name=f'calibration_{family}_{i}', random_price=random_price)
            jobs.append((cfg, variant, 31, ROOT/'cache'/variant['name'], True))
    jobs_run(jobs, workers)
    selection = {}
    for family in ('q2', 'q4'):
        rows = []
        for i, candidate in enumerate(config['candidates']):
            trace = load_trace(ROOT/'cache'/f'calibration_{family}_{i}')
            daily = summarize(trace)
            val = daily[15:31]
            cost = sum(x['total_cost'] for x in val)
            rows.append(dict(candidate=candidate, validation_cost_cny=cost,
                             initial_energy_kwh=val[0]['initial_energy'], final_energy_kwh=val[-1]['final_energy']))
        # The finite candidate choice is made only once all Jan validation is revealed.
        chosen = min(rows, key=lambda x: x['validation_cost_cny'])
        selection[family] = dict(selected=chosen['candidate'], candidates=rows,
                               available_at='2025-02-01T00:00:00')
    write_json(ROOT/'cache/calibration.json', selection)
    return selection


def selected_config(config, selection, variant):
    family = 'q4' if variant.get('random_price') else 'q2'
    return {**config, **selection[family]['selected']}


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['all','smoke','calibrate','backtest','ablations','publish','reports'], default='all')
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    os.chdir(ROOT)
    for folder in ('cache','logs','reports'):
        (ROOT/folder).mkdir(exist_ok=True)
    config = json.loads((ROOT/'configs/default.json').read_text(encoding='utf-8'))
    # The optimizer and independent verifier deliberately encode the same fixed
    # physical contract separately. Reject unsupported edits instead of silently
    # accepting config values which those modules would ignore.
    supported = dict(dt_hours=1/6, capacity_kwh=12000, energy_min_kwh=1200,
                     energy_max_kwh=10800, initial_energy_kwh=6000,
                     roundtrip_efficiency=.9, power_limit_kw=5000,
                     power_measurement='bus', free_curtailment=True, physical_tolerance=1e-5)
    for key,value in supported.items():
        if config[key] != value:
            raise ValueError(f'Unsupported physical contract edit: {key}; update optimizer and verifier together')
    initial_hash = hash_file(ROOT/'data/data_clean.csv')
    if args.stage in ('all', 'smoke'):
        tested = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                capture_output=True, text=True)
        (ROOT/'logs/tests.log').write_text(tested.stdout+tested.stderr, encoding='utf-8')
        print((tested.stdout+tested.stderr)[-300:], flush=True)
        tested.check_returncode()
        data = Data(ROOT)
        write_audit_reports(ROOT, data)
        trace = q1(data)
        verify_trace(trace, 'result1')
        save_trace(trace, ROOT/'cache/result1')
        write_json(ROOT/'cache/result1/daily.json', summarize(trace))
        # Cross Jan/Feb and multiple plan revisions before undertaking the full run.
        jobs_run([(config,v,35,ROOT/'cache'/('smoke_'+v['name'])) for v in main_variants()], args.workers)
    if args.stage in ('all','calibrate'):
        selection = calibrate(config, args.workers)
    elif args.stage != 'smoke':
        selection = json.loads((ROOT/'cache/calibration.json').read_text(encoding='utf-8'))
    if args.stage in ('all','backtest'):
        if not (ROOT/'cache/result1/trace.npz').exists():
            save_trace(q1(Data(ROOT)), ROOT/'cache/result1')
        jobs_run([(selected_config(config,selection,v),v,365,ROOT/'cache'/v['name'])
                  for v in main_variants()], args.workers)
    if args.stage in ('all','ablations'):
        jobs_run([(selected_config(config,selection,v),v,365,ROOT/'cache'/v['name'])
                  for v in ablation_variants()], args.workers)
    if args.stage in ('all','publish'):
        for variant in ablation_variants():
            if not (ROOT/'cache'/variant['name']/'trace.npz').exists():
                raise ValueError('Required full-period ablation missing: '+variant['name'])
        traces = {k: load_trace(ROOT/'cache'/k) for k in ('result1','result2','result3','result4-2','result4-3')}
        publication = write_results(ROOT/'data/results_template_clean.csv', traces)
        write_json(ROOT/'logs/publication.json', publication)
    assert hash_file(ROOT/'data/data_clean.csv') == initial_hash, 'Read-only input changed'
    manifest = dict(python=sys.version, executable=sys.executable,
        packages={n: importlib.metadata.version(n) for n in ('numpy','pandas','scipy','matplotlib')},
        solver='SciPy HiGHS 1.8.0; one thread per optimizer', config=config,
        input_sha256=initial_hash, source_sha256={str(p.relative_to(ROOT)):hash_file(p)
            for base in ('src','scripts','tests','configs') for p in sorted((ROOT/base).rglob('*')) if p.suffix in ('.py','.json')},
        stage=args.stage, generated_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    write_json(ROOT/'logs/run_manifest.json', manifest)
    if args.stage in ('all','reports','publish'):
        from src.reporting import generate_reports
        generate_reports(ROOT)
    print('Completed stage:', args.stage, flush=True)


if __name__ == '__main__':
    main()
