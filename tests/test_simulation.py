"""End-to-end information perturbations and immutable executed-plan prefixes."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from src.simulation import simulate


def small_data(days=3):
    """Synthetic observations, with the real causal forecasting implementation."""
    slots = np.arange(144)
    dates = [(datetime(2025, 1, 1) + timedelta(days=d)).strftime('%Y-%m-%d')
             for d in range(days)]
    load = np.stack([950. + 350. * np.cos((slots - 100) * np.pi / 72)
                     + d * 40. for d in range(days)])
    pv = np.stack([700. * np.maximum(0., np.sin((slots - 36) * np.pi / 72))
                   * (1. + .1 * d) for d in range(days)])
    tariff = .4 + .8 * ((slots >= 54) & (slots < 120))
    price = np.stack([tariff * (1. + .1 * d + .1 * np.sin(slots / 13.))
                     for d in range(days)])
    return SimpleNamespace(dates=dates,
                           weekdays=np.array([datetime.fromisoformat(d).weekday() for d in dates]),
                           load=load, pv=pv, price=price, tariff=tariff,
                           actual=np.stack([load, pv, price], axis=-1))


CONFIG = dict(mode='weekday', bandwidth=1., scenario_count=3, grid_size=21,
              terminal_factor=.9, revision_tolerance_cny=.01,
              seed=20250912, history_window_days=84)


class DeterministicStageForecaster:
    """Known-at-issue forecasts change sufficiently to trigger real revisions."""
    future_levels = (1200., 200., 1600., 100.)

    def __init__(self, data, **kwargs):
        self.data = data

    def scenarios(self, day, start=0, **kwargs):
        return dict(net=np.full((1, 144 - start), self.future_levels[start // 36]),
                    prices=np.ones((1, 144 - start)), weights=np.ones(1), source_days=[])


class SimulationCausalityTests(unittest.TestCase):
    def test_future_actuals_cannot_change_any_prior_action_or_issued_plan(self):
        original = small_data()
        altered = deepcopy(original)
        day, first_hidden_slot = 1, 57
        # Change all three variables after the same information boundary,
        # including every subsequent day; keep both representations in sync.
        altered.actual[day, first_hidden_slot:, :] *= np.array([4., .2, 3.])
        altered.actual[day + 1:, :, :] *= np.array([.3, 5., 2.])
        altered.load = altered.actual[:, :, 0].copy()
        altered.pv = altered.actual[:, :, 1].copy()
        altered.price = altered.actual[:, :, 2].copy()
        variant = dict(name='causality_probe', random_price=True, revisions=True)
        base = simulate(original, CONFIG, variant, days=3)
        changed = simulate(altered, CONFIG, variant, days=3)
        # Zero-point and 06:00 plans were both issued before the perturbation.
        np.testing.assert_array_equal(base['q0'][:day + 1], changed['q0'][:day + 1])
        np.testing.assert_array_equal(base['versions'][day, 0], changed['versions'][day, 0])
        for key in ('q', 'c', 'd', 'u', 'r'):
            with self.subTest(key=key):
                np.testing.assert_array_equal(base[key][:day], changed[key][:day])
                np.testing.assert_array_equal(base[key][day, :first_hidden_slot],
                                              changed[key][day, :first_hidden_slot])
        np.testing.assert_array_equal(base['E'][day, :first_hidden_slot + 1],
                                      changed['E'][day, :first_hidden_slot + 1])
        # The test is not vacuous: revealed modified demand eventually changes
        # execution, while earlier actions remain exactly identical.
        self.assertTrue(any(np.any(base[k][day, first_hidden_slot:] != changed[k][day, first_hidden_slot:])
                            for k in ('c', 'd', 'u', 'r')))

    def test_adopted_revisions_overwrite_only_unexecuted_suffix(self):
        data = small_data(days=1)
        data.load[:] = 400.
        data.pv[:] = 0.
        data.actual[:, :, 0] = data.load
        data.actual[:, :, 1] = 0.
        with patch('src.simulation.Forecaster', DeterministicStageForecaster):
            trace = simulate(data, CONFIG, dict(name='version_probe', revisions=True), days=1)
        self.assertGreaterEqual(trace['metadata']['adopted_count'], 2)
        expected = trace['q0'][0].copy()
        snapshots = []
        for i, start in enumerate((36, 72, 108)):
            version = trace['versions'][0, i]
            self.assertTrue(np.isnan(version[:start]).all())
            if np.isfinite(version[start:]).any():
                self.assertTrue(np.isfinite(version[start:]).all())
                frozen = expected[:start].copy()
                expected[start:] = version[start:]
                np.testing.assert_array_equal(expected[:start], frozen)
                snapshots.append((start, expected.copy()))
        np.testing.assert_array_equal(trace['q'][0], expected)
        np.testing.assert_array_equal(trace['q'][0, :36], trace['q0'][0, :36])
        for position, (start, snapshot) in enumerate(snapshots):
            next_start = snapshots[position + 1][0] if position + 1 < len(snapshots) else 144
            np.testing.assert_array_equal(trace['q'][0, start:next_start], snapshot[start:next_start])

    def test_month_end_selected_mode_and_bandwidth_do_not_change_january(self):
        data = small_data(days=3)
        first_config = {**CONFIG, 'mode': 'yesterday', 'bandwidth': .5}
        second_config = {**CONFIG, 'mode': 'lastweek', 'bandwidth': 2.}
        variant = dict(name='warmup_probe', random_price=True, revisions=True)
        first = simulate(data, first_config, variant, days=3)
        second = simulate(data, second_config, variant, days=3)
        for key in ('q0', 'versions', 'q', 'c', 'd', 'u', 'r', 'E'):
            with self.subTest(key=key):
                np.testing.assert_array_equal(first[key], second[key])
        np.testing.assert_array_equal(first['E'][:-1, -1], first['E'][1:, 0])

    def test_calibration_candidates_enter_validation_with_identical_inventory(self):
        data = small_data(days=16)
        first_config = {**CONFIG, 'mode': 'yesterday', 'bandwidth': .5}
        second_config = {**CONFIG, 'mode': 'lastweek', 'bandwidth': 2.}
        variant = dict(name='calibration_boundary_probe', random_price=True)
        first = simulate(data, first_config, variant, days=16, calibration=True)
        second = simulate(data, second_config, variant, days=16, calibration=True)
        # January 1--15 is common default warmup for candidate evaluation.
        for key in ('q0', 'q', 'c', 'd', 'u', 'r', 'E'):
            with self.subTest(key=key):
                np.testing.assert_array_equal(first[key][:15], second[key][:15])
        self.assertEqual(first['E'][15, 0], second['E'][15, 0])
        # Distinct candidates actually become active at the January 16 boundary.
        self.assertTrue(np.any(first['q0'][15] != second['q0'][15]))


if __name__ == '__main__':
    unittest.main()
