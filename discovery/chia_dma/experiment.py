"""Head-only search state: SQL history, native checkpoints, and forks."""

from pathlib import Path
import fcntl
import json
import shutil
import uuid
import time
import sqlite3
import ray
from chia.database.sqlite_node import SQLiteNode
from skydiscover.config import OpenEvolveNativeDatabaseConfig
from skydiscover.search.base_database import Program
from skydiscover.search.openevolve_native.database import OpenEvolveNativeDatabase
from .profiling import record_scores
from .source import load_proposal, save_proposal, read_json, write_json


class Experiment:
    def __init__(self, configuration, run):
        self.configuration = configuration
        self.run = Path(run)
        self.run.mkdir(parents=True, exist_ok=True)
        self.admitted = set()
        native_fields = OpenEvolveNativeDatabaseConfig.__dataclass_fields__
        native_options = {}
        for name, value in configuration["search"].items():
            if name in native_fields:
                native_options[name] = value
        population_config = OpenEvolveNativeDatabaseConfig(**native_options)
        population_config.db_path = None
        self.population = Population(self, population_config)
        latest_path = self.run / "latest.json"
        if latest_path.exists():
            latest = read_json(latest_path)
            checkpoint_directory = self.run / "checkpoints" / latest["checkpoint"]
            envelope = read_json(checkpoint_directory / "state.json")
            self.population.load(str(checkpoint_directory / "population"))
            self.admitted = set(envelope["admitted"])
        self.sql = SQLiteNode(
            str(self.run / "metrics.db"),
            require_colocated=False,
            pin_to_current_node=ray.is_initialized(),
        )
        self.sql.init_schema("""CREATE TABLE IF NOT EXISTS proposals
          (id TEXT PRIMARY KEY,parent TEXT,island INTEGER,sequence INTEGER,kind TEXT,
           request TEXT,patch TEXT,patch_hash TEXT,generation TEXT,backend TEXT,model TEXT,metrics TEXT,program_id TEXT);
          CREATE TABLE IF NOT EXISTS benchmarks
          (candidate TEXT,name TEXT,metrics TEXT,PRIMARY KEY(candidate,name));""")
        self.recovery = self.pending_proposals()

    def selection(self, candidate_id):
        saved_request = self.sql.query_value(
            "SELECT request FROM proposals WHERE id=?", (candidate_id,)
        )
        return json.loads(saved_request)

    def selections(self):
        rows = self.sql.query("SELECT request FROM proposals ORDER BY sequence")
        return [json.loads(row["request"]) for row in rows]

    def save_selection(self, parent, inspirations, *, candidate_id=None, kind="search"):
        request = {
            "id": candidate_id or "candidate-" + uuid.uuid4().hex,
            "kind": kind,
            "selected_at": time.time(),
            "sequence": self.sql.query_value(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM proposals"
            ),
            "parent": parent.to_dict(),
            "inspirations": [p.to_dict() for p in inspirations],
            "island": parent.metadata["island"],
            "backend": self.configuration["agent"]["backend"],
        }
        previous = self.sql.query_value(
            "SELECT metrics FROM proposals WHERE island=? AND kind='search' "
            "AND metrics IS NOT NULL ORDER BY sequence DESC LIMIT 1",
            (request["island"],),
        )
        if previous and json.loads(previous).get("correctness") == 0:
            request["feedback"] = (
                "The previous proposal failed hidden correctness tests and received zero fitness. "
                "Preserve the computation for runtime-varying inputs. Test contents are private."
            )
        self.sql.execute(
            "INSERT INTO proposals(id,parent,island,sequence,kind,request,backend) VALUES (?,?,?,?,?,?,?)",
            (
                request["id"],
                (
                    None
                    if kind == "seed"
                    else parent.metadata.get("candidate_id", parent.id)
                ),
                request["island"],
                request["sequence"],
                kind,
                json.dumps(request),
                request["backend"],
            ),
        )
        return request

    def load_proposal(self, candidate_id):
        return load_proposal(self.sql, candidate_id)

    def save_proposal(self, candidate_id, patch, generation):
        return save_proposal(self.sql, candidate_id, patch, generation)

    def evaluation_result(self, candidate_id):
        value = self.sql.query_value(
            "SELECT metrics FROM proposals WHERE id=?", (candidate_id,)
        )
        return json.loads(value) if value else None

    def benchmarks(self, candidate_id):
        return {
            row["name"]: json.loads(row["metrics"])
            for row in self.sql.query(
                "SELECT name,metrics FROM benchmarks WHERE candidate=?", (candidate_id,)
            )
        }

    def pending_proposals(self):
        unfinished = []
        for request in self.selections():
            if request["kind"] != "search" or request["id"] in self.admitted:
                continue
            metrics = self.evaluation_result(request["id"])
            if metrics is not None and metrics.get("validity") == 0:
                continue
            unfinished.append(request)
        return unfinished

    def save_evaluation(self, candidate_id, metrics, vector, program_id=""):
        operations = [
            (
                "UPDATE proposals SET metrics=?,program_id=? WHERE id=?",
                (json.dumps(metrics), program_id, candidate_id),
            )
        ]
        for workload, measurements in vector.items():
            operations.append(
                (
                    "INSERT OR REPLACE INTO benchmarks VALUES (?,?,?)",
                    (candidate_id, workload, json.dumps(measurements)),
                )
            )
        self.sql.transaction(operations)
        return record_scores(self, candidate_id, metrics)

    def save_checkpoint(self, iteration=0):
        root = self.run / "checkpoints"
        root.mkdir(exist_ok=True)
        sequence = 0
        for checkpoint_directory in root.glob("[0-9]*"):
            previous_sequence = int(checkpoint_directory.name.split("-")[0])
            sequence = max(sequence, previous_sequence + 1)
        name = f"{sequence:08d}-" + uuid.uuid4().hex[:8]
        temp = root / ("." + name)
        temp.mkdir()
        self.population.save(
            str(temp / "population"), iteration=self.population.last_iteration
        )
        requests = {
            r["id"]: {"generated": r["generated"], "evaluated": r["evaluated"]}
            for r in self.sql.query(
                "SELECT id,patch IS NOT NULL AS generated,metrics IS NOT NULL AS evaluated FROM proposals"
            )
        }
        write_json(
            temp / "state.json",
            {
                "admitted": sorted(self.admitted),
                "requests": requests,
            },
        )
        temp.rename(root / name)
        write_json(self.run / "latest.json", {"checkpoint": name})
        return name

    def close(self):
        self.sql.wal_checkpoint()
        self.sql.close()


