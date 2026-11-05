#!/usr/bin/env bash
# Remove what install.sh put on this Mac.
#   ./uninstall.sh        the relay: login service, state and copies, logs, ~/.config/agent-relay (webhook env + key)
#   ./uninstall.sh --all  also leave the team, unwire the agents (setup --remove, plus the dsh bridge block if present),
#                         remove ~/.reason/network and the binary
# Add -y to skip the confirmation. The cloud side is separate: pause or delete the routine in Grok Bot and remove
# /workspace/agent-mesh on the cloud machine.
set -uo pipefail
LABEL="com.local.agent-relay"
RN="$HOME/.local/bin/reason-network"
DSH_PATCH="$HOME/.dsh/cordis.patch.yml"
ALL=0; YES=0
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    -y|--yes) YES=1 ;;
    *) echo "usage: $0 [--all] [-y]"; exit 2 ;;
  esac
done
[ "$(uname -s)" = Darwin ] || { echo "[LOCAL] this uninstaller requires macOS"; exit 1; }
[ "$(id -u)" != 0 ] || { echo "run as the logged-in Mac user, without sudo"; exit 1; }
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || { echo "custom REASON_NETWORK_HOME is not supported; review your deployment before uninstalling"; exit 1; }
export REASON_NETWORK_HOME="$HOME/.reason/network"
echo "Will remove: login service $LABEL, ~/.local/lib/agent-mesh, ~/Library/Application Support/agent-relay,"
echo "             ~/Library/Logs/agent-relay*.log*, ~/.config/agent-relay (webhook env and signing key)"
[ "$ALL" = 1 ] && echo "        and: leave the team, setup --remove, ~/.reason/network, $RN"
[ "$ALL" = 1 ] && grep -qs 'reason-network-bridge' "$DSH_PATCH" && echo "        and: the dsh bridge block in $DSH_PATCH"
if [ "$YES" != 1 ]; then
  read -r -p "Continue? [y/N] " ans
  [[ "$ans" == [yY]* ]] || { echo "cancelled"; exit 1; }
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/.local/lib/agent-mesh" "$HOME/.config/agent-relay" "$HOME/Library/Application Support/agent-relay"
rm -f "$HOME"/Library/Logs/agent-relay*.log*
echo "relay removed."

if [ "$ALL" = 1 ]; then
  if grep -qs 'reason-network-bridge' "$DSH_PATCH"; then
    "$BASH" "$(dirname "$0")/dsh/mac-uninstall.sh" --no-restore || {
      echo "dsh bridge not removed, so Reason Network was left in place; fix $DSH_PATCH and run ./uninstall.sh --all again"
      exit 1
    }
  fi
  if [ -x "$RN" ]; then
    "$RN" leave --force
    "$RN" setup --remove
  fi
  rm -rf "$HOME/.reason/network"
  rm -f "$RN"
  echo "Reason Network removed. Reopen your agent sessions. To start over with new names, run ./install.sh again,"
  echo "re-invite the cloud machine and copy the new outbound.key to it."
fi
