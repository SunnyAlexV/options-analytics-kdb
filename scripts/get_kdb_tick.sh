#!/usr/bin/env bash
# Download KX's standard kdb+tick scripts (tick.q, tick/u.q, tick/r.q) into q/.
#
# Why download instead of committing them: KX's kdb-tick repository has no
# licence file, so we don't redistribute their code in this public repo.
# KX's own README recommends exactly this: download the code and keep it under
# your own version control and testing. The files are pinned to one commit and
# checked against SHA-256 hashes, so everyone runs byte-identical code.
#
# Run once from the project folder:   bash scripts/get_kdb_tick.sh
set -euo pipefail

COMMIT=85c08ff192b0a103b323246c7300a37919be6159        # KxSystems/kdb-tick, 13 Aug 2024
BASE="https://raw.githubusercontent.com/KxSystems/kdb-tick/$COMMIT"
cd "$(dirname "$0")/../q"
mkdir -p tick

fetch() {   # fetch <path> <sha256>
  curl -fsSL "$BASE/$1" -o "$1"
  echo "$2  $1" | sha256sum --check --quiet || { echo "Hash mismatch for $1"; exit 1; }
  echo "  ok  q/$1"
}

echo "Fetching KX kdb+tick @ ${COMMIT:0:7}"
fetch tick.q   a1bd144d1650539bb653c868490cef0ba7044657113ca18919d6a250eda91c6c
fetch tick/u.q 053847464cc7de7eb370ad0fafa2dd4e25d4930525a16bd8a4e53bef61438e4e
fetch tick/r.q c492dffbc5dfdc65955db4ff763b5b38dce6987745858e6827324af6893a30b0
