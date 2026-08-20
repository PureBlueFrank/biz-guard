#!/bin/sh
# JDK-only, offline Maven-wrapper fallback. It performs real Java compilation
# for these deliberately dependency-free fixture projects.
set -eu
project_dir=${FIXTURE_PROJECT_DIR:?FIXTURE_PROJECT_DIR is required}
case "${1:-}" in
  --offline|test|package|verify) ;;
  *) echo "usage: ./mvnw --offline [test|package]" >&2; exit 2 ;;
esac
target="$project_dir/target/classes"
mkdir -p "$target"
sources=$(find "$project_dir/src/main/java" -name '*.java' -type f | sort)
[ -n "$sources" ]
# shellcheck disable=SC2086
javac --release 17 -d "$target" $sources
test_sources=$(find "$project_dir/src/test/java" -name '*Test.java' -type f 2>/dev/null | sort || true)
if [ -n "$test_sources" ]; then
  test_target="$project_dir/target/test-classes"
  mkdir -p "$test_target"
  # shellcheck disable=SC2086
  javac --release 17 -cp "$target" -d "$test_target" $test_sources
  find "$project_dir/src/test/java" -name '*Test.java' -type f | while IFS= read -r source; do
    class=$(sed -n 's/^package \(.*\);$/\1/p' "$source" | head -n 1).$(basename "$source" .java)
    java -ea -cp "$target:$test_target" "$class"
  done
fi