class Population(OpenEvolveNativeDatabase):
    """Public sample/add hooks preserve selections and pin admission islands."""

    def __init__(self, owner, config):
        self.owner = owner
        super().__init__("openevolve_native", config)

    def sample(self, num_context_programs=4, **kwargs):
        if self.owner.recovery:
            request = self.owner.recovery.pop(0)
            parent = Program(**request["parent"])
            context = [Program(**p) for p in request["inspirations"]]
        else:
            parent, context = super().sample(num_context_programs, **kwargs)
            request = self.owner.save_selection(parent, context)
        parent = Program(**parent.to_dict())
        parent.metadata["request_id"] = request["id"]
        return parent, context

    def add(self, program, iteration=None, **kwargs):
        candidate = json.loads(program.solution)
        candidate_id = candidate["proposal_id"]
        if program.metrics.get("validity") != 1 and candidate_id != "seed-dma":
            return None
        for dimension in self.feature_dimensions:
            if dimension not in ("complexity", "diversity", "score"):
                program.metrics.setdefault(dimension, 0)
        request = self.owner.selection(candidate_id)
        saved = self.owner.load_proposal(candidate_id)
        program.metadata.update(
            candidate_id=candidate_id, patch_hash=saved["patch_hash"]
        )
        kwargs.setdefault("target_island", request["island"])
        result = super().add(program, iteration=iteration, **kwargs)
        if (
            not kwargs.get("_is_migration")
            and kwargs["target_island"] == request["island"]
        ):
            self.owner.sql.execute(
                "UPDATE proposals SET program_id=? WHERE id=?",
                (program.id, candidate_id),
            )
        self.owner.admitted.add(candidate_id)
        return result


def fork(origin, checkpoint, target, *, candidate=None):
    """Keep the native population and only SQL history visible at this checkpoint."""
    if target.resolve().is_relative_to(origin.resolve()):
        raise ValueError("Fork destination must be outside the source experiment")
    if candidate:
        history = sorted((origin / "checkpoints").glob("[!.]*/state.json"))
        checkpoint = None
        for path in history:
            saved_state = read_json(path)
            if candidate in saved_state["admitted"]:
                checkpoint = path.parent.name
                break
        if checkpoint is None:
            raise ValueError("No committed checkpoint contains candidate: " + candidate)
    if checkpoint is None:
        latest = read_json(origin / "latest.json")
        checkpoint = latest["checkpoint"]
    checkpoint_directory = origin / "checkpoints" / checkpoint
    envelope = read_json(checkpoint_directory / "state.json")
    shutil.copytree(
        origin,
        target,
        ignore=shutil.ignore_patterns(
            "checkpoints",
            "cache",
            "artifacts",
            "work",
            ".writer.lock",
            "metrics.db*",
        ),
    )
    shutil.copytree(checkpoint_directory, target / "checkpoints" / checkpoint)
    with sqlite3.connect(
        "file:" + str(origin / "metrics.db") + "?mode=ro", uri=True
    ) as source_database, sqlite3.connect(target / "metrics.db") as fork_database:
        source_database.backup(fork_database)
        restore_history(fork_database, envelope)
    meta = read_json(target / "run.json")
    meta.update(
        run_id=target.name, forked_from={"run": origin.name, "checkpoint": checkpoint}
    )
    write_json(target / "run.json", meta)
    write_json(target / "latest.json", {"checkpoint": checkpoint})


def restore_history(fork_database, envelope):
    requests_at_checkpoint = envelope["requests"]
    for row in fork_database.execute("SELECT id FROM proposals").fetchall():
        candidate_id = row[0]
        flags = requests_at_checkpoint.get(candidate_id)
        if flags is None:
            fork_database.execute("DELETE FROM proposals WHERE id=?", (candidate_id,))
            fork_database.execute(
                "DELETE FROM benchmarks WHERE candidate=?", (candidate_id,)
            )
            continue
        if not flags["generated"]:
            fork_database.execute(
                "UPDATE proposals SET patch=NULL,patch_hash=NULL,generation=NULL,model=NULL,backend=json_extract(request,'$.backend') WHERE id=?",
                (candidate_id,),
            )
        if not flags["evaluated"]:
            fork_database.execute(
                "UPDATE proposals SET metrics=NULL WHERE id=?", (candidate_id,)
            )
            fork_database.execute(
                "DELETE FROM benchmarks WHERE candidate=?", (candidate_id,)
            )
        if candidate_id not in envelope["admitted"]:
            fork_database.execute(
                "UPDATE proposals SET program_id=NULL WHERE id=?", (candidate_id,)
            )


class RunLock:
    """One population writer per run; separate runs can share immutable artifacts."""

    def __init__(self, run):
        self.path = Path(run) / ".writer.lock"

    def __enter__(self):
        self.file = self.path.open("a")
        fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *args):
        self.file.close()
