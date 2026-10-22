#!/usr/bin/env bash
# Everything CI checks, in one file you can run by hand identically. The CI
# calls nothing else.
#
# Sections: the repository has its shape (required files, licence terms);
# then ruff and pytest. Nothing here needs a GPU, Docker or a network.
set -u
RED() { echo "$1"; echo "FAILED"; exit 1; }
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

# 1. Shape: the files a reader is entitled to find, and a licence that holds
#    its terms rather than a notice pointing at them.
missing=0
for f in README.md LICENSE CHANGELOG.md CONTRIBUTING.md .gitignore; do
  [ -f "$f" ] || { echo "missing: $f"; missing=1; }
done
[ "$missing" = 0 ] || RED "required files missing"
declared=$(grep -m1 -E '^\s*license\s*=' pyproject.toml | grep -oE 'MIT' | head -1)
[ "$declared" = MIT ] || RED "pyproject.toml does not declare the MIT license"
grep -qF 'Permission is hereby granted, free of charge' LICENSE \
  || RED "LICENSE holds a notice, not the MIT terms"
echo "OK  shape, license MIT with its terms"

# 2. Code: ruff at the version pyproject pins, then the tests.
#    With uv, the environment is synced from pyproject; without it, the tools
#    already on the PATH are used (`pip install -e '.[dev]'` provides them).
if command -v uv >/dev/null; then
  uv sync --all-extras -q || RED "uv sync failed"
  RUN="uv run"
else
  RUN=""
fi
$RUN ruff check . || RED "ruff"
timeout 300 $RUN pytest -q -p no:cacheprovider || RED "pytest"
echo "OK  ruff, pytest"
echo "PASSED"
