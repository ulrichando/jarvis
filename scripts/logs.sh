#!/usr/bin/env bash
# JARVIS Log Monitor — live view of all JARVIS activity
# Usage:
#   ./scripts/logs.sh          # all logs
#   ./scripts/logs.sh voice    # Ulrich speech only
#   ./scripts/logs.sh llm      # LLM queries & responses
#   ./scripts/logs.sh tools    # tool calls
#   ./scripts/logs.sh errors   # errors only

MODE="${1:-all}"

# ANSI
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'
CYN='\033[0;36m'; MAG='\033[0;35m'; DIM='\033[2m'; RST='\033[0m'

echo -e "${CYN}━━━ JARVIS Log Monitor [${MODE}] ━━━${RST}"
echo -e "${DIM}Server: /tmp/jarvis-web.log | Desktop: /tmp/jarvis-desktop.log${RST}"
echo -e "${DIM}Ctrl+C to stop${RST}"
echo ""

# Build mode filter pattern
case "$MODE" in
  voice)  MODE_PAT='Ulrich|STT|Heard|Whisper|VAD|Ambient|transcript|echo' ;;
  llm)    MODE_PAT='Response:|Brain|think|model|stream|JARVIS.*query' ;;
  tools)  MODE_PAT='Tool:|Result:|tool_call|bash|read_file|write_file|search' ;;
  errors) MODE_PAT='[Ee]rror|[Ff]ail|Exception|Traceback|WARNING' ;;
  *)      MODE_PAT='.' ;;
esac

# Noise suppression — strip ALSA/JACK/OpenCV noise + Python ResourceWarnings
NOISE_PAT='ALSA|JACK|OpenCV|ioctl|VIDEOIO|obsensor|JackShm|Cannot connect to server|jack server|pcm_d|pcm\.c|snd_pcm|CONSOLE LOG|CONSOLE WARN|libayatana|appindicator|Gtk-WARNING|GLib-WARNING|BusyIndicator|ResourceWarning|tracemalloc|PyGIDeprecation|RuntimeWarning|coroutine.*never awaited|pydantic|gi\.overrides|frozen importlib|asyncio\.runners|base_events|site-packages'

# awk colorizer — runs in-process, no subshells, fully line-buffered
AWK_PROG='
BEGIN {
  RED  = "\033[0;31m"
  GRN  = "\033[0;32m"
  YLW  = "\033[0;33m"
  CYN  = "\033[0;36m"
  MAG  = "\033[0;35m"
  BLU  = "\033[0;34m"
  BCYN = "\033[1;36m"
  BWHT = "\033[1;37m"
  DIM  = "\033[2m"
  RST  = "\033[0m"
}
{
  line = $0

  # Platform separator lines
  if (line ~ /^--- (SERVER|DESKTOP) ---$/) {
    printf "%s%s%s\n", DIM, line, RST

  # Ulrich speaking (voice or text) — bright white, platform badge colored
  } else if (line ~ /^\[Ulrich/) {
    sub(/\[Ulrich:desktop\]/, BCYN "[ULRICH:DESKTOP]" RST BWHT, line)
    sub(/\[Ulrich:web\]/, CYN "[ULRICH:WEB]" RST BWHT, line)
    sub(/\[Ulrich\]/, CYN "[ULRICH]" RST BWHT, line)
    printf "%s%s%s\n", BWHT, line, RST

  # JARVIS speaking / TTS
  } else if (line ~ /\[JARVIS\] (Speaking|Hearing|Ambient)/) {
    printf "%s%s%s\n", MAG, line, RST

  # JARVIS LLM response
  } else if (line ~ /\[JARVIS\] Response:/) {
    printf "%s%s%s\n", GRN, line, RST

  # Tool calls / results
  } else if (line ~ /\[JARVIS\] (Tool:|Result:)/) {
    printf "%s%s%s\n", YLW, line, RST

  # Errors
  } else if (line ~ /[Ee]rror|[Ff]ail|Exception|Traceback/) {
    printf "%s%s%s\n", RED, line, RST

  # General JARVIS system messages
  } else if (line ~ /^\[JARVIS\]/) {
    printf "%s%s%s\n", CYN, line, RST

  # Everything else
  } else {
    printf "%s%s%s\n", DIM, line, RST
  }
  fflush()
}
'

# Collect available log files
LOG_FILES=()
[ -f /tmp/jarvis-web.log ]     && LOG_FILES+=(/tmp/jarvis-web.log)
[ -f /tmp/jarvis-clean.log ]   && LOG_FILES+=(/tmp/jarvis-clean.log)
[ -f /tmp/jarvis-desktop.log ] && LOG_FILES+=(/tmp/jarvis-desktop.log)

if [ ${#LOG_FILES[@]} -eq 0 ]; then
  echo -e "${RED}No log files found. Start JARVIS first with ./scripts/start-jarvis.sh${RST}"
  exit 1
fi

tail -f "${LOG_FILES[@]}" 2>/dev/null \
  | sed -u \
      -e 's|==> /tmp/jarvis-web\.log <==|--- SERVER ---|' \
      -e 's|==> /tmp/jarvis-clean\.log <==|--- SERVER ---|' \
      -e 's|==> /tmp/jarvis-desktop\.log <==|--- DESKTOP ---|' \
  | grep --line-buffered -Ev "$NOISE_PAT" \
  | grep --line-buffered -E "$MODE_PAT" \
  | awk "$AWK_PROG"
