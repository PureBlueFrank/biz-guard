#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for the production Compose verification" >&2
  exit 2
fi

verification_root="$(mktemp -d)"
export COMPOSE_PROJECT_NAME="bizguard-ci-${GITHUB_RUN_ID:-local}-$$"

cleanup() {
  docker compose --project-directory "$project_root" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$verification_root"
}
trap cleanup EXIT

governance_root="$verification_root/governance"
secret_root="$verification_root/secrets"
mkdir -p "$governance_root/knowledge/published" "$governance_root/knowledge/invariants" "$secret_root"
cp "$project_root/src/bizguard/semantic/catalog.yaml" "$governance_root/catalog.yaml"
cp "$project_root/policy/phase5-registry.yaml" "$governance_root/policy-registry.yaml"
cp "$project_root/registry/contracts.yaml" "$governance_root/contracts.yaml"
cp "$project_root/policy/invariants.yaml" "$governance_root/invariants.yaml"
cp "$project_root/policy/calibration-gates.yaml" "$governance_root/calibration-gates.yaml"
cp "$project_root/policy/calibration-public-key.pem" "$governance_root/calibration-public-key.pem"
cp "$project_root"/knowledge/published/*.md "$governance_root/knowledge/published/"
cp "$project_root"/knowledge/*.md "$governance_root/knowledge/invariants/"

printf '%s\n' 'bizguard-test-only' > "$secret_root/database-password.txt"
printf '%s\n' 'postgresql://bizguard:bizguard-test-only@postgres:5432/bizguard' > "$secret_root/database-url.txt"
printf '%s\n' 'ci-not-a-real-zhipu-key' > "$secret_root/zhipu-api-key.txt"
chmod 600 "$secret_root"/*.txt

export BIZGUARD_REPOSITORY_PATH="$project_root/fixtures/java-microservices"
export BIZGUARD_GOVERNANCE_PATH="$governance_root"
export BIZGUARD_DATABASE_URL_FILE="$secret_root/database-url.txt"
export BIZGUARD_DATABASE_PASSWORD_FILE="$secret_root/database-password.txt"
export BIZGUARD_ZHIPU_API_KEY_FILE="$secret_root/zhipu-api-key.txt"
export BIZGUARD_ALLOWED_HOSTS="127.0.0.1:8000"
export BIZGUARD_AUTH_ISSUER_URL="https://auth.example.test"
export BIZGUARD_AUTH_JWKS_URL="https://auth.example.test/jwks.json"
export BIZGUARD_AUTH_AUDIENCE="https://bizguard.example.test/mcp"
export BIZGUARD_RESOURCE_URL="https://bizguard.example.test/mcp"
export BIZGUARD_CALLER_IDENTITY="compose-smoke-test"
export BIZGUARD_CALLER_ROLES="engineering"

docker compose --project-directory "$project_root" config --quiet
docker compose --project-directory "$project_root" up --build --detach --wait --wait-timeout 180
curl --fail --silent --show-error http://127.0.0.1:8000/healthz >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8000/readyz >/dev/null
docker compose --project-directory "$project_root" run --rm migrate >/dev/null
docker compose --project-directory "$project_root" exec -T postgres \
  psql -U bizguard -d bizguard -v ON_ERROR_STOP=1 -Atc \
  "SELECT version FROM bizguard_schema_migrations ORDER BY version" \
  | grep -qx '001_initial.sql'
