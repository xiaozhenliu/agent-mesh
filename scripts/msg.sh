MSG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/messages"

msg_format() {
  local files=("$2"/*.tsv)
  [ -e "${files[0]}" ] || return 1
  awk -F '\t' -v k="$1" '$1 == k { sub(/^[^\t]*\t/, ""); print; found = 1; exit } END { exit !found }' "${files[@]}"
}

msg_lang() {
  case "${AGENT_MESH_LANG:-en}" in zh-CN) echo zh-CN ;; *) echo en ;; esac
}

msg_lookup() {
  msg_format "$1" "$MSG_DIR/$(msg_lang)" || msg_format "$1" "$MSG_DIR/en" || printf '%s' "$1"
}

tq() {
  local fmt
  fmt="$(msg_lookup "$1")"
  shift
  if [ $# -gt 0 ]; then
    printf -- "$fmt" "$@"
  else
    printf -- "$fmt"
  fi
}

t() { tq "$@"; printf '\n'; }
