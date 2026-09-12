"""Economic benchmark tests with a separate one-period linear program.

These tests deliberately omit storage and terminal value. The resulting
quantiles must never be substituted for the coupled storage optimizer.
"""
import unittest

import numpy as np
from scipy.optimize import linprog


def one_period_lp(demand, probability, price, original=None):
    """Solve purchasing plus scenario shortage cost independently of main code."""
    demand = np.asarray(demand, dtype=float)
    probability = np.asarray(probability, dtype=float)
    price = np.broadcast_to(np.asarray(price, dtype=float), demand.shape)
    m = len(demand)
    count = 1 + m + (original is not None)
    objective = np.zeros(count)
    objective[0] = probability @ price
    objective[1:1 + m] = 5 * probability * price
    inequality = np.zeros((m + (2 if original is not None else 0), count))
    rhs = np.zeros(len(inequality))
    for j, n in enumerate(demand):
        inequality[j, 0] = -1
        inequality[j, 1 + j] = -1
        rhs[j] = -n
    if original is not None:
        objective[-1] = 0.5 * (probability @ price)
        inequality[m, 0] = 1
        inequality[m, -1] = -1
        rhs[m] = original
        inequality[m + 1, 0] = -1
        inequality[m + 1, -1] = -1
        rhs[m + 1] = -original
    solved = linprog(objective, A_ub=inequality, b_ub=rhs,
                     bounds=(0, None), method="highs")
    if not solved.success:
        raise AssertionError(solved.message)
    return solved.x[0], solved.fun


class EconomicBenchmarks(unittest.TestCase):
    def setUp(self):
        self.demand = np.array([10., 20., 30., 40., 50.])
        self.probability = np.array([.05, .10, .25, .45, .15])

    def test_no_storage_asymmetric_cost_selects_eightieth_quantile(self):
        # F(30)=0.40 < 0.8 < F(40)=0.85: optimum is uniquely 40.
        q, cost = one_period_lp(self.demand, self.probability, 2.)
        self.assertAlmostEqual(q, 40.)
        self.assertAlmostEqual(cost, 95.)

    def test_revision_seventieth_to_ninetieth_quantile_band(self):
        # Q(.7)=40, Q(.9)=50; increasing, retaining and decreasing cases.
        for original, expected in [(25., 40.), (45., 45.), (80., 50.)]:
            with self.subTest(original=original):
                q, _ = one_period_lp(self.demand, self.probability, 1., original)
                self.assertAlmostEqual(q, expected)

    def test_high_price_and_shortage_joint_tail_changes_procurement(self):
        fixed_q, _ = one_period_lp(self.demand, self.probability, 1.)
        prices = np.array([1., 1., 1., 1., 12.])
        random_q, _ = one_period_lp(self.demand, self.probability, prices)
        self.assertAlmostEqual(fixed_q, 40.)
        self.assertAlmostEqual(random_q, 50.)
        cumulative_price_mass_at_40 = (.85 / (self.probability @ prices))
        self.assertLess(cumulative_price_mass_at_40, .8)

    def test_revision_downward_contract_example(self):
        # Demand is exactly 80; LP chooses 80 from original 100. Total is
        # 80 for retained energy + 10 cancellation, and no emergency bill.
        q, cost = one_period_lp([80.], [1.], 1., original=100.)
        self.assertAlmostEqual(q, 80.)
        self.assertAlmostEqual(cost, 90.)

    def test_unused_information_cannot_worsen_the_exact_feasible_optimum(self):
        original = 45.
        q, optimal_cost = one_period_lp(self.demand, self.probability, 1., original)
        keep_cost = original + 5 * (self.probability @ np.maximum(self.demand - original, 0.))
        self.assertLessEqual(optimal_cost, keep_cost + 1e-9)
        self.assertAlmostEqual(q, original)


if __name__ == "__main__":
    unittest.main()
