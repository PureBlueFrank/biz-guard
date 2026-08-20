#!/usr/bin/env sh
set -eu
offline=false
fixture=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --offline) offline=true ;;
    --fixture) fixture="$2"; shift ;;
    *) exit 2 ;;
  esac
  shift
done
[ "$offline" = true ] && [ -n "$fixture" ]
python -m bizguard.cli doctor --json
python -m bizguard.ci.check --diff "$fixture" --base-revisions bench/fixtures/phase3-revisions.yaml --json
