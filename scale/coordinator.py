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


class Coordinator:
    def __init__(self, directory, baseline):
        self.directory = Path(directory)
        self.best = baseline
        self.lock = threading.Lock()
        (self.directory / "best.json").write_text(json.dumps(dict(name=baseline.name, directory=baseline.directory, score=baseline.score), indent=2) + "\n")

    def snapshot(self):
        with self.lock:
            return self.best

    def publish(self, design):
        """Called only after run_agent has passed the complete configured suite."""
        if not math.isfinite(design.score) or design.score <= 0:
            raise ValueError("Invalid speedup score")
        with self.lock:
            if design.score <= self.best.score:
                return False
            record = dict(name=design.name, directory=design.directory, score=design.score)
            temporary = self.directory / "best.json.tmp"
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(self.directory / "best.json")
            self.best = design
            return True
