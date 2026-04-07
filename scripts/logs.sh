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
echo -e "${DIM}Server: /tmp/jarvis-clean.log | Desktop: /tmp/jarvis-desktop.log${RST}"
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

# Noise suppression — always strip ALSA/JACK/OpenCV noise
NOISE_PAT='ALSA|JACK|OpenCV|ioctl|VIDEOIO|obsensor|JackShm|Cannot connect to server|jack server|pcm_d|pcm\.c|snd_pcm|CONSOLE LOG|CONSOLE WARN|libayatana|appindicator|Gtk-WARNING|GLib-WARNING|BusyIndicator'

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
    printf DIM line RST "\n"

  # Ulrich speaking (voice or text) — bright white, platform badge colored
  } else if (line ~ /^\[Ulrich/) {
    # Colorize the [Ulrich:platform] tag
    sub(/\[Ulrich:desktop\]/, BCYN "[ULRICH:DESKTOP]" RST BWHT, line)
    sub(/\[Ulrich:web\]/, CYN "[ULRICH:WEB]" RST BWHT, line)
    sub(/\[Ulrich\]/, CYN "[ULRICH]" RST BWHT, line)
    printf BWHT line RST "\n"

  # JARVIS speaking / TTS
  } else if (line ~ /\[JARVIS\] (Speaking|Hearing|Ambient)/) {
    printf MAG line RST "\n"

  # JARVIS LLM response
  } else if (line ~ /\[JARVIS\] Response:/) {
    printf GRN line RST "\n"

  # Tool calls / results
  } else if (line ~ /\[JARVIS\] (Tool:|Result:)/) {
    printf YLW line RST "\n"

  # Errors
  } else if (line ~ /[Ee]rror|[Ff]ail|Exception|Traceback/) {
    printf RED line RST "\n"

  # General JARVIS system messages
  } else if (line ~ /^\[JARVIS\]/) {
    printf CYN line RST "\n"

  # Everything else
  } else {
    printf DIM line RST "\n"
  }
  fflush()
}
'

# Collect available log files
LOG_FILES=()
[ -f /tmp/jarvis-clean.log ]   && LOG_FILES+=(/tmp/jarvis-clean.log)
[ -f /tmp/jarvis-desktop.log ] && LOG_FILES+=(/tmp/jarvis-desktop.log)

if [ ${#LOG_FILES[@]} -eq 0 ]; then
  echo -e "${RED}No log files found. Start JARVIS first with ./scripts/start-jarvis.sh${RST}"
  exit 1
fi

tail -f "${LOG_FILES[@]}" 2>/dev/null \
  | sed -u \
      -e 's|==> /tmp/jarvis-clean\.log <==|--- SERVER ---|' \
      -e 's|==> /tmp/jarvis-desktop\.log <==|--- DESKTOP ---|' \
  | grep --line-buffered -Ev "$NOISE_PAT" \
  | grep --line-buffered -E "$MODE_PAT" \
  | awk "$AWK_PROG"
