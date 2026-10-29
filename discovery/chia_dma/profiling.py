"""Score metadata and filtered views of CHIA's native profile events."""

from collections import defaultdict, deque
import json
from pathlib import Path
import threading

import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.trace.profiler import get_collector, get_profiler

_score_lock = threading.Lock()


@ChiaFunction()
def score_point(metric_series, candidate_id, evaluation_id, combined_score, validity, stage):
    get_profiler().add_info(dict(
        metric_series=metric_series, candidate_id=candidate_id, evaluation_id=evaluation_id,
        combined_score=combined_score, validity=validity, stage=stage,
    ))


def record_scores(experiment, candidate_id, metrics, stage="evaluation"):
    if not ray.is_initialized() or not get_profiler().enabled:
        return {}
    # Keep best-so-far points ordered when candidate evaluations overlap.
    with _score_lock:
        rows = experiment.sql.query(
            "SELECT id,metrics FROM proposals WHERE json_extract(metrics, '$.validity')=1 "
            "ORDER BY json_extract(metrics, '$.combined_score') DESC,sequence LIMIT 1"
        )
        best_id = rows[0]["id"] if rows else ""
        best = float(json.loads(rows[0]["metrics"])["combined_score"]) if rows else 0.0
        valid = int(metrics.get("validity", 0) == 1)
        score = float(metrics.get("combined_score", 0.0)) if valid else 0.0
        score_point("candidates", candidate_id, candidate_id, score, valid, stage)
        score_point("best", best_id, candidate_id, best, int(bool(best_id)), stage)
        return {"candidate_id": candidate_id, "combined_score": score,
                "best_candidate_id": best_id, "best_combined_score": best, "validity": valid}


def export_profiles(directory):
    """Keep full task/best-score and candidate-only logs in native JSONL format."""
    collector = get_collector()
    if collector is None:
        return
    # This actor call also flushes earlier score events before reading its log.
    source = Path(get(collector.get_log_path.remote()))
    events = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    pending, candidates = defaultdict(deque), set()
    for index, event in enumerate(events):
        if event.get("func") != "score_point":
            continue
        key = (event["call_id"], event["func"])
        if event["type"] == "local_start":
            pending[key].append(index)
        elif event["type"] == "local_end" and pending[key]:
            start = pending[key].popleft()
            if event.get("extra", {}).get("metric_series") == "candidates":
                candidates.update((start, index))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, selected in (("prof_full.log", False), ("prof_candidates.log", True)):
        with (directory / filename).open("w") as stream:
            for index, event in enumerate(events):
                if (index in candidates) == selected:
                    stream.write(json.dumps(event) + "\n")
