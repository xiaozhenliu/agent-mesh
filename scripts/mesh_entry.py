"""The agent-mesh command in ~/.local/bin: runs update.sh, check.sh or install.sh --rename in the recorded checkout."""
import os
import shlex
import tempfile
from pathlib import Path

from messages import t

MARK = "# agent-mesh-entry v1"
TEMPLATE = """#!/bin/bash
{mark}: written by install.sh and update.sh of agent-mesh; uninstall.sh --level program removes it.
set -u
case "${{1:-}}" in
  update) script=update.sh ;;
  check) script=check.sh ;;
  rename) script=install.sh ;;
  *) printf '%s\\n' {usage} >&2; exit 2 ;;
esac
shift
dir="$(python3 -I -c 'import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get("checkout")
except (OSError, ValueError, AttributeError):
    value = None
print(value if isinstance(value, str) else "")' "$HOME/.local/lib/agent-mesh/deployed.json")" || dir=""
if [ -z "$dir" ] || [ ! -f "$dir/$script" ]; then
  printf {gone} "${{dir:-?}}" >&2
  exit 1
fi
if [ "$script" = install.sh ]; then exec "$BASH" "$dir/$script" --rename "$@"; fi
exec "$BASH" "$dir/$script" "$@"
"""


def path(home):
    return Path(home) / ".local/bin/agent-mesh"


def ours(target):
    try:
        with open(target, encoding="utf-8", errors="replace") as stream:
            head = [stream.readline() for _ in range(2)]
    except OSError:
        return False
    return not Path(target).is_symlink() and any(line.startswith(MARK) for line in head)


def text(lang):
    return TEMPLATE.format(mark=MARK, usage=shlex.quote(t("entry.usage", lang=lang)),
                           gone=shlex.quote(t("entry.gone", lang=lang) + "\\n"))


def write(home, lang):
    target = path(home)
    if target.is_symlink() or (target.exists() and not ours(target)):
        return t("entry.foreign", target, lang=lang)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = text(lang)
    if target.is_file() and target.read_text(encoding="utf-8", errors="replace") == body:
        os.chmod(target, 0o755)
    else:
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".agent-mesh.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body)
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    folders = [os.path.realpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.realpath(target.parent) not in folders:
        return t("entry.no_path", target.parent, lang=lang)
    return None
