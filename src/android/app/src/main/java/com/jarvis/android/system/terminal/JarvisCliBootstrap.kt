package com.jarvis.android.system.terminal

import android.content.Context
import android.util.Log
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.domain.model.CloudProvider
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Installs a `jarvis` shell script (and companion config) into the app's
 * private filesDir so terminal sessions can invoke the Jarvis CLI the same
 * way the user does on their laptop.
 *
 * The app can't drop a TypeScript/Bun runtime onto the phone, so this is a
 * pragmatic bridge: the script uses `curl` (bundled with Android since
 * API 24) to hit the Groq chat-completions endpoint directly, reading the
 * user's stored API key from a file written here on session start.
 *
 * Contract:
 *   - [binDir]         : $filesDir/bin   (added to PATH by terminal init)
 *   - [envFile]        : $filesDir/jarvis.env  (GROQ_API_KEY, etc.)
 *   - [jarvisScript]   : $filesDir/bin/jarvis  (chmod 755)
 *
 * Called from [TerminalSessionManager.createSession] before the session's
 * init injection runs, so the PATH entry is live when the shell starts.
 */
@Singleton
class JarvisCliBootstrap @Inject constructor(
    @ApplicationContext private val ctx: Context,
    private val apiKeys: ApiKeyProviderImpl,
) {

    val binDir:       File get() = File(ctx.filesDir, "bin")
    val tmpDir:       File get() = File(ctx.filesDir, "tmp")
    val envFile:      File get() = File(ctx.filesDir, "jarvis.env")
    val jarvisScript: File get() = File(binDir, "jarvis")

    /** Write or refresh the script + env. Safe to call repeatedly. */
    fun install() {
        try {
            binDir.mkdirs()
            tmpDir.mkdirs()
            writeEnv()
            writeJarvisScript()
        } catch (t: Throwable) {
            Log.e(TAG, "install failed: ${t.message}", t)
        }
    }

    private fun writeEnv() {
        val groq   = apiKeys.getProviderKey(CloudProvider.GROQ)
        val model  = "openai/gpt-oss-120b"
        envFile.writeText(
            buildString {
                append("# jarvis shell-CLI env — written by JarvisCliBootstrap\n")
                append("# Do not edit by hand; set keys in the app's Settings → Providers.\n")
                append("GROQ_API_KEY='").append(groq).append("'\n")
                append("JARVIS_MODEL='").append(model).append("'\n")
                // /data/local/tmp is not writable by the app uid on modern
                // Android; route tmp to the app's private files/tmp dir.
                append("TMPDIR='").append(tmpDir.absolutePath).append("'\n")
            },
            Charsets.UTF_8,
        )
        envFile.setReadable(true, /*ownerOnly=*/true)
    }

    private fun writeJarvisScript() {
        jarvisScript.writeText(JARVIS_SH, Charsets.UTF_8)
        jarvisScript.setExecutable(true, /*ownerOnly=*/false)
    }

    private companion object {
        const val TAG = "JarvisCliBootstrap"

        // Full REPL shipped as one script. Tools used (all guaranteed on
        // modern Android): /system/bin/sh (mksh), curl, awk, sed, mktemp.
        // Streams Groq Server-Sent Events through an awk SSE parser that
        // prints delta.content as it arrives, keeps a JSON history file
        // for multi-turn context, and handles the usual slash commands.
        val JARVIS_SH = """
#!/system/bin/sh
# jarvis — interactive agent REPL. Talks to the Android app's loopback
# HTTP+SSE server (JarvisLoopbackServer on 127.0.0.1:47811) which in turn
# drives the real agent loop, so every tool the in-app chat can use
# (bash_exec, read_file, web-aware tools, terminal, system info, local
# inference, and the rest of the 23 tools in JarvisToolDispatcher) is
# available from the terminal. Installed by JarvisCliBootstrap.

ENV_FILE="${'$'}{JARVIS_ENV:-/data/data/com.jarvis.android.debug/files/jarvis.env}"
[ -f "${'$'}ENV_FILE" ] && . "${'$'}ENV_FILE"

MODEL="${'$'}{JARVIS_MODEL:-openai/gpt-oss-120b}"
ENDPOINT="${'$'}{JARVIS_ENDPOINT:-http://127.0.0.1:47811/chat}"
GOLD='\033[38;5;220m'
CYAN='\033[38;5;45m'
GREEN='\033[38;5;82m'
RED='\033[38;5;196m'
DIM='\033[38;5;244m'
RST='\033[0m'

# Quick liveness probe — if the server isn't up we can't do anything.
health_ok() {
    curl -s --max-time 2 http://127.0.0.1:47811/health 2>/dev/null | grep -q '^ok${'$'}'
}

# ── SSE parser for the loopback server ─────────────────────────────────
# Each `data:` line carries one JSON event:
#   {"type":"text","content":"…"}        — stream delta; print inline
#   {"type":"tool_start","name":"…"}     — render as "● Name(...)"
#   {"type":"tool_end","result":"…"}     — render as "⎿ <first line>"
#   {"type":"warning","message":"…"}     — yellow line
#   {"type":"error","message":"…"}       — red line, abort
#   {"type":"done"}                      — end of stream
# The awk program uses a tiny JSON extractor for each field it needs.
sse_parse() {
    awk -v gold="${'$'}GOLD" -v cyan="${'$'}CYAN" -v green="${'$'}GREEN" \
        -v red="${'$'}RED" -v dim="${'$'}DIM" -v rst="${'$'}RST" '
    function unesc(s,   out,i,c,n,L) {
        out=""; i=1; L=length(s)
        while (i<=L) {
            c=substr(s,i,1)
            if (c=="\\") {
                n=substr(s,i+1,1)
                if      (n=="n")  out=out "\n"
                else if (n=="t")  out=out "\t"
                else if (n=="r")  out=out "\r"
                else if (n=="\"") out=out "\""
                else if (n=="\\") out=out "\\"
                else if (n=="u")  { out=out "?"; i=i+6; continue }
                else              out=out n
                i=i+2
            } else if (c=="\"") { return out }
            else { out=out c; i=i+1 }
        }
        return out
    }
    function jstr(src, key,   p, needle) {
        needle = "\"" key "\":\""
        p = index(src, needle)
        if (p == 0) return ""
        return unesc(substr(src, p + length(needle)))
    }
    BEGIN { in_text = 0 }
    /^data: / {
        d = substr(${'$'}0, 7)
        t = jstr(d, "type")
        if (t == "text") {
            c = jstr(d, "content")
            if (c != "") { printf "%s", c; fflush(); in_text = 1 }
        } else if (t == "tool_start") {
            if (in_text) { printf "\n"; in_text = 0 }
            printf "\n%s●%s %s%s%s(…)\n", gold, rst, cyan, jstr(d,"name"), rst
            fflush()
        } else if (t == "tool_end") {
            if (in_text) { printf "\n"; in_text = 0 }
            r = jstr(d, "result")
            # Show first line of result, indent rest.
            n = split(r, lines, "\n")
            printf "  %s⎿%s %s", dim, rst, lines[1]
            if (n > 1) printf " %s(+%d lines)%s", dim, n-1, rst
            printf "\n"
            fflush()
        } else if (t == "warning") {
            if (in_text) { printf "\n"; in_text = 0 }
            printf "%s⚠ %s%s\n", dim, jstr(d,"message"), rst
            fflush()
        } else if (t == "error") {
            if (in_text) { printf "\n"; in_text = 0 }
            printf "%s✖ %s%s\n", red, jstr(d,"message"), rst
            fflush()
        } else if (t == "done") {
            if (in_text) printf "\n"
            exit
        }
    }
    '
}

# ── JSON-escape a shell string ──────────────────────────────────────────
json_escape() {
    awk 'BEGIN{ORS=""} {
        gsub(/\\/, "\\\\")
        gsub(/"/, "\\\"")
        gsub(/\t/, "\\t")
        gsub(/\r/, "\\r")
        if (NR>1) printf "\\n"
        printf "%s", ${'$'}0
    }'
}

# ── Send one prompt through the loopback server ────────────────────────
ask() {
    _p=${'$'}1
    _esc=${'$'}(printf '%s' "${'$'}_p" | json_escape)
    _body=${'$'}(printf '{"prompt":"%s","model":"%s"}' "${'$'}_esc" "${'$'}MODEL")
    curl -sN --max-time 300 \
        -H "Content-Type: application/json" \
        --data-raw "${'$'}_body" \
        "${'$'}ENDPOINT" 2>/dev/null | sse_parse
}

if ! health_ok; then
    printf "${'$'}{RED}jarvis: loopback server on 127.0.0.1:47811 is not reachable.${'$'}{RST}\n" >&2
    printf "${'$'}{DIM}The Jarvis foreground service starts it on launch — force-stop\n" >&2
    printf "and reopen the app to bring it up.${'$'}{RST}\n" >&2
    exit 1
fi

# ── One-shot mode: args given, or stdin is a pipe ───────────────────────
if [ "${'$'}#" -gt 0 ] || [ ! -t 0 ]; then
    if [ "${'$'}#" -gt 0 ]; then P=${'$'}*; else P=${'$'}(cat); fi
    ask "${'$'}P"
    exit 0
fi

# ── Interactive REPL ────────────────────────────────────────────────────
clear
# Banner (matches the laptop CLI's logo)
printf "${'$'}GOLD"
printf ' ▐▛███▜▌   ';    printf "${'$'}RST";  printf 'Jarvis CLI (mobile)\n'
printf "${'$'}GOLD▝▜█████▛▘${'$'}RST  "
printf "${'$'}DIM"; printf 'Groq · %s' "${'$'}MODEL"; printf "${'$'}RST\n"
printf "${'$'}GOLD  ▘▘ ▝▝${'$'}RST    "
printf "${'$'}DIM"; printf '%s' "${'$'}PWD"; printf "${'$'}RST\n\n"
printf "${'$'}DIM"; printf 'Slash commands: /help /clear /model <name> · exit to quit'; printf "${'$'}RST\n"

# The agent loop on the server side keeps its own conversation state per
# Conversation id. We let each REPL turn spin up a fresh conversation for
# now — simpler than tracking conv ids round-trip and the agent is already
# stateless per turn in that mode.

while : ; do
    printf '\n'
    printf "${'$'}GOLD❯${'$'}RST "
    if ! IFS= read -r LINE; then echo; break; fi
    [ -z "${'$'}LINE" ] && continue
    case "${'$'}LINE" in
        exit|quit|.q|.exit) break ;;
        /clear)  clear; continue ;;
        /help|\?)
            printf "${'$'}DIM"
            echo '  /help            this text'
            echo '  /clear           clear screen'
            echo '  /model <name>    switch model for the next turn'
            echo '  exit             quit'
            printf "${'$'}RST"
            continue ;;
        /model*)
            NEW=${'$'}(printf '%s' "${'$'}LINE" | sed 's|^/model[ 	]*||')
            if [ -n "${'$'}NEW" ]; then
                MODEL=${'$'}NEW
                printf "${'$'}DIM(using model: %s)${'$'}RST\n" "${'$'}MODEL"
            else
                printf "${'$'}DIM(current model: %s)${'$'}RST\n" "${'$'}MODEL"
            fi
            continue ;;
    esac

    printf '\n'
    ask "${'$'}LINE"
done

printf "${'$'}DIM(goodbye)${'$'}RST\n"
        """.trimIndent()
    }
}
