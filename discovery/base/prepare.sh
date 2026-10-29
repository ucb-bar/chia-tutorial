#!/usr/bin/env bash
set -euo pipefail
cd /home/ray/chipyard
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'
test "$(git rev-parse HEAD)" = "$1"
mkdir -p /seed/tree
git ls-files --recurse-submodules -z |
    tar --null --no-recursion -T - -cf - |
    tar -C /seed/tree -xf -

# Preserve the existing repositories, including submodule history and relative links.
cp -a --no-preserve=ownership .git /seed/tree/.git
git submodule foreach --quiet --recursive \
    'cp -a --no-preserve=ownership .git "/seed/tree/$displaypath/.git"'
cd /seed/tree
git apply /base/chipyard.patch
mkdir -p tests/memory-tests
cp /base/memory-tests/Makefile /base/memory-tests/workloads.json /base/memory-tests/check.py tests/memory-tests/
cp -a /base/memory-tests/src /base/memory-tests/support tests/memory-tests/

# Record the tutorial baseline once; candidate patches are relative to these commits.
export GIT_AUTHOR_NAME='CHIA Tutorial' GIT_COMMITTER_NAME='CHIA Tutorial'
export GIT_AUTHOR_EMAIL='tutorial@chia.local' GIT_COMMITTER_EMAIL='tutorial@chia.local'
export GIT_AUTHOR_DATE="$(git show -s --format=%cI HEAD)"
export GIT_COMMITTER_DATE="$GIT_AUTHOR_DATE"
git submodule foreach --quiet --recursive 'printf "%s\n" "$displaypath"' |
    sort -r | while IFS= read -r repository; do
        git -C "$repository" add --all
        git -C "$repository" -c core.hooksPath=/dev/null commit --quiet --no-gpg-sign --allow-empty -m 'Tutorial baseline'
    done
git add --all
git add --force tests/memory-tests
git -c core.hooksPath=/dev/null commit --quiet --no-gpg-sign --allow-empty -m 'Tutorial baseline'
