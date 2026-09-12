"""Information-set and scenario-ablation checks using the real typed input.

The expensive CSV parse is shared; only a few decisions are evaluated. These
tests do not re-run the full-year dispatch or use forecast accuracy assertions.
"""
from datetime import datetime
from pathlib import Path
import unittest

import numpy as np

from src.forecasting import Data, Forecaster, causality_smoke_check, week_tensor


class ForecastingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Data(Path(__file__).resolve().parents[1])
        cls.forecaster = Forecaster(cls.data, use_a3=True, use_price=True)

    def test_unseen_actuals_publications_and_forbidden_A4_cannot_change_decisions(self):
        # This helper creates fresh forecasters after perturbing same-day unseen
        # intervals, later days and later publications; no stale caches mask a leak.
        result = causality_smoke_check(self.data)
        self.assertEqual(result['future_observation_and_publication_perturbation'], 'passed')
        self.assertEqual(result['A4_exclusion_from_Q2_Q3_forecasting'], 'passed')

    def test_published_hourly_points_integrate_causally_with_frozen_issue_ablation(self):
        day, start = 171, 72
        energy, audit = self.data.forecast(day, start)
        issue = datetime.fromisoformat(audit['forecast_issue_time'])
        points = sorted(self.data.forecast_versions[issue].items())
        first, second = points[0][1], points[1][1]
        self.assertEqual(issue.isoformat(), self.data.dates[day] + 'T12:00:00')
        self.assertEqual(len(energy), 72)
        # The first unknown hour is explicitly constant at this issue's first
        # prediction. Next interval integrates the rising/falling straight line.
        np.testing.assert_allclose(energy[:6], first / 6, rtol=0, atol=1e-10)
        self.assertAlmostEqual(energy[6], (first + (second - first) / 12) / 6, places=9)
        frozen, frozen_audit = self.data.forecast(day, start, issue_cap=0)
        midnight, midnight_audit = self.data.forecast(day, 0)
        self.assertEqual(frozen_audit['forecast_issue_time'], self.data.dates[day] + 'T00:00:00')
        self.assertEqual(frozen_audit['forecast_issue_time'], midnight_audit['forecast_issue_time'])
        np.testing.assert_array_equal(frozen, midnight[start:])

    def test_weight_ablation_has_common_support_and_uniform_shuffling_keeps_marginals(self):
        args = dict(d=100, start=36, count=8)
        uniform = self.forecaster.scenarios(**args, conditional=False)
        kernel = self.forecaster.scenarios(**args, conditional=True)
        self.assertEqual(uniform['source_days'], kernel['source_days'])
        np.testing.assert_array_equal(uniform['net'], kernel['net'])
        np.testing.assert_array_equal(uniform['prices'], kernel['prices'])
        np.testing.assert_allclose(uniform['weights'], 1 / 8, rtol=0, atol=0)
        self.assertAlmostEqual(kernel['weights'].sum(), 1., places=13)
        self.assertTrue(np.all(kernel['weights'] >= 0))
        shuffled = self.forecaster.scenarios(**args, conditional=False, independent=True)
        for key in ('loads', 'pvs', 'prices'):
            # Empirical marginal values at each time remain exactly identical.
            np.testing.assert_array_equal(np.sort(uniform[key], axis=0),
                                          np.sort(shuffled[key], axis=0))
        price_shuffled = self.forecaster.scenarios(**args, conditional=False, independent_price=True)
        np.testing.assert_array_equal(price_shuffled['net'], uniform['net'])
        # Price independence swaps complete paths, rather than scrambling hours.
        np.testing.assert_array_equal(price_shuffled['prices'], np.roll(uniform['prices'], 1, axis=0))

    def test_week_masks_and_history_window_never_manufacture_observations(self):
        X, mask = week_tensor(self.data, before_day=31)
        self.assertEqual(X.shape, (53, 7, 144, 3))
        self.assertEqual(mask.sum(), 31)
        self.assertEqual(mask.all(axis=1).sum(), 3)
        self.assertTrue(np.isnan(X[~mask]).all())
        np.testing.assert_array_equal(X[0, 2], self.data.actual[0])
        f = Forecaster(self.data, use_a3=False, use_price=False, history_window_days=3)
        scene = f.scenarios(31, 0, count=8)
        self.assertEqual(set(scene['source_day_indices']), {28, 29, 30})
        self.assertEqual(scene['features_audit']['eligible_blocks'], 3)
        cold = f.scenarios(0, 0)
        self.assertEqual(cold['source_days'], [])
        self.assertEqual(cold['history_max_day_index'], -1)
        self.assertTrue(cold['features_audit']['cold_start'])
        self.assertEqual(cold['net'].shape, (1, 144))
        np.testing.assert_array_equal(cold['prices'][0], self.data.tariff)


if __name__ == '__main__':
    unittest.main()
