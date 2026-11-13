#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/scripts/msg.sh"
die() { echo "uninstall: $*" >&2; exit 1; }
UNINSTALL_LANG=""; prev=""
for arg in "$@"; do
  [ "$prev" != --lang ] || UNINSTALL_LANG="$arg"
  case "$arg" in --lang=*) UNINSTALL_LANG="${arg#--lang=}" ;; esac
  prev="$arg"
done
if [ -n "$UNINSTALL_LANG" ]; then
  case "$UNINSTALL_LANG" in
    en|zh-CN) ;;
    *) printf '%s / %s\n' "$(AGENT_MESH_LANG=en tq uninstall.lang_unsupported "$UNINSTALL_LANG")" \
         "$(AGENT_MESH_LANG=zh-CN tq uninstall.lang_unsupported "$UNINSTALL_LANG")" >&2; exit 2 ;;
  esac
elif command -v python3 >/dev/null; then
  UNINSTALL_LANG="$(python3 "$HERE/scripts/deploy_state.py" lang 2>/dev/null)" || UNINSTALL_LANG=""
fi
if [ -z "$UNINSTALL_LANG" ]; then
  case "${AGENT_MESH_LANG:-}" in zh-CN) UNINSTALL_LANG=zh-CN ;; *) UNINSTALL_LANG=en ;; esac
fi
export AGENT_MESH_LANG="$UNINSTALL_LANG"
for arg in "$@"; do case "$arg" in -h|--help) t uninstall.help; exit 0 ;; esac; done
[ "$(uname -s)" = Darwin ] || die "$(tq uninstall.need_macos)"
[ "$(id -u)" != 0 ] || die "$(tq uninstall.no_sudo)"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || die "$(tq uninstall.custom_rn_home)"
[[ "$HOME" = /* ]] && [ -d "$HOME" ] || die "$(tq uninstall.bad_home)"
command -v python3 >/dev/null || die "$(tq uninstall.no_python)"
PYTHON="$(python3 -c 'import sys; print(sys.executable)')" || die "$(tq uninstall.python_broken)"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "$(tq uninstall.python_old)"
export REASON_NETWORK_HOME="$HOME/.reason/network"
exec "$PYTHON" "$HERE/scripts/uninstall.py" "$@"
