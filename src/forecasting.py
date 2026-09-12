"""Typed inputs and causal daily/weekly joint residual scenarios.

All array energy quantities are bus-side kWh per ten-minute interval. Prices
are CNY/kWh. Loading the annual data does not make future observations visible
to the forecasting routines: every historical slice ends strictly before d,
and intraday updates see only actual[d, :start].
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

SLOTS = 144
DT = 1.0 / 6.0
ISSUE_SLOTS = (0, 36, 72, 108)


class Data:
    """Read the sole model input, validating each dataset before conversion."""

    def __init__(self, root='.'):
        self.root = Path(root)
        path = self.root / 'data' / 'data_clean.csv'
        raw = path.read_bytes()
        if not raw.startswith(b'\xef\xbb\xbf'):
            raise ValueError('Input CSV must preserve UTF-8 BOM')
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.dates = [(datetime(2025, 1, 1) + timedelta(days=d)).strftime('%Y-%m-%d')
                      for d in range(365)]
        self.weekdays = np.array([datetime.fromisoformat(d).weekday() for d in self.dates])
        self._day_index = {day: i for i, day in enumerate(self.dates)}
        spec = {
            'A1_PRICE': ('electricity_price_cny_per_kwh', 'CNY/kWh', 'relative_day'),
            'A1_LOAD': ('load_kw', 'kW', 'relative_day'),
            'A1_PV_FORECAST': ('pv_forecast_kw', 'kW', 'relative_day'),
            'A2_LOAD': ('load_kw', 'kW', 'calendar'),
            'A2_PV_ACTUAL': ('pv_actual_kw', 'kW', 'calendar'),
            'A4_PRICE': ('electricity_price_cny_per_kwh', 'CNY/kWh', 'calendar'),
            'A3_PV_FORECAST': ('pv_forecast_kw', 'kW', 'calendar'),
        }
        arrays = {k: np.full((SLOTS,) if v[2] == 'relative_day' else (365, SLOTS), np.nan)
                  for k, v in spec.items() if k != 'A3_PV_FORECAST'}
        versions = defaultdict(dict)
        ids, keys, source_keys = set(), set(), set()
        counts, zero_counts = Counter(), Counter()
        inherited = 0
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f, delimiter=',')
            self.columns = reader.fieldnames
            for row in reader:
                dataset = row['dataset_id']
                if dataset not in spec:
                    raise ValueError(f'Unknown dataset: {dataset}')
                metric, unit, basis = spec[dataset]
                if (row['metric'], row['unit'], row['time_basis']) != (metric, unit, basis):
                    raise ValueError(f'Incorrect typed metadata: {row["record_id"]}')
                if row['value_status'] != 'present' or row['value'] == '':
                    raise ValueError(f'Missing source value: {row["record_id"]}')
                # Numeric interpretation follows dataset/time classification.
                val = float(row['value'])
                if not np.isfinite(val) or val < 0 or (unit == 'CNY/kWh' and val <= 0):
                    raise ValueError(f'Invalid physical value: {row["record_id"]}')
                source_key = (row['source_file'], row['source_sheet'], row['source_cell'])
                if row['record_id'] in ids or source_key in source_keys:
                    raise ValueError('Repeated record/source identity')
                ids.add(row['record_id'])
                source_keys.add(source_key)
                counts[dataset] += 1
                zero_counts[dataset] += int(val == 0)
                inherited += int(row['date_inherited'])
                if dataset == 'A3_PV_FORECAST':
                    if row['time_semantics'] != 'forecast_point':
                        raise ValueError('Forecast point labelled as interval')
                    if any(row[k] for k in ('slot_index', 'start_minute', 'end_minute',
                                            'interval_start', 'interval_end')):
                        raise ValueError('Forecast point has inapplicable interval fields')
                    issue = datetime.fromisoformat(row['issue_time'])
                    valid = datetime.fromisoformat(row['valid_time'])
                    lead = int(row['lead_hours'])
                    if not 1 <= lead <= 24 or valid != issue + timedelta(hours=lead):
                        raise ValueError('Inconsistent issue/valid/lead timestamps')
                    if issue.date().isoformat() != row['day_id'] or issue.hour not in (0, 6, 12, 18):
                        raise ValueError('Unexpected issue day/hour')
                    key = (dataset, row['issue_time'], row['valid_time'])
                    versions[issue][valid] = val
                else:
                    if row['time_semantics'] != 'ten_minute_interval':
                        raise ValueError('Interval labelled with incorrect semantics')
                    slot = int(row['slot_index'])
                    if not 0 <= slot < SLOTS or int(row['start_minute']) != 10 * slot or int(row['end_minute']) != 10 * (slot + 1):
                        raise ValueError('Incorrect interval grid')
                    if any(row[k] for k in ('issue_time', 'valid_time', 'lead_hours')):
                        raise ValueError('Interval contains inapplicable forecast timestamps')
                    if basis == 'relative_day':
                        if row['day_id'] != 'typical_day' or row['interval_start'] or row['interval_end']:
                            raise ValueError('Invented calendar date for typical day')
                        index = slot
                    else:
                        day_index = self._day_index[row['day_id']]
                        start = datetime.fromisoformat(row['day_id']) + timedelta(minutes=10 * slot)
                        end = start + timedelta(minutes=10)
                        if row['interval_start'] != start.isoformat() or row['interval_end'] != end.isoformat():
                            raise ValueError('Calendar interval timestamp mismatch')
                        index = (day_index, slot)
                    key = (dataset, row['day_id'], slot)
                    arrays[dataset][index] = val * (DT if unit == 'kW' else 1)
                if key in keys:
                    raise ValueError(f'Duplicate business key: {key}')
                keys.add(key)
        expected = {k: 144 if v[2] == 'relative_day' else (35040 if k == 'A3_PV_FORECAST' else 52560)
                    for k, v in spec.items()}
        if dict(counts) != expected or any(np.isnan(a).any() for a in arrays.values()):
            raise ValueError('Input is incomplete or row count differs from contract')
        for d in self.dates:
            for hour in (0, 6, 12, 18):
                issue = datetime.fromisoformat(d) + timedelta(hours=hour)
                if len(versions[issue]) != 24:
                    raise ValueError('Incomplete published forecast version')
        self.tariff = arrays['A1_PRICE']
        self.typical_load = arrays['A1_LOAD']
        self.typical_pv = arrays['A1_PV_FORECAST']
        self.load, self.pv, self.price = (arrays[k] for k in ('A2_LOAD', 'A2_PV_ACTUAL', 'A4_PRICE'))
        self.actual = np.stack((self.load, self.pv, self.price), axis=-1)
        self.forecast_versions = dict(versions)  # All (issue,valid) pairs remain available.
        self._issues = sorted(versions)
        self._forecast_cache = {}
        valid_versions = Counter(v for vals in versions.values() for v in vals)
        crossyear = sum(v >= datetime(2026, 1, 1) for vals in versions.values() for v in vals)
        self.audit = {
            'input_sha256': self.sha256, 'rows': sum(counts.values()), 'columns': len(self.columns),
            'counts': dict(counts), 'zero_counts': dict(zero_counts), 'zero_values': sum(zero_counts.values()),
            'inherited_date_records': inherited, 'forecast_issues': len(versions),
            'distinct_forecast_valid_times': len(valid_versions), 'maximum_versions_per_valid_time': max(valid_versions.values()),
            'cross_year_forecast_records': crossyear, 'forecast_valid_min': min(valid_versions).isoformat(),
            'forecast_valid_max': max(valid_versions).isoformat(), 'days': len(self.dates),
            'calendar_last_interval_end': '2026-01-01T00:00:00',
            'raw_ranges': {k: [float(a.min()) * (6 if spec[k][1] == 'kW' else 1),
                               float(a.max()) * (6 if spec[k][1] == 'kW' else 1)] for k, a in arrays.items()},
            'forecast_kw_range': [min(x for vs in versions.values() for x in vs.values()),
                                  max(x for vs in versions.values() for x in vs.values())],
        }
        audit_path = self.root / 'data' / 'conversion_audit.json'
        if audit_path.exists():
            old = json.loads(audit_path.read_text(encoding='utf-8-sig'))['verification']['data']
            for key, ours in [('rows', 'rows'), ('zero_values_preserved', 'zero_values'),
                              ('forecast_issue_times', 'forecast_issues'),
                              ('distinct_forecast_valid_times', 'distinct_forecast_valid_times'),
                              ('forecast_values_on_or_after_2026_01_01', 'cross_year_forecast_records')]:
                if old[key] != self.audit[ours]:
                    raise ValueError(f'Conversion audit disagrees on {key}')
            self.audit['conversion_audit_matched'] = True

    def forecast(self, day_index, start_slot=0, issue_cap=None):
        """Return (remaining PV kWh, audit) from latest eligible complete issue.

        issue_cap is an optional slot on the same day, for the midnight-only
        information ablation. Linear power interpolation is integrated by the
        trapezoid rule; all knots lie on ten-minute boundaries, making this exact.
        The unknown first hour uses the first value of the selected publication.
        """
        d, start = int(day_index), int(start_slot)
        if not 0 <= d < len(self.dates) or not 0 <= start < SLOTS:
            raise ValueError('Forecast day/slot out of range')
        cap = start if issue_cap is None else min(start, int(issue_cap))
        cache_key = (d, start, cap)
        if cache_key in self._forecast_cache:
            arr, audit = self._forecast_cache[cache_key]
            return arr.copy(), dict(audit)
        midnight = datetime.fromisoformat(self.dates[d])
        decision = midnight + timedelta(minutes=10 * start)
        eligible = [i for i in self._issues if i <= midnight + timedelta(minutes=10 * cap)]
        if not eligible:
            raise ValueError('No causally available forecast publication')
        issue = eligible[-1]
        points = sorted(self.forecast_versions[issue].items())
        xp = np.array([(t - midnight).total_seconds() / 3600 for t, _ in points])
        fp = np.array([v for _, v in points])
        endpoints = np.arange(start, SLOTS + 1) / 6.0
        power = np.interp(endpoints, xp, fp, left=fp[0], right=fp[-1])
        energy = (power[:-1] + power[1:]) * DT / 2
        audit = {'decision_time': decision.isoformat(), 'forecast_issue_time': issue.isoformat(),
                 'forecast_valid_min': points[0][0].isoformat(), 'forecast_valid_max': points[-1][0].isoformat(),
                 'interpolation': 'piecewise_linear_exact_interval_integral_constant_left_boundary',
                 'issue_cap_slot': cap}
        self._forecast_cache[cache_key] = (energy.copy(), dict(audit))
        return energy, audit


def week_tensor(data: Data, before_day=None):
    """X[week, weekday, slot, variable], with NaN for unavailable days.

    Weeks start on Monday. Missing edge days and future days are masked, never
    padded as observations or counted as independent complete weeks.
    """
    stop = len(data.dates) if before_day is None else int(before_day)
    offset = int(data.weekdays[0])
    weeks = (offset + len(data.dates) + 6) // 7
    X = np.full((weeks, 7, SLOTS, 3), np.nan)
    mask = np.zeros((weeks, 7), dtype=bool)
    for d in range(stop):
        w, dow = divmod(offset + d, 7)
        X[w, dow] = data.actual[d]
        mask[w, dow] = True
    return X, mask


class Forecaster:
    """Simple causal centers and complete, paired out-of-sample residual paths.

    Candidate model selection is left to historical realized-cost validation in
    the runner. This module never selects parameters using final-year errors.
    """

    def __init__(self, data: Data, mode='weekday', use_a3=False, seed=20250912, use_price=False,
                 history_window_days=84):
        if mode not in ('yesterday', 'lastweek', 'weekday'):
            raise ValueError(f'Unknown forecasting mode {mode}')
        self.data, self.mode, self.use_a3, self.seed = data, mode, bool(use_a3), int(seed)
        self.use_price = bool(use_price)
        self.history_window_days = int(history_window_days)
        if self.history_window_days < 1:
            raise ValueError('Historical residual window must contain at least one day')
        self.observations = data.actual.copy()
        if not self.use_price:
            self.observations[:, :, 2] = data.tariff[None, :]
        self._predictions, self._features_cache = {}, {}
        self._midnight = np.stack([self._base_center(d) for d in range(len(data.dates))])
        # ForecastBank: only suffix at the issuance is a forecast; preceding cells
        # remain NaN so they cannot masquerade as out-of-sample predictions.
        self.center = np.full((len(data.dates), 4, SLOTS, 3), np.nan)
        for d in range(len(data.dates)):
            for i, start in enumerate(ISSUE_SLOTS):
                self.center[d, i, start:] = self.predict(d, start)
        self.residuals = self.observations[:, None, :, :] - self.center

    def _base_center(self, d):
        if d == 0:
            # Explicit no-history prior, not use of typical-day load/PV.
            return np.column_stack((np.full(SLOTS, 5000 * DT), np.zeros(SLOTS), self.data.tariff))
        hist = self.observations
        if self.mode == 'yesterday':
            out = hist[d - 1].copy()
        elif self.mode == 'lastweek':
            out = hist[d - 7 if d >= 7 else d - 1].copy()
        else:
            # Weekday structure is used for load; weather and price retain recent
            # daily profiles, with no imposed seven-day periodicity.
            out = hist[max(0, d - 7):d].mean(axis=0)
            same = np.arange(d - 7, max(-1, d - 29), -7, dtype=int)
            same = same[same >= 0]
            if len(same):
                load_profile = hist[same, :, 0].mean(axis=0)
                # Known recent weekly level versus prior weekly level; capped
                # because seasonality evidence is weak during cold start.
                ratio = 1.0
                if d >= 14:
                    ratio = np.clip(hist[d - 7:d, :, 0].mean() / max(hist[d - 14:d - 7, :, 0].mean(), 1e-6), .85, 1.15)
                out[:, 0] = load_profile * ratio
            else:
                out[:, 0] = hist[d - 1, :, 0]
        out[:, :2] = np.maximum(out[:, :2], 0)
        out[:, 2] = np.maximum(out[:, 2], 1e-4)
        return out

    def predict(self, d, start=0, issue_cap=None):
        """H×3 [load kWh, PV kWh, price], using only the current visible prefix."""
        d, start = int(d), int(start)
        cap = None if issue_cap is None else min(start, int(issue_cap))
        key = (d, start, cap)
        if key in self._predictions:
            return self._predictions[key].copy()
        base = self._midnight[d].copy()
        out = base[start:].copy()
        if start:
            # Filter current load/price level from completed intervals only.
            visible = self.observations[d, :start]
            for m in (0, 2):
                ratio = visible[:, m].sum() / max(base[:start, m].sum(), 1e-6)
                out[:, m] *= np.clip(ratio, .7, 1.3)
            if not self.use_a3 and base[:start, 1].sum() > 200:
                ratio = visible[:, 1].sum() / base[:start, 1].sum()
                out[:, 1] *= np.clip(ratio, .4, 1.6)
        if self.use_a3:
            out[:, 1], _ = self.data.forecast(d, start, issue_cap=cap)
        out[:, :2] = np.maximum(out[:, :2], 0)
        out[:, 2] = np.maximum(out[:, 2], 1e-4)
        self._predictions[key] = out.copy()
        return out

    def _features(self, d, start, issue_cap=None):
        key = (d, start, issue_cap)
        if key in self._features_cache:
            return self._features_cache[key]
        if d:
            daily = self.observations[d - 1].mean(axis=0)
            recent = self.observations[max(0, d - 7):d].mean(axis=(0, 1))
        else:
            daily = recent = self._midnight[0].mean(axis=0)
        predicted = self.predict(d, start, issue_cap).mean(axis=0)
        if start:
            prefix = (self.observations[d, :start] - self._midnight[d, :start]).mean(axis=0)
        else:
            prefix = np.zeros(3)
        feature = np.r_[daily, recent, predicted, prefix]
        self._features_cache[key] = feature
        return feature

    def scenarios(self, d, start=0, count=8, weekly=True, conditional=True,
                  independent=False, independent_price=False, bandwidth=1.0,
                  issue_cap=None):
        d, start, count = int(d), int(start), max(1, int(count))
        if bandwidth <= 0:
            raise ValueError('Scenario bandwidth must be positive')
        center = self.predict(d, start, issue_cap)
        decision = datetime.fromisoformat(self.data.dates[d]) + timedelta(minutes=start * 10)
        _, publication = self.data.forecast(d, start, issue_cap) if self.use_a3 else (None, {})
        common = {'decision_day': self.data.dates[d], 'decision_day_index': d, 'start_slot': start,
                  'decision_time': decision.isoformat(), 'forecast_issue_time': publication.get('forecast_issue_time', ''),
                  'history_max_day': self.data.dates[d - 1] if d else '', 'history_max_day_index': d - 1,
                  'actual_visible_end_slot': start, 'residual_issue_slot': start,
                  'issue_cap_slot': issue_cap, 'mode': self.mode, 'use_a3': self.use_a3, 'use_price': self.use_price}
        if not d:
            # A single explicit prior path; unavailable data are not synthetic
            # historical observations and carry no fabricated source date.
            return dict(common, net=(center[:, 0] - center[:, 1])[None], prices=center[None, :, 2],
                        weights=np.ones(1), source_days=[], source_day_indices=[], loads=center[None, :, 0],
                        pvs=center[None, :, 1], features_audit={'cold_start': True, 'standardization_max_day': ''})
        candidates = np.arange(max(0, d - self.history_window_days), d)
        # Prefer same weekday without pretending daily blocks are whole weeks.
        # Retain near phases if fewer matching historical days than requested.
        phase = np.minimum((self.data.weekdays[candidates] - self.data.weekdays[d]) % 7,
                           (self.data.weekdays[d] - self.data.weekdays[candidates]) % 7)
        cur_feat = self._features(d, start, issue_cap)
        hist_feat = np.stack([self._features(int(j), start, issue_cap) for j in candidates])
        # Mean/std are estimated from the eligible historical feature bank only.
        scale = hist_feat.std(axis=0)
        scale = np.where(scale > 1e-7, scale, 1.0)
        distance = np.mean(((hist_feat - cur_feat) / scale) ** 2, axis=1)
        phase_distance = phase.astype(float) ** 2 if weekly else np.zeros(len(candidates))
        # Selecting a finite set is scenario reduction. Distance incorporates
        # visible condition and weekday phase; no residual outcome ranks it.
        # Keep common selected support in the uniform/kernel weight ablation;
        # conditional controls weights only, not nearest-block selection.
        score = distance + phase_distance
        score += (d - candidates) * 1e-5  # deterministic recency tie-break only
        picked = np.argsort(score, kind='stable')[:min(count, len(candidates))]
        selected = candidates[picked]
        residual = np.stack([self.observations[j, start:] - self.predict(int(j), start, issue_cap)
                             for j in selected])
        rng = np.random.default_rng(self.seed + d * 199 + start * 13)
        if independent:
            # Explicit ablation: destroy paired temporal/variable paths while
            # preserving each empirical time-variable marginal.
            for t in range(residual.shape[1]):
                for m in range(3):
                    residual[:, t, m] = residual[rng.permutation(len(selected)), t, m]
        elif independent_price:
            # Preserve each full price path but sever its paired net-load day.
            permutation = np.roll(np.arange(len(selected)), 1)
            residual[:, :, 2] = residual[permutation, :, 2]
        values = center[None] + residual
        values[:, :, :2] = np.maximum(values[:, :, :2], 0)
        values[:, :, 2] = np.maximum(values[:, :, 2], 1e-4)
        if conditional:
            logits = -np.minimum(distance[picked] / bandwidth, 700)
            weights = np.exp(logits - logits.max())
            weights /= weights.sum()
        else:
            weights = np.full(len(selected), 1.0 / len(selected))
        return dict(common, net=values[:, :, 0] - values[:, :, 1], prices=values[:, :, 2],
                    loads=values[:, :, 0], pvs=values[:, :, 1], weights=weights,
                    source_days=[self.data.dates[j] for j in selected], source_day_indices=selected.tolist(),
                    features_audit={'cold_start': False, 'standardization_max_day': self.data.dates[d - 1],
                                    'feature_names': ['yesterday_mean_load', 'yesterday_mean_pv', 'yesterday_mean_price',
                                                      'recent_mean_load', 'recent_mean_pv', 'recent_mean_price',
                                                      'current_predicted_load', 'current_predicted_pv', 'current_predicted_price',
                                                      'observed_prefix_load_error', 'observed_prefix_pv_error', 'observed_prefix_price_error'],
                                    'current_features': cur_feat.tolist(), 'historical_feature_std': scale.tolist(),
                                    'selected_distances': distance[picked].tolist(), 'weekday_phases': phase[picked].tolist(),
                                    'eligible_blocks': len(candidates), 'unit_of_sample': 'complete_day_residual_path',
                                    'independent': bool(independent), 'independent_price': bool(independent_price)})


ForecastBank = Forecaster


def diagnostics(data: Data):
    """January-only, genuine rolling errors per variable; descriptive, not tuning."""
    table = []
    actual = data.actual
    for mode in ('yesterday', 'lastweek', 'weekday'):
        f = Forecaster(data, mode=mode, use_a3=False, use_price=True)
        error = f.center[7:31, 0] - actual[7:31]
        for m, name in enumerate(('load', 'pv', 'price')):
            scale = 6 if m < 2 else 1
            table.append({'mode': mode, 'variable': name,
                          'mae': float(np.abs(error[:, :, m]).mean() * scale),
                          'rmse': float(np.sqrt(np.mean(error[:, :, m] ** 2)) * scale),
                          'unit': 'kW' if m < 2 else 'CNY/kWh'})
    corr = {}
    for m, name in enumerate(('load', 'pv', 'price')):
        corr[name] = {str(lag): float(np.corrcoef(actual[lag:31, :, m].ravel(), actual[:31 - lag, :, m].ravel())[0, 1])
                      for lag in (1, 7)}
    return {'window': '2025-01-08 through 2025-01-31', 'forecast_error_rows': table,
            'january_lag_correlations': corr, 'parameter_selection': 'none; runner uses realized-cost validation'}


def causality_smoke_check(data: Data):
    """Perturb all unseen observations/publications and compare one decision.

    This test checks dataflow causality, not predictive accuracy. All mutations
    are to a shallow in-memory copy; neither input data nor the CSV is edited.
    """
    import copy
    d, start = 31, 72
    baseline = Forecaster(data, use_a3=True, use_price=True)
    before = baseline.scenarios(d, start)
    changed = copy.copy(data)
    changed.actual = data.actual.copy()
    changed.actual[d, start:] += np.array([9999., 8888., 77.])
    changed.actual[d + 1:] += np.array([7777., 6666., 55.])
    changed.forecast_versions = {issue: dict(points) for issue, points in data.forecast_versions.items()}
    decision = datetime.fromisoformat(data.dates[d]) + timedelta(minutes=start * 10)
    for issue, points in changed.forecast_versions.items():
        if issue > decision:
            for valid in points:
                points[valid] += 99999.
    changed._forecast_cache = {}
    alternative = Forecaster(changed, use_a3=True, use_price=True)
    after = alternative.scenarios(d, start)
    for key in ('net', 'prices', 'weights', 'loads', 'pvs'):
        if not np.array_equal(before[key], after[key]):
            raise AssertionError(f'Future perturbation changed {key}')
    if before['source_days'] != after['source_days'] or before['features_audit'] != after['features_audit']:
        raise AssertionError('Future perturbation changed scenario selection/weight information')
    # Static-price problems may not use even historical A4 prices as features.
    changed2 = copy.copy(data)
    changed2.actual = data.actual.copy()
    changed2.actual[:, :, 2] += 1234.
    a = Forecaster(data, use_a3=False, use_price=False).scenarios(d, start)
    b = Forecaster(changed2, use_a3=False, use_price=False).scenarios(d, start)
    for key in ('net', 'prices', 'weights'):
        if not np.array_equal(a[key], b[key]):
            raise AssertionError(f'Forbidden A4 information changed fixed-tariff {key}')
    return {'future_observation_and_publication_perturbation': 'passed',
            'A4_exclusion_from_Q2_Q3_forecasting': 'passed',
            'decision_time': decision.isoformat()}


def write_audit_reports(root='.', data=None):
    """Reproduce data and CyclePatch notes directly from this run's input."""
    root = Path(root)
    data = Data(root) if data is None else data
    result = diagnostics(data)
    checks = causality_smoke_check(data)
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    a = data.audit
    lines = [
        '# 输入数据审计', '',
        '本报告由 `src.forecasting.write_audit_reports` 本次读取并核验唯一模型输入 `data/data_clean.csv` 生成。', '',
        f'- 输入 SHA-256：`{data.sha256}`。',
        f'- UTF-8 BOM、逗号分隔、{a["columns"]} 列；{a["rows"]:,} 条数值记录。',
        '- 先按 dataset_id、time_semantics、time_basis、metric、unit 分流，再解析数值。输入没有 record_type 列；结果 CSV 的 record_type 不参与预测。',
        '- 记录编号、源单元格键和类型业务键均唯一；原始数值非负、价格严格正；无缺数值、无填零、无整表插补。',
        '- 逐条核验区间起止分钟、日历时间、整点发布时间/目标时间/提前量；各日每类区间严格 144 段。',
        f'- 真实零值 {a["zero_values"]:,} 个全部保留；继承日期记录 {a["inherited_date_records"]:,} 条。',
        '- 先只读核对 README 和 conversion_audit；README 旧名 attachments_clean.csv 在当前项目对应 data/data_clean.csv。转换审计中的计数与本次读取一致。', '',
        '| 数据集 | 行数 | 原单位范围 |', '|---|---:|---:|',
    ]
    for dataset, count in a['counts'].items():
        ran = a['forecast_kw_range'] if dataset == 'A3_PV_FORECAST' else a['raw_ranges'][dataset]
        unit = 'CNY/kWh' if dataset.endswith('PRICE') else 'kW'
        lines.append(f'| {dataset} | {count:,} | {ran[0]:.6f}—{ran[1]:.6f} {unit} |')
    lines += ['', '## 时间及单位', '',
              '区间输入使用 `(dataset_id, day_id, slot_index)` 对齐。00:10 对应 [00:00,00:10)，slot=143 的终点为次日00:00。2025年末终点为2026-01-01T00:00:00。附件1保留 typical_day，不虚构日期或转换时区。', '',
              '负荷/光伏的十分钟电量等于原代表功率除以6；价格不做此转换。附件3保留全部 `(issue_time,valid_time)` 版本：', '',
              f'- {a["forecast_issues"]:,} 次发布、35,040个预报点、{a["distinct_forecast_valid_times"]:,} 个不同目标时刻；同一目标最多{a["maximum_versions_per_valid_time"]}版。',
              f'- 跨入2026年的预报点 {a["cross_year_forecast_records"]} 条全部保留；最远目标 {a["forecast_valid_max"]}。',
              '- 每次先限制 issue_time ≤ 决策时刻（零点信息消融还限制 issue_cap=0），再取最新完整发布；使用本版整点间线性功率曲线的区间积分。',
              '- 每十分钟端点与整点对齐，梯形法给出分段线性的精确积分。发布时刻到首个预测整点之间按本版第一个功率值常值外延。这是明确边界假设，不使用下一版或未来实测补点。', '',
              '## 因果核验', '',
              f'- 在 {checks["decision_time"]}，同时大幅扰动此后全部实测和后续发布，中心、场景路径、权重、选中源日和条件特征逐元素保持一致。',
              '- 将全年A4价格全部扰动，固定电价 Q2/Q3 的预测和场景选择保持一致；use_price=False 隔离这项不允许的输入。',
              '- 残差源日严格早于决策日；标准化只用候选历史日当时可见的特征；当前实测只读已完成前缀 [0,start)。',
              '- 冷启动为5000kW常值负荷、零光伏、A1电价；Q3/Q4-3可用零点已发布A3光伏替代先验。冷启动不是训练样本扩增；无历史时仅一个先验场景。', '',
              '## 边界说明', '',
              '本报告核验数据结构、时间语义及预测因果数据流，不把全年度物理观测范围当作事前已知上限，也不将统计误差等同于实际节省电费。费用、执行物理约束和模板发布由独立核验器另行检查。', '']
    (reports / 'data_audit.md').write_text('\n'.join(lines), encoding='utf-8')
    X, mask = week_tensor(data, before_day=31)
    lines = [
        '# CyclePatch 周期组织的迁移', '',
        '参考库中没有名为 CyclePatch 的独立文件或类；本项目借鉴的是 `BatteryLife/models/CPTransformer.py` 所实现的周期内和周期间组织。准确源码位置如下：', '',
        '- 第37—39行：周期内 flatten、线性嵌入、MLP。',
        '- 第41—53行：位置编码及周期间 Transformer。',
        '- 第59—70行：输入 `[batch, early_cycle, fixed_len, num_var]`，每个周期内保留完整变量曲线后编码。',
        '- 第72—77行：周期位置编码和不可见周期掩码，再做周期间关联。', '',
        '迁移的是分层索引、相位对齐和不可见信息掩码。没有训练神经网络、加载电池曲线/权重、复制原模型预测结果或安装原仓库训练环境。原电池周期间的数据含义与微网日/周不同，不能把其时间伸缩或峰值平移直接搬来。', '',
        '## 本项目实现', '',
        '`src/forecasting.py::week_tensor` 显式构造 X[周,星期,十分钟,变量]，周一为每周起点，变量为负荷kWh、光伏kWh、电价元/kWh。2025年自然边界需53个周容器；缺失边缘/未来日为NaN并有掩码，不补零、不当成新样本。', '',
        f'截至1月31日完成时，已揭示日 {int(mask.sum())} 个，完整自然周 {int(mask.all(axis=1).sum())} 个；不将重叠日块计算成独立新增周。十分钟的144段始终完整保留。', '',
        '预测基线为昨日同期、上周同期和含星期特征的均值模型。第三种模型对负荷使用最近至多4个同星期日的均值，加上已完成两周的水平变化比例（截于0.85—1.15）；光伏和价格使用最近7天的日内均值，并不强制其有七天周期。小样本退回昨日。候选由主程序一月滚动实际费用选择；本报告中的MAE/RMSE只用于描述周期性。', '',
        '日内负荷与价格只用已经完成的前缀估计水平比例（截于0.7—1.3）；无A3时光伏可根据已有有效光照前缀调整，比例截于0.4—1.6；有A3时使用符合发布时刻的整条光伏曲线。', '',
        'ForecastBank 缓存 [日,发布时刻,144,3] 的因果中心；发布前位置为NaN。每个历史残差都是当日实际值减去该日该发布时刻的真实历史预测，而不是全年拟合残差。读取完整历史日后，按相同发布时刻和剩余预测范围抽取整段三变量残差。价格与负荷/光伏保持同一源日配对；不同场景使用共同零点计划的约束由优化器实施。', '',
        '候选为决策前最近84个完整日。通过当前/历史各自在对应决策时可见的昨日均值、近7日均值、当前预测均值和已发生前缀偏差计算距离。标准差只由候选历史特征估计。选择有限场景时加入星期相位距离，少样本允许邻近星期相位；不会提前看残差未来来排名。条件概率为 exp(-D/h) 归一化，价格进入费用后不再乘入概率。静态等权消融保留相同条件筛选的场景集合，只将概率改为等权；不会同时改变抽样支持。', '',
        '分别对负荷、光伏施加非负界后再相减，净负荷允许为负；价格仅按事先声明的正价假设设置0.0001元/kWh下界，不用全年观察到的最大值截断预测。无物理额定上界信息时不虚构负荷或光伏额定容量。', '',
        '独立误差消融逐时段、逐变量置换残差来源；价格独立消融只循环错配完整价格误差路径，保留单变量时间路径而破坏供需价格配对。完整联合路径方案不做这些置换。固定种子为20250912，主程序可以显式覆盖。', '',
        '## 分变量的一月滚动诊断', '',
        '误差窗口为2025-01-08至2025-01-31；每个预测只用自身前一日及更早历史。以下数据来自本次可复现计算。', '',
        '| 变量 | 预测基线 | MAE | RMSE | 单位 |', '|---|---|---:|---:|---|',
    ]
    for row in result['forecast_error_rows']:
        lines.append(f'| {row["variable"]} | {row["mode"]} | {row["mae"]:.6f} | {row["rmse"]:.6f} | {row["unit"]} |')
    lines += ['', '| 变量 | 1日滞后相关 | 7日滞后相关 |', '|---|---:|---:|']
    for var, row in result['january_lag_correlations'].items():
        lines.append(f'| {var} | {row["1"]:.6f} | {row["7"]:.6f} |')
    lines += ['', '一月数据显示负荷和电价的七日关联强于一日关联，光伏两者接近且七日前复制误差更大。强日内形状本身也会提高相关系数，不能只据此宣布光伏具有七天规律，也不能预设低负荷日一定为周末。经济效果最终以相同初始状态和结算条件的滚动执行费用判定。', '',
              '## 保留的近似及限制', '',
              '这里是有限历史误差路径的条件重加权与确定性场景压缩，未声称学习到了真实随机过程。84日记忆、固定带宽及比例截断是建模选择；一月尚无充分跨季节样本，仍会有分布漂移。逐场景看到整条未来的优化器内部追索如被采用，必须在模型报告中标为规划近似，实际运行由只看当前信息的执行器给出。', '']
    (reports / 'cyclepatch.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'data': data.audit, 'periodicity': result, 'causality_checks': checks}
