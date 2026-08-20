#!/usr/bin/env sh
set -eu
offline=false
fixture="bench/fixtures/phase5/cross-service-dto-breaking.diff"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --offline) offline=true ;;
    --fixture) fixture="$2"; shift ;;
    *) exit 2 ;;
  esac
  shift
done
[ "$offline" = true ] || { echo "verify_install.sh requires --offline" >&2; exit 2; }
[ -f "$fixture" ] || { echo "fixture does not exist: $fixture" >&2; exit 2; }
python -m bizguard.cli doctor --json
python -m bizguard.ci.check --diff "$fixture" --base-revisions bench/fixtures/phase3-revisions.yaml --json
