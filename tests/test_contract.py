"""Regression tests for the physical, causal, settlement and CSV contracts."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.results import _verify_source_alignment, write_results
from src.verification import ETA, emergency_events, summarize, verify_trace

ROOT = Path(__file__).resolve().parents[1]


def trace_fixture(dates=("2025-02-01",)):
    n = len(dates)
    trace = {key: np.zeros((n, 144)) for key in ("q0", "q", "c", "d", "u", "r", "load", "pv", "price")}
    for key in ("q0", "q", "load"):
        trace[key][:] = 100.0
    trace["price"][:] = 1.0
    trace.update(dates=list(dates), E=np.full((n, 145), 6000.0),
                 versions=np.full((n, 3, 144), np.nan), audits=[])
    return trace


class SettlementAndPhysicsTests(unittest.TestCase):
    def test_reduction_100_to_80_costs_90(self):
        trace = trace_fixture()
        trace["versions"][0, 0, 36:] = 100.0
        trace["versions"][0, 0, 120] = 80.0
        trace["q"][0, 120] = trace["load"][0, 120] = 80.0
        summary = summarize(trace)[0]
        self.assertEqual(summary["planned_settled"] - 14300, 90.0)
        self.assertEqual(summary["planned_cost"], 14400.0)
        self.assertEqual(summary["adjustment_fee"], 10.0)
        verify_trace(trace, "result3")

    def test_multiple_revisions_only_charge_signed_changes_and_fees(self):
        trace = trace_fixture()
        for v, value in enumerate((80.0, 90.0, 70.0)):
            trace["versions"][0, v, (v + 1) * 36:] = 100.0
            trace["versions"][0, v, 120] = value
        trace["q"][0, 120] = trace["load"][0, 120] = 70.0
        summary = summarize(trace)[0]
        self.assertEqual(summary["planned_settled"], 14300 + 70 + 10 + 5 + 10)
        self.assertEqual(summary["revision_delta"], -5.0)
        verify_trace(trace, "result3")

    def test_event_merge_split_midnight_and_no_event(self):
        emergency = np.zeros((2, 144))
        emergency[0, 1:3], emergency[0, 20], emergency[0, 143] = 2, 3, 4
        emergency[1, :2] = 5
        first = emergency_events(emergency[0], "2025-01-31")
        second = emergency_events(emergency[1], "2025-02-01")
        self.assertEqual([(e["start_minute"], e["end_minute"], e["value"]) for e in first],
                         [(10, 30, 4.0), (200, 210, 3.0), (1430, 1440, 4.0)])
        self.assertEqual(first[-1]["interval_end"], "2025-02-01T00:00:00")
        self.assertEqual(second[0]["interval_start"], "2025-02-01T00:00:00")
        self.assertEqual(emergency_events(np.zeros(144), "2025-02-01"), [])

    def test_cross_day_storage_continuity_rejects_reset(self):
        trace = trace_fixture(("2025-01-01", "2025-01-02"))
        trace["d"][0, -1] = 10.0
        trace["q"][0, -1] = trace["q0"][0, -1] = 90.0
        trace["E"][0, -1] -= 10.0 / ETA
        trace["E"][1, :] = trace["E"][0, -1]
        verify_trace(trace, "result2")
        trace["E"][1, :] = 6000.0
        with self.assertRaisesRegex(ValueError, "midnight"):
            verify_trace(trace, "result2")

    def test_future_forecast_and_history_rejected(self):
        trace = trace_fixture()
        trace["audits"] = [{"decision_time": "2025-02-01T06:00:00",
                            "issue_time": "2025-02-01T12:00:00"}]
        with self.assertRaisesRegex(ValueError, "future forecast"):
            verify_trace(trace, "result3")
        trace["audits"][0] = {"decision_time": "2025-02-01T00:00:00", "historical_days": ["2025-02-01"]}
        with self.assertRaisesRegex(ValueError, "historical day"):
            verify_trace(trace, "result3")

    def test_failed_solver_audit_rejected(self):
        trace = trace_fixture()
        trace["audits"] = [{"decision_time": "2025-02-01T00:00:00", "solver_status": "infeasible"}]
        with self.assertRaisesRegex(ValueError, "nonoptimal solver"):
            verify_trace(trace, "result3")
        trace["audits"][0].update(solver_status="optimal", solver_success=False)
        with self.assertRaisesRegex(ValueError, "failed solve"):
            verify_trace(trace, "result3")

    def test_frozen_revision_and_simultaneous_flows_rejected(self):
        trace = trace_fixture()
        trace["versions"][0, 0, 0] = 99
        with self.assertRaisesRegex(ValueError, "executed slot"):
            verify_trace(trace, "result3")
        trace["versions"][:] = np.nan
        trace["c"][0, 0] = trace["d"][0, 0] = 1
        with self.assertRaisesRegex(ValueError, "simultaneous"):
            verify_trace(trace, "result3")


class ResultsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = []
        with (ROOT / "data/results_template_clean.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            cls.fields = reader.fieldnames
            for row in reader:
                if row["result_id"] == "result3" and row["day_id"] == "2025-02-01":
                    # Tests need the original placeholder, even after a production run appended events.
                    if row["record_type"] == "emergency_event" and row["event_index"] != "1":
                        continue
                    row.update(value="", status="unfilled")
                    if row["record_type"] == "emergency_event":
                        for key in ("start_minute", "end_minute", "interval_start", "interval_end"):
                            row[key] = ""
                    cls.template.append(row)
                if row["result_id"] == "result3" and row["day_id"] == "2025-02-02":
                    break
        if len(cls.template) != 379:
            raise AssertionError(f"unexpected daily template size: {len(cls.template)}")

    def _write(self, root, trace):
        path = Path(root) / "result.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.fields)
            writer.writeheader()
            writer.writerows(self.template)
        original = path.read_bytes()
        audit = write_results(path, {"result3": trace}, require_full_year=False)
        self.assertEqual(Path(audit["backup"]).read_bytes(), original)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, self.fields)
            rows = list(reader)
        self.assertEqual([x["record_id"] for x in rows[:379]], [x["record_id"] for x in self.template])
        for old, new in zip(self.template, rows):
            mutable = {"value", "status"}
            if old["record_type"] == "emergency_event":
                mutable |= {"start_minute", "end_minute", "interval_start", "interval_end", "event_index"}
            self.assertEqual({k: v for k, v in old.items() if k not in mutable},
                             {k: v for k, v in new.items() if k not in mutable})
        return rows, audit

    def test_adopted_unchanged_is_100_not_zero_and_unadopted_blank(self):
        trace = trace_fixture()
        trace["versions"][0, 0, 36:] = 100.0
        with tempfile.TemporaryDirectory() as root:
            rows, audit = self._write(root, trace)
        adopted = [x for x in rows if x["plan_version"] == "update_06"]
        self.assertEqual(len(adopted), 108)
        self.assertTrue(all(x["value"] == "100" and x["status"] == "filled" for x in adopted))
        unused = [x for x in rows if x["plan_version"] in ("update_12", "update_18")]
        self.assertTrue(all(x["value"] == "" and x["status"] == "not_used" for x in unused))
        events = [x for x in rows if x["record_type"] == "emergency_event"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "no_event")
        self.assertEqual(audit["appended_emergency_rows"], 0)

    def test_more_than_one_emergency_appends_and_preserves_original_rows(self):
        trace = trace_fixture()
        trace["u"][0, :2], trace["u"][0, 10], trace["u"][0, 143] = 3, 4, 2
        trace["load"] += trace["u"]
        with tempfile.TemporaryDirectory() as root:
            rows, audit = self._write(root, trace)
        events = [x for x in rows if x["record_type"] == "emergency_event"]
        self.assertEqual([x["event_index"] for x in events], ["1", "2", "3"])
        self.assertEqual([float(x["value"]) for x in events], [6, 4, 2])
        self.assertEqual(len({x["record_id"] for x in rows}), 381)
        self.assertEqual(audit["appended_emergency_rows"], 2)
        self.assertEqual(events[-1]["interval_end"], "2025-02-02T00:00:00")

    def test_invalid_physics_never_overwrites_template(self):
        trace = trace_fixture()
        trace["q"][0, 0] = 0
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "result.csv"
            path.write_text("sentinel", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "balance"):
                write_results(path, {"result3": trace}, require_full_year=False)
            self.assertEqual(path.read_text(), "sentinel")

    def test_corrupted_staging_metadata_is_rejected_before_replace(self):
        import src.results as results
        original_filler = results._fill_row

        def corrupted(*args):
            row = original_filler(*args)
            row["source_reference"] = "changed"
            return row

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "result.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=self.fields)
                writer.writeheader()
                writer.writerows(self.template)
            original = path.read_bytes()
            with patch.object(results, "_fill_row", corrupted):
                with self.assertRaisesRegex(ValueError, "immutable template field"):
                    write_results(path, {"result3": trace_fixture()}, require_full_year=False)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_independent_source_alignment_detects_changed_price(self):
        trace = trace_fixture(("typical_day",))
        keys = {"A1_LOAD": "load", "A1_PV_FORECAST": "pv", "A1_PRICE": "price"}
        seen = 0
        source = ROOT / "data/data_clean.csv"
        with source.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["dataset_id"] in keys:
                    key = keys[row["dataset_id"]]
                    trace[key][0, int(row["slot_index"])] = float(row["value"]) / (1 if key == "price" else 6)
                    seen += 1
                    if seen == 432:
                        break
        audit = _verify_source_alignment(source, {"result1": trace})
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["independently_parsed_interval_rows"], 158112)
        trace["price"][0, 72] += 0.01
        with self.assertRaisesRegex(ValueError, "trace/source mismatch: result1/price/A1_PRICE"):
            _verify_source_alignment(source, {"result1": trace})


if __name__ == "__main__":
    unittest.main()
