"""Physical and causal-policy tests distinct from the accounting verifier."""
import unittest

import numpy as np

from src.optimization import (ETA_C, ETA_D, E_MIN, E_MAX, P_MAX,
                              eliminate_simultaneous, execute_step, greedy_step,
                              solve_plan, value_policy)


class OptimizationTests(unittest.TestCase):
    def test_lp_repair_preserves_inventory_and_balance(self):
        c = np.array([100., 200., 0.])
        d = np.array([180., 90., 40.])
        r = np.array([0., 30., 0.])
        cp, dp, rp = eliminate_simultaneous(c, d, r)
        np.testing.assert_allclose(ETA_C * cp - dp / ETA_D, ETA_C * c - d / ETA_D, atol=1e-12)
        np.testing.assert_allclose(dp - cp - rp, d - c - r, atol=1e-12)
        self.assertTrue(np.all(np.minimum(cp, dp) < 1e-12))
        self.assertTrue(np.all(rp >= r))

    def test_milp_and_repaired_lp_have_equal_cyclic_cost(self):
        net = np.array([700., -400., 900., 1000., 200., 800.])
        prices = np.array([.3, .2, 1.5, 1.3, .4, .9])
        lp = solve_plan(net, prices, [1.], 6000., cyclic=True, allow_emergency=False)
        integer = solve_plan(net, prices, [1.], 6000., cyclic=True, allow_emergency=False, milp=True)
        self.assertAlmostEqual(lp["objective"], integer["objective"], places=5)
        for solution in (lp, integer):
            self.assertAlmostEqual(solution["E"][0, -1], 6000., places=6)
            self.assertLess(solution["maximum_physical_error"], 1e-6)
            self.assertLess(np.max(np.minimum(solution["c"], solution["d"])), 1e-8)
            self.assertEqual(solution["u"].sum(), 0.)

    def test_downward_revision_total_is_ninety(self):
        solution = solve_plan([[80.]], [[1.]], [1.], 6000., old_plan=[100.], storage=False)
        self.assertAlmostEqual(solution["q"][0], 80.)
        self.assertAlmostEqual(solution["expected_cost"], 90.)

    def test_fixed_plan_scenario_balances_are_distinct(self):
        solution = solve_plan([[100., 200.], [300., 400.]], [[1., 2.], [2., 3.]],
                              [.4, .6], E_MIN, fixed_plan=[100., 100.], storage=False)
        np.testing.assert_allclose(solution["q"], [100., 100.])
        np.testing.assert_allclose(solution["u"], [[0., 100.], [200., 300.]])
        self.assertAlmostEqual(solution["expected_cost"], 4720.)

    def test_terminal_piecewise_cost_is_not_actual_bill(self):
        terminal = {"grid": [E_MIN, 6000., E_MAX], "value": [4800., 0., 0.]}
        solution = solve_plan([[100.]], [[1.]], [1.], 6000., terminal_value=terminal)
        self.assertAlmostEqual(solution["q"][0], 100.)
        self.assertAlmostEqual(solution["terminal_cost"], 0.)
        self.assertAlmostEqual(solution["expected_cost"], 100.)

    def test_opportunity_value_reserves_energy_for_expensive_deficit(self):
        net = np.array([[500., 500.]])
        prices = np.array([[1., 10.]])
        policy = value_policy(net, prices, [1.], [0., 0.], grid_size=161)
        E = E_MIN + 600.
        first = execute_step(500., 1., 0., E, policy["V"][1], policy["grid"])
        greedy = greedy_step(500., 1., 0., E)
        second = execute_step(500., 10., 0., first["E_next"], policy["V"][2], policy["grid"])
        greedy_second = greedy_step(500., 10., 0., greedy["E_next"])
        self.assertLess(first["d"], greedy["d"])
        self.assertLess(first["emergency_cost"] + second["emergency_cost"],
                        greedy["emergency_cost"] + greedy_second["emergency_cost"])

    def test_execution_cannot_charge_using_emergency_and_respects_capacity(self):
        grid = np.linspace(E_MIN, E_MAX, 81)
        valuable_inventory = -100. * grid
        deficit = execute_step(1000., 1., 0., 6000., valuable_inventory, grid)
        self.assertEqual(deficit["c"], 0.)
        self.assertEqual(deficit["d"], 0.)
        self.assertAlmostEqual(deficit["u"], 1000.)
        surplus = execute_step(-5000., 1., 0., E_MAX - 10., valuable_inventory, grid)
        self.assertAlmostEqual(surplus["E_next"], E_MAX)
        self.assertLessEqual(surplus["c"], P_MAX)
        self.assertEqual(surplus["d"], 0.)
        self.assertAlmostEqual(-5000. + surplus["c"] + surplus["r"], 0.)

    def test_cross_day_continuation_never_resets_inventory(self):
        grid = np.linspace(E_MIN, E_MAX, 81)
        final_slot = execute_step(-300., .5, 0., 6000., -grid, grid)
        first_next = execute_step(200., .5, 0., final_slot["E_next"], np.zeros(81), grid)
        self.assertAlmostEqual(first_next["E_next"], 6000. + 300. * ETA_C - 200. / ETA_D)

    def test_invalid_weights_and_future_price_placeholders_rejected(self):
        with self.assertRaises(ValueError):
            solve_plan([[100.]], [[1.]], [.5], 6000.)
        with self.assertRaises(ValueError):
            value_policy([[100.]], [[np.nan]], [1.], [100.])


if __name__ == "__main__":
    unittest.main()
