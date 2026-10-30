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
- **Select a candidate:** `python loop.py fork --run DMADiscovery --candidate CANDIDATE_ID --new-run tutorial` saves the full search state and history through that candidate's first checkpoint, plus a recipe for rebuilding its source seed. Replace `CANDIDATE_ID` with an admitted ID; `experiments/tutorial` must not already exist.
- **Create the distribution directory:** `mkdir -p checkpoints`
- **Package:** `tar -czf checkpoints/tutorial.tar.gz experiments/tutorial` bundles the branch only. Distribute it with compatible tutorial code and the original `base/` files.

Checkpoints are automatic; the first is saved after the baseline evaluation
finishes. Until then, `status` reports that there is no checkpoint yet. The
branch retains the selected snapshot's entire
population, including other candidates admitted concurrently. Forking omits
cache, build workspaces, and build/simulation logs; SQL history, checkpoint
state, and a compact score-only profile are retained. The tarball contains candidate patches and search
history, without the large Chipyard source tree, Git histories, or other cached
seeds. Every fork includes the rebuild recipe. If
`experiments/tutorial` already exists from an earlier fork, use a fresh branch
name throughout these commands.

## 2. Tutorial attendee run

<!-- Clone a compatible tutorial repository version, complete the same setup, and
work in `chia-tutorial/discovery`. Place the distributed
tarball at `checkpoints/tutorial.tar.gz`. The resumed search continues from the
prepared checkpoint until you stop it, or until the run holds
`tutorial.search.iterations` proposals in total (`status` reports the current
count). Extract into a checkout without an existing `experiments/tutorial` run. -->

1. **Extract:** `tar -xzf checkpoints/tutorial.tar.gz` restores the run and all history through the selected checkpoint under `experiments`.
1. **Load environment:** `source load-env.sh`
1. **Prepare restored paths:** `python loop.py prepare --run tutorial` reconstructs the source seed from the recorded Docker image digest and `base/` files, and verifies the baseline. The first restoration can take time; the seed is then cached locally.
1. **Start workers:** `chia up cluster.yaml -y`
1. **Resume:** `chia job submit --chia-cluster cluster.yaml --submission-id tutorial-live --working-dir . -- python "$PWD/loop.py" resume --run tutorial` (choose a fresh submission ID for each resume).
1. **Follow progress:** the submit command streams the loop's output; from a second terminal (after `source load-env.sh`), `chia job logs --chia-cluster cluster.yaml tutorial-live --follow` streams it again and `python loop.py status --run tutorial` reports the proposal count and admitted candidates in the latest checkpoint.
1. **Stop:** when you have seen enough, `chia job stop tutorial-live --chia-cluster cluster.yaml` stops the search. Checkpoints are automatic, so a later resume continues from the latest one.
1. **Visualize the candidate timeline:** after the job exits, run `chia viz-profile experiments/tutorial/artifacts/profile/prof_candidates.log --format html` and open `prof_candidates_profile.html`; hover over or select a `score_point` to see its candidate ID and score.
1. **Visualize the full task timeline:** `chia viz-profile experiments/tutorial/artifacts/profile/ChiaProfileCollector.log --format html` opens the complete execution timeline in `ChiaProfileCollector_profile.html`.
1. **Plot candidate scores:** `python loop.py scores --run tutorial` writes `experiments/tutorial/artifacts/profile/scores.html`: time on X in 30-minute increments, score on Y in 0.5 increments, one dot per candidate labeled with its last 6 ID characters, and a separate best-score readout. Best valid dots are green. Hover or select a dot, then use **Copy ID** to copy its full ID. New forks retain the score history through their checkpoint, so this works before resuming and while the loop runs; rerun the command and reload the page for newer results. Older archives without a profile log need the original log to show pre-fork history.
1. **Tear down:** `chia down cluster.yaml -y`

<!-- CHIA manages the cluster, job submission, and HTML visualization; `loop.py`
provides the tutorial's preparation and checkpoint operations. `--working-dir .`
uploads the loop code to every worker; `experiments/` (`.gitignore`) and the hidden
correctness tests in `private/` (`.rayignore`) are not uploaded. -->
