#!/bin/sh
# Compile every Java fixture without network access and prove class files exist.
set -eu
if [ "${1:-}" != "--offline" ] || [ "$#" -ne 1 ]; then
  echo "usage: $0 --offline" >&2
  exit 2
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for fixture in coupon-core merchant-service coupon-contract; do
  project="$root/fixtures/java-microservices/$fixture"
  (cd "$project" && ./mvnw --offline test)
  test -d "$project/target/classes"
  test "$(find "$project/target/classes" -name '*.class' -type f | wc -l | tr -d ' ')" -gt 0
done
echo "verified 3 Java fixtures offline with JDK $(java -version 2>&1 | sed -n '1s/.*\"\([^\"]*\)\".*/\1/p')"
