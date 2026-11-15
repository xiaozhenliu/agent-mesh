#!/usr/bin/env bash
set -eu
set -o pipefail

cd "$(dirname "$0")/.."

case "${1:-}" in
  "") full=1 ;;
  --syntax-only) full=0 ;;
  *) echo "usage: bash scripts/check_shell.sh [--syntax-only]" >&2; exit 2 ;;
esac

if ! list="$(find . -type f -name '*.sh' -not -path './.git/*' -not -path './scripts/msg.sh' | LC_ALL=C sort)"; then
  echo "FAIL could not list the shell scripts under $(pwd)" >&2
  exit 1
fi

scripts=()
while IFS= read -r f; do
  if [ -n "$f" ]; then
    scripts+=("${f#./}")
  fi
done <<< "$list"

if [ "${#scripts[@]}" -eq 0 ]; then
  echo "FAIL no shell scripts found under $(pwd)" >&2
  exit 1
fi

for f in "${scripts[@]}" scripts/msg.sh; do
  bash -n "$f"
done
echo "PASS bash -n: ${#scripts[@]} scripts and scripts/msg.sh"

[ "$full" -eq 1 ] || exit 0

if ! command -v shellcheck >/dev/null; then
  echo "FAIL shellcheck not found; install it, or run with --syntax-only to check syntax only" >&2
  exit 1
fi
shellcheck -x --source-path=SCRIPTDIR "${scripts[@]}"
shellcheck -s bash -e SC2059 scripts/msg.sh
echo "PASS shellcheck: ${#scripts[@]} scripts and scripts/msg.sh"
