# shellcheck shell=bash disable=SC2034
BOT_STATUS_KEYS=" kind stage webhook relay prompt cloud removing record address "

bot_status_read() {
  BS_NAME=""; BS_KIND=""; BS_STAGE=""; BS_WEBHOOK=""; BS_RELAY=""; BS_PROMPT=""; BS_CLOUD=""; BS_REMOVING=""
  BS_ADDRESS=""; BS_KEYS=" "
  [ "$#" -ge 2 ] && [ "$1" = bot ] || return 1
  BS_NAME="$2"
  shift 2
  while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || return 1
    case "$BOT_STATUS_KEYS" in
      *" $1 "*)
        case "$BS_KEYS" in *" $1 "*) return 1 ;; esac
        BS_KEYS="$BS_KEYS$1 "
        case "$1" in
          kind) BS_KIND="$2" ;; stage) BS_STAGE="$2" ;; webhook) BS_WEBHOOK="$2" ;; relay) BS_RELAY="$2" ;;
          prompt) BS_PROMPT="$2" ;; cloud) BS_CLOUD="$2" ;; removing) BS_REMOVING="$2" ;; address) BS_ADDRESS="$2" ;;
        esac ;;
    esac
    shift 2
  done
}

bot_status_has() {
  local key
  for key in "$@"; do
    case "$BS_KEYS" in *" $key "*) ;; *) return 1 ;; esac
  done
}
