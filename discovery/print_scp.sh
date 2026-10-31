#!/usr/bin/env bash
# Print an scp command that copies a file from this VM to the user's local machine.
# Usage: ./print_scp.sh <file> [local destination, default .]
set -euo pipefail

C=$'\033[36m'; R=$'\033[0m'

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <file> [local destination]" >&2
  exit 1
fi

FILE=$(realpath "$1")
DEST=${2:-.}
[[ -e "$FILE" ]] || { echo "no such file: $FILE" >&2; exit 1; }

SSH_USER=${SSH_USER:-$USER}
DEV_IP=${DEV_IP:-$(curl -sf -m 2 -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)}

RECURSIVE=""
[[ -d "$FILE" ]] && RECURSIVE="-r "

echo "Run this on your local machine:"
echo
echo "  ${C}scp ${RECURSIVE}-o IdentitiesOnly=yes -i <path to your GCP key> $SSH_USER@${DEV_IP:-EXTERNAL_IP}:$FILE $DEST${R}"
echo
