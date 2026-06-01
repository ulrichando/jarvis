# Credential rotation checklist

This runs you through rotating every credential that has appeared in this repo's git history. **Every key listed here is in the leaked tier** — assume it was scraped the moment the repo was cloned by anyone, even briefly.

## Prerequisites

- Browser with active sessions for: Groq Cloud, DeepSeek, LangSmith, Google AI Studio
- Terminal in `/home/ulrich/Documents/Projects/jarvis`

## 1. LiveKit — DONE

Already rotated automatically (2026-05-04). New keys live in `~/.jarvis/livekit-keys.yaml` (chmod 600). No action needed.

## 2. Groq

1. Open <https://console.groq.com/keys>
2. Find the key starting with `gsk_8MwNK8v3PcczfTbxutQNW…` (current value in `.env`). Click **Revoke**.
3. Click **Create API Key**, name it `jarvis-laptop-2026-05-04`, copy the new value.
4. Paste into `.env`:
   ```
   GROQ_API_KEY=gsk_<new value>
   ```
5. Restart: `systemctl --user restart jarvis-proxy.service jarvis-voice-agent.service`

## 3. DeepSeek

1. Open <https://platform.deepseek.com/api_keys>
2. Find `***REMOVED-LEAKED-KEY***`. Click trash icon.
3. **Create new key** named `jarvis-laptop`, copy value.
4. Paste into `.env`:
   ```
   DEEPSEEK_API_KEY=sk-<new value>
   ```
5. Restart: `systemctl --user restart jarvis-proxy.service jarvis-voice-agent.service`

## 4. LangSmith / LangChain

1. Open <https://smith.langchain.com/o/-/settings/apikeys>
2. Find `lsv2_pt_e278be2cbe454501adf3f0cbcd556a6c_…`. Revoke.
3. **Create API Key**, copy value.
4. Paste into `.env`:
   ```
   LANGCHAIN_API_KEY=lsv2_pt_<new value>
   ```
5. No restart needed (used only for tracing, picked up on next process start).

## 5. Google API key

1. Open <https://console.cloud.google.com/apis/credentials>
<<<<<<< HEAD
2. Find `***REMOVED-LEAKED-KEY***`. Click **Delete**.
=======
2. Find `REDACTED_GOOGLE_KEY`. Click **Delete**.
>>>>>>> origin/master
3. **Create credentials → API key**. Restrict to: Generative Language API + Geolocation API.
4. Paste into `src/voice-agent/.env`:
   ```
   GOOGLE_API_KEY=AIzaSy<new value>
   ```
5. Restart: `systemctl --user restart jarvis-voice-agent.service`

## 6. Postgres password

The DSN `postgresql://jarvis:697968751ando@localhost:5432/jarvis` was committed. The Postgres user `jarvis` is local-only, but the password is now public.

```bash
# Pick a new password (24+ random chars)
NEW_PG_PASSWORD=$(openssl rand -base64 24 | tr -d '=+/' | head -c 24)

# Update Postgres
sudo -u postgres psql -c "ALTER USER jarvis WITH PASSWORD '$NEW_PG_PASSWORD';"

# Update .env
sed -i "s|JARVIS_PG_DSN=.*|JARVIS_PG_DSN=postgresql://jarvis:${NEW_PG_PASSWORD}@localhost:5432/jarvis|" .env
echo "New password: $NEW_PG_PASSWORD (saved to .env, paste into a password manager too)"
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
journalctl --user -u jarvis-proxy.service -u jarvis-voice-agent.service --since "5 minutes ago" | grep -iE "(401|403|unauthorized|invalid api key)"
# should be empty
```

## After rotation

Move to git-history scrub (`docs/runbook/git-history-scrub.md`) — until that's done, anyone with a clone has the *old* keys, but they're now revoked, so the leak window is closed even if history isn't scrubbed.
