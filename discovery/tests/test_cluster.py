"""Existing-host deployment and simulation fan-out, without launching infrastructure."""

from types import SimpleNamespace
import pytest
import yaml
from chia.cluster.config import load_config, assign_nodes
from chia_dma.config_loader import load
from chia_dma.evaluation import DMAEvaluator


@pytest.mark.parametrize("state", ["unprepared", "prepared", "checkpointed"])
def test_status_handles_runs_before_and_after_the_first_checkpoint(tmp_path, monkeypatch, capsys, state):
    import json
    import sys
    import loop
    from chia_dma.source import write_json

    run = tmp_path / "DMADiscovery"
    monkeypatch.setattr(loop, "load", lambda path: {"storage": str(tmp_path)})
    monkeypatch.setattr(sys, "argv", ["loop.py", "status", "--run", "DMADiscovery"])
    if state == "unprepared":
        with pytest.raises(SystemExit) as error:
            loop.main()
        assert error.value.code == 2
        assert "has not been prepared" in capsys.readouterr().err
        assert not run.exists()
        return

    write_json(run / "run.json", {"run_id": "DMADiscovery"})
    saved = {"admitted": ["seed-dma"], "requests": {"seed-dma": {"generated": True, "evaluated": True}}}
    if state == "checkpointed":
        write_json(run / "latest.json", {"checkpoint": "00000000-test"})
        write_json(run / "checkpoints/00000000-test/state.json", saved)
    loop.main()
    output = capsys.readouterr().out
    if state == "prepared":
        assert "No checkpoint yet" in output
        assert "baseline evaluation" in output
        assert not (run / "latest.json").exists()
    else:
        assert json.loads(output) == saved


def test_native_cluster_uses_every_existing_worker(monkeypatch):
    from chia_dma.config_loader import ROOT
    monkeypatch.setenv('CHIA_HEAD', '10.0.0.1')
    monkeypatch.setenv('DMA_ROOT', '/loop')
    configuration = load()
    path = ROOT / 'cluster.yaml'
    deployment = yaml.safe_load(path.read_text())
    native = load_config(str(path))
    assignments = assign_nodes(native)
    generators = [a for a in assignments if a.node_type.name == 'generation']
    count = deployment['available_node_types']['generation']['num_workers']
    assert {a.worker_index for a in generators} == set(range(count))
    assert all(a.ip == '10.0.0.1' and a.resources['generation'] == 1 for a in generators)
    simulations = [a for a in assignments if a.node_type.name == 'simulation']
    assert {a.ip for a in simulations} == {f'chia-worker-{i}' for i in range(1, 6)}
    assert len(simulations) == 5
    assert 'gcp_nodes' not in deployment
    assert deployment['auth']['ssh_private_key'].endswith('/.ssh/chia-key')
    assert configuration['agent']['effort'] == 'medium'
    # Loop code arrives through `chia job submit --working-dir .`, not file mounts.
    assert 'file_mounts' not in deployment
    assert 'run_options' not in deployment['available_node_types']['simulation']['docker']


def test_tutorial_namespace_preserves_native_chia_settings(tmp_path):
    from chia_dma.config_loader import ROOT
    document = yaml.safe_load((ROOT / 'cluster.yaml').read_text())
    document['worker_env_commands'] = ['export TEST_NATIVE_SETTING=1']
    path = tmp_path / 'cluster.yaml'
    path.write_text(yaml.safe_dump(document))
    configuration = load(path)
    assert configuration['cluster']['worker_env_commands'] == ['export TEST_NATIVE_SETTING=1']
    assert 'tutorial' not in configuration['cluster']
    assert configuration['search']['iterations'] == document['tutorial']['search']['iterations']
    assert set(document['tutorial']['agent']) == {'model', 'effort'}
    assert set(document['tutorial']['search']) == {'iterations', 'max_in_flight'}


def test_benchmark_fanout_visits_all_simulation_nodes(monkeypatch):
    from chia_dma import evaluation
    ids = [f'{i + 1:056x}' for i in range(5)]
    nodes = [dict(NodeID=node_id, Alive=True, Resources={'verilator_run': 32}) for node_id in ids]
    nodes += [dict(NodeID='head', Alive=True, Resources={}),
              dict(NodeID='dead', Alive=False, Resources={'verilator_run': 32})]
    monkeypatch.setattr(evaluation.ray, 'nodes', lambda: nodes)
    submitted = []
    def options(**kwargs):
        def submit(*args, **tags):
            submitted.append((kwargs['scheduling_strategy'].node_id, args, tags))
            return object()
        return SimpleNamespace(chia_remote=submit)
    monkeypatch.setattr(evaluation, 'simulate', SimpleNamespace(options=options))
    evaluator = DMAEvaluator.__new__(DMAEvaluator)
    evaluator.configuration = {}
    evaluator.expected = {f'test-{i}': {} for i in range(6)}
    evaluator.dram_settings = {'dram.ini': b'settings'}
    software = {name: b'ELF' for name in evaluator.expected}
    tasks = evaluator.run_simulations(b'simulator', software, 'tag')
    assert len(tasks) == 6
    assert {node for node, _, _ in submitted} == set(ids)
    assert all(args[1] == b'simulator' and args[2] == b'ELF' for _, args, _ in submitted)
    monkeypatch.setattr(evaluation.ray, 'nodes', lambda: [])
    with pytest.raises(RuntimeError, match='No existing simulation workers'):
        evaluator.run_simulations(b'simulator', software, 'tag')


def test_loader_accepts_extra_resources_and_keeps_checkout_slots_serial(tmp_path):
    from chia_dma.config_loader import ROOT
    document = yaml.safe_load((ROOT / "cluster.yaml").read_text())
    workers = document["available_node_types"]
    workers["generation"]["resources"] = {"generation": 4, "custom": 2}
    workers["generation"]["num_workers"] = 0
    workers["build"]["resources"] = {"chipyard": 2, "extra": 1}
    path = tmp_path / "cluster.yaml"
    path.write_text(yaml.safe_dump(document))
    configuration = load(path)
    workers = configuration["cluster"]["available_node_types"]
    assert workers["generation"]["resources"] == {"generation": 1, "custom": 2}
    assert workers["generation"]["num_workers"] == 1
    assert workers["build"]["resources"] == {"chipyard": 1, "extra": 1}


def test_cpu_and_timeout_changes_reuse_build_cache_keys(tmp_path, base_tree):
    import copy
    import shutil
    seed = tmp_path / "seed"
    shutil.copytree(base_tree, seed / "tree")
    configuration = load()
    original = DMAEvaluator(configuration, None, seed)
    changed = copy.deepcopy(configuration)
    changed["hardware"]["make_jobs"] += 8
    changed["hardware"]["build_timeout_seconds"] += 600
    assert DMAEvaluator(changed, None, seed).cache_prefix == original.cache_prefix
    changed["hardware"]["config"] = "AnotherConfig"
    assert DMAEvaluator(changed, None, seed).cache_prefix != original.cache_prefix
