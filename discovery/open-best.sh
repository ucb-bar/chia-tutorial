#!/usr/bin/env bash
# Run on chia-dev after source load-env.sh: bash open-best.sh tutorial
set -euo pipefail
if [[ $# -ne 1 || $1 == --help ]]; then
    echo "Usage: bash open-best.sh RUN" >&2
    exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")"
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT

python - "$1" "$staging" <<'PY'
import json
import sys
from pathlib import Path
from chia.database.sqlite_node import SQLiteNode
from chia_dma.config_loader import load
from chia_dma.source import read_json, seed_recipe

configuration = load()
run = Path(configuration["storage"]) / sys.argv[1]
destination = Path(sys.argv[2])
if not (run / "metrics.db").is_file():
    sys.exit(f"No results database: {run / 'metrics.db'}")
# Read locally without starting Ray or its profiler; this also works after chia down.
best = SQLiteNode.query_one._chia_original(str(run / "metrics.db"), """
    SELECT id, patch, json_extract(metrics, '$.combined_score') AS score
    FROM proposals WHERE patch IS NOT NULL
      AND json_extract(metrics, '$.validity') = 1
      AND json_type(metrics, '$.combined_score') IN ('integer', 'real')
    ORDER BY score DESC, sequence ASC, id ASC LIMIT 1
""")
if best is None:
    sys.exit("No successfully evaluated candidates in this run yet")
meta = read_json(run / "run.json")
recipe = meta.get("seed_recipe")
if recipe is None:
    seed = run.parent / "seeds" / Path(meta["seed"]).name
    recipe = seed_recipe(seed if seed.is_dir() else Path(meta["seed"]))
if recipe["manifest"]["files"] != configuration["base"]["files"]:
    sys.exit("Cannot reconstruct this design: use the run's original base/ files")
destination.joinpath("best.patch").write_text(best.pop("patch"))
destination.joinpath("selection.json").write_text(json.dumps({**best, "recipe": recipe}))
destination.joinpath("image").write_text(recipe["image"])
print(f"Best valid candidate: {best['id']} (score {best['score']:g})", flush=True)
PY

container="dma-${staging##*.}"
image=$(cat "$staging/image")
docker run -d --init --name "$container" --user ray \
    --mount "type=bind,src=$PWD/base,dst=/opt/dma-loop/base,readonly" \
    --mount "type=bind,src=$PWD/chia_dma,dst=/opt/dma-loop/chia_dma,readonly" \
    --entrypoint sleep "$image" infinity
printf 'Inspection container: %s\nRemove when finished: docker rm -f %s\n' "$container" "$container"
# Copy only the selected patch and recipe; the live experiment is never mounted.
docker cp "$staging/selection.json" "$container:/tmp/selection.json"
docker cp "$staging/best.patch" "$container:/tmp/best.patch"
docker exec -i --user root "$container" /home/ray/anaconda3/bin/python - <<'PY'
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, "/opt/dma-loop")
from chia_dma.source import prepare_checkout, apply_candidate_diff, seed_revisions

tree = Path("/home/ray/dma-build")
recipe = json.loads(Path("/tmp/selection.json").read_text())["recipe"]
# First the tutorial baseline (base patch + workload files), then the candidate diff.
prepare_checkout(tree, recipe["repositories"][""])
if seed_revisions(tree) != recipe["repositories"]:
    sys.exit("Build image does not reproduce the run's baseline repositories")
apply_candidate_diff(tree, tree, Path("/tmp/best.patch").read_text())
for name in (".sbt", ".ivy2"):
    if not (tree / name).exists():
        (tree / name).symlink_to(Path("/home/ray/chipyard") / name)
subprocess.run(["chown", "-R", "ray:ray", str(tree)], check=True)
PY

docker exec --user ray "$container" bash -ec '
    mkdir -p /home/ray/.local/bin
    curl -fsSL "https://code.visualstudio.com/sha/download?build=stable&os=cli-alpine-x64" \
        -o /tmp/vscode-cli.tar.gz
    tar -xzf /tmp/vscode-cli.tar.gz -C /home/ray/.local/bin code
    rm /tmp/vscode-cli.tar.gz
'
echo 'Complete the GitHub device sign-in below, then open the VS Code link. Keep this terminal open.'
docker exec -it --user ray --workdir /home/ray/dma-build "$container" bash -ec '
    /home/ray/.local/bin/code tunnel user login --provider github
    printf "\nWorkspace: https://vscode.dev/tunnel/%s/home/ray/dma-build\n\n" "$1"
    exec /home/ray/.local/bin/code tunnel --accept-server-license-terms --name "$1"
' bash "$container"
