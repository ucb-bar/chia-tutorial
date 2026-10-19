"""One lock around best-design publication; no agent-to-agent workspace writes."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading


@dataclass(frozen=True)
class Design:
    name: str
    directory: str
    body: str
    patch: str
    score: float
    fingerprint: str


class Coordinator:
    def __init__(self, directory, baseline):
        self.directory = Path(directory)
        self.best = baseline
        self.lock = threading.Lock()
        self.history = []
        (self.directory / "best.json").write_text(json.dumps(dict(name=baseline.name, directory=baseline.directory, score=baseline.score, fingerprint=baseline.fingerprint), indent=2) + "\n")

    def snapshot(self):
        with self.lock:
            return self.best

    def publish(self, design):
        if not math.isfinite(design.score) or design.score <= 0:
            raise ValueError("Invalid speedup score")
        summary = json.loads((Path(design.directory) / "summary.json").read_text())
        if (summary.get("status") != "passed" or summary.get("validated_profile") != "full"
                or summary.get("fingerprint") != design.fingerprint
                or summary.get("score") != design.score):
            raise ValueError("Only completed, full-validated designs may be published")
        with self.lock:
            if design.fingerprint != self.best.fingerprint:
                raise ValueError("Candidate uses a different baseline/suite/environment")
            if design.score <= self.best.score:
                return False
            record = dict(name=design.name, directory=design.directory, score=design.score,
                          fingerprint=design.fingerprint)
            temporary = self.directory / "best.json.tmp"
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(self.directory / "best.json")
            self.best = design
            self.history.append(record)
            return True
