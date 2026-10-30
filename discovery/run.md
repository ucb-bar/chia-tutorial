# Discovery runbook

## 1. Prep

Complete [setup.md](../setup.md). Run commands on
`chia-dev`, inside `chia-tutorial/discovery`. The experiment
is named `DMADiscovery`; adjust `tutorial.search.iterations` in `cluster.yaml`
to set the total proposal budget.

- **Load environment:** `source load-env.sh` activates the setup script's `chia_env` and installs SkyDiscover on the head.
- **Prepare:** `python loop.py prepare --run DMADiscovery` preserves the baseline source seed.
- **Start workers:** `chia up cluster.yaml -y`
- **Run:** `chia job submit --chia-cluster cluster.yaml --submission-id DMADiscovery --working-dir . -- python "$PWD/loop.py" run --run DMADiscovery` (choose a fresh submission ID for each submission).
- **Inspect checkpoints:** `python loop.py status --run DMADiscovery` lists admitted candidate IDs in the latest automatic checkpoint.
- **Stop:** `chia job stop DMADiscovery --chia-cluster cluster.yaml` stops the driver; wait for it to finish before teardown.
- **Tear down:** `chia down cluster.yaml -y`
- **Select a candidate:** `python loop.py fork --run DMADiscovery --candidate CANDIDATE_ID --new-run tutorial` creates a distributable branch with the full search state and history through the earliest checkpoint admitting that candidate. Replace `CANDIDATE_ID` with an admitted ID; `experiments/tutorial` must not already exist.
- **Create the distribution directory:** `mkdir -p checkpoints`
- **Package:** `tar -czf checkpoints/tutorial.tar.gz experiments/tutorial experiments/seeds` bundles the branch and preserved source seeds. Distribute this tarball with the matching tutorial repository revision.

Checkpoints are automatic. The branch retains the selected snapshot's entire
population, including other candidates admitted concurrently. Forking omits
cache, build workspaces, and artifact logs; SQL history and
checkpoint state are retained.

## 2. Tutorial attendee run

Clone the matching tutorial repository revision, complete the same setup, and
work in `chia-tutorial/discovery`. Place the distributed
tarball at `checkpoints/tutorial.tar.gz`. Increase
`tutorial.search.iterations` above the number of proposals already saved to
generate additional candidates. Extract into a checkout without an existing
`experiments/tutorial` run.

- **Extract:** `tar -xzf checkpoints/tutorial.tar.gz` restores the run, all history through the selected checkpoint, and source seeds under `experiments`.
- **Load environment:** `source load-env.sh`
- **Prepare restored paths:** `python loop.py prepare --run tutorial` locates the restored seed.
- **Start workers:** `chia up cluster.yaml -y`
- **Resume:** `chia job submit --chia-cluster cluster.yaml --submission-id attendee --working-dir . -- python "$PWD/loop.py" resume --run tutorial` (choose a fresh submission ID for each resume).
- **Visualize scores over time:** after the job exits, run `chia viz-profile experiments/tutorial/artifacts/profile/prof_candidates.log --format html` and open the generated `prof_candidates_profile.html`; select a `score_point` to see its candidate ID and score. This timeline covers evaluations performed after the fork; earlier history remains in `metrics.db`.
- **Tear down:** `chia down cluster.yaml -y`

CHIA manages the cluster, job submission, and HTML visualization; `loop.py`
provides the tutorial's preparation and checkpoint operations. `--working-dir .`
uploads the loop code to every worker; `experiments/` (`.gitignore`) and the hidden
correctness tests in `private/` (`.rayignore`) are not uploaded.
