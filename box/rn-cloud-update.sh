#!/usr/bin/env bash
set -uo pipefail

main() {
  local A r=agent-mesh-result after=0
  A="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || { echo "STOP: cannot find the clone of this script"; exit 1; }
  stop() { echo "STOP: $*"; exit 1; }
  bounded() { if command -v timeout >/dev/null; then timeout "$@"; else shift; "$@"; fi; }
  older() {
    local IFS=. i a b
    read -r -a a <<< "$1"
    read -r -a b <<< "$2"
    for i in 0 1 2; do
      [ "$((10#${a[$i]}))" -lt "$((10#${b[$i]}))" ] && return 0
      [ "$((10#${a[$i]}))" -gt "$((10#${b[$i]}))" ] && return 1
    done
    return 1
  }
  if [ "${1:-}" = --after-checkout ]; then after=1; shift; fi
  if [ $# != 3 ]; then
    echo "usage: rn-cloud-update.sh <vX.Y.Z> <commit> <request>" >&2
    exit 2
  fi
  local TAG="$1" COMMIT="$2" REQ="$3" st cur got head sums
  [[ "$REQ" =~ ^[0-9a-f]{8,32}$ ]] || stop "the request id must be 8 to 32 lowercase hex characters; nothing was changed"
  [ "$after" = 1 ] || echo "$r request $REQ"
  [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || stop "the tag must look like v1.2.3; nothing was changed"
  [[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || stop "the commit must be 40 lowercase hex characters; nothing was changed"
  cd "$A" || stop "no clone at $A; nothing was changed"
  export GIT_TERMINAL_PROMPT=0
  if [ "$after" = 0 ]; then
    st="$(git status --porcelain)" || stop "$A is not a git clone; nothing was changed"
    if [ -n "$st" ]; then
      echo "changed files in $A:"
      printf '%s\n' "$st" | head -n 20 | sed 's/^/  /'
      stop "$A has local changes; nothing was changed. Ask the user what to do with them"
    fi
    cur="$(tr -d '[:space:]' < VERSION)" || stop "cannot read $A/VERSION; nothing was changed"
    [[ "$cur" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || stop "$A/VERSION is not a version; nothing was changed"
    if older "${TAG#v}" "$cur"; then
      stop "this clone is at $cur, newer than $TAG; it is never downgraded; nothing was changed"
    fi
    echo "origin: $(git remote get-url origin)"
    bounded 120 git fetch -q --no-tags origin "refs/tags/$TAG:refs/tags/$TAG" </dev/null \
      || stop "could not fetch $TAG, or a different tag $TAG is already here; nothing was changed"
    got="$(git rev-parse -q --verify "refs/tags/$TAG^{commit}")" || stop "$TAG is not a commit; nothing was changed"
    [ "$got" = "$COMMIT" ] || stop "$TAG points to $got, not $COMMIT; nothing was changed"
    git checkout -q --detach "$COMMIT" </dev/null || stop "checking out $TAG failed; run git status in $A before anything else"
    exec bash "$A/box/rn-cloud-update.sh" --after-checkout "$TAG" "$COMMIT" "$REQ"
  fi
  head="$(git rev-parse HEAD)" || stop "cannot read HEAD"
  [ "$head" = "$COMMIT" ] || stop "HEAD is $head, not $COMMIT"
  bash "$A/box/rn-cloud-setup.sh" --upgrade-engine </dev/null \
    || stop "the engine step failed (see the lines above); the clone stays at $TAG"
  sums="$(sha256sum box/rn-cloud.sh box/rn-fetch.py box/rn-cloud-setup.sh box/rn-engine.py)" \
    || stop "cannot compute the checksums after checking out $TAG"
  echo "$r commit $head"
  printf '%s\n' "$sums" | sed "s/^/$r sum /"
  echo "$r DONE $TAG"
}

main "$@"
exit
