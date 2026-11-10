#!/usr/bin/env bash
# Remove agent-mesh from this Mac and, guided, from the Bot's cloud computer. Without options it only shows
# what is installed; nothing is removed until you choose a level and confirm it.
#   ./uninstall.sh --plan [--level L]        what is installed and what a level would do; removes nothing
#   ./uninstall.sh --level program [--yes]   the relay program only; team, settings, key and cloud stay
#   ./uninstall.sh --level identity          also leave the team on both computers, remove keys and identity
#   ./uninstall.sh --level all               also remove logs, the engine and the cloud's data (--all for short)
#   ./uninstall.sh --residual --level identity|all   no identity left on this Mac (e.g. after an older --all)
#   ./uninstall.sh --cloud-prompt --level identity|all   only the cloud step again
#   --lang en|zh-CN                          language of the printed cloud prompt
# Levels identity and all run only in your own terminal and ask you to type a confirmation.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
die() { echo "uninstall: $*" >&2; exit 1; }
case "${1:-}" in -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
[ "$(uname -s)" = Darwin ] || die "[LOCAL] this uninstaller requires macOS"
[ "$(id -u)" != 0 ] || die "run as the logged-in Mac user, without sudo"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || die "custom REASON_NETWORK_HOME is not supported; review your deployment before uninstalling"
[[ "$HOME" = /* ]] && [ -d "$HOME" ] || die "HOME must be an absolute home directory"
command -v python3 >/dev/null || die "python3 is missing; see README prerequisites"
PYTHON="$(python3 -c 'import sys; print(sys.executable)')" || die "python3 cannot start"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "Python 3.9+ is required"
export REASON_NETWORK_HOME="$HOME/.reason/network"
exec "$PYTHON" "$HERE/scripts/uninstall.py" "$@"
