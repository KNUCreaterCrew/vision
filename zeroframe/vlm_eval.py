"""Image-free evaluation of diagnostic labels; never enables runtime verification."""

from collections import Counter
from statistics import median

from .state import Status


LABELS = tuple(status.value for status in Status)


def summarize_results(records):
    groups = {}
    for record in records:
        key = f"{record['profile']}_{record['image_edge']}"
        groups.setdefault(key, []).append(record)
    summaries = {}
    for key, rows in groups.items():
        labeled = [row for row in rows if row.get("expected") in LABELS]
        model_rows = [row for row in rows if row.get("model_ran") is True]
        labeled_model = [row for row in labeled if row.get("model_ran") is True]
        valid = sum(row.get("format_valid") is True for row in model_rows)
        model_correct = sum(row.get("format_valid") is True and row["label"] == row["expected"]
                            for row in labeled_model)
        occupied = [row for row in labeled if row["expected"] in ("OCCUPIED", "AWAY")]
        false_empty = sum(row["label"] == "EMPTY" for row in occupied)
        counts = Counter(row["expected"] for row in labeled)
        matrix = {expected: {label: 0 for label in LABELS} for expected in LABELS}
        for row in labeled:
            matrix[row["expected"]][row["label"]] += 1
        times = [row["inference_seconds"] for row in model_rows if "inference_seconds" in row]
        summaries[key] = {
            "samples": len(rows), "labeled_samples": len(labeled),
            "coverage": {label: counts[label] for label in LABELS},
            "all_states_sampled": all(counts[label] for label in LABELS),
            "model_runs": len(model_rows), "valid_model_outputs": valid,
            "format_compliance": valid / len(model_rows) if model_rows else None,
            "labeled_model_runs": len(labeled_model), "model_correct": model_correct,
            "model_accuracy": model_correct / len(labeled_model) if labeled_model else None,
            "system_correct": sum(row["label"] == row["expected"] for row in labeled),
            "unknown_count": sum(row["label"] == "UNKNOWN" for row in rows),
            "guard_abstentions": sum(row.get("reason") == "LOW_VISIBILITY" for row in rows),
            "timeouts": sum(row.get("reason") == "VLM_TIMEOUT" for row in rows),
            "occupied_samples": len(occupied), "false_empty_count": false_empty,
            "false_empty_rate": false_empty / len(occupied) if occupied else None,
            "unknown_as_empty_count": sum(row["expected"] == "UNKNOWN" and row["label"] == "EMPTY" for row in labeled),
            "median_inference_seconds": round(median(times), 3) if times else None,
            "confusion_matrix": matrix,
        }
    return {"by_variant": summaries, "runtime_enabled": False}
