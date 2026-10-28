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
mkdir -p tests/memory-tests
cp /base/memory-tests/Makefile /base/memory-tests/workloads.json /base/memory-tests/check.py tests/memory-tests/
cp -a /base/memory-tests/src /base/memory-tests/support tests/memory-tests/
cd /home/ray/chipyard
git submodule foreach --quiet --recursive \
    'mkdir -p "/seed/tree/$displaypath"; printf "# Initialized image source\n" > "/seed/tree/$displaypath/.git"'
printf '# Initialized image source\n' > /seed/tree/.git
