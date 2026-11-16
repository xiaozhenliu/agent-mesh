# shellcheck shell=bash
# Sourced after scripts/msg.sh by install.sh, update.sh and uninstall.sh. mesh_lock_take PYTHON takes the management
# lock (scripts/mesh_lock.py) on fd 9 for the life of this shell, or, with AGENT_MESH_LOCK_FD=9 from the command that
# called us, checks that fd 9 already holds it. Commands that may start the engine daemon run with 9>&- so the daemon
# never keeps the lock.
MESH_LOCKFILE="$HOME/.local/lib/agent-mesh.lock"
MESH_LOCK_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mesh_lock.py"

mesh_lock_take() {
  local python="$1" rc tries=0
  if [ -n "${AGENT_MESH_LOCK_FD:-}" ]; then
    [ "$AGENT_MESH_LOCK_FD" = 9 ] || { t lock.not_inherited "$AGENT_MESH_LOCK_FD" "$MESH_LOCKFILE" >&2; return 1; }
    "$python" "$MESH_LOCK_HELPER" inherit --fd 9 --path "$MESH_LOCKFILE" || return 1
    return 0
  fi
  mkdir -p "$(dirname "$MESH_LOCKFILE")" || { t lock.failed "$MESH_LOCKFILE" mkdir >&2; return 1; }
  while :; do
    exec 9>>"$MESH_LOCKFILE" || { t lock.failed "$MESH_LOCKFILE" open >&2; return 1; }
    rc=0
    "$python" "$MESH_LOCK_HELPER" acquire --fd 9 --path "$MESH_LOCKFILE" --wait "${AGENT_MESH_LOCK_WAIT:-0}" \
      --legacy-dir "$HOME/.local/lib/agent-mesh/update.lock" || rc=$?
    case "$rc" in
      0) export AGENT_MESH_LOCK_FD=9; return 0 ;;
      5) exec 9>&-
         [ "$tries" = 0 ] || { t lock.moved "$MESH_LOCKFILE" >&2; return 1; }
         tries=1 ;;
      *) exec 9>&-; return 1 ;;
    esac
  done
}
