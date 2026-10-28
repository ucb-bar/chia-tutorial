"""Preserved source, durable proposals, and native search checkpoints."""

from pathlib import Path
import fcntl
import json
import os
import shutil
import subprocess
import uuid
import sqlite3
import ray
from chia.base.ChiaFunction import get
from chia.database.sqlite_node import SQLiteNode
from skydiscover.config import OpenEvolveNativeDatabaseConfig
from skydiscover.search.base_database import Program
from skydiscover.search.openevolve_native.database import OpenEvolveNativeDatabase
from .config import ROOT, ALLOWLIST, digest, frozen


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
           request TEXT,source TEXT,source_hash TEXT,generation TEXT,backend TEXT,model TEXT,metrics TEXT,program_id TEXT);
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
            "sequence": self.sql.query_value(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM proposals"
            ),
            "parent": parent.to_dict(),
            "inspirations": [p.to_dict() for p in inspirations],
            "island": parent.metadata["island"],
            "backend": self.configuration["agent"]["backend"],
        }
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

    def save_proposal(self, candidate_id, source, generation):
        return save_proposal(self.sql, candidate_id, source, generation)

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
                "SELECT id,source IS NOT NULL AS generated,metrics IS NOT NULL AS evaluated FROM proposals"
            )
        }
        write_json(
            temp / "state.json",
            {
                "frozen_hash": digest(frozen(self.configuration)),
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
        if program.metrics.get("validity") != 1:
            raise ValueError("Only fully valid candidates may enter the population")
        candidate = json.loads(program.solution)
        candidate_id = candidate["proposal_id"]
        request = self.owner.selection(candidate_id)
        saved = self.owner.load_proposal(candidate_id)
        program.metadata.update(
            candidate_id=candidate_id, source_hash=saved["source_hash"]
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


def load_proposal(sql, candidate_id, *, remote=False):
    query = sql.query_one
    args = (
        "SELECT source,source_hash,generation,backend,model FROM proposals WHERE id=?",
        (candidate_id,),
    )
    row = get(query.chia_remote(*args)) if remote else query(*args)
    if not row or row["source"] is None:
        return None
    return {
        "id": candidate_id,
        **row,
        "source": json.loads(row["source"]),
        "generation": json.loads(row["generation"]),
    }


def save_proposal(sql, candidate_id, source, generation, *, remote=False):
    """Save the proposal before generation returns; never replace it."""
    validate_candidate_files(source)
    args = (
        "UPDATE proposals SET source=?,source_hash=?,generation=?,backend=?,model=? WHERE id=? AND source IS NULL",
        (
            json.dumps(source),
            digest(source),
            json.dumps(generation),
            generation.get("backend"),
            generation.get("model"),
            candidate_id,
        ),
    )
    if remote:
        get(sql.execute.chia_remote(*args))
    else:
        sql.execute(*args)
    saved = load_proposal(sql, candidate_id, remote=remote)
    if saved is None or saved["source"] != source:
        raise ValueError("Missing request or immutable proposal ID reused")
    return saved


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
            "deployment",
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
                "UPDATE proposals SET source=NULL,source_hash=NULL,generation=NULL,model=NULL,backend=json_extract(request,'$.backend') WHERE id=?",
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


def preserve_source(configuration):
    image = configuration["cluster"]["available_node_types"]["build"]["docker"]["image"]
    manifest = {"image": image, **configuration["base"]}
    seed_store = Path(configuration["storage"]) / "seeds"
    seed_store.mkdir(parents=True, exist_ok=True)
    seed_directory = seed_store / digest(manifest)
    if seed_directory.exists():
        return seed_directory

    capture_directory = seed_store / (".capture-" + uuid.uuid4().hex)
    capture_directory.mkdir()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,src={ROOT / 'base'},dst=/base,readonly",
            "--mount",
            f"type=bind,src={capture_directory},dst=/seed",
            "--entrypoint",
            "bash",
            image,
            "/base/prepare.sh",
            configuration["base"]["chipyard_revision"],
        ],
        check=True,
    )
    write_json(capture_directory / "manifest.json", manifest)
    subprocess.run(["chmod", "-R", "a-w", str(capture_directory)], check=True)
    capture_directory.rename(seed_directory)
    return seed_directory


def read_candidate_files(tree):
    return {name: (Path(tree) / name).read_text() for name in ALLOWLIST}


def validate_candidate_files(bundle):
    if not isinstance(bundle, dict) or set(bundle) != set(ALLOWLIST):
        raise ValueError(
            "Candidate must contain exactly the two DMA files and six C workload files"
        )
    return bundle


def validate_integration(bundle, seed_bundle):
    """The allowlisted adapter also contains a fixed SoC integration mixin."""
    name = ALLOWLIST[1]
    marker = "\nclass WithRoCCDMA("
    if (
        marker not in bundle[name]
        or bundle[name].split(marker, 1)[1] != seed_bundle[name].split(marker, 1)[1]
    ):
        raise ValueError("The fixed WithRoCCDMA integration configuration changed")

    def l1_assignments(source):
        return [line.strip() for line in source.splitlines() if "io.mem." in line]

    if l1_assignments(bundle[name]) != l1_assignments(seed_bundle[name]):
        raise ValueError("The unused L1 interface must remain disabled")


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


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None
