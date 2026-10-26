#!/usr/bin/env bash
set -euo pipefail
cd /home/ray/chipyard
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'
test "$(git rev-parse HEAD)" = "$1"
mkdir -p /seed/tree
git ls-files --recurse-submodules -z |
    tar --null --no-recursion -T - -cf - |
    tar -C /seed/tree -xf -
cd /seed/tree
git apply /base/chipyard.patch
cd /home/ray/chipyard
git submodule foreach --quiet --recursive \
    'mkdir -p "/seed/tree/$displaypath"; printf "# Initialized image source\n" > "/seed/tree/$displaypath/.git"'
printf '# Initialized image source\n' > /seed/tree/.git
