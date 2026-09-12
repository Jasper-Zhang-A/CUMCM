"""Contract-preserving, staged and independently checked results publication."""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .verification import emergency_events, summarize, verify_trace

RESULT_IDS = {"result1", "result2", "result3", "result4-2", "result4-3"}
EVENT_FIELDS = {"start_minute", "end_minute", "interval_start", "interval_end", "event_index"}
REVISION_INDEX = {"update_06": 0, "update_12": 1, "update_18": 2}
SUMMARY_KEYS = {
    "daily_planned_purchase_kwh": "planned_kwh",
    "daily_planned_purchase_cost_cny": "planned_cost",
    "daily_adjusted_purchase_kwh": "effective_kwh",
    "daily_adjusted_purchase_cost_cny": "planned_settled",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_source_alignment(source: Path, traces: dict) -> dict:
    """Reread the sole input directly; no imports from forecasting or optimization.

    Classify dataset, time semantics and units before parsing a number. This
    catches physically consistent traces that nevertheless used the wrong load,
    PV, price dataset, date, slot, or power-to-energy conversion.
    """
    spec = {
        "A1_LOAD": ("load_kw", "kW", "relative_day"),
        "A1_PV_FORECAST": ("pv_forecast_kw", "kW", "relative_day"),
        "A1_PRICE": ("electricity_price_cny_per_kwh", "CNY/kWh", "relative_day"),
        "A2_LOAD": ("load_kw", "kW", "calendar"),
        "A2_PV_ACTUAL": ("pv_actual_kw", "kW", "calendar"),
        "A4_PRICE": ("electricity_price_cny_per_kwh", "CNY/kWh", "calendar"),
    }
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(365)]
    day_indices = {day: i for i, day in enumerate(dates)}
    inputs = {dataset: np.full((144,) if basis == "relative_day" else (365, 144), np.nan)
              for dataset, (_, _, basis) in spec.items()}
    counts = Counter()
    original_hash = _sha256(source)
    with source.open("rb") as stream:
        if stream.read(3) != b"\xef\xbb\xbf":
            raise ValueError("model input lost UTF-8 BOM")
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            dataset = row["dataset_id"]
            counts[dataset] += 1
            if dataset == "A3_PV_FORECAST":
                # Issued predictions are checked by causal audits, not mistaken
                # for the realized PV used to recompute electrical balance.
                if row["time_semantics"] != "forecast_point":
                    raise ValueError("A3 forecast record has interval semantics")
                continue
            if dataset not in spec:
                raise ValueError(f"unknown input dataset during independent audit: {dataset}")
            metric, unit, basis = spec[dataset]
            if (row["metric"], row["unit"], row["time_basis"], row["time_semantics"]) != (
                    metric, unit, basis, "ten_minute_interval"):
                raise ValueError(f"source type/unit/time metadata mismatch: {row['record_id']}")
            slot = int(row["slot_index"])
            if not 0 <= slot < 144 or int(row["start_minute"]) != slot * 10 or int(row["end_minute"]) != (slot + 1) * 10:
                raise ValueError("source ten-minute alignment mismatch")
            if basis == "relative_day":
                if row["day_id"] != "typical_day" or row["interval_start"] or row["interval_end"]:
                    raise ValueError("typical-day source assigned a calendar date")
                location = slot
            else:
                if row["day_id"] not in day_indices:
                    raise ValueError("source calendar date outside 2025")
                location = (day_indices[row["day_id"]], slot)
                midnight = datetime.fromisoformat(row["day_id"])
                if row["interval_start"] != (midnight + timedelta(minutes=slot * 10)).isoformat():
                    raise ValueError("source interval_start disagrees with date and slot")
                if row["interval_end"] != (midnight + timedelta(minutes=(slot + 1) * 10)).isoformat():
                    raise ValueError("source interval_end disagrees with date and slot")
            if row["value_status"] != "present" or row["value"] == "":
                raise ValueError("missing source observation")
            value = float(row["value"])
            if not np.isfinite(value) or value < 0 or (unit == "CNY/kWh" and value <= 0):
                raise ValueError("invalid source observation")
            if np.isfinite(inputs[dataset][location]):
                raise ValueError("duplicated input dataset/day/slot key")
            inputs[dataset][location] = value / 6.0 if unit == "kW" else value
    for dataset, values in inputs.items():
        if not np.isfinite(values).all() or counts[dataset] != values.size:
            raise ValueError(f"incomplete independently read source dataset: {dataset}")
    if counts["A3_PV_FORECAST"] != 35040:
        raise ValueError("source forecast version record count changed")
    compared = {}
    max_error = 0.0
    for result_id, trace in traces.items():
        typical = result_id == "result1"
        mapping = {"load": "A1_LOAD" if typical else "A2_LOAD",
                   "pv": "A1_PV_FORECAST" if typical else "A2_PV_ACTUAL",
                   "price": "A4_PRICE" if result_id in ("result4-2", "result4-3") else "A1_PRICE"}
        selected_days = [day_indices[str(day)] for day in trace["dates"]] if not typical else None
        compared[result_id] = {}
        for name, dataset in mapping.items():
            expected = inputs[dataset]
            actual = np.asarray(trace[name], dtype=float)
            if expected.ndim == 1:
                expected = np.broadcast_to(expected, actual.shape)
            else:
                expected = expected[selected_days]
            error = float(np.max(np.abs(actual - expected)))
            if error > 1e-9:
                raise ValueError(f"trace/source mismatch: {result_id}/{name}/{dataset}, max error={error:g}")
            max_error = max(max_error, error)
            compared[result_id][name] = {"dataset_id": dataset, "compared_values": int(actual.size),
                                        "max_absolute_error": error}
    if _sha256(source) != original_hash:
        raise ValueError("sole model input changed during source audit")
    return {"passed": True, "input_path": str(source), "input_sha256": original_hash,
            "input_rows": sum(counts.values()), "independently_parsed_interval_rows": sum(x.size for x in inputs.values()),
            "max_absolute_error": max_error, "comparisons": compared,
            "method": "independent typed CSV reread; kW divided by 6; dataset/day/slot alignment"}


