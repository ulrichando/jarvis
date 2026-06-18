# Credential rotation checklist

This runs you through rotating every credential that has appeared in this repo's git history. **Every key listed here is in the leaked tier** — assume it was scraped the moment the repo was cloned by anyone, even briefly.

## Prerequisites

- Browser with active sessions for: Groq Cloud, DeepSeek, LangSmith, Google AI Studio
- Terminal in `/home/ulrich/Documents/Projects/jarvis`

## 1. LiveKit — DONE

Already rotated automatically (2026-05-04). New keys live in `~/.jarvis/livekit-keys.yaml` (chmod 600). No action needed.

## 2. Groq

1. Open <https://console.groq.com/keys>
2. Find the key whose prefix matches `GROQ_API_KEY` in your `.env`
   (`grep -o 'gsk_.\{8\}' .env`). Click **Revoke**.
3. Click **Create API Key**, name it `jarvis-<host>-<date>`, copy the new value.
4. Paste into `.env`:
   ```
   GROQ_API_KEY=gsk_<new value>
   ```
5. Restart: `systemctl --user restart jarvis-voice-agent.service` and
   relaunch the desktop app (the bridge + proxy read `.env` at launch).

## 3. DeepSeek

1. Open <https://platform.deepseek.com/api_keys>
2. Find the key matching `DEEPSEEK_API_KEY` in your `.env`. Click trash icon.
3. **Create new key** named `jarvis-<host>`, copy value.
4. Paste into `.env`:
   ```
   DEEPSEEK_API_KEY=sk-<new value>
   ```
5. Restart: `systemctl --user restart jarvis-voice-agent.service` and
   relaunch the desktop app.

## 4. LangSmith / LangChain

1. Open <https://smith.langchain.com/o/-/settings/apikeys>
2. Find the key matching `LANGCHAIN_API_KEY` in your `.env`. Revoke.
3. **Create API Key**, copy value.
4. Paste into `.env`:
   ```
   LANGCHAIN_API_KEY=lsv2_pt_<new value>
   ```

> **Never paste real key material (even prefixes) into this file** — it is
> tracked in git. Pre-2026-06-11 revisions of this runbook contained real
> prefixes; one (LangChain) matched a then-live key. If you need to identify
> a key, grep your local `.env` instead.
5. No restart needed (used only for tracing, picked up on next process start).

## 5. Google API key

1. Open <https://console.cloud.google.com/apis/credentials>
2. Find `REDACTED_GOOGLE_KEY`. Click **Delete**.
3. **Create credentials → API key**. Restrict to: Generative Language API + Geolocation API.
4. Paste into `src/voice-agent/.env`:
   ```
   GOOGLE_API_KEY=AIzaSy<new value>
   ```
5. Restart: `systemctl --user restart jarvis-voice-agent.service`

## 6. Postgres password

A DSN of the form `postgresql://jarvis:<password>@localhost:5432/jarvis` was committed to git history (the literal password used to be reproduced in THIS file too — removed 2026-06-11; it is still in old revisions). The Postgres user `jarvis` is local-only, but treat the password as burned and rotate it.

```bash
# Pick a new password (24+ random chars)
NEW_PG_PASSWORD=$(openssl rand -base64 24 | tr -d '=+/' | head -c 24)

# Update Postgres
sudo -u postgres psql -c "ALTER USER jarvis WITH PASSWORD '$NEW_PG_PASSWORD';"

# Update keys.env (single secret store — JARVIS_PG_DSN moved here 2026-06-15).
sed -i "s|JARVIS_PG_DSN=.*|JARVIS_PG_DSN=postgresql://jarvis:${NEW_PG_PASSWORD}@localhost:5432/jarvis|" ~/.jarvis/keys.env
# The ACTUAL Postgres consumer is DATABASE_URL in src/web/.env.local — rotate it too:
sed -i "s|DATABASE_URL=.*|DATABASE_URL=postgresql://jarvis:${NEW_PG_PASSWORD}@localhost:5432/jarvis|" src/web/.env.local
echo "New password: $NEW_PG_PASSWORD (saved to ~/.jarvis/keys.env + web/.env.local, paste into a password manager too)"
```

## 7. ElevenLabs

Already rotated 2026-05-01 per `src/voice-agent/.env` comment. No action needed.

## Verification

After all rotations:

```bash
# 1. Confirm voice path works
curl -sS http://127.0.0.1:8767/status | jq '.connected, .agent_present'
# both should be true

# 2. Trigger a turn (say "Jarvis" into the mic) → should reply

# 3. Check no service is logging auth errors
# (voice-agent logs to a file, not journald; bridge/proxy log to /tmp)
tail -n 500 ~/.local/share/jarvis/logs/voice-agent.log /tmp/jarvis-proxy.log 2>/dev/null | grep -iE "(401|403|unauthorized|invalid api key)"
# should be empty
```

## After rotation

Move to git-history scrub (`docs/runbook/git-history-scrub.md`) — until that's done, anyone with a clone has the *old* keys, but they're now revoked, so the leak window is closed even if history isn't scrubbed.