def _format(value) -> str:
    value = float(value)
    if not np.isfinite(value):
        raise ValueError("cannot publish a non-finite result")
    return format(0.0 if value == 0 else value, ".15g")


def _fill_row(row: dict, trace: dict, day_index: int, summary: dict) -> dict:
    row = dict(row)
    record_type = row["record_type"]
    if record_type == "planned_purchase":
        value = trace["q0"][day_index, int(row["slot_index"])]
    elif record_type == "adjusted_purchase":
        value = trace["versions"][day_index, REVISION_INDEX[row["plan_version"]], int(row["slot_index"])]
        if np.isnan(value):
            row.update(value="", status="not_used")
            return row
    elif record_type in ("planned_daily_summary", "adjusted_daily_summary"):
        value = summary[SUMMARY_KEYS[row["metric"]]]
    elif record_type == "storage_flow":
        key = {"charge_kwh": "c", "discharge_kwh": "d"}[row["metric"]]
        value = np.sum(trace[key][day_index, int(row["start_minute"]) // 10:int(row["end_minute"]) // 10])
    elif record_type == "storage_state":
        value = trace["E"][day_index, int(row["state_minute"]) // 10]
    elif record_type == "emergency_event":
        event_index = int(row["event_index"])
        events = summary["events"]
        if not events:
            if event_index != 1:
                raise ValueError("existing extra event rows cannot be deleted or silently reused")
            row.update(value="", status="no_event")
            for key in EVENT_FIELDS - {"event_index"}:
                row[key] = ""
            return row
        if not 1 <= event_index <= len(events):
            raise ValueError("existing event rows exceed recomputed events; preserve template and stop")
        event = events[event_index - 1]
        for key in EVENT_FIELDS:
            row[key] = str(event[key])
        value = event["value"]
    else:
        raise ValueError(f"unsupported result record type: {record_type}")
    row.update(value=_format(value), status="filled")
    return row


def _reference_daily(trace: dict, i: int) -> dict:
    """Separate scalar accounting used to check serialized daily summaries."""
    price = np.asarray(trace["price"])[i]
    original = np.asarray(trace["q0"])[i]
    effective = original.copy()
    penalty = 0.0
    for v in range(3):
        candidate = np.asarray(trace["versions"])[i, v]
        for t in range(144):
            if np.isfinite(candidate[t]):
                penalty += 0.5 * float(price[t]) * abs(float(candidate[t] - effective[t]))
                effective[t] = candidate[t]
    return {
        "daily_planned_purchase_kwh": sum(float(x) for x in original),
        "daily_planned_purchase_cost_cny": sum(float(p) * float(q) for p, q in zip(price, original)),
        "daily_adjusted_purchase_kwh": sum(float(x) for x in effective),
        "daily_adjusted_purchase_cost_cny": sum(float(p) * float(q) for p, q in zip(price, effective)) + penalty,
    }


def _validate_value(row: dict, trace: dict, index: int, reference: dict) -> None:
    kind, metric = row["record_type"], row["metric"]
    expected_status = "filled"
    if kind == "planned_purchase":
        slot = int(row["slot_index"])
        if row["plan_version"] != "initial_00" or int(row["decision_minute"]) != 0:
            raise ValueError("initial plan metadata inconsistent")
        expected = float(trace["q0"][index, slot])
    elif kind == "adjusted_purchase":
        v = REVISION_INDEX[row["plan_version"]]
        slot = int(row["slot_index"])
        if int(row["decision_minute"]) != (v + 1) * 360 or slot < (v + 1) * 36:
            raise ValueError("revision metadata changes an executed slot")
        expected = float(trace["versions"][index, v, slot])
        if np.isnan(expected):
            expected_status, expected = "not_used", None
    elif kind in ("planned_daily_summary", "adjusted_daily_summary"):
        expected = reference[metric]
    elif kind == "storage_flow":
        a, b = int(row["start_minute"]) // 10, int(row["end_minute"]) // 10
        if a % 24 or b != a + 24 or b > 144:
            raise ValueError("invalid four-hour storage block")
        key = {"charge_kwh": "c", "discharge_kwh": "d"}[metric]
        expected = sum(float(trace[key][index, t]) for t in range(a, b))
    elif kind == "storage_state":
        state_minute = int(row["state_minute"])
        if state_minute not in (0, 1440):
            raise ValueError("invalid storage state time")
        expected = float(trace["E"][index, state_minute // 10])
    elif kind == "emergency_event":
        positive = np.asarray(trace["u"])[index] > 1e-7
        if not positive.any():
            if int(row["event_index"]) != 1 or any(row[k] for k in EVENT_FIELDS - {"event_index"}):
                raise ValueError("no-event row contains event interval data")
            expected_status, expected = "no_event", None
        else:
            starts = [t for t in range(144) if positive[t] and (t == 0 or not positive[t - 1])]
            event_index = int(row["event_index"])
            if not 1 <= event_index <= len(starts):
                raise ValueError("invalid event index")
            a = starts[event_index - 1]
            b = a + 1
            while b < 144 and positive[b]:
                b += 1
            if int(row["start_minute"]) != 10 * a or int(row["end_minute"]) != 10 * b:
                raise ValueError("event not maximal consecutive emergency interval")
            midnight = datetime.fromisoformat(row["day_id"])
            if row["interval_start"] != (midnight + timedelta(minutes=10 * a)).isoformat():
                raise ValueError("incorrect event interval_start")
            if row["interval_end"] != (midnight + timedelta(minutes=10 * b)).isoformat():
                raise ValueError("incorrect event interval_end, including midnight")
            expected = sum(float(trace["u"][index, t]) for t in range(a, b))
    else:
        raise ValueError(f"unknown record_type {kind}")
    if row["status"] != expected_status:
        raise ValueError(f"incorrect result status at {row['record_id']}")
    if expected is None:
        if row["value"] != "":
            raise ValueError("unused/no-event result must stay blank")
    else:
        actual = float(row["value"])
        if not np.isfinite(actual) or not np.isclose(actual, expected, atol=1e-7, rtol=1e-12):
            raise ValueError(f"serialized value mismatch at {row['record_id']}: {actual} != {expected}")


def _validate_staging(original: Path, staging: Path, fields: list[str], traces: dict,
                      indices: dict, references: dict) -> dict:
    identifiers = set()
    status_counts, type_counts = Counter(), Counter()
    events_seen = Counter()
    original_count = 0
    with original.open(encoding="utf-8-sig", newline="") as old_stream, staging.open(encoding="utf-8-sig", newline="") as new_stream:
        old_reader, new_reader = csv.DictReader(old_stream), csv.DictReader(new_stream)
        if old_reader.fieldnames != fields or new_reader.fieldnames != fields:
            raise ValueError("result column order changed")
        old_iterator = iter(old_reader)
        for row in new_reader:
            old = next(old_iterator, None)
            if old is not None:
                original_count += 1
                mutable = {"value", "status"}
                if old["record_type"] == "emergency_event":
                    mutable |= EVENT_FIELDS
                for key in fields:
                    if key not in mutable and old[key] != row[key]:
                        raise ValueError(f"immutable template field changed: {old['record_id']}/{key}")
            elif row["record_type"] != "emergency_event":
                raise ValueError("only emergency events may be appended")
            if row["record_id"] in identifiers:
                raise ValueError("duplicate result record_id")
            identifiers.add(row["record_id"])
            result_id, day_id = row["result_id"], row["day_id"]
            if result_id not in traces or day_id not in indices[result_id]:
                raise ValueError("template has result/date absent from verified trace")
            _validate_value(row, traces[result_id], indices[result_id][day_id], references[result_id][day_id])
            status_counts[row["status"]] += 1
            type_counts[(result_id, row["record_type"])] += 1
            if row["record_type"] == "emergency_event":
                event_key = (result_id, day_id, row["event_index"])
                events_seen[event_key] += 1
                if events_seen[event_key] != 1:
                    raise ValueError("duplicate emergency event business key")
        if next(old_iterator, None) is not None:
            raise ValueError("original template rows were deleted")
    for result_id, day_id, _ in events_seen:
        expected = emergency_events(traces[result_id]["u"][indices[result_id][day_id]], day_id)
        count = sum((result_id, day_id, str(j)) in events_seen for j in range(1, max(1, len(expected)) + 1))
        if count != max(1, len(expected)):
            raise ValueError("missing serialized emergency event")
    with staging.open("rb") as stream:
        if stream.read(3) != b"\xef\xbb\xbf":
            raise ValueError("missing UTF-8 BOM")
    return {"rows": len(identifiers), "original_rows_preserved": original_count,
            "appended_emergency_rows": len(identifiers) - original_count,
            "status_counts": dict(status_counts), "column_count": len(fields),
            "immutable_metadata_preserved": True, "all_values_independently_recomputed": True}


def write_results(path, traces: dict, require_full_year: bool = True) -> dict:
    """Back up, stage, validate, then atomically replace the sole formal answer CSV.

    ``require_full_year=False`` exists only for synthetic contract tests; the
    production entry point uses its strict default.
    """
    path = Path(path).resolve()
    if require_full_year and set(traces) != RESULT_IDS:
        raise ValueError("publication requires all five result IDs")
    checks, summaries, indices, references = {}, {}, {}, {}
    expected_dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(365)]
    for result_id, trace in traces.items():
        checks[result_id] = verify_trace(trace, result_id)
        if require_full_year and result_id != "result1":
            if list(trace["dates"]) != expected_dates:
                raise ValueError("production trace requires causal January warm-up and full 2025")
            audits = trace.get("audits", [])
            midnight_days = {str(a.get("decision_time", ""))[:10] for a in audits
                             if str(a.get("decision_time", ""))[11:16] == "00:00"}
            if midnight_days != set(expected_dates):
                raise ValueError("production trace requires a midnight information audit for every day")
        indices[result_id] = {str(day): i for i, day in enumerate(trace["dates"])}
        summaries[result_id] = {row["day_id"]: row for row in summarize(trace)}
        references[result_id] = {str(day): _reference_daily(trace, i) for i, day in enumerate(trace["dates"])}
    source_alignment = (_verify_source_alignment(path.parent / "data_clean.csv", traces)
                        if require_full_year else {"skipped_for_synthetic_test": True})
    original_hash = _sha256(path)
    existing_events, prototypes, maximum_id = {}, {}, 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        for row in reader:
            identifier = row["record_id"]
            if identifier.startswith("R") and identifier[1:].isdigit():
                maximum_id = max(maximum_id, int(identifier[1:]))
            if row["record_type"] == "emergency_event":
                key = (row["result_id"], row["day_id"])
                existing_events.setdefault(key, set()).add(int(row["event_index"]))
                prototypes.setdefault(key, row)
    backup_root = path.parent.parent / "cache" / "backups" if path.parent.name == "data" else path.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_root / f"{path.stem}.{stamp}.{original_hash[:12]}.csv"
    shutil.copy2(path, backup)
    if _sha256(backup) != original_hash:
        raise ValueError("result template backup failed verification")
    fd, staging_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staging = Path(staging_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as output, path.open(encoding="utf-8-sig", newline="") as source:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for row in csv.DictReader(source):
                rid, day = row["result_id"], row["day_id"]
                writer.writerow(_fill_row(row, traces[rid], indices[rid][day], summaries[rid][day]))
            for (rid, day), prototype in prototypes.items():
                events = summaries[rid][day]["events"]
                for event_index in range(1, len(events) + 1):
                    if event_index in existing_events[(rid, day)]:
                        continue
                    maximum_id += 1
                    row = dict(prototype)
                    row.update(record_id=f"R{maximum_id:06d}", event_index=str(event_index))
                    writer.writerow(_fill_row(row, traces[rid], indices[rid][day], summaries[rid][day]))
            output.flush()
            os.fsync(output.fileno())
        audit = _validate_staging(path, staging, fields, traces, indices, references)
        if _sha256(path) != original_hash:
            raise ValueError("template changed concurrently; refusing atomic replacement")
        audit.update(trace_checks=checks, original_sha256=original_hash,
                     output_sha256=_sha256(staging), backup=str(backup),
                     atomic_replacement=True, complete_year_required=require_full_year,
                     source_alignment_audit=source_alignment)
        os.replace(staging, path)
        return audit
    finally:
        if staging.exists():
            staging.unlink()
