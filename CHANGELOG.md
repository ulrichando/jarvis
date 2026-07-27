# Changelog

All notable changes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

For the full commit-level history, run `git log --oneline`.

---

## [0.2.3](https://github.com/ulrichando/jarvis/compare/v0.2.2...v0.2.3) (2026-07-27)


### Features

* **cli,web:** local LLM auto-failover for CLI + web (offline parity, Stage 2) ([#198](https://github.com/ulrichando/jarvis/issues/198)) ([839bb06](https://github.com/ulrichando/jarvis/commit/839bb060b5045c512eed54e422a0f70327a01d4d))
* **cli:** `jarvis keys push` — make local provider keys web-managed ([#192](https://github.com/ulrichando/jarvis/issues/192)) ([983df10](https://github.com/ulrichando/jarvis/commit/983df103f3f5c9fc07de3e1ecf89b1cded55be78))
* **cli:** add "Start a new session" option to the ultraplan post-teleport dialog ([#335](https://github.com/ulrichando/jarvis/issues/335)) ([3c37c7c](https://github.com/ulrichando/jarvis/commit/3c37c7c75019871fd65f055ccb4b38fd7c5f189a))
* **cli:** add a manual /recap command ([#305](https://github.com/ulrichando/jarvis/issues/305)) ([90fe047](https://github.com/ulrichando/jarvis/commit/90fe04769229872039457fc050f50006276e821c))
* **cli:** add the /goal command -- auto-continue until a condition is met ([#306](https://github.com/ulrichando/jarvis/issues/306)) ([1b0f44c](https://github.com/ulrichando/jarvis/commit/1b0f44c64c7991ba26adea21077908762e5d2b70))
* **cli:** BUDDY companion observer + soul-gen, reset gate, bubble fix ([#307](https://github.com/ulrichando/jarvis/issues/307)) ([a6b0963](https://github.com/ulrichando/jarvis/commit/a6b0963ece4624c3c938ed0387136871abc2d72c))
* **cli:** enable the away-summary "while you were away" recap card ([#304](https://github.com/ulrichando/jarvis/issues/304)) ([acee235](https://github.com/ulrichando/jarvis/commit/acee23596f0a9aaf0e1dce6c21b3231d83c14509))
* **cli:** Jarvis-in-Chrome MCP — browser tools for the CLI agent via the bridge ([#178](https://github.com/ulrichando/jarvis/issues/178)) ([0654b13](https://github.com/ulrichando/jarvis/commit/0654b13094a0b8670eca252f7619e64899835b17))
* **cli:** show the per-turn "Worked for Xs" indicator on every turn ([#308](https://github.com/ulrichando/jarvis/issues/308)) ([0e768cd](https://github.com/ulrichando/jarvis/commit/0e768cd71b04f5fd7620b83c9194dcb9331d09b4))
* **cli:** surface the AskUserQuestion option picker on open-ended requests ([#298](https://github.com/ulrichando/jarvis/issues/298)) ([3edae13](https://github.com/ulrichando/jarvis/commit/3edae13057e79071927be671beb3f346e53e0c9a))
* **cli:** ultracode keyword UX (rainbow + notification + alt+w ignore + xhigh wire) and voice-notice latch fix ([#171](https://github.com/ulrichando/jarvis/issues/171)) ([d9b81b7](https://github.com/ulrichando/jarvis/commit/d9b81b79d9ff817afbbf3967a034522be9550206))
* default local Ollama model to qwen3:4b-instruct-2507 (fits consumer GPUs) ([#207](https://github.com/ulrichando/jarvis/issues/207)) ([70ce326](https://github.com/ulrichando/jarvis/commit/70ce32614ead30075f26ae773e6d93a8d85438a3))
* **desktop:** cross-surface logout hard-stop + device-fit local tray models ([#209](https://github.com/ulrichando/jarvis/issues/209)) ([e0ca547](https://github.com/ulrichando/jarvis/commit/e0ca54756a8cb83912a9641ab320247116e36fca))
* **desktop:** on-demand local web uses light prod server, not next dev (offline parity, Stage 3) ([#199](https://github.com/ulrichando/jarvis/issues/199)) ([8db7eff](https://github.com/ulrichando/jarvis/commit/8db7eff527547fbaf9e405a07cede5f32c30198e))
* **install:** auto-install the local voice stack (STT/LiveKit/TTS) + fix 2 installer crashes ([#194](https://github.com/ulrichando/jarvis/issues/194)) ([801c0b4](https://github.com/ulrichando/jarvis/commit/801c0b4f0daaf92a4acc83f793391021914f28ee))
* **install:** pin + alias the local model to short 'qwen3-4b' ([#211](https://github.com/ulrichando/jarvis/issues/211)) ([f183853](https://github.com/ulrichando/jarvis/commit/f1838539553389449a0330d7d19e030d3a2bf96f))
* **iot:** local network device discovery + identification (Phase 1) ([#217](https://github.com/ulrichando/jarvis/issues/217)) ([5b795d7](https://github.com/ulrichando/jarvis/commit/5b795d7c77a8c87aba6f12a21e168bd1e736ac88))
* **iot:** smart-home device control — HA adapter + LG webOS + categorized UI + voice + smart-only discovery ([#264](https://github.com/ulrichando/jarvis/issues/264)) ([0814ecc](https://github.com/ulrichando/jarvis/commit/0814ecc472d52e982def75c468b19abf9cdd9228))
* **ops:** enterprise-grade jarvis-health -- parallel probes, depth, actionable severity ([#299](https://github.com/ulrichando/jarvis/issues/299)) ([9094866](https://github.com/ulrichando/jarvis/commit/90948669d1d16e0e5b1dc89e4853064e5e84a4ca))
* **proxy+web:** MCP connector read-only lockdown, tool-access modes, shared config ([#256](https://github.com/ulrichando/jarvis/issues/256)) ([ab34bf4](https://github.com/ulrichando/jarvis/commit/ab34bf4c726632391dbe11636e7ff8206157eb3f))
* **proxy:** inject Anthropic MCP connector for thin mobile clients ([#250](https://github.com/ulrichando/jarvis/issues/250)) ([fb32da4](https://github.com/ulrichando/jarvis/commit/fb32da40d8115786c16b9ab1f8e93fa1d0ccbcd5))
* **proxy:** web research returns up to 15 sources (was 8) ([#257](https://github.com/ulrichando/jarvis/issues/257)) ([e591637](https://github.com/ulrichando/jarvis/commit/e5916372f514b70dcad6555e9c793f76b607a159))
* run /ultraplan on jarvis-web via the container engine (repo-clone optional) ([#173](https://github.com/ulrichando/jarvis/issues/173)) ([e77add0](https://github.com/ulrichando/jarvis/commit/e77add02ce0407039eb503e06e5dbba956edefb5))
* **search:** enterprise research pass — query rewrite, Cohere/Jina rerank, cited grounding ([#254](https://github.com/ulrichando/jarvis/issues/254)) ([fdb6da9](https://github.com/ulrichando/jarvis/commit/fdb6da95bce9272f08b813da166ca4ddbfef34c5))
* **search:** professional web research — Brave primary, content fetch, clean/rerank ([#253](https://github.com/ulrichando/jarvis/issues/253)) ([60bdb38](https://github.com/ulrichando/jarvis/commit/60bdb3801e65edc74292eaef47e6fe331f93def6))
* **sync:** /api/sync/push — accept mirrored chats from the mobile app ([27b5394](https://github.com/ulrichando/jarvis/commit/27b539405070f4f2a5b673bc3f3d3d670c888962))
* **sync:** GET /api/sync pull routes + change_seq on web writes ([7830288](https://github.com/ulrichando/jarvis/commit/783028829c1b10113e3ec72c1c2ebe56d5094c85))
* **sync:** pull voice + task conversations down to the phone ([228dc35](https://github.com/ulrichando/jarvis/commit/228dc3518e927bf13a0069dcbdb7a231abfaa91a))
* **sync:** soft-delete + tombstone pull for delete sync (slice 3) ([c510804](https://github.com/ulrichando/jarvis/commit/c510804577c260b31b66f69c24ade29a4f280df4))
* **ultraplan:** bundle-and-upload a local repo so ultraplan works without GitHub ([#336](https://github.com/ulrichando/jarvis/issues/336)) ([1cafd16](https://github.com/ulrichando/jarvis/commit/1cafd1643b362d311582faa8df78b65a97ec104f))
* **voice:** _web_api JARVIS_WEB_TOKEN — target a remote web (0wlan.com) ([aeb6d3f](https://github.com/ulrichando/jarvis/commit/aeb6d3f52acc7793aeec34c1d1b49e411a00d0d1))
* **voice:** _web_api JARVIS_WEB_TOKEN — target a remote web (0wlan.com) ([be63c96](https://github.com/ulrichando/jarvis/commit/be63c969e41919bb6c6d26061f2c36b4fec2adf8))
* **voice-agent-lk:** cloud conversation memory (Postgres via web API, replaces JSONL) ([ebad343](https://github.com/ulrichando/jarvis/commit/ebad3437cbf40357c542d77159abd5aa750150c6))
* **voice-agent-lk:** cross-session per-user voice memory (identity from signed token) ([2724b84](https://github.com/ulrichando/jarvis/commit/2724b848d90f9a048388317607124bb4faea3133))
* **voice-agent-lk:** curated memory (USER/MEMORY/PROCEDURE) + memory tool in the cloud voice ([a2d7581](https://github.com/ulrichando/jarvis/commit/a2d75817bf91a19ea6f14de7f9ba874ca8323864))
* **voice-agent-lk:** honcho semantic recall in the cloud voice (Phase 2, fail-soft) ([059ea25](https://github.com/ulrichando/jarvis/commit/059ea257ae0c884b069a0827fe71a8b4cdea845d))
* **voice-agent-lk:** publish full reply text for the phone's karaoke read-along ([f0a674c](https://github.com/ulrichando/jarvis/commit/f0a674c6a84be8195db93ab565081a41622d2f58))
* **voice-agent-lk:** push edge-tts word timings for phone karaoke read-along ([4911356](https://github.com/ulrichando/jarvis/commit/4911356d204b54d501315b3c4a8bc437f5946106))
* **voice-agent:** always-on honcho context + dual-sync (keep local honcho fresh) ([6761fd2](https://github.com/ulrichando/jarvis/commit/6761fd26a9849734b4f5b5dae0e017be28a70713))
* **voice-agent:** flag-gated cloud memory for the local agent (shared brain) ([e0ae247](https://github.com/ulrichando/jarvis/commit/e0ae2478bc400d1aaf5297e61f4cab74e9d1898e))
* **voice-agent:** honcho semantic search as recall(mode="search") ([927b4e6](https://github.com/ulrichando/jarvis/commit/927b4e687711b2f433f8025e861cd3e88add25f4))
* **voice-agent:** inject honcho's durable user model into per-turn context ([74981a7](https://github.com/ulrichando/jarvis/commit/74981a7713f8aadf85896391652b182228b330b7))
* **voice-agent:** live type-as-you-speak STT + web-search sources ([#249](https://github.com/ulrichando/jarvis/issues/249)) ([89da7d3](https://github.com/ulrichando/jarvis/commit/89da7d3208537c304187ae1635ee03d025e28ad4))
* **voice-agent:** local agent on the shared cloud brain + full honcho use ([f622a69](https://github.com/ulrichando/jarvis/commit/f622a6908a20483033ec81c80f6fe8e2274792d7))
* **voice-agent:** port phone-selected TTS voice into the repo ([7f40318](https://github.com/ulrichando/jarvis/commit/7f40318243f0d195a8e1945c0d4f564a496d9b61))
* **voice-agent:** self-knowledge of live runtime config + bake base.en interim ([#252](https://github.com/ulrichando/jarvis/issues/252)) ([0fa491f](https://github.com/ulrichando/jarvis/commit/0fa491fbc9e2db837aa154bfa8090dd8835fed57))
* **voice/stt:** compression-ratio hallucination signal + env-tunable thresholds ([#287](https://github.com/ulrichando/jarvis/issues/287)) ([3a404d0](https://github.com/ulrichando/jarvis/commit/3a404d08e04f3c7ae66f881ce8afd25d32b2d29d))
* **voice→web:** voice control over the web app (workspace, project, routine, code) ([8f62ccd](https://github.com/ulrichando/jarvis/commit/8f62ccddf90fd4f13e1254358f8ada81aef4282f))
* **voice+web:** Edge voices matching mobile app + settings autofill fix ([#258](https://github.com/ulrichando/jarvis/issues/258)) ([607b5d0](https://github.com/ulrichando/jarvis/commit/607b5d079244bac1ee8a62cfdd01c22364595c56))
* **voice+web:** web_code tool — voice control over web coding sessions ([d8e3c7e](https://github.com/ulrichando/jarvis/commit/d8e3c7ec0f26ecc2421061fef7fec8d844a66d5a))
* **voice+web:** web_project tool + shared-token auth for user-scoped routes ([94ae3d0](https://github.com/ulrichando/jarvis/commit/94ae3d07e49ecb91f98388157c3c219bf1bf7901))
* **voice+web:** web_routine tool — voice control over web routines ([c4eb6b4](https://github.com/ulrichando/jarvis/commit/c4eb6b484e06d0b897b1c49de5f91b69c150c279))
* **voice:** chat-context seed + web search + per-job model ([6b40041](https://github.com/ulrichando/jarvis/commit/6b40041dc7420654210827de1c90583f709e1475))
* **voice:** circle back to a question the user was interrupted on ([#291](https://github.com/ulrichando/jarvis/issues/291)) ([d5069a1](https://github.com/ulrichando/jarvis/commit/d5069a19323494190cab709818e986a27d3fa549))
* **voice:** cloud memory for the LiveKit voice agent (conversation + curated + honcho recall) ([6e18c0e](https://github.com/ulrichando/jarvis/commit/6e18c0e51c917d98566fb18278894455aabb8165))
* **voice:** directedness gate — durable replacement for the ambient time-window (todo [#3](https://github.com/ulrichando/jarvis/issues/3)) ([#294](https://github.com/ulrichando/jarvis/issues/294)) ([b683ff1](https://github.com/ulrichando/jarvis/commit/b683ff1016ab9c9b73ff0e261b11cdf164107170))
* **voice:** gemini/openai realtime modes expose the full tool registry ([#196](https://github.com/ulrichando/jarvis/issues/196)) ([a567331](https://github.com/ulrichando/jarvis/commit/a56733167a4121189dbd6355588097fdd2117481))
* **voice:** learned raw-audio end-of-turn detector (Smart Turn v3, todo [#2](https://github.com/ulrichando/jarvis/issues/2)) ([#292](https://github.com/ulrichando/jarvis/issues/292)) ([24a5d54](https://github.com/ulrichando/jarvis/commit/24a5d5410a9f8923dc07ce34a93729b6506819f4))
* **voice:** local LLM auto-failover (offline parity, Stage 1) + installer provisions ollama ([#195](https://github.com/ulrichando/jarvis/issues/195)) ([d2f690b](https://github.com/ulrichando/jarvis/commit/d2f690bbf3fe9b20bac59757b797a45531d73d2c))
* **voice:** pull provider keys from the server on voice-agent startup ([#191](https://github.com/ulrichando/jarvis/issues/191)) ([91f9c1b](https://github.com/ulrichando/jarvis/commit/91f9c1b7773ce0cbda379d326e80090c9e06587f))
* **voice:** scheduled tool — list + run Home scheduled tasks by voice ([#222](https://github.com/ulrichando/jarvis/issues/222)) ([0cf38e3](https://github.com/ulrichando/jarvis/commit/0cf38e37d54c9a3dc45d51fe6236be8d1eea8d07))
* **voice:** scheduled tool queries both VPS + local instances ([#226](https://github.com/ulrichando/jarvis/issues/226)) ([d7abf6d](https://github.com/ulrichando/jarvis/commit/d7abf6d3a8795e4c8745eddb891a6a1fa0a63efd))
* **voice:** web_workspace tool — voice control over the web app's workspaces ([2cd4145](https://github.com/ulrichando/jarvis/commit/2cd41459eed43f89a8731bc86afd5b2dcdc86bcb))
* **voice:** WiFi-independent WebRTC media transport (jarvis0 dummy NIC) ([#225](https://github.com/ulrichando/jarvis/issues/225)) ([dbcc5e1](https://github.com/ulrichando/jarvis/commit/dbcc5e195f7dad58b5496e7505104b45def44058))
* **web-chat:** webFetch + memory + fileSearch + code interpreter ([#283](https://github.com/ulrichando/jarvis/issues/283)) ([fa132c9](https://github.com/ulrichando/jarvis/commit/fa132c91668380c78aed570efa710f778d7a7473))
* **web,infra:** scheduled-task voice reminders across local + VPS ([#216](https://github.com/ulrichando/jarvis/issues/216)) ([36fd9a8](https://github.com/ulrichando/jarvis/commit/36fd9a8e560f285d15f2098228d2c0e63946b79d))
* **web/chat:** 15 web-search sources (mobile parity) ([#282](https://github.com/ulrichando/jarvis/issues/282)) ([a69ff48](https://github.com/ulrichando/jarvis/commit/a69ff48e810cbd9c4293d4040fefc1c97513631a))
* **web/chat:** DeepSeek search actually fires + shows Sources chips (mobile parity) ([#279](https://github.com/ulrichando/jarvis/issues/279)) ([60b9fd1](https://github.com/ulrichando/jarvis/commit/60b9fd162a7ceacefb3bf4686008bbd77a0f1dcb))
* **web/chat:** deterministic server-side web search for DeepSeek (weak tool-callers) ([#278](https://github.com/ulrichando/jarvis/issues/278)) ([b34de01](https://github.com/ulrichando/jarvis/commit/b34de0194664086097d4de2ff5e8021d71046915))
* **web/chat:** query-gen REFINES DeepSeek search (regex-gated, no latency tax) ([#281](https://github.com/ulrichando/jarvis/issues/281)) ([a2acbc9](https://github.com/ulrichando/jarvis/commit/a2acbc976dc7a7a9650395fd5e03c4c6e5cdb096))
* **web/chat:** redesign model picker + functional Effort/Thinking controls ([#262](https://github.com/ulrichando/jarvis/issues/262)) ([cac0aac](https://github.com/ulrichando/jarvis/commit/cac0aacb2db11e29d809423a216cc3d2ffbaf931))
* **web/tts:** online Edge TTS voices in chat voice mode + settings (mobile parity) ([#263](https://github.com/ulrichando/jarvis/issues/263)) ([88a3c0d](https://github.com/ulrichando/jarvis/commit/88a3c0d08f8a0ef45fbdff2cf95d820aef4f4db7))
* **web/voice:** immersive voice-mode overlay + push-to-talk/hands-free + speech rate ([#265](https://github.com/ulrichando/jarvis/issues/265)) ([f4ba62f](https://github.com/ulrichando/jarvis/commit/f4ba62fa3ba2663d35bb9757d282480a6882d4dd))
* **web:** accept per-user API/bridge tokens on the voice→web control routes ([deafde0](https://github.com/ulrichando/jarvis/commit/deafde03416be09fb4fb62e7da61e6d6c282be71))
* **web:** accept per-user API/bridge tokens on the voice→web control routes ([0d4dd43](https://github.com/ulrichando/jarvis/commit/0d4dd43c4bd509935f4fc59605fd5a97fd01b670))
* **web:** browser Web Speech API STT fallback when no server STT key ([#205](https://github.com/ulrichando/jarvis/issues/205)) ([0cbef06](https://github.com/ulrichando/jarvis/commit/0cbef06d1dba6a5e4a82db260eab9867a865507d))
* **web:** Cowork, Incognito, Scheduled tasks + voice reminders ([#218](https://github.com/ulrichando/jarvis/issues/218)) ([78248ff](https://github.com/ulrichando/jarvis/commit/78248ffedb6444f4745738abfd703d66c50209c8))
* **web:** cross-surface logout — revoke all sessions + bridge token ([#204](https://github.com/ulrichando/jarvis/issues/204)) ([27d9e08](https://github.com/ulrichando/jarvis/commit/27d9e082bed7650d07117a5fdf101506867a8523))
* **web:** Dispatch (keep-awake + Web Push) + LiveKit realtime-voice token ([67959d5](https://github.com/ulrichando/jarvis/commit/67959d5261afbcc690ce33ce447a0fe40a83a0f4))
* **web:** give scheduled runs tool access (web search + MCP connectors) ([#224](https://github.com/ulrichando/jarvis/issues/224)) ([b549412](https://github.com/ulrichando/jarvis/commit/b54941295c8634290afa1c2f1949521c0e472f66))
* **web:** restore voice input — /api/stt as an OpenAI-compatible transcription proxy ([#169](https://github.com/ulrichando/jarvis/issues/169)) ([47cb70f](https://github.com/ulrichando/jarvis/commit/47cb70f2aba0726274332e884a13166dba263412))
* **web:** Settings → Skills page — real personal skills CRUD, CLI/voice interop format ([#181](https://github.com/ulrichando/jarvis/issues/181)) ([44f14f4](https://github.com/ulrichando/jarvis/commit/44f14f46c8392187be6a62ccf9a724583bf4cd26))
* **web:** settings/customize modal + sidebar & code-shell parity ([#214](https://github.com/ulrichando/jarvis/issues/214)) ([01c4292](https://github.com/ulrichando/jarvis/commit/01c42924d7945e4ee5273774c9eda952bdf50c1b))
* **web:** token-auth scheduled list + run-now for the JARVIS voice tool ([#220](https://github.com/ulrichando/jarvis/issues/220)) ([a3f2c85](https://github.com/ulrichando/jarvis/commit/a3f2c85d12bfd0a888c58fb5f66912a4fe383a57))


### Bug Fixes

* CCR teleport reaches environments (proxy SELF_AUTH) + no git repo required in JARVIS mode ([#174](https://github.com/ulrichando/jarvis/issues/174)) ([9094c2f](https://github.com/ulrichando/jarvis/commit/9094c2f4731e33f0dbc16fa0a95bbf61e9442d9f))
* **ci:** accept two unfixable-in-place pip-audit vulns (setuptools, json-repair) ([#233](https://github.com/ulrichando/jarvis/issues/233)) ([6072073](https://github.com/ulrichando/jarvis/commit/6072073dd9db79cd0bef2f2541d37065d9749a63))
* **cli:** /goal headless output + judge hardening on counting conditions ([#312](https://github.com/ulrichando/jarvis/issues/312)) ([4393219](https://github.com/ulrichando/jarvis/commit/43932194bdb5da2b5b96309af4dee058e6509556))
* **cli:** author the missing Ultraplan dialogs — typing "ultraplan" crashed the REPL ([#172](https://github.com/ulrichando/jarvis/issues/172)) ([23ef567](https://github.com/ulrichando/jarvis/commit/23ef567e43e9a11e6e2374d03a500713296a4458))
* **cli:** enforce Settings → Jarvis-in-Chrome policy at the bridge ([#186](https://github.com/ulrichando/jarvis/issues/186)) ([b8f6627](https://github.com/ulrichando/jarvis/commit/b8f66278d961f98dd2a048d3245899cc3568e099))
* **cli:** fetchEnvironments JARVIS bridge auth (unblocks ultraplan teleport) ([#175](https://github.com/ulrichando/jarvis/issues/175)) ([766a294](https://github.com/ulrichando/jarvis/commit/766a29496a8f32c05fdf92060c2e7425af8cfb13))
* **cli:** make local Ollama models actually usable (num_ctx + lean requests) ([b3f507d](https://github.com/ulrichando/jarvis/commit/b3f507dcf08f2682e38f218315d50c43490c9bdc))
* **cli:** run /ultraplan on DeepSeek, not Claude (cost) ([#338](https://github.com/ulrichando/jarvis/issues/338)) ([58e1183](https://github.com/ulrichando/jarvis/commit/58e1183ff93a83111cb8dd450fb14c5c63fc5e95))
* **cli:** surface /env as a real command + defuse the remoteControlServer boot landmine ([#309](https://github.com/ulrichando/jarvis/issues/309)) ([8f7ce2b](https://github.com/ulrichando/jarvis/commit/8f7ce2b944768040cc5132ac5e113cdfdfc7f719))
* **cli:** surface background Workflow results to the model on completion ([#317](https://github.com/ulrichando/jarvis/issues/317)) ([3e02030](https://github.com/ulrichando/jarvis/commit/3e020305f86209410226e6e2e3b42e22e810e11d))
* **cli:** swarm/agent banner rules overrun the companion sprite ([#339](https://github.com/ulrichando/jarvis/issues/339)) ([d2f0c18](https://github.com/ulrichando/jarvis/commit/d2f0c187efd7109e793ed954959874b624fe2404))
* **cli:** sync bun.lock with package.json so the gh-app Docker build stops failing ([#326](https://github.com/ulrichando/jarvis/issues/326)) ([c1a25cf](https://github.com/ulrichando/jarvis/commit/c1a25cf33f2e9c7efe534deb4b28ea92673f60f2))
* **cli:** ultrathink chip reports the real boosted effort, not hardcoded "high" ([#170](https://github.com/ulrichando/jarvis/issues/170)) ([fba54c9](https://github.com/ulrichando/jarvis/commit/fba54c9e10bef54294058eba50b9fa7d7d613362))
* **deploy:** make box-local config hygiene explicit + self-diagnosing wedges ([#251](https://github.com/ulrichando/jarvis/issues/251)) ([75c2206](https://github.com/ulrichando/jarvis/commit/75c2206a5edbe19a61a5ef1c3174146cd81ddad2))
* **desktop:** launcher computed repo root 2 levels up, doubling src/ — bridge never started ([#177](https://github.com/ulrichando/jarvis/issues/177)) ([6d51920](https://github.com/ulrichando/jarvis/commit/6d519203d8823c9ef45b17770b37e77451cfd54e))
* **desktop:** tray "Sign in to JARVIS Server" now reflects login state ([#190](https://github.com/ulrichando/jarvis/issues/190)) ([7188214](https://github.com/ulrichando/jarvis/commit/7188214f73f67fcb822b3b8adb8310a789823241))
* **desktop:** tray conversation modes set per-provider fast-voice + capable-tools pair ([#193](https://github.com/ulrichando/jarvis/issues/193)) ([49b5a66](https://github.com/ulrichando/jarvis/commit/49b5a661172983cf146bb14a825d32c5d8594e3d))
* **extension:** settings badge showed 'Not signed in' while signed in — key off bridge_token, not account_email ([#180](https://github.com/ulrichando/jarvis/issues/180)) ([7fb8a2b](https://github.com/ulrichando/jarvis/commit/7fb8a2b02202e1ec7e381bee5f836502ebae5fd0))
* **honcho:** pin the fallback clone + enable deriver flush (v3.0.9) ([fd460e0](https://github.com/ulrichando/jarvis/commit/fd460e0e4826e2e0a6fbf20aa90dad14b5a8ec79))
* **honcho:** same v3.0.9 fixes for the LOCAL installer + robust key source ([920d650](https://github.com/ulrichando/jarvis/commit/920d6500ebe578b4920a9cb63389f404de1b06f4))
* **install:** keep only the short 'qwen3-4b' tag (rm the canonical after alias) ([#212](https://github.com/ulrichando/jarvis/issues/212)) ([f710a71](https://github.com/ulrichando/jarvis/commit/f710a71372c45d487dbd5ea8f0d3ba80060adc8f))
* make /ultraplan actually plan (4 stacked container defects) ([#337](https://github.com/ulrichando/jarvis/issues/337)) ([650c06a](https://github.com/ulrichando/jarvis/commit/650c06a3e518437ae84566a55c13cd582dc5f8a4))
* **ops:** jarvis-slo-check -- correct p95 at small n + staleness guard ([#303](https://github.com/ulrichando/jarvis/issues/303)) ([992c417](https://github.com/ulrichando/jarvis/commit/992c4177892e4ce353580fb58d4927c36d78c0bf))
* **proxy:** recover DeepSeek tool-calls leaked as text; voice speaking-rate ([f602d63](https://github.com/ulrichando/jarvis/commit/f602d63f1927f4fcdfcec45410e47c91386e90e7))
* **sync:** fixes from an independent Fable-5 audit (server) ([0146b82](https://github.com/ulrichando/jarvis/commit/0146b823253fdc0e28e94f1268b02535490d8209))
* **sync:** harden schema-ensure concurrency + sequence default (pre-deploy audit) ([5a9a8af](https://github.com/ulrichando/jarvis/commit/5a9a8af3822abd6074e2a8f7a4419279f284ab2d))
* **sync:** transaction + selective bump + correct messageCount ([e7b25bf](https://github.com/ulrichando/jarvis/commit/e7b25bf28e6ded9643d2d2eeefca68dc8c6e31d7))
* **voice-agent-lk:** correct word-timing interpolation denominator (Fable review) ([5972df6](https://github.com/ulrichando/jarvis/commit/5972df6df6c49c8fa198317fa9684a89dda11fc5))
* **voice-agent-lk:** harden CPU STT against non-speech hallucination ([a85ad4c](https://github.com/ulrichando/jarvis/commit/a85ad4c6aefbb960e9628a9b1cfab2e9952ef275))
* **voice-agent-lk:** stop memory self-poisoning with "I can't remember" denials ([fa7e708](https://github.com/ulrichando/jarvis/commit/fa7e708024347fbdcc4a30206b6b286fae4f38d2))
* **voice-agent:** give the agent today's date + prefer fresh search over stale memory ([#255](https://github.com/ulrichando/jarvis/issues/255)) ([9eb84a0](https://github.com/ulrichando/jarvis/commit/9eb84a082067231defdb9be39b6782f91b6cc72d))
* **voice-agent:** guard desktop memory against self-poisoning denials ([16aa840](https://github.com/ulrichando/jarvis/commit/16aa840cbfd703f29c17c76024ff21018f0345ef))
* **voice-agent:** local-failover rung survives a fresh box (qwen3 default + ctx-variant self-heal) ([#229](https://github.com/ulrichando/jarvis/issues/229)) ([a75ba7c](https://github.com/ulrichando/jarvis/commit/a75ba7c2431c24956f3f6133015d13b4edb6f7be))
* **voice-agent:** make local-mode speech-pin test hermetic vs live ollama ([#227](https://github.com/ulrichando/jarvis/issues/227)) ([f8239d7](https://github.com/ulrichando/jarvis/commit/f8239d7c3d8d0a16607923b5e7b845db156a7412))
* **voice-agent:** retry honcho init instead of latching a whole session off ([1b878fc](https://github.com/ulrichando/jarvis/commit/1b878fc4acd3ba987b405d4349ac1d77ef904b42))
* **voice-stt:** self-heal a wedged local GPU to CPU instead of flooding notifications ([ea8ed02](https://github.com/ulrichando/jarvis/commit/ea8ed02e8d4bdcefd9008f3ddb7be395f297d12e))
* **voice-stt:** self-heal a wedged local GPU to CPU instead of flooding notifications ([2224cdc](https://github.com/ulrichando/jarvis/commit/2224cdc646eac65a57528fa96f801bdea99bf62a))
* **voice-stt:** signal the user once when STT sticks to CPU (review follow-up) ([347b785](https://github.com/ulrichando/jarvis/commit/347b7856990f5c43f738b76274d206bb9533eebf))
* **voice/client:** don't fire a false "mic stall" while JARVIS is speaking ([#289](https://github.com/ulrichando/jarvis/issues/289)) ([bbafb9f](https://github.com/ulrichando/jarvis/commit/bbafb9fe3762a74a7368fd75b5ccd6f8d83a0492))
* **voice/prompt:** "go do X and let me know" → dispatch background, don't just promise ([#290](https://github.com/ulrichando/jarvis/issues/290)) ([9316dde](https://github.com/ulrichando/jarvis/commit/9316dde05237227105497100048d87a1acd13031))
* **voice/stt:** persist GPU-wedge episode across job-process churn ([#269](https://github.com/ulrichando/jarvis/issues/269)) ([cd9b0aa](https://github.com/ulrichando/jarvis/commit/cd9b0aa8d098b55c32720bb4363787c3d565ebef))
* **voice/stt:** stop faster-whisper hallucinating phantom turns from silence ([#285](https://github.com/ulrichando/jarvis/issues/285)) ([9ff558e](https://github.com/ulrichando/jarvis/commit/9ff558ec0405f82062a6390de376531c5f78a9a9))
* **voice/wake:** accept faster-whisper's d/t/ch mis-renderings of "Jarvis" ([#288](https://github.com/ulrichando/jarvis/issues/288)) ([7fc453b](https://github.com/ulrichando/jarvis/commit/7fc453b00c6f425449284a60f26f3f3625d59fc7))
* **voice+search:** recreate-safe voice agent, SearXNG hub URL + engines ([676b649](https://github.com/ulrichando/jarvis/commit/676b6493ce6468364f797b2b8f233063f44b04c1))
* **voice:** address Fable fact-check — gap-immune network suppression + strip nits ([804af36](https://github.com/ulrichando/jarvis/commit/804af362f8163292160ed85d9d49bb5e235de88e))
* **voice:** cap DeepSeek voice-LLM output ~200 tok (todo [#5](https://github.com/ulrichando/jarvis/issues/5)) ([#295](https://github.com/ulrichando/jarvis/issues/295)) ([7afca05](https://github.com/ulrichando/jarvis/commit/7afca05c40a8f61295f903970bc378eae4807d24))
* **voice:** default to brief spoken replies, long answers only on explicit ask ([#261](https://github.com/ulrichando/jarvis/issues/261)) ([84bee45](https://github.com/ulrichando/jarvis/commit/84bee45cf4638b7dba3df417189884effb073cad))
* **voice:** dispatch_agent runs the LOCAL model in local mode + sanitized env ([#201](https://github.com/ulrichando/jarvis/issues/201)) ([2c25013](https://github.com/ulrichando/jarvis/commit/2c25013c4d9129399d295d52261955e48f19bf74))
* **voice:** engage silent mode on verbose mute requests ([#189](https://github.com/ulrichando/jarvis/issues/189)) ([f9e3a7d](https://github.com/ulrichando/jarvis/commit/f9e3a7de7d94ef1cd2f9d172b2d82b809fe46c08))
* **voice:** enterprise research — 15 results, adaptive answer length, no re-search loop ([#260](https://github.com/ulrichando/jarvis/issues/260)) ([16c2d00](https://github.com/ulrichando/jarvis/commit/16c2d00d96fb9449ec4d8034e943fea231801dd9))
* **voice:** grant canUpdateOwnMetadata so phone voice selection survives deploy ([d4bf38a](https://github.com/ulrichando/jarvis/commit/d4bf38a71135f4ed6d34fb3ab28034747bf7f29f))
* **voice:** karaoke timings, sycophancy/STT hardening, web search + small.en ([ea1be2a](https://github.com/ulrichando/jarvis/commit/ea1be2acef44f54429831ed4212688f9f670b30b))
* **voice:** kill 'You're right' sycophancy opener + swallow transient network blips ([0375fa8](https://github.com/ulrichando/jarvis/commit/0375fa8c17f1d8d04f37cbf11de192f1da0c28e6))
* **voice:** local mode auto-discovers an installed Ollama model ([#197](https://github.com/ulrichando/jarvis/issues/197)) ([16c6824](https://github.com/ulrichando/jarvis/commit/16c6824791c07265fb2583b4e1983b78d15843c0))
* **voice:** local STT survives GPU contention + stop mislabeling it as DeepSeek ([#200](https://github.com/ulrichando/jarvis/issues/200)) ([b4de318](https://github.com/ulrichando/jarvis/commit/b4de318e63cc5cb41fc5390b582ba082d9d84401))
* **voice:** local STT uses large-v3-turbo — fits alongside qwen3:4b on a 6 GB GPU ([#210](https://github.com/ulrichando/jarvis/issues/210)) ([7e8e663](https://github.com/ulrichando/jarvis/commit/7e8e663a904e6699fe8545c64ae6bdf15c8f4786))
* **voice:** local voice mode fits qwen3-4b on 6 GB + honest error labels ([#213](https://github.com/ulrichando/jarvis/issues/213)) ([e5f552c](https://github.com/ulrichando/jarvis/commit/e5f552c2a3ac683d4cf15774e542762b9fc2c358))
* **voice:** pre-merge deploy-seam + cutover fixes for cloud memory ([#234](https://github.com/ulrichando/jarvis/issues/234)) ([1fe38dc](https://github.com/ulrichando/jarvis/commit/1fe38dcf3f87aa08ad8146862318d7f5ac3cbb87))
* **voice:** snappier turns — 0.5s endpointing + base.en STT ([e44ec5b](https://github.com/ulrichando/jarvis/commit/e44ec5b48faa5a028d99e3bd8ba2bdfdf787e6fc))
* **voice:** snappier turns — 0.5s endpointing + base.en STT ([4d18bd7](https://github.com/ulrichando/jarvis/commit/4d18bd710fd8e36761de2cc0d5707821e01a7118))
* **voice:** stop JARVIS interrupting himself (re-home echo-gate speech feed) ([#259](https://github.com/ulrichando/jarvis/issues/259)) ([49efd43](https://github.com/ulrichando/jarvis/commit/49efd43047ae9e4181a839bb17ef2383fbd3942b))
* **voice:** stop the ambient gate dropping the user's real speech ([#286](https://github.com/ulrichando/jarvis/issues/286)) ([b204520](https://github.com/ulrichando/jarvis/commit/b204520b18cfb3a0fe3a7069ceb7e6c1026f7b61))
* **voice:** stop the pytest suite from firing REAL desktop notifications ([#271](https://github.com/ulrichando/jarvis/issues/271)) ([7be284c](https://github.com/ulrichando/jarvis/commit/7be284c3435f7b2bb6976eb9c4e229569eafd582))
* **voice:** stop TTS from reading special characters aloud ([#272](https://github.com/ulrichando/jarvis/issues/272)) ([613ed48](https://github.com/ulrichando/jarvis/commit/613ed4845cc494db3b525e28cfd42c48185335cc))
* **voice:** STT back to small.en (best local accuracy, benchmarked) ([b9f4b6e](https://github.com/ulrichando/jarvis/commit/b9f4b6e29114d9d6531bf6e28749e9f2d3ddad7a))
* **voice:** STT back to small.en (best local accuracy, benchmarked) ([f8e8f14](https://github.com/ulrichando/jarvis/commit/f8e8f146af14299a9e80930c057792ddc7787391))
* **voice:** STT-GPU error notification no longer blames a non-existent local LLM ([#270](https://github.com/ulrichando/jarvis/issues/270)) ([db985ff](https://github.com/ulrichando/jarvis/commit/db985ffc69bb7232e7516cbbb50645050e666999))
* **vps-deploy:** observable, race-tolerant, self-edit-safe health gate ([#168](https://github.com/ulrichando/jarvis/issues/168)) ([3c4571b](https://github.com/ulrichando/jarvis/commit/3c4571bdd7b0c9fde5819c8d26d1ba12ab4a5f3b))
* **web/chat:** cleaner thinking indicator + stop voice reading symbols aloud ([#273](https://github.com/ulrichando/jarvis/issues/273)) ([c76b234](https://github.com/ulrichando/jarvis/commit/c76b2348ef6c0ef77fa3337e287a076fb63b7788))
* **web/chat:** fix the REAL reasoning block + Brave-only web search ([#274](https://github.com/ulrichando/jarvis/issues/274)) ([4c7e69c](https://github.com/ulrichando/jarvis/commit/4c7e69cbac5f3f0d0f38c2907fd1d552d22b9828))
* **web/chat:** represent thinking as JUST the 3 loading dots (no box) ([#276](https://github.com/ulrichando/jarvis/issues/276)) ([00c4537](https://github.com/ulrichando/jarvis/commit/00c45371b195a2a8d0cb19a2863c524900dad987))
* **web/chat:** stop &lt;/jarvisArtifact&gt; leaking into Mermaid (+ plan/results) content ([#277](https://github.com/ulrichando/jarvis/issues/277)) ([8e0df2f](https://github.com/ulrichando/jarvis/commit/8e0df2f6443e325a2196a9fb3bc15efbd8c030cf))
* **web/chat:** web search ON by default (the real reason DeepSeek couldn't search) ([#280](https://github.com/ulrichando/jarvis/issues/280)) ([22d7b5a](https://github.com/ulrichando/jarvis/commit/22d7b5a41dbb9e84c9cc74c5503f261c5636fcdb))
* **web/tts:** Edge failure falls back to local Kokoro, not the robotic browser voice ([#275](https://github.com/ulrichando/jarvis/issues/275)) ([39d1b99](https://github.com/ulrichando/jarvis/commit/39d1b99d4c87c1e0dde4d24661f3717e0cced467))
* **web/voice:** hear the user better — deliver late transcripts, capture quiet speech ([#267](https://github.com/ulrichando/jarvis/issues/267)) ([fd74bce](https://github.com/ulrichando/jarvis/commit/fd74bce57bc60e5f66993bd27016ab43fad3c341))
* **web+cli:** ultraplan teleport-back returns to the terminal ([#179](https://github.com/ulrichando/jarvis/issues/179)) ([75a88ad](https://github.com/ulrichando/jarvis/commit/75a88ad2be8ef40850bde3423b4eea939e6d3a0a))
* **web:** auth-gate POST /api/mcp/test + GET /api/mcp (SSRF/recon) ([#184](https://github.com/ulrichando/jarvis/issues/184)) ([d7b33fb](https://github.com/ulrichando/jarvis/commit/d7b33fb1dd871e2ac1176a1cd35efbe8f94baa1d))
* **web:** carry free-text feedback on ultraplan plan reject (refine loop) ([#334](https://github.com/ulrichando/jarvis/issues/334)) ([dbf8201](https://github.com/ulrichando/jarvis/commit/dbf8201120eacbeaa853cc5150984bf51a7ef10e))
* **web:** close open-redirect bypass in login next-param guard ([#232](https://github.com/ulrichando/jarvis/issues/232)) ([6374458](https://github.com/ulrichando/jarvis/commit/63744580ea2e267be205100b7eb09b29269d1abf))
* **web:** close two live bridge auth holes from the src/web review ([#161](https://github.com/ulrichando/jarvis/issues/161) findings [#2](https://github.com/ulrichando/jarvis/issues/2), [#3](https://github.com/ulrichando/jarvis/issues/3)) ([#327](https://github.com/ulrichando/jarvis/issues/327)) ([2a80c62](https://github.com/ulrichando/jarvis/commit/2a80c62b4c07c1a80f3bda695fc59bebc8c2cd74))
* **web:** constant-time token compare on /api/scheduled/voice-pending ([#219](https://github.com/ulrichando/jarvis/issues/219)) ([654d297](https://github.com/ulrichando/jarvis/commit/654d297f5b8ac6d595a5c7c76858858ab765d574))
* **web:** DELETE workspace handler referenced undefined 'req' — build failed to type-check ([#167](https://github.com/ulrichando/jarvis/issues/167)) ([40ad580](https://github.com/ulrichando/jarvis/commit/40ad5809adf6652f6074b8b67529c05b1d9e6868))
* **web:** give no-repo /code sessions a truthful from-scratch prompt (ultraplan empty-repo confusion) ([#333](https://github.com/ulrichando/jarvis/issues/333)) ([5c3f070](https://github.com/ulrichando/jarvis/commit/5c3f070791c8c346c05f66fff5b43d19a4851e79))
* **web:** manual run-now doesn't queue a voice reminder (no double-speak) ([#223](https://github.com/ulrichando/jarvis/issues/223)) ([bf7ffad](https://github.com/ulrichando/jarvis/commit/bf7ffad8efbb190d179b995bbd7e3d49e851831e))
* **web:** pause AskUserQuestion on /code instead of auto-approving it ([#324](https://github.com/ulrichando/jarvis/issues/324)) ([14078ff](https://github.com/ulrichando/jarvis/commit/14078ff1aae66d5afe446775421e15d8f5cb275f))
* **web:** plug two resource leaks from the src/web review ([#161](https://github.com/ulrichando/jarvis/issues/161) findings [#8](https://github.com/ulrichando/jarvis/issues/8), [#9](https://github.com/ulrichando/jarvis/issues/9)) ([#329](https://github.com/ulrichando/jarvis/issues/329)) ([d54433e](https://github.com/ulrichando/jarvis/commit/d54433ef8d6be152a2d191f89a75de303b212a3a))
* **web:** prompt for the 2FA code at login (fixes 2FA lockout) ([#230](https://github.com/ulrichando/jarvis/issues/230)) ([b003f89](https://github.com/ulrichando/jarvis/commit/b003f89c12033267919ce199897722d2e2d284d1))
* **web:** PTY server auth ON by default + gate the host-shell fallback ([#161](https://github.com/ulrichando/jarvis/issues/161) finding [#4](https://github.com/ulrichando/jarvis/issues/4)) ([#328](https://github.com/ulrichando/jarvis/issues/328)) ([34a2902](https://github.com/ulrichando/jarvis/commit/34a2902cc9c59dc5b201e3dbf633ebeec27901c0))
* **web:** raise proxy body limit so /ultraplan bundle uploads over 10MB work ([#340](https://github.com/ulrichando/jarvis/issues/340)) ([d90a396](https://github.com/ulrichando/jarvis/commit/d90a396d78d5f9448db8d96a157de9f54b376aa3))
* **web:** route chat web search through SearXNG (DuckDuckGo CAPTCHA-blocks the VPS IP) ([#202](https://github.com/ulrichando/jarvis/issues/202)) ([624d39b](https://github.com/ulrichando/jarvis/commit/624d39bf9dc0f65890346849caef5fd113260278))
* **web:** session actually times out — 8h idle / 7d absolute cap ([#203](https://github.com/ulrichando/jarvis/issues/203)) ([e0ffd53](https://github.com/ulrichando/jarvis/commit/e0ffd5355cc4991f8f9339eb3dee2ee04e061798))
* **web:** Settings → Providers review — Test toast, status dot, Ollama autodetect, a11y ([#188](https://github.com/ulrichando/jarvis/issues/188)) ([6d105e1](https://github.com/ulrichando/jarvis/commit/6d105e192aeb4c0bae7bfd64336bc4c79882ea3e))
* **web:** settings Data/Privacy/Knowledge — honest toasts + real file guards ([#187](https://github.com/ulrichando/jarvis/issues/187)) ([cbcaacb](https://github.com/ulrichando/jarvis/commit/cbcaacb41fe40f39bc689230ce419e7585fce955))
* **web:** settings writes no longer delete CLI/voice keys from shared settings.json ([#185](https://github.com/ulrichando/jarvis/issues/185)) ([87baf06](https://github.com/ulrichando/jarvis/commit/87baf06d57f81fbf4419175f3a63c48a6b83e194))
* **web:** shared-token auth resolves to the real box owner, not LOCAL_USER_ID ([874a029](https://github.com/ulrichando/jarvis/commit/874a029b6d4a811aa48e8c871e0489ce02576f6c))
* **web:** stage progression sends the finalized history, not a stale closure ([#161](https://github.com/ulrichando/jarvis/issues/161) finding [#5](https://github.com/ulrichando/jarvis/issues/5)) ([#332](https://github.com/ulrichando/jarvis/issues/332)) ([26e1eae](https://github.com/ulrichando/jarvis/commit/26e1eae666dbf8b44449a4abb570b857663bd6ef))
* **web:** two-way delete sync — hide + propagate tombstones in the browser ([#284](https://github.com/ulrichando/jarvis/issues/284)) ([a8dbf6c](https://github.com/ulrichando/jarvis/commit/a8dbf6c80196a1d6bdac1c0db1a7cf5de432072e))
* **web:** ultraplan pauses for browser review instead of auto-executing ([#176](https://github.com/ulrichando/jarvis/issues/176)) ([7367dd7](https://github.com/ulrichando/jarvis/commit/7367dd76eb35afae5a319a9f0d89023ee30a0362))
* **web:** wire Settings &gt; Capabilities toggles to the render path ([#183](https://github.com/ulrichando/jarvis/issues/183)) ([631b8ce](https://github.com/ulrichando/jarvis/commit/631b8ce2859f0af822e93afd7dcd396aed46b8f8))


### Performance Improvements

* **web:** sidebar sessions list via point queries, not a full transcript read per poll ([#161](https://github.com/ulrichando/jarvis/issues/161) finding [#7](https://github.com/ulrichando/jarvis/issues/7)) ([#330](https://github.com/ulrichando/jarvis/issues/330)) ([36bf8d0](https://github.com/ulrichando/jarvis/commit/36bf8d01db2bfa26100865e73466822ece995190))


### Reverts

* **web/voice:** drop the full-screen overlay UI from [#265](https://github.com/ulrichando/jarvis/issues/265) ([#266](https://github.com/ulrichando/jarvis/issues/266)) ([15ed784](https://github.com/ulrichando/jarvis/commit/15ed784bbc6e0d50eb4f75b5f11a3d472c141c9f))

## [0.2.2](https://github.com/ulrichando/jarvis/compare/v0.2.1...v0.2.2) (2026-07-09)


### Features

* **bridge:** browser-agent acting loop for the Chrome extension ([db16031](https://github.com/ulrichando/jarvis/commit/db1603139095f6157676796cb5ec21c5d7e3ad1a))
* **bridge:** route image queries to a vision model ([db32258](https://github.com/ulrichando/jarvis/commit/db32258654e3c27ddea91f33c0c23cf79cef7373))
* **bridge:** tab-group agent tools (activate_tab, group_tabs) ([8f844df](https://github.com/ulrichando/jarvis/commit/8f844dfb093af6a64fc16859d28b0af92a072739))
* CCR-compat backend + plan approval for /ultraplan (Phase B) ([1c5fdcf](https://github.com/ulrichando/jarvis/commit/1c5fdcfb9578c77287ae2dbe1b692b38693fc1e2))
* **cli-gateway:** remote model-gateway wiring — web gatewayUrl + persist + binary bootstrap ([eb0d51b](https://github.com/ulrichando/jarvis/commit/eb0d51b4063a02d0ee072265f03e4290298349dd))
* **cli,web:** 'jarvis keys pull' — sync provider keys from the server ([088ae36](https://github.com/ulrichando/jarvis/commit/088ae369c24d1ac6d102217bc7ccd2cb021eba7a))
* **cli:** [@jarvis](https://github.com/jarvis) GitHub-native webhook bot (gh-action v1) ([3c2cae0](https://github.com/ulrichando/jarvis/commit/3c2cae00d9b6ae884f6b040cc12bbf908828edd2))
* **cli:** /teleport auto-switches into the session's repo checkout ([3e0e500](https://github.com/ulrichando/jarvis/commit/3e0e500ea573888b6260ec6e2007a6f88824a7bc))
* **cli:** /teleport interactive arrow-key session picker (claude.ai parity) ([90db2a7](https://github.com/ulrichando/jarvis/commit/90db2a754ff85a71b2f093e8b5df33279b7e4a85))
* **cli:** /teleport matches claude.ai — same-repo, resume in place (no clone) ([1ee2571](https://github.com/ulrichando/jarvis/commit/1ee25719f6532db4417760d33e9dd1618216fcd0))
* **cli:** /teleport uses the fork's REAL Claude teleport machinery (not my custom picker) ([3b26d2e](https://github.com/ulrichando/jarvis/commit/3b26d2ee0c8bac1f0604e8f6a8bb4499c5de62be))
* **cli:** /workflows listing, slash commands, permission + detail dialogs ([a9861b9](https://github.com/ulrichando/jarvis/commit/a9861b9eb9f9499c3378896c65c6cce011364bd0))
* **cli+web:** jarvis cloud + full teleport — claude.ai --cloud/--teleport parity ([63c6c78](https://github.com/ulrichando/jarvis/commit/63c6c78f2d0592cc96981926d1b22ef9a017f814))
* **cli+web:** jarvis cloud + full teleport — claude.ai --cloud/--teleport parity ([42e16ee](https://github.com/ulrichando/jarvis/commit/42e16ee8ff1cabef11abba754bf2758013d58e5a))
* **cli+web:** jarvis cloud + full teleport — claude.ai --cloud/--teleport parity ([97d6591](https://github.com/ulrichando/jarvis/commit/97d65919e624c800309fc634b1c29ee23ac8efd1))
* **cli:** add bg.ts session manager + 21 more feature flags ([0cf821e](https://github.com/ulrichando/jarvis/commit/0cf821eb3990adb617d1fc01e9ae95c7a1e10052))
* **cli:** add bg.ts session manager + 21 more feature flags ([dd13a7c](https://github.com/ulrichando/jarvis/commit/dd13a7ce5ce3865b59d5a21fc7678ac56ffb9b57))
* **cli:** batch workflow progress into task state + sdk events ([f6740f6](https://github.com/ulrichando/jarvis/commit/f6740f688df58762b3f36ec86c59ce54b7b07d25))
* **cli:** dynamic-workflows + history-snip engines + SearXNG web_search backend ([baafa7b](https://github.com/ulrichando/jarvis/commit/baafa7be5959bb6105623db5d05c7e9cc0e062c9))
* **cli:** enable /files, /version, ConfigTool for JARVIS ([d08e9f5](https://github.com/ulrichando/jarvis/commit/d08e9f53315c8b8a57658795fb98e18a1fb99932))
* **cli:** enable /files, /version, ConfigTool for JARVIS (was ant-only) ([3d3137c](https://github.com/ulrichando/jarvis/commit/3d3137c71f2410b07b4fedaddc601c7032176448))
* **cli:** enable /files, /version, ConfigTool for JARVIS (was ant-only) ([ae2c8d6](https://github.com/ulrichando/jarvis/commit/ae2c8d636e9dec5699fa7f48f59a55013278f093))
* **cli:** enable HISTORY_SNIP feature flag ([11be424](https://github.com/ulrichando/jarvis/commit/11be4247db9e13efc2a3f82a58fdb8d4cc55d8c9))
* **cli:** enable HOOK_PROMPTS + EXTRACT_MEMORIES + BUILDING_CLAUDE_APPS; pin system ripgrep ([b9e0221](https://github.com/ulrichando/jarvis/commit/b9e022172bcc0bfec2e5ba806ddb62e2ef75a41f))
* **cli:** enable WORKFLOW_SCRIPTS feature flag ([d1ca7d0](https://github.com/ulrichando/jarvis/commit/d1ca7d02f90c3a8fc6d5b5f155c550b364fc6c16))
* **cli:** gh-agent config loader + author allowlist gate ([a61a64a](https://github.com/ulrichando/jarvis/commit/a61a64a6526a2ccd3ff8b6cb03a9ebce33419e4f))
* **cli:** gh-agent gh wrappers (listMentions, postComment) ([d5edfab](https://github.com/ulrichando/jarvis/commit/d5edfabf7d77dcf2d092b93e906b29565dc87613))
* **cli:** gh-agent one-sweep loop (poll, gate, ack, cursor) ([c5c6de1](https://github.com/ulrichando/jarvis/commit/c5c6de170aed6f0c76747eb4e7f6e159156e8c0d))
* **cli:** gh-agent per-repo cursor (no-replay marker) ([f75af7a](https://github.com/ulrichando/jarvis/commit/f75af7ae6e2fb000dc967de3b15c29a61931cbb9))
* **cli:** history-snip runtime (queue, nudge pacing, boundary insert) ([a60dae3](https://github.com/ulrichando/jarvis/commit/a60dae34762a1014d5c78ad12ede9e1162e544ca))
* **cli:** id-anchored Snip tool + boundary message render ([bf03024](https://github.com/ulrichando/jarvis/commit/bf030241e120316c184fea1c7ef279ca461dc341))
* **cli:** jarvis computer-use — drive the desktop from the terminal ([#50](https://github.com/ulrichando/jarvis/issues/50)) ([1918a55](https://github.com/ulrichando/jarvis/commit/1918a55cafad8758171951da7b0225daead8ae57))
* **cli:** jarvis uninstall — self-uninstall subcommand (idiomatic, like rustup self uninstall) ([fda15e5](https://github.com/ulrichando/jarvis/commit/fda15e5a2bfef740cd65ea7fede6e80530da2ad3))
* **cli:** LocalWorkflowTask state + real skip/kill ([0b8c45c](https://github.com/ulrichando/jarvis/commit/0b8c45c7a34ecbc2df81256061028bdfd1e83b4e))
* **cli:** named-workflow loader (user + project dirs) ([de1b3b0](https://github.com/ulrichando/jarvis/commit/de1b3b0f1fb79fd23cd7b6dda62e10bf761bfa88))
* **cli:** Phase 1 — working standalone binary build pipeline ([58ad98d](https://github.com/ulrichando/jarvis/commit/58ad98d9e38eb5f7400d9c85cb8cbcc83ec7e466))
* **cli:** Phase 1 — working standalone binary build pipeline ([2af350a](https://github.com/ulrichando/jarvis/commit/2af350ae319bb453f98f19e5cd6f1c52441a573c))
* **cli:** real WorkflowTool (validate, permissions, background launch) ([98ea2bb](https://github.com/ulrichando/jarvis/commit/98ea2bbfd3e80d41195086a858422398401f2935))
* **cli:** register jarvis gh-agent command (P1 poll+ack) ([2ceca93](https://github.com/ulrichando/jarvis/commit/2ceca93b6f90d38425a347b78c17964161390072))
* **cli:** restore tool/subcommand functionality + standalone binary + web installer ([14532a2](https://github.com/ulrichando/jarvis/commit/14532a2bd389c18312efac26e780fa4de06708b1))
* **cli:** SdkWorkflowProgress type + additive workflow_progress schema field ([c950fca](https://github.com/ulrichando/jarvis/commit/c950fca909844790aa21c1b95dd3398ee0f2c74f))
* **cli:** snip projection (stateless boundary-based filtering) ([6940a59](https://github.com/ulrichando/jarvis/commit/6940a59bb6773c4829579c4ee5e2f9a61a1086bd))
* **cli:** snip range math + boundary creation (resume-shape) ([af728c1](https://github.com/ulrichando/jarvis/commit/af728c1943456220273597834d4e9a56c08e1057))
* **cli:** surface /ultraplan — set JARVIS_ULTRAPLAN + local CCR base ([d5452a6](https://github.com/ulrichando/jarvis/commit/d5452a6504784448d04a0885c323471a317497c1))
* **cli:** surface /ultraplan — set JARVIS_ULTRAPLAN + local CCR base ([4b3255d](https://github.com/ulrichando/jarvis/commit/4b3255dd04d14047a653b81b6b0f2fb3528f84e9))
* **cli:** ultracode effort mode + per-model effort ladder (ultrathink→max) ([b403ad6](https://github.com/ulrichando/jarvis/commit/b403ad6d5563664d9fd93a742c8b55277373dc14))
* **cli:** un-stub /teleport as a working slash command (plus /tp) ([b8b71ef](https://github.com/ulrichando/jarvis/commit/b8b71efa72bc05b81507d150bdd1b29890caf4aa))
* **cli:** unlock 11 feature-gated tools + shared launcher refactor ([309c727](https://github.com/ulrichando/jarvis/commit/309c7279a46a907c8756aa8a166b034c47acd813))
* **cli:** unlock 11 feature-gated tools + shared launcher refactor ([f9ccc58](https://github.com/ulrichando/jarvis/commit/f9ccc58d23dc05fab0d02e6a1397af7dc1cc1b30))
* **cli:** verbatim upstream Workflow tool prompt ([e3eca24](https://github.com/ulrichando/jarvis/commit/e3eca2483faf89c598d18df896a14023e3c50d40))
* **cli:** workflow agent() bridge (schema/skip/journal/progress) ([0de4b26](https://github.com/ulrichando/jarvis/commit/0de4b2619770a62efd9d1700c3cd1f4e5513daa1))
* **cli:** workflow concurrency limiter (min(16,cores-2), 1000 cap) ([8ee551b](https://github.com/ulrichando/jarvis/commit/8ee551b2980538a8059791bd6204c0c96ad30f5b))
* **cli:** workflow journal with prefix-semantics resume cache ([f3b442c](https://github.com/ulrichando/jarvis/commit/f3b442c191af944f5bdc8b9537535e26f022f913))
* **cli:** workflow meta parser + determinism guard ([7e7a615](https://github.com/ulrichando/jarvis/commit/7e7a6157d3dfd0f6e4a0847ca370f47227c3d9f5))
* **cli:** workflow pipeline/parallel combinators ([c986dc5](https://github.com/ulrichando/jarvis/commit/c986dc557b5c492154824199d247b6401d2cebc1))
* **cli:** workflow runAgent dispatch bridge + built-in workflow agent ([6177444](https://github.com/ulrichando/jarvis/commit/6177444e3c6fa44ac1b25ab07b41f6897b5945d1))
* **cli:** workflow runner (journal+vm+serialize+abort race) ([f8b1471](https://github.com/ulrichando/jarvis/commit/f8b1471a17e2f83a670cea651ba4dabd523dfb8d))
* **cli:** workflow vm runtime (globals, determinism guards) ([2bbc27d](https://github.com/ulrichando/jarvis/commit/2bbc27d7c0410f912fc534ceef78a8e075a5cfbe))
* **computer-use:** add a Menu button (start-menu) to the cloud desktop panel ([85ec5bf](https://github.com/ulrichando/jarvis/commit/85ec5bfface1726b0b140bf32efb4656c4e98081))
* **computer-use:** add a Menu button to the cloud desktop panel ([d8b1c03](https://github.com/ulrichando/jarvis/commit/d8b1c0360c0874e4cb01be79cc6993a1262cad47))
* **computer-use:** add claude-sonnet-5 to the CU roster ([d6a3a69](https://github.com/ulrichando/jarvis/commit/d6a3a69afe0ca2c1459bcc5894b0990cc7908bf5))
* **computer-use:** app launcher in the panel (reliable menu, rendered-verified) ([ac3f1bd](https://github.com/ulrichando/jarvis/commit/ac3f1bded4d08b21baf12187e9557490ef0e05bc))
* **computer-use:** cloud desktop container — VPS sandbox for account-connected computer use ([d5600bd](https://github.com/ulrichando/jarvis/commit/d5600bd8c0245f2a010340025365e33e4440f4b2))
* **computer-use:** cloud desktop container — VPS sandbox for account-connected computer use ([0e2a224](https://github.com/ulrichando/jarvis/commit/0e2a224659a0af16b145f2966c22f2447406494f))
* **computer-use:** cloud desktop ships real apps + a desktop surface ([34ee143](https://github.com/ulrichando/jarvis/commit/34ee14305ae5c3ce3b19711bb69d5cb254c7ec8e))
* **computer-use:** cloud desktop, native Anthropic adapter, Sonnet 5, CLI command ([00d39bf](https://github.com/ulrichando/jarvis/commit/00d39bf44c0278d0aa6ea3a83aab0e27571cc32a))
* **computer-use:** detached web runs — sessions survive the browser, Auto applies live ([77b6e21](https://github.com/ulrichando/jarvis/commit/77b6e21a1377e7c092b0454de8405fbfd83ce3c2))
* **computer-use:** detached web runs + voice-agent review fixes + complete Groq purge ([27b91c9](https://github.com/ulrichando/jarvis/commit/27b91c9cea3ecedb34d3741607201ae2a311cb7e))
* **computer-use:** make apps discoverable — desktop icons + agent app hint ([cc29917](https://github.com/ulrichando/jarvis/commit/cc29917865134becff0a1a1843fd7b82b48f31c1))
* **computer-use:** make cloud desktop apps discoverable (agent could not find them) ([1d014e0](https://github.com/ulrichando/jarvis/commit/1d014e06e15ea6c4b41e3c88ac51bc5bf4779b97))
* **computer-use:** make the cloud desktop a real desktop, not just a browser ([f4b666b](https://github.com/ulrichando/jarvis/commit/f4b666b1f22f07fd63ceb3220134f1abb056257b))
* **computer-use:** native computer_20251124 Anthropic adapter for the sidecar ([d38ffb7](https://github.com/ulrichando/jarvis/commit/d38ffb79dac36257a98276d9044be2dd255530fc))
* **computer-use:** reliable app launcher in the panel (rendered-verified) ([33b2af9](https://github.com/ulrichando/jarvis/commit/33b2af955c0d27d8a069a4b651dd5ed868350dca))
* **computer-use:** upgrade native CU loop — Sonnet 5, thinking, sandbox bash ([c558dab](https://github.com/ulrichando/jarvis/commit/c558dab2e689e73680a58111ba6ad1731ab928f1))
* **computer-use:** upgrade the native Anthropic CU loop — Sonnet 5, thinking, sandbox bash ([ead35b9](https://github.com/ulrichando/jarvis/commit/ead35b9c03b681c853a9af1e017f2ae2ec1b9666))
* continuous VPS deploy pipeline + settings/gh-agent/desktop wave ([37b3483](https://github.com/ulrichando/jarvis/commit/37b3483e97f299fafcd3b9259de93bed66a599c2))
* **deploy:** continuous deploy — VPS polls origin/master and self-updates ([0f9cff6](https://github.com/ulrichando/jarvis/commit/0f9cff6ef6e4542eea6fef9a2d36771889cc6630))
* **desktop:** chat-panel account/CLI/restart controls + stall recovery ([480eb50](https://github.com/ulrichando/jarvis/commit/480eb50946fff5ac067fabdf534e299d6f976485))
* **desktop:** make the voice-UI sign-in state-aware (Sign in ⇄ Signed in · &lt;server&gt; · Sign out) ([8bede50](https://github.com/ulrichando/jarvis/commit/8bede503af97f94ca744067f3731166989161fb8))
* **desktop:** move Sign-in/CLI shortcuts to the voice-agent UI ([fd4f0c9](https://github.com/ulrichando/jarvis/commit/fd4f0c919ad2aec8ecd1c31d475e3a2416433ae6))
* **desktop:** open deployed web, fall back to local when the VPS is down ([b5f7562](https://github.com/ulrichando/jarvis/commit/b5f75620615bbbeea87463616759e462559ee68b))
* **desktop:** show Claude + DeepSeek as conversation modes; notify real LLM ([760746a](https://github.com/ulrichando/jarvis/commit/760746a4013df3ceb834dcae0cab81f06a25f9ef))
* **desktop:** tray voice controls — speech-rate presets, live ✓ sync, model-pick preservation ([1a95fdf](https://github.com/ulrichando/jarvis/commit/1a95fdf75823686b46818321bdc337354137f0ad))
* **evolution:** AutoData-informed queue admission, retry feedback + fitness learnability ([c1d3066](https://github.com/ulrichando/jarvis/commit/c1d30665702d35e1e8e12508e66eab5a7948f35c))
* **evolution:** batch review council — review all pending at once ([8eff9fc](https://github.com/ulrichando/jarvis/commit/8eff9fcd972044808ca58392ed46027ffb3528a3))
* **evolution:** incremental review-all — background run + live progress ([7ddd98c](https://github.com/ulrichando/jarvis/commit/7ddd98c8b0e5cb65db61fb979d6ec6df6435e1f9))
* **evolution:** lived-experience shadow trial + loop heartbeat ([c635a0b](https://github.com/ulrichando/jarvis/commit/c635a0b4f5206193cbd13a6ac83b0cdab37a792e))
* **ext:** + menu — Take a screenshot / Add an image (vision context) ([93db16b](https://github.com/ulrichando/jarvis/commit/93db16b9abb4214306453e04ea5137ca6c70f6f3))
* **ext:** auto-create the Jarvis tab group on panel open (Claude parity) ([f65cda0](https://github.com/ulrichando/jarvis/commit/f65cda0293ed37a9b6272a62937e454202f3b47f))
* **ext:** bigger composer + slash commands (/compact, /clear) ([d1b96ae](https://github.com/ulrichando/jarvis/commit/d1b96ae0c7afca7bd8803bf3ad1ab93c069b7b40))
* **ext:** full-page settings (Permissions/Shortcuts/Options) — Claude parity ([ff34f32](https://github.com/ulrichando/jarvis/commit/ff34f329bc3b79a527463d2b77c1637f0c0b8d27))
* **ext:** Jarvis in Chrome — acting loop UI, site permissions, console + task fixes ([59a0bb5](https://github.com/ulrichando/jarvis/commit/59a0bb5bd42d03ec171003584e42a01d583bb631))
* **ext:** Jarvis in Chrome — Claude-for-Chrome-parity extension + browser-agent bridge ([c04581b](https://github.com/ulrichando/jarvis/commit/c04581b0c987248193e5c91abc14f101984fdc80))
* **ext:** Jarvis in Chrome — Claude-for-Chrome-parity extension + browser-agent bridge ([6636d7e](https://github.com/ulrichando/jarvis/commit/6636d7ef348ab71b96301463700f55dfe938b046))
* **ext:** record a workflow -&gt; shortcut (pointer button, Claude parity) ([80b47ac](https://github.com/ulrichando/jarvis/commit/80b47ac410ef9a06982989a25b609a88ff3f2da3))
* **ext:** richer approval — Allow / Decline / Always allow on this site ([f9586a1](https://github.com/ulrichando/jarvis/commit/f9586a1b407100d88e3a2cf3256f23940073c9fb))
* **ext:** tab groups — group Jarvis's tabs + cross-tab context ([f554bc5](https://github.com/ulrichando/jarvis/commit/f554bc5e913fba37ad835ba937977bf181f4b88b))
* **gh-action:** author-association authorization guard ([1a7a701](https://github.com/ulrichando/jarvis/commit/1a7a7012abc76ad5d4b82fba5899a23b1245ecda))
* **gh-action:** author-association authorization guard ([93f72b6](https://github.com/ulrichando/jarvis/commit/93f72b662ebfff0516665c5746366641a2b360f0))
* **gh-action:** capture triggering comment id on ActionEvent ([e3787ef](https://github.com/ulrichando/jarvis/commit/e3787eff925a23272bf9704b85c342dc42572ddf))
* **gh-action:** jarvis.yml workflow template (webhook [@jarvis](https://github.com/jarvis) trigger) ([a890f46](https://github.com/ulrichando/jarvis/commit/a890f467fe9a2d06d9ec33dba510483375193ece))
* **gh-action:** jarvis.yml workflow template (webhook [@jarvis](https://github.com/jarvis) trigger) ([9e525b8](https://github.com/ulrichando/jarvis/commit/9e525b87387bcb80fb00a0ed53fee1d2f722ff1b))
* **gh-action:** orchestrator — guard, neutralize hooks, run jarvis, publish ([626c336](https://github.com/ulrichando/jarvis/commit/626c336ada9eff4595fdbb583da3d525079a202f))
* **gh-action:** orchestrator — guard, neutralize hooks, run jarvis, publish ([fe14c13](https://github.com/ulrichando/jarvis/commit/fe14c136beef5dc2de974fd510047dc1f15ff4aa))
* **gh-action:** parse GitHub Actions event → normalized task ([58098f7](https://github.com/ulrichando/jarvis/commit/58098f74a635123928c4c6fb941c7db64374c757))
* **gh-action:** parse GitHub Actions event → normalized task ([227f4b1](https://github.com/ulrichando/jarvis/commit/227f4b1c6f3b72a33213d64c37f60a4d9900b3ba))
* **gh-action:** register jarvis gh-action command + launcher skip-list ([fd5211e](https://github.com/ulrichando/jarvis/commit/fd5211ed4e00e35bcbc064107c691f7ae998d852))
* **gh-action:** register jarvis gh-action command + launcher skip-list ([c3818af](https://github.com/ulrichando/jarvis/commit/c3818afdba6214902f77fd901c61e511b94902d5))
* **gh-agent:** P2 execution + P3 systemd timer ([cdba904](https://github.com/ulrichando/jarvis/commit/cdba904b4d9652ed4132f8b894fa764734f46f31))
* **gh-agent:** P2 execution + P3 systemd timer (poll-runner) ([e4280b8](https://github.com/ulrichando/jarvis/commit/e4280b8e4f9da2305318064df328138efadb915c))
* **gh-app:** [@jarvis-gh-bot](https://github.com/jarvis-gh-bot) can merge PRs ([fd23ad8](https://github.com/ulrichando/jarvis/commit/fd23ad81f31a39ae60c0e229d540dadd5b5e657c))
* **gh-app:** [@jarvis-gh-bot](https://github.com/jarvis-gh-bot) can merge pull requests ([46e2fd1](https://github.com/ulrichando/jarvis/commit/46e2fd10f12bd40e68257dc0d21a74fa03f6e91e))
* **gh-app:** app JWT + installation token minting ([b87e486](https://github.com/ulrichando/jarvis/commit/b87e4867914fa8e287f197eda8a9f0cae02a55a5))
* **gh-app:** app manifest + setup page ([0051ef9](https://github.com/ulrichando/jarvis/commit/0051ef9f63889c7e4bd401992da3a64132f25f3f))
* **gh-app:** carry comment_id + tracking_comment_id on jobs ([9759e5b](https://github.com/ulrichando/jarvis/commit/9759e5b556b1faddf431bb343e5aaa715e0bc90b))
* **gh-app:** code-session runner behind GH_APP_USE_CODE_SESSIONS — dispatch, poll, PR via /code ([cb99a36](https://github.com/ulrichando/jarvis/commit/cb99a36df7d61be9a3555311c4062cec423da955))
* **gh-app:** deploy (compose service, gh.0wlan.com route, runbook) ([05746b8](https://github.com/ulrichando/jarvis/commit/05746b8d62972b5294dae0ce9e8db4734aa7abae))
* **gh-app:** feedback module — 👀 ack, tracking comment, outcome edit ([60232f0](https://github.com/ulrichando/jarvis/commit/60232f0dd20b7a88a7057f109d4362433cd3c34f))
* **gh-app:** gh.0wlan.com root redirects to the Jarvis settings bot card ([b5770e5](https://github.com/ulrichando/jarvis/commit/b5770e50c72b8a94f69d00730f9105bce015c29f))
* **gh-app:** HTTP server (setup, webhook, health) + worker ([e839075](https://github.com/ulrichando/jarvis/commit/e839075a2d5f77fc38a676a3124c4ff6dfa084d7))
* **gh-app:** manifest-code → credentials capture ([06e12ef](https://github.com/ulrichando/jarvis/commit/06e12ef5b741cac7e9917999f11f4e67439b3596))
* **gh-app:** pass the triggering comment id through the webhook ([1724735](https://github.com/ulrichando/jarvis/commit/172473540b8ffa67c49ce1bf5dad3ce50e6d7936))
* **gh-app:** private JARVIS GitHub App — install-from-page [@jarvis](https://github.com/jarvis) bot (VPS backend) ([06f70ff](https://github.com/ulrichando/jarvis/commit/06f70ffe993e29f696cf635fcd08c24e3179c279))
* **gh-app:** run [@jarvis-gh-bot](https://github.com/jarvis-gh-bot) jobs as watchable /code sessions (claude.ai/code parity) ([a530031](https://github.com/ulrichando/jarvis/commit/a5300317e24b5ef0e223a366ad9a16eebd76e133))
* **gh-app:** sandboxed run via executeTask + installation token ([77aef0f](https://github.com/ulrichando/jarvis/commit/77aef0ff57095d6ff8cee7270361590b5aba7c28))
* **gh-app:** service root redirects to the Jarvis settings bot card ([2b5b213](https://github.com/ulrichando/jarvis/commit/2b5b213bf8fc11af7eb4028ba31b61b01c0f4edd))
* **gh-app:** thread feedback links the watchable /code session (watch-live + watch-the-run) ([227dd43](https://github.com/ulrichando/jarvis/commit/227dd43c41518a8a5bf738d7a2f9ff137edababc))
* **gh-app:** visible thread feedback — 👀 reaction + tracking comment + result (claude-code-action style) ([e691ccf](https://github.com/ulrichando/jarvis/commit/e691ccfcfb99fbfffd6bd853a102d93da354479d))
* **gh-app:** webhook handler (verify, gate, enqueue) + jobs table ([01b3ba7](https://github.com/ulrichando/jarvis/commit/01b3ba76eba7383648dccd9b133029ad5ff0e8fa))
* **gh-app:** webhook HMAC signature verification ([79f046d](https://github.com/ulrichando/jarvis/commit/79f046dc8acad2bfc98f66b16818cee3d6d401ce))
* **gh-app:** wire thread feedback into the worker lifecycle ([73ce304](https://github.com/ulrichando/jarvis/commit/73ce304e4912656e0ac547d830b93b917becfdc1))
* **gh-app:** worker (token, caps, sandbox dispatch) ([62e9a1f](https://github.com/ulrichando/jarvis/commit/62e9a1fc45ff94109effba20bb70b7bcbd351291))
* **hub:** /config diagnostic endpoint (provider key-presence + default route) ([e226282](https://github.com/ulrichando/jarvis/commit/e22628218ed73f953a6dfdd9d016b9a6a8ce1465))
* **hub:** Dockerfile for the VPS hub gateway (Bun proxy, auth-required) ([3443c30](https://github.com/ulrichando/jarvis/commit/3443c30ad4d0ee6119bfff986f0f4846cf4aa37e))
* **hub:** OpenAI-shaped ingress on the proxy (/v1/chat/completions passthrough) ([93bb782](https://github.com/ulrichando/jarvis/commit/93bb78218ffe388458ebeeb0ddafd04c26a0e7ce))
* **hub:** wire hub container into web compose + Caddy /hub route ([8d8014f](https://github.com/ulrichando/jarvis/commit/8d8014f07ee50f6b6703152fae840e270882c47b))
* local install/uninstall toolkit (CLI/voice/desktop/web channels) ([ec4ed61](https://github.com/ulrichando/jarvis/commit/ec4ed610b794f0850df66c78a4da9171af2c330d))
* **notifications:** read OS notifications via D-Bus (no vision) ([718634e](https://github.com/ulrichando/jarvis/commit/718634e7b096addeb0b819f31f613ef3bfa9c0aa))
* **slo:** prompt-cache hit-rate floor in jarvis-slo-check ([14df50d](https://github.com/ulrichando/jarvis/commit/14df50d7835f8abc1b61fce28dac85375d0a3140))
* **voice:** /modes + /mode HTTP endpoints (select/create/update/delete) ([c909a12](https://github.com/ulrichando/jarvis/commit/c909a12931d11357cd69a395901206ccda023098))
* **voice:** conversation_modes resolve + apply (writes setting files) ([0b0c4ac](https://github.com/ulrichando/jarvis/commit/0b0c4ac1db1509a400db8f8d35859b96ec408413))
* **voice:** conversation_modes store — schema + seed + load ([b9edf3c](https://github.com/ulrichando/jarvis/commit/b9edf3c49919dc3c0c86c630c9b01633be7eebe0))
* **voice:** per-mode tool allowlist filter in load_all_livekit_tools ([779d5bc](https://github.com/ulrichando/jarvis/commit/779d5bceedf53d0e2eaf762fba8ddf25ff350889))
* **voice:** provider-error classifier — explicit spoken/notified errors (out-of-credits, rate-limit, auth, quota, …) not raw HTTP ([d8a9494](https://github.com/ulrichando/jarvis/commit/d8a9494c2badafc4d4c013e5e681ae6585ca8a2e))
* **voice:** wire provider-error classifier into session error/close handlers — speak the specific error + notify; recoverable gate supersedes _UNRECOVERABLE_LLM_ERR_RE ([404e036](https://github.com/ulrichando/jarvis/commit/404e0368bea4f14eb255787d37af18bf0c96570f))
* **web/code:** render AskUserQuestion as clickable option chips (claude.ai parity) ([5851db8](https://github.com/ulrichando/jarvis/commit/5851db8cc0fe2bcc01d5f33fc3564d1f6f051941))
* **web/code:** session changes outlive the container + post-turn suggestion chips ([b32a156](https://github.com/ulrichando/jarvis/commit/b32a156fdda0fa8e6ab67ebce5c1156e9b56959c))
* **web/code:** session changes outlive the container + post-turn suggestion chips ([c1f8725](https://github.com/ulrichando/jarvis/commit/c1f87251c35ccb357df0ce12a6add02be9268803))
* **web+cli:** Phase 2/3 — claude-style installer served from jarvis web ([b4251d4](https://github.com/ulrichando/jarvis/commit/b4251d4b1b59d827a56616c307d63747b5a5a2cb))
* **web+cli:** Phase 2/3 — claude-style installer served from jarvis web ([1c7b43d](https://github.com/ulrichando/jarvis/commit/1c7b43d95744c053ecda7c2f0881d3e8e742c4af))
* **web:** add /extension/authorize — consent-gated proxy-JWT mint for the Jarvis Chrome extension ([885bf64](https://github.com/ulrichando/jarvis/commit/885bf64fb7c0856fc6733d8e249eebd739623557))
* **web:** add /extension/authorize — consent-gated proxy-JWT mint for the Jarvis Chrome extension ([#80](https://github.com/ulrichando/jarvis/issues/80)) ([2b8ea1a](https://github.com/ulrichando/jarvis/commit/2b8ea1a10832dd6f862fad116a348bcc5653b14c))
* **web:** add self-hosted SearXNG service to deploy stack (JARVIS web_search backend; JSON API enabled) ([1cb2dba](https://github.com/ulrichando/jarvis/commit/1cb2dbabe443a871a8aaf25ea4b9388c9c155335))
* **web:** API Tokens settings section — generate/revoke user tokens for the Jarvis API ([e7e5264](https://github.com/ulrichando/jarvis/commit/e7e526441fb3065ad981e3a8dc10a2215ddfe9bb))
* **web:** API Tokens settings section — user-generated tokens for the Jarvis API ([a277bdb](https://github.com/ulrichando/jarvis/commit/a277bdbdff5b7749d3259c08a7a417c7ff93799c))
* **web:** drop broken Cookbook settings section, make Usage real ([75e12af](https://github.com/ulrichando/jarvis/commit/75e12af99864f4f15363dfd074a64178437b707d))
* **web:** evolution console redesign — status card, tab bar, single run-state control ([bf9bf69](https://github.com/ulrichando/jarvis/commit/bf9bf69e493343fd60ff8af177f592cfe5302787))
* **web:** external bot jobs commit + PR as the App bot with the injected token ([7aa4acc](https://github.com/ulrichando/jarvis/commit/7aa4acc24d4e619776a70958afde59417ce8fe5a))
* **web:** git proxy uses a per-session injected App installation token ([fa42819](https://github.com/ulrichando/jarvis/commit/fa4281928119815d3aa619773638faa71cf0f688))
* **web:** internal gh-app dispatch route — bot job → watchable /code session ([e049bfc](https://github.com/ulrichando/jarvis/commit/e049bfc80bb2300460631f5b3e80c193751ccd3f))
* **web:** let users reset/remove the 2FA authenticator in Settings → Security ([30fedde](https://github.com/ulrichando/jarvis/commit/30fedde8378fb4d4e6d9275f634d418c1433ad60))
* **web:** make Settings → Jarvis in Chrome real (persisted prefs + live status) ([8cde290](https://github.com/ulrichando/jarvis/commit/8cde2906cd6aa9cf74d2c594e0e94c959b57bace))
* **web:** mobile-responsive shell + settings; UI cleanups ([ce26ed9](https://github.com/ulrichando/jarvis/commit/ce26ed9fcebbd7c53616f1c55a3fdad05cc4a69b))
* **web:** online CLI uninstaller (curl 0wlan.com/uninstall.sh | bash), client-side only ([3de5639](https://github.com/ulrichando/jarvis/commit/3de5639081c8072b118d81d7956f67568ef6988d))
* **web:** per-user auth on CCR /v1/sessions read routes (online teleport) ([b420dd5](https://github.com/ulrichando/jarvis/commit/b420dd536c226dc938504924f7506b212062a192))
* **web:** per-user auth on CCR /v1/sessions read routes so online teleport works ([6791d41](https://github.com/ulrichando/jarvis/commit/6791d41aeebc760f973567fa08e34635b6a2fcf4))
* **web:** restore the global Knowledge store, API, and Settings tab ([bf0c84e](https://github.com/ulrichando/jarvis/commit/bf0c84ede7fe235fbe304d2ff9c6a3c0984df0e2))
* **web:** session GET exposes ccrSessionStatus — the gh-app poll done-signal ([f6dead8](https://github.com/ulrichando/jarvis/commit/f6dead823b8d6db7cd90e1e814a8855643ce2e9f))
* **web:** settings — drop broken Cookbook section, make Usage real ([d5dd938](https://github.com/ulrichando/jarvis/commit/d5dd938283538f5fc69324188175899e2d79acf9))
* **web:** settings — drop broken Cookbook section, make Usage real ([7f346ee](https://github.com/ulrichando/jarvis/commit/7f346eef0879f0ed05a39dcc6eb64f1fcd43c0f0))
* **web:** show signed-in identity + Sign out in Settings → Account ([d1a623b](https://github.com/ulrichando/jarvis/commit/d1a623b902650321874a19db0fc47ba0cec371fe))
* **web:** stamp the watchable session URL into external-job PRs + commits ([a5885b2](https://github.com/ulrichando/jarvis/commit/a5885b2dae2623cb3ba93eecc5e415539c8520e8))
* **web:** teleport reconstructs a resumable transcript from persisted events ([a174815](https://github.com/ulrichando/jarvis/commit/a174815f4930d557e938d9f3ec155e7d4b6d8dbd))
* **web:** teleport reconstructs a resumable transcript from persisted events ([ec3709c](https://github.com/ulrichando/jarvis/commit/ec3709c256b69a308a2145039c84513090d5bdc3))


### Bug Fixes

* **automod,ops:** pause suppresses proposal notifications; jarvis-health port-checks launcher services ([1c07490](https://github.com/ulrichando/jarvis/commit/1c07490e3ce3c71099e4e0b9e8f2b00d97693ceb))
* **automod:** close diff-path-extraction blocklist bypasses (rename/quoted/..) ([58b69a8](https://github.com/ulrichando/jarvis/commit/58b69a8a0c9ef5b522971e69f80e332b6f324984))
* **automod:** derive build-prompt blocklist from HARD_BLOCKLIST_PATHS ([55d1f40](https://github.com/ulrichando/jarvis/commit/55d1f40f37e76c61513a85d0d57daaab0a44d049))
* **automod:** suppress proposal notifications under pytest (tests spammed real popups) ([fd3f00c](https://github.com/ulrichando/jarvis/commit/fd3f00ce61b4176e7192c095095e82eb6e45f1d7))
* **automod:** watchdog re-verifies health after rollback before clearing marker ([1be7d48](https://github.com/ulrichando/jarvis/commit/1be7d48bc84dbdd48e740d3bd13f88ea01f6d330))
* **backup:** retry offsite ssh push 3x so a transient blip self-heals ([5546c06](https://github.com/ulrichando/jarvis/commit/5546c061f4ae734f1fb876b7a54e4d82dde311a6))
* **cli/bridge:** bound the three unbounded bridge HTTP calls ([8f694c0](https://github.com/ulrichando/jarvis/commit/8f694c070dde59b65f30cb4e26307441c06c4405))
* **cli/bridge:** bound the three unbounded bridge HTTP calls ([f2bff64](https://github.com/ulrichando/jarvis/commit/f2bff64115e16b2e1bd54b304d61e32dc194f9e1))
* **cli/bridge:** dead claude.ai keychain token no longer blocks self-hosted Remote Control ([2941953](https://github.com/ulrichando/jarvis/commit/29419538bcbce142680545419962f38bb794474e))
* **cli/bridge:** dead claude.ai token no longer blocks self-hosted Remote Control ([9615171](https://github.com/ulrichando/jarvis/commit/9615171133180fc4a4d9dbe0466489adee087722))
* **cli/bridge:** redact the session bearer from stdin/stdout debug echoes ([25e34e9](https://github.com/ulrichando/jarvis/commit/25e34e9691c6fd04d0da5f5500934bc4171cd1b6))
* **cli/bridge:** redact the session bearer from stdin/stdout debug echoes ([ec5d161](https://github.com/ulrichando/jarvis/commit/ec5d161b8eeaf29867c38f826716a51dd6f8fbb2))
* **cli/bridge:** session-timeout watchdog escalates SIGTERM → SIGKILL ([9ca6c85](https://github.com/ulrichando/jarvis/commit/9ca6c85607e0b3509ce51cbc120f079b626ccfb5))
* **cli/bridge:** session-timeout watchdog escalates SIGTERM → SIGKILL ([4b697cb](https://github.com/ulrichando/jarvis/commit/4b697cb21153297b817863376e51986b77ea83f8))
* **cli/bridge:** spawn-bridge activity trail + title derivation work in default runs ([867ca6e](https://github.com/ulrichando/jarvis/commit/867ca6e8d26a634e825f4a27b5a9d96b43354f1b))
* **cli/bridge:** spawn-bridge activity trail + title derivation work in default runs ([8e5c57c](https://github.com/ulrichando/jarvis/commit/8e5c57c7b3c7472abb4c74359013a1aad2753298))
* **cli/bridge:** stale transport can't fire terminal 'failed' during a JWT-refresh rebuild ([cd2f5dd](https://github.com/ulrichando/jarvis/commit/cd2f5dd380a50195febf3329ce1f5a8e3c163fe8))
* **cli/bridge:** stale transport can't fire terminal 'failed' during JWT-refresh rebuild ([9ddeece](https://github.com/ulrichando/jarvis/commit/9ddeece7d562fc027339a188797c6a4eadbc96e2))
* **cli/bridge:** status UI recovers after a transient poll failure ([4b70f69](https://github.com/ulrichando/jarvis/commit/4b70f6935ae6a62cee367ae0ce58e1848bb988a3))
* **cli/bridge:** status UI recovers after a transient poll failure ([8215a3b](https://github.com/ulrichando/jarvis/commit/8215a3be4f65158e68693fa04d19f3907853d68b))
* **cli/bridge:** v2 transport swap no longer drops messages for a registerWorker RTT ([9a89a11](https://github.com/ulrichando/jarvis/commit/9a89a11a9a4346d7212998edcc4bb4d8653e3be5))
* **cli/bridge:** v2 transport swap no longer drops messages for a registerWorker RTT ([e0c60b0](https://github.com/ulrichando/jarvis/commit/e0c60b025754572cd0d255ef06063004dae1232a))
* **cli/gh-agent:** atomic (tmp+rename) writes for cursor + handled-id store ([317b2e5](https://github.com/ulrichando/jarvis/commit/317b2e5a304cee71656379a09dc102a965d655a2))
* **cli/gh-agent:** atomic writes for cursor + handled-id store ([8c9d675](https://github.com/ulrichando/jarvis/commit/8c9d67501adee108e2650d6c6e91b6131f91931c))
* **cli/gh-agent:** per-sweep wall-clock budget so systemd can't SIGKILL mid-task ([836b5dd](https://github.com/ulrichando/jarvis/commit/836b5ddf8c5a29925df359fcd6ebd305fb0f4547))
* **cli/gh-agent:** taskText trigger-stripping must match listMentions filter ([2c2d7d4](https://github.com/ulrichando/jarvis/commit/2c2d7d494321d91db397fd3f6c8a27b4a28de265))
* **cli/gh-agent:** taskText trigger-stripping must match listMentions filter ([52f7081](https://github.com/ulrichando/jarvis/commit/52f708131b55659a287a3b6edd7f367252d4aaa9))
* **cli/proxy:** anthropic passthrough logs one row per stream (was two, masking errors) ([f354ed8](https://github.com/ulrichando/jarvis/commit/f354ed84647c3a8843e6f2e681690113da3a7c44))
* **cli/proxy:** anthropic passthrough logs one row per stream (was two, masking errors) ([8baf46b](https://github.com/ulrichando/jarvis/commit/8baf46b11ea0a238114cdfae17797bf903c28811))
* **cli/proxy:** warn on unknown-model silent default fallback ([9c3469a](https://github.com/ulrichando/jarvis/commit/9c3469a6d545ce1ea2fbf00920eaf84419cb3683))
* **cli/proxy:** warn when a named model isn't in the registry (silent default fallback) ([9294c2e](https://github.com/ulrichando/jarvis/commit/9294c2e601c90d7af536f1c5aead3dc87db80aaf))
* **cli:** /remote-control session lifecycle works self-hosted (token + web URL) ([315761d](https://github.com/ulrichando/jarvis/commit/315761d778d9e60819f265e9bbe5b5197fd851d5))
* **cli:** /remote-control session lifecycle works self-hosted (token + web URL) ([bfaed93](https://github.com/ulrichando/jarvis/commit/bfaed933d107d2fd7dc3714a13594f260c89a556))
* **cli:** /teleport shows ALL your sessions (stop the "no sessions" lie) ([4dd8e0d](https://github.com/ulrichando/jarvis/commit/4dd8e0d71b0ef9376b2a486ec91d8192dd0747cf))
* **cli:** /teleport surfaces the cwd change when a resume fails mid-switch ([629b11c](https://github.com/ulrichando/jarvis/commit/629b11cc3cc2ab47f5d9f66c81acaf89f44430f1))
* **cli:** /teleport surfaces the cwd change when a resume fails mid-switch ([7e3c6c6](https://github.com/ulrichando/jarvis/commit/7e3c6c6bb25adb7e587229103a49254170e3f069))
* **cli/teleport:** re-teleport no longer silently resumes on a stale branch ([d3e443a](https://github.com/ulrichando/jarvis/commit/d3e443a5486319e36b124dca5c691152e2f88ce3))
* **cli/teleport:** re-teleport no longer silently resumes on a stale branch ([8f53035](https://github.com/ulrichando/jarvis/commit/8f53035eda455ee0a9ef9fe07efd6bbe52cdb3b9))
* **cli:** /version never surfaced — move out of INTERNAL_ONLY_COMMANDS ([ccebd2b](https://github.com/ulrichando/jarvis/commit/ccebd2bd32bc917a2ac4a10da4710ab75410bd51))
* **cli:** /version never surfaced — move out of INTERNAL_ONLY_COMMANDS ([f1f791b](https://github.com/ulrichando/jarvis/commit/f1f791b7444ab829aff0fdd1a5b3c1fa09ee3c37))
* **cli:** `jarvis logs/attach/kill` no longer eat the launcher's injected flags ([7e838c3](https://github.com/ulrichando/jarvis/commit/7e838c3c2729ad78094c35535e0451d456f7aea9))
* **cli:** add gh-agent to start.sh commander-subcommand skip-list ([a69a0b0](https://github.com/ulrichando/jarvis/commit/a69a0b0f43980893b399d647bca492787d3bee83))
* **cli:** add runSkillGenerator.ts stub to prevent crash when RUN_SKILL_GENERATOR enabled ([42306c4](https://github.com/ulrichando/jarvis/commit/42306c4973b79199f4c070f95b124f3c3e4c9863))
* **cli:** add runSkillGenerator.ts stub to prevent crash when RUN_SKILL_GENERATOR enabled ([5b40ecd](https://github.com/ulrichando/jarvis/commit/5b40ecd332d8c9c73dcf3e4937e8e0e282112ebb))
* **cli:** auth login detects edge-gate redirect instead of crashing silently ([2a55b12](https://github.com/ulrichando/jarvis/commit/2a55b1224146698ed72b54f9fc0e4825bd004e0e))
* **cli:** auth login sends first-party headers so deployed server accepts it ([7e5b860](https://github.com/ulrichando/jarvis/commit/7e5b860ba49f8459853e081ae9f965eb7e9a3350))
* **cli:** binary remote-control env hydration + gh-agent sweep wall-clock budget ([afce58d](https://github.com/ulrichando/jarvis/commit/afce58d13d5e2e3c83aed66a88a73873dcc451bf))
* **cli:** bust repo caches after /teleport auto-switch so resume validates ([b62b942](https://github.com/ulrichando/jarvis/commit/b62b942465c186c616a4a8fd82f269bac558b915))
* **cli:** claude-api skill crashed on invoke — graceful live-docs fallback ([ef22cf4](https://github.com/ulrichando/jarvis/commit/ef22cf442df70ed67bc0a49bbe6f1f55d5b9c168))
* **cli:** define isSnipMarkerMessage — was an undefined require crashing message render ([a5c3ce4](https://github.com/ulrichando/jarvis/commit/a5c3ce4fac75462976397924635e76f144af915f))
* **cli:** define restoreFromEntries — resume (/resume + /teleport) threw on it ([b1c47b7](https://github.com/ulrichando/jarvis/commit/b1c47b70cde07e8e73c6726784bff6ddc366abac))
* **cli:** drop phantom HISTORY_SNIP flag — was exposing a dead Snip tool ([8dca0e6](https://github.com/ulrichando/jarvis/commit/8dca0e68ae0df0e58024f92b2f68550865dc69c0))
* **cli:** drop phantom HISTORY_SNIP flag — was exposing a dead Snip tool ([8d12b3a](https://github.com/ulrichando/jarvis/commit/8d12b3aef7d32f7ec2821cc22284677b3e10031a))
* **cli:** drop phantom WORKFLOW_SCRIPTS — WorkflowTool has no engine ([0c1740c](https://github.com/ulrichando/jarvis/commit/0c1740ca66af2639a43617000a3eed1d9fc024e4))
* **cli:** drop phantom WORKFLOW_SCRIPTS — WorkflowTool has no engine ([34ab42a](https://github.com/ulrichando/jarvis/commit/34ab42a0c9a6b3800a86ecc02f946b453374d26a))
* **cli:** effort indicator never showed bottom-right (Infinity timeout clamped to 1ms) ([726f125](https://github.com/ulrichando/jarvis/commit/726f12594b518f5fa4dd585e4e6ad62397d7a7b4))
* **cli:** effort indicator transient, not persistent — my prior fix starved notifications ([ed29599](https://github.com/ulrichando/jarvis/commit/ed2959953f2b592e794553f0bc6a55fe773d762c))
* **cli:** gh-action .claude neutralization survives cross-device (EXDEV) ([1c57f93](https://github.com/ulrichando/jarvis/commit/1c57f93959a0be4dbf2123c764c7ceb0379b39ab))
* **cli:** gh-action .claude neutralization survives cross-device (EXDEV) ([5dc5671](https://github.com/ulrichando/jarvis/commit/5dc5671ab2481a18deac8f6f0d070278f9ed734b))
* **cli:** gh-agent command preserves module exit code (failed poll → nonzero) ([eba3c95](https://github.com/ulrichando/jarvis/commit/eba3c952e545bbf3ebce799109ee796e0c4fe513))
* **cli:** gh-agent no-replay by comment-id dedupe + monotonic cursor ([c48fb81](https://github.com/ulrichando/jarvis/commit/c48fb81f96b1fa63da1c225e2072ba473e03007e))
* **cli:** Grep/Glob fall back to system rg when the vendored binary is absent ([5c42d8d](https://github.com/ulrichando/jarvis/commit/5c42d8d638b4627502ba1297996120e08c87dd0c))
* **cli:** harden gh-agent — slurp pagination, dry-run no-writes, fail-safe acks, self-marker, window advance ([799d7b0](https://github.com/ulrichando/jarvis/commit/799d7b033e95118f38e0ac22a4a96f2dea5970ee))
* **cli:** harden teleport branch handling against argv flag smuggling ([b956a52](https://github.com/ulrichando/jarvis/commit/b956a52cdcfea65f77f678d882eb76bab64fefa8))
* **cli:** hydrate Remote Control + teleport env in the compiled binary ([e4f66f0](https://github.com/ulrichando/jarvis/commit/e4f66f0d20742cabca8a1df50323c94bec6812b1))
* **cli:** jarvis auth login prefers your real server, falls back to local only if it's not responding ([a7abe6e](https://github.com/ulrichando/jarvis/commit/a7abe6ec4b285721a120bc67792dc82c355ee730))
* **cli:** jarvis logs/attach/kill no longer consume the launcher's injected flags ([e238320](https://github.com/ulrichando/jarvis/commit/e238320030517f93c6a9598b9f58d6f6a9fd6192))
* **cli:** jarvis uninstall --purge refuses to wipe shared provider keys (voice/web) ([52898e5](https://github.com/ulrichando/jarvis/commit/52898e5df3749592c67b9486b15d155c1cbce008))
* **cli:** ModelPicker effort cycle offers only rungs the focused model accepts ([ecc6879](https://github.com/ulrichando/jarvis/commit/ecc6879c59098baa8ae35dc01cb406b01eb36d01))
* **cli:** ModelPicker effort cycle offers only rungs the focused model accepts ([c934cb8](https://github.com/ulrichando/jarvis/commit/c934cb813f3745c7d4a64916f9cbef748037b316))
* **cli:** proxy surfaces real upstream errors + survives broken fallbacks ([bfd2194](https://github.com/ulrichando/jarvis/commit/bfd2194d7e91a3592ac7c170b048db742bdb4399))
* **cli:** proxy surfaces real upstream errors + survives broken fallbacks ([b7d0996](https://github.com/ulrichando/jarvis/commit/b7d0996fc5eafe42d48c1d484e3b9c1b30900565))
* **cli:** proxy tool_choice/stream-usage/fallback-leak + deepseek-reasoner thinking ([515c4d3](https://github.com/ulrichando/jarvis/commit/515c4d3a06bf8f5fff7b0598d5f50004c017f74c))
* **cli:** proxy tool_choice/stream-usage/fallback-leak + deepseek-reasoner thinking ([18770f5](https://github.com/ulrichando/jarvis/commit/18770f530bcf589651fdf086219291c2b3d01e71))
* **cli:** proxy translates OpenAI GPT-5 effort; DeepSeek is max-capable ([014defd](https://github.com/ulrichando/jarvis/commit/014defd3f0be5afbd8d37a2e234993f103626fa0))
* **cli:** proxy web_search uses SearXNG (SEARXNG_URL) + CAPTCHA is a failure ([b107872](https://github.com/ulrichando/jarvis/commit/b1078723a596480f7258d45b447ca3ecc81d3ab8))
* **cli:** proxy web_search uses SearXNG + treats CAPTCHA as a failure ([73c87e5](https://github.com/ulrichando/jarvis/commit/73c87e56a8e212ba88ee1597172c1ab1365085a3))
* **cli:** reasoning-cache atomic write + per-entry size cap ([636b9ed](https://github.com/ulrichando/jarvis/commit/636b9ed525d2f91d8f702f1c2fd6f6b0b51ee036))
* **cli:** reasoning-cache atomic write + per-entry size cap ([6d5b697](https://github.com/ulrichando/jarvis/commit/6d5b697b1ee37d7dc17800bc6b12a1485ce05df5))
* **cli:** register /teleport in the MAIN command list (was stripped for external builds) ([25e8f40](https://github.com/ulrichando/jarvis/commit/25e8f408f9d9dfc7b38db6133d53c387f563a9ec))
* **cli:** remote-control placeholder title is the bare slug (drop 'remote-control-' prefix) ([87fcba1](https://github.com/ulrichando/jarvis/commit/87fcba1e784da14c44c13259eadc12d92e4f66ac))
* **cli:** restore autonomous interactive mode (bypass) with full Shift+Tab carousel ([7f057ae](https://github.com/ulrichando/jarvis/commit/7f057aec41673dd632d39f4ad7ba6adec190417c))
* **cli:** restore the interactive REPL — binary for TUI, IS_DEMO removed ([48277ee](https://github.com/ulrichando/jarvis/commit/48277ee4d48953396491aaae2be7ac9b6158e31e))
* **cli:** root-cause the interactive deadlock — break the bootstrap/state import cycle ([eb65fd2](https://github.com/ulrichando/jarvis/commit/eb65fd29d17aea01d8cb99a567bdf96e5c0e362f))
* **cli:** subcommand fast-paths never fired — args ordering + bg session env/name ([30c2e9d](https://github.com/ulrichando/jarvis/commit/30c2e9d93e981ddae2c3eabe5a5fee135d9acdb7))
* **cli:** subcommand fast-paths never fired — args ordering + bg session env/name ([da9a600](https://github.com/ulrichando/jarvis/commit/da9a6002eb7c0b52a147acd949f94f909d34ea67))
* **cli:** sync src/cli to master — restore /ultraplan + /swarm (Phase B) ([a6db7f4](https://github.com/ulrichando/jarvis/commit/a6db7f41284483426a774dd682b6aa40943d6ec4))
* **cli:** teleport log-fetch uses /v1/sessions/{id}/events in JARVIS mode ([d6be0d2](https://github.com/ulrichando/jarvis/commit/d6be0d2b5db7ba239b8a4c9f2e24ebef2c070325))
* **cli:** teleport works cross-repo + Claude-style picker layout + checkout fix ([dd56d33](https://github.com/ulrichando/jarvis/commit/dd56d3342589b9f3e8f701bb38f21a04ee250da2))
* **cli:** teleport/cloud hit the correct online URL (strip doubled /api/bridge) ([4d39381](https://github.com/ulrichando/jarvis/commit/4d39381f9ebe347163096c0fe1ffdd3c5977d6fd))
* **cli:** treat 204/409 as archive success (jarvis server returns 204 No Content) ([4696661](https://github.com/ulrichando/jarvis/commit/4696661ceeb6f2373826363c571f4ca2eec44211))
* **cli:** true root cause of the blank interactive REPL — gated module-level require()s ([2131d8a](https://github.com/ulrichando/jarvis/commit/2131d8a6c4c9182c9a55b727961df54f2435f27d))
* **cli:** two strays imported npm 'ink' instead of the vendored ink ([5a9dd90](https://github.com/ulrichando/jarvis/commit/5a9dd902d55f868ad238fc4ed5b82684b0e99a5e))
* **cli:** ultrathink raises real effort param; --effort accepts xhigh + signposts ultracode; picker footer/notes clamp per-model ([bba1f80](https://github.com/ulrichando/jarvis/commit/bba1f80c2ca13c172595236f4eafea733e1e0f77))
* **cli:** ultrathink raises real effort; --effort xhigh; DeepSeek max; OpenAI proxy effort ([21d4af8](https://github.com/ulrichando/jarvis/commit/21d4af8c5bc42f51e384009888ebd3961575be31))
* **cli:** unblock headless/tool path — taskSummary.ts, drop REVIEW_ARTIFACT, silence proxy banner ([a66ed23](https://github.com/ulrichando/jarvis/commit/a66ed231fc8e1249c9936d5780a8a375c44dcb80))
* **cli:** unblock headless/tool path — taskSummary.ts, drop REVIEW_ARTIFACT, silence proxy banner ([2342d79](https://github.com/ulrichando/jarvis/commit/2342d7955673c1a592f458d8df874912d4993d37))
* **cli:** validate teleport repo name before gh clone (argv injection guard) ([dfb8389](https://github.com/ulrichando/jarvis/commit/dfb8389057ac544afaba929c83ce238a8acc480d))
* **cli:** warn (not silently) when workflow worktree isolation falls back ([e32aaaa](https://github.com/ulrichando/jarvis/commit/e32aaaa19ce85959cd186d8e09d465cbc91395a1))
* **cli:** warn when workflow worktree isolation silently falls back ([c376469](https://github.com/ulrichando/jarvis/commit/c37646980b0ce8e75fdb6be83de0caeaf1c7c246))
* **cli:** wire the 5 workflow parity stubs (nested workflow, custom agentType, worktree isolation, real budget.spent, resume journal read-back) ([2888b48](https://github.com/ulrichando/jarvis/commit/2888b48264704277b42ae0f4f2dbaef0660d371b))
* **cli:** workflow dispatch canUseTool must echo tool input — empty updatedInput clobbered Bash's command (undefined.includes crash) in workflow agents ([40da326](https://github.com/ulrichando/jarvis/commit/40da326a71ad244096de0ad04808ce00b17bd1ef))
* **cli:** workflow journal is call-index addressed — parallel resume correct ([ba272eb](https://github.com/ulrichando/jarvis/commit/ba272eb354703b558c2f24a3bb5c38e8f6ff08da))
* **cli:** workflow journal is call-index addressed — parallel resume correct ([2bb8dfe](https://github.com/ulrichando/jarvis/commit/2bb8dfe222fc23f0450a266b46678170d9ced4d7))
* **computer-use:** drop xcalc — no install candidate on trixie slim (broke deploy) ([469a9f2](https://github.com/ulrichando/jarvis/commit/469a9f2345aa059b55063123a8843aff98c32671))
* **computer-use:** drop xcalc (broke the desktop-apps deploy) ([732b442](https://github.com/ulrichando/jarvis/commit/732b442e58a0a61704636aa148d4d733a1ef1ce5))
* **computer-use:** Gemini adapter — strip additionalProperties from the tool schema ([443eafd](https://github.com/ulrichando/jarvis/commit/443eafd1152b58f9d60e9dd64cfdd7e959e0af50))
* **computer-use:** mkdir the wallpaper dir before convert writes to it ([8f67b93](https://github.com/ulrichando/jarvis/commit/8f67b938ae16bbf1417ec853eed2335678a71f8d))
* **computer-use:** mkdir wallpaper dir before convert (deploy build fix) ([57f0f8b](https://github.com/ulrichando/jarvis/commit/57f0f8b39520713513017507918e1fbdd3056a15))
* **computer-use:** never persist a session ending on an unanswered tool call ([5db3666](https://github.com/ulrichando/jarvis/commit/5db36667c4705b0e42b69079d11ae32ec142a7c4))
* **computer-use:** paint a real desktop wallpaper (black void → finished desktop) ([4849e82](https://github.com/ulrichando/jarvis/commit/4849e82389cff191f1e8034fec2d9ff6eb16ec67))
* **computer-use:** paint a real desktop wallpaper (was a black void) ([ebf0428](https://github.com/ulrichando/jarvis/commit/ebf042851e20e443e6dae8a1028e55d5f1a918e9))
* **computer-use:** sidecar bind host env-configurable — container binds 0.0.0.0 ([428bef9](https://github.com/ulrichando/jarvis/commit/428bef90b797eb45a49edfcfdda0b7d107e7f764))
* **computer-use:** sidecar bind host env-configurable for the cloud container ([7294682](https://github.com/ulrichando/jarvis/commit/72946822c580bfd859b7af53a0262c53089e68cc))
* **deploy:** gh-app source changes trigger the compose rebuild ([7c2567f](https://github.com/ulrichando/jarvis/commit/7c2567f4ba4f45e1c80cd1951685670f6392039b))
* **deploy:** rebuild computer-use on voice-agent-only changes ([9ababa2](https://github.com/ulrichando/jarvis/commit/9ababa21110c01ad784ec9ae44dd49a01a7161e8))
* **deploy:** rebuild the computer-use service on voice-agent-only changes ([84bc894](https://github.com/ulrichando/jarvis/commit/84bc8944bf658c246dffefa54c4dc7095b546507))
* **desktop:** don't fight jarvis-proxy.service for :4000 ([f79b76b](https://github.com/ulrichando/jarvis/commit/f79b76b03cf9b6afdc9df71676a5bf5e5122f551))
* **desktop:** let jarvis-proxy.service reclaim :4000 after the stale-proxy pkill ([b9f18c3](https://github.com/ulrichando/jarvis/commit/b9f18c36e7056b0abeb2ecffba1e9d65170199a1))
* **desktop:** restyle the API Keys window ([fdebe89](https://github.com/ulrichando/jarvis/commit/fdebe89ea4f95810ad8c9de1e113533cc9a36c87))
* **desktop:** tray CLI opens in ~/Jarvis, not the repo ([e842dd0](https://github.com/ulrichando/jarvis/commit/e842dd0ae5483d82033ebe64ccf3f14a87a69c8b))
* **ext:** clearer message when workflow-record can't attach (chrome:// vs stale tab) ([f5726df](https://github.com/ulrichando/jarvis/commit/f5726dffe6b7d06f9573b74ca0c39135ed92d3cd))
* **ext:** security — 'Always allow site' must not bypass destructive confirms ([818beef](https://github.com/ulrichando/jarvis/commit/818beefdd4c8e81f85f2a1918bb1bc3f9a0581f9))
* **ext:** show the attached image in the chat (was text-only, image vanished on send) ([20fef24](https://github.com/ulrichando/jarvis/commit/20fef249afc129c9d7c63ee044c2c7c914f1f5c4))
* **ext:** workflow recording works on any page (browser-level nav capture) ([aa3850b](https://github.com/ulrichando/jarvis/commit/aa3850bc17211bee024d352a543d0efb18c6f17d))
* **gh-action:** restore target .claude before git add (rename must not land in the PR) ([4b4e160](https://github.com/ulrichando/jarvis/commit/4b4e160b9ce2072c958296f1a448004734d2ae6c))
* **gh-action:** restore target .claude before git add (rename must not land in the PR) ([9861fd1](https://github.com/ulrichando/jarvis/commit/9861fd1146bc10d987aba684b9d0512581667724))
* **gh-app:** C1 — done-signal can no longer fire on the pre-task init-idle ([0e97d82](https://github.com/ulrichando/jarvis/commit/0e97d8214696cf95d42a64fad42dbb844f72b6c3))
* **gh-app:** cap /webhook body size at GitHub's 25MB payload limit ([1225a6e](https://github.com/ulrichando/jarvis/commit/1225a6e6ab335612ab27d831468feacbf9f212d3))
* **gh-app:** final bot slug is talos-hq — align install links + bot-login defaults ([122ac92](https://github.com/ulrichando/jarvis/commit/122ac920646814267ff8119c1921e0a81882fcc2))
* **gh-app:** final bot slug is talos-hq — align install links + defaults ([3ca4af2](https://github.com/ulrichando/jarvis/commit/3ca4af24f985438dd362b05047e8cdb05e7401b9))
* **gh-app:** I1 — abort-bound every code-session fetch (10s dispatch/poll, 90s PR) ([f2c5cec](https://github.com/ulrichando/jarvis/commit/f2c5cec6ab70f164b3ed81720599d8e0fb6271f9))
* **gh-app:** I2 — isPR jobs stay on the sandbox even with code sessions on ([9bee107](https://github.com/ulrichando/jarvis/commit/9bee1078610eb64079196a1668bc78d78856dfff))
* **gh-app:** installation tokens refresh on use — bot sessions no longer die at the ~1h mark ([fc91b2c](https://github.com/ulrichando/jarvis/commit/fc91b2c6a747c35f2de8faf02c435fc32b3b2e4d))
* **gh-app:** M1+M3 — archived is terminal; abandoned sessions get archived ([082cee8](https://github.com/ulrichando/jarvis/commit/082cee8c963bc4f5d6ba17d48803e7c65c711e39))
* **gh-app:** one-shot credential capture — /setup/callback refuses when creds exist ([02eddc6](https://github.com/ulrichando/jarvis/commit/02eddc65f42ab7979f00f42ed06d2a064f9fa83e))
* **gh-app:** refresh installation tokens on use — bot /code sessions no longer die at the ~1h mark ([41e8626](https://github.com/ulrichando/jarvis/commit/41e8626a46ac90b4c62f6696793f4d00be74d9cc))
* **gh-app:** sandbox env (IS_SANDBOX + skip login) so jarvis -p runs ([b73b37c](https://github.com/ulrichando/jarvis/commit/b73b37c6fe046bbe388dbab7ee2d19433a789bb5))
* **gh-app:** sandbox needs IS_SANDBOX=1 + JARVIS_REQUIRE_LOGIN=0 ([f251f74](https://github.com/ulrichando/jarvis/commit/f251f74b8fe9d17b7109cbe462c4aeb6c0c9c78e))
* **gh-app:** sandbox network is required + fail-closed — never spawn on the open bridge ([1366f76](https://github.com/ulrichando/jarvis/commit/1366f768d81a11a119571ec511c76d3729af893a))
* **gh-app:** scope installation tokens per-repo with least-privilege permissions ([40b8ef7](https://github.com/ulrichando/jarvis/commit/40b8ef7d1271940d4e3dfa18587587936cb57175))
* **hub:** exclude .env* from image context + null-guard stream passthrough ([4ce7a7a](https://github.com/ulrichando/jarvis/commit/4ce7a7adcc07315037824ea1bc96d941f06ea472))
* in-REPL /remote-control works self-hosted (session lifecycle, wave 2) ([5c7a768](https://github.com/ulrichando/jarvis/commit/5c7a768fe59a1cea2f1517fdcf4865333d46e5c3))
* **ops:** service-review hardening pass ([c58f172](https://github.com/ulrichando/jarvis/commit/c58f17224a8d28b2c5ccfd5976857e9c58144f50))
* **proxy:** multi-provider tool-use reliability (Kimi/DeepSeek) ([e4614e8](https://github.com/ulrichando/jarvis/commit/e4614e81d5a9d75fdc94773ec6efa8932f014cee))
* **searxng:** pin general engines that answer from the VPS IP + auto-restart on CD ([26b2b8c](https://github.com/ulrichando/jarvis/commit/26b2b8cd422abba2092f1fda859e16a894deeba9))
* **searxng:** pin working engines (VPS default search returned 0 results) ([3265730](https://github.com/ulrichando/jarvis/commit/32657308a7c53b4d798476a9597c53f9a7a45688))
* **security:** CCR /api/v1/sessions fails closed — GET had no auth at all ([e7225f5](https://github.com/ulrichando/jarvis/commit/e7225f580efac064ad211de9160b57394b35e334))
* **security:** CCR sessions routes fail closed (unauthenticated list was live) ([4ef29c5](https://github.com/ulrichando/jarvis/commit/4ef29c5a26fec084f260c1d416bfca3bcf71fb66))
* **security:** explicit owner match — ownerless envs unreachable via user tokens (IDOR) ([568c57d](https://github.com/ulrichando/jarvis/commit/568c57d7e9fe146a8c24e3fe1bb3fd3dfcf32926))
* **security:** explicit owner match — ownerless envs unreachable via user tokens (IDOR) ([96f22f4](https://github.com/ulrichando/jarvis/commit/96f22f4afc3784c7f14c3956be2ca609f7c36cf7))
* **security:** media_type is re-read from a constant allowlist (kills the CodeQL flow) ([3a94c29](https://github.com/ulrichando/jarvis/commit/3a94c29b070c0f0fc12f6ed3488b5b33bd483481))
* **security:** pin attached-image components at the source (extension panel) ([1607460](https://github.com/ulrichando/jarvis/commit/16074605d965569cf0da3e2731c7ab30479f1b1b))
* **security:** resolve the two CodeQL highs gating PR [#114](https://github.com/ulrichando/jarvis/issues/114) ([bb4c358](https://github.com/ulrichando/jarvis/commit/bb4c3585dbe61c21cd26acc71a1dd16f474102a1))
* **test:** _wait_for_file waits for content, not mere existence (flaky test_hooks) ([89c1fe2](https://github.com/ulrichando/jarvis/commit/89c1fe2115aac3e36be4eeba43e3a9444816a8c1))
* **voice-client:** stop SIGABRT on service stop (PortAudio write/close race) ([4dd0a78](https://github.com/ulrichando/jarvis/commit/4dd0a7849b37ea8c8212b1debe6e8a0d6d9f36d8))
* **voice/computer-use:** sidecar history-trim orphan tool_result 400 + Anthropic step caching/effort/image cap ([82a91ae](https://github.com/ulrichando/jarvis/commit/82a91aef15c7bc899ef94711deef98da6206616e))
* **voice:** addressed-window init/stale stamp is -inf, not 0.0 (monotonic is uptime) ([131f026](https://github.com/ulrichando/jarvis/commit/131f0262614144b29e8eb0eccdd4b1491444796e))
* **voice:** auth-gate the voice-client control API against browser drive-by ([e40aa7c](https://github.com/ulrichando/jarvis/commit/e40aa7cecacdae9ba1190cfcfdaf2794ca323564))
* **voice:** echo-bargein consults the discard gates — hallucinations stop clipping live TTS ([7cc06b4](https://github.com/ulrichando/jarvis/commit/7cc06b49c0bee71bb6afd7310e430173dcf18d79))
* **voice:** echo-bargein consults the discard gates — hallucinations stop clipping live TTS ([57c7494](https://github.com/ulrichando/jarvis/commit/57c74946f8c14c7e186f88eb326cd11452d72d2a))
* **voice:** pin DeepSeek voice models to explicit deepseek-v4-flash + forced non-thinking (extra_body thinking:disabled) — the bare deepseek-chat alias is discontinued 2026-07-24 and V4 defaults to thinking (slow TTFT + tool_choice=required 400s) ([45f43ad](https://github.com/ulrichando/jarvis/commit/45f43ada509dafeedccf73d1dd6ff18d21d210f2))
* **voice:** pin fallback rung + dispatcher DeepSeek non-thinking (outage audit) ([3dfe607](https://github.com/ulrichando/jarvis/commit/3dfe607901b91f0f544bc22d5033382b13d4404b))
* **voice:** review remediation + complete repo-wide Groq purge ([40761a1](https://github.com/ulrichando/jarvis/commit/40761a14fef7dae1c750ae3753ee990c4554de1f))
* **voice:** silence ambient backchannel fillers — DISCRETION enforced in code ([4f313b7](https://github.com/ulrichando/jarvis/commit/4f313b7b9b8d069104cbf25e0d430b77fa54d23a))
* **voice:** silent-after-task — gate-veto + resurrection in turn_rescue; tool_call_count telemetry ([ef12ab3](https://github.com/ulrichando/jarvis/commit/ef12ab39a311af443063284977775224e9e83253))
* **voice:** silent-after-task — gate-veto + resurrection in turn_rescue; tool_call_count telemetry ([3e4a0a2](https://github.com/ulrichando/jarvis/commit/3e4a0a230de6aced66d7a4d8869d9ec028ac6bcb))
* **voice:** silent-after-task — search fallthrough, turn-rescue inversion, TTS clipping ([89bd301](https://github.com/ulrichando/jarvis/commit/89bd301fe8ec5b7f1a8a3c5d5d8d3066f0ccbf0c))
* **voice:** spoken mute engages deterministically, not via LLM ack lottery ([c475aeb](https://github.com/ulrichando/jarvis/commit/c475aeb2f5a22b5b3175a38928eb68d77ed89e88))
* **voice:** spoken mute engages deterministically, not via LLM ack lottery ([5dfe2a3](https://github.com/ulrichando/jarvis/commit/5dfe2a36cbd95747adf59c3913d4e3872994e6d5))
* **voice:** web_search block-fallback points to browser_task, not the removed transfer_to_browser ([f0b945b](https://github.com/ulrichando/jarvis/commit/f0b945b80a037efb015680fc5ad09409e157716f))
* **voice:** web_search walks all search backends; empty answer = honest no-results, not DDG fallthrough ([022fdbd](https://github.com/ulrichando/jarvis/commit/022fdbd874a0c18c1548320658e4ed2e7d38c9c7))
* **voice:** web_search walks all search backends; empty answer = honest no-results, not DDG fallthrough ([f6baa93](https://github.com/ulrichando/jarvis/commit/f6baa935d90d8a117d5d1e0c9fb940b817776a13))
* **web-tests:** scrub cross-file env pollution in vitest workers ([fd11e1d](https://github.com/ulrichando/jarvis/commit/fd11e1d3f83c104240c6164cebf99f070f7c0613))
* **web:** /code container session routes (git + worker) self-auth past the local-token gate ([c2ffe44](https://github.com/ulrichando/jarvis/commit/c2ffe440e6dfa7000eb03039cc16d70e9f1b45ee))
* **web:** /code container session routes self-auth past the local-token gate (gap [#3](https://github.com/ulrichando/jarvis/issues/3)) ([e9205ec](https://github.com/ulrichando/jarvis/commit/e9205ec2ce81292285dab6ae12cff5bdaf05cfe6))
* **web:** /code containers reach the git-proxy + model over an internal bridge ([aa5d144](https://github.com/ulrichando/jarvis/commit/aa5d144416435b3fb2bd50c174a434851da70598))
* **web:** /code containers reach the git-proxy + model over an internal bridge (gap [#2](https://github.com/ulrichando/jarvis/issues/2)) ([f5f2b20](https://github.com/ulrichando/jarvis/commit/f5f2b203161ab5015dd7b5ced79f9176c471dff1))
* **web/code:** hide per-repo sandboxes from picker + sidebar shows last activity ([01a2d86](https://github.com/ulrichando/jarvis/commit/01a2d861930c57ec677c03cc4d9fc6ffa5767819))
* **web/code:** hide per-repo sandboxes from picker + sidebar shows last activity ([0f02b74](https://github.com/ulrichando/jarvis/commit/0f02b74bef5d8bd099036e85f62a6511dfd49173))
* **web/code:** static git credential helper — a single proxy 401 no longer wedges every push ([43ce82d](https://github.com/ulrichando/jarvis/commit/43ce82d00d53bd00c50b84efa2f503fec9065b0d))
* **web/code:** static git credential helper — proxy 401 no longer wedges session pushes ([2081001](https://github.com/ulrichando/jarvis/commit/208100193a3a3f074bc66c0b1b7d1e87943f6d20))
* **web:** bot /code sessions owned by the box's real user so they're watchable in the UI ([dab240d](https://github.com/ulrichando/jarvis/commit/dab240d052e5f488b49e9b4c3e8e24b47df898ea))
* **web:** bot /code sessions watchable in the user's UI + volume-ownership hardening ([4a676cd](https://github.com/ulrichando/jarvis/commit/4a676cd939a3ed622202318e763e0c7d5e4603a7))
* **web:** branded 404 + /api index, GitHub-bot settings card, Chrome extension download ([6a27255](https://github.com/ulrichando/jarvis/commit/6a27255935fc47c6765f712f8919a887359a9ee0))
* **web:** branded 404 + /api index, GitHub-bot settings card, Chrome extension download ([32d9934](https://github.com/ulrichando/jarvis/commit/32d9934da003c48f181eb8bfdf25736ffb9d7996))
* **web:** CCR /v1/sessions returns session_context (repo+branch) — fixes teleport "Error loading sessions" ([#108](https://github.com/ulrichando/jarvis/issues/108)) ([1223f94](https://github.com/ulrichando/jarvis/commit/1223f94c6c1fed7d85fdffff30c6e00fd2ddf08f))
* **web:** commit the isSharedLocalToken definition (was stranded uncommitted) ([6bfbf6a](https://github.com/ulrichando/jarvis/commit/6bfbf6a7d43551244ff04b01b6dec976c2d40089))
* **web:** commit the live tunnel-shaped Caddyfile + searx.0wlan.com route ([d825838](https://github.com/ulrichando/jarvis/commit/d825838f6f4bde6cabefae5723bd9f1775ad5482))
* **web:** createContainerPR surfaces push failures; record the v1 token-lifetime decision ([48f2f37](https://github.com/ulrichando/jarvis/commit/48f2f3756d27121c79ce2300474289fb22c71580))
* **web:** CSRF hard-stop on cross-site /api writes + handler auth on workspace exec/file ([d29ccb7](https://github.com/ulrichando/jarvis/commit/d29ccb7f63e56bc5518c863c36fe606b28156467))
* **web:** CSRF hard-stop on cross-site /api writes + handler auth on workspace exec/file ([0f4dbe5](https://github.com/ulrichando/jarvis/commit/0f4dbe5d59963badac404b302c162de21ab29d76))
* **web:** dedupe readCliTranscript block duplicated by the master merge ([a8987aa](https://github.com/ulrichando/jarvis/commit/a8987aac6c1f90cedfd7e37686e6dc73e601a28e))
* **web:** dedupe the web service networks key (compose parse failure on deploy) ([2594dcc](https://github.com/ulrichando/jarvis/commit/2594dcc9e4f45996708521098a1ea7052ab98658))
* **web:** forward content-encoding in git proxy so /code can clone large repos ([b7cadf3](https://github.com/ulrichando/jarvis/commit/b7cadf3c424a5a90d5258c585870aadf51cf1c1d))
* **web:** forward content-encoding so /code can clone large repos ([0284b53](https://github.com/ulrichando/jarvis/commit/0284b53bf04413d57d290fa376f5d3d7ca997614))
* **web:** gh-app bot jobs get a dedicated locked-down env — never a user /code env ([140e3de](https://github.com/ulrichando/jarvis/commit/140e3de008828786c1a9d8b48c984a55fbeeea37))
* **web:** gh-app dispatch requires botLogin — external commits always attributed to the bot ([7893f7f](https://github.com/ulrichando/jarvis/commit/7893f7f24b8cd2ccd60cb10636682726f00b12ac))
* **web:** gh-app dispatch service token moves to a dedicated X-GH-App-Token header ([9b983a1](https://github.com/ulrichando/jarvis/commit/9b983a1d288a44ec80035b9c43b19f385837bbab))
* **web:** harden input validation — dot-only repo segments + degenerate publicOrigin ([ef35dea](https://github.com/ulrichando/jarvis/commit/ef35dea94e8834bcdcd339c9bd55eb8d3e666f28))
* **web:** headless /code workbench CLI skips the interactive login gate ([4e67416](https://github.com/ulrichando/jarvis/commit/4e674163e0ee29eb709d4142e73025f32fa2cb13))
* **web:** headless /code workbench CLI skips the interactive login gate (gap [#6](https://github.com/ulrichando/jarvis/issues/6)) ([13b73e0](https://github.com/ulrichando/jarvis/commit/13b73e00f620705969b29856c78304c3137f32ff))
* **web:** hide the gh-app bot env from the /code picker (claude.ai parity) ([7923d11](https://github.com/ulrichando/jarvis/commit/7923d112a503a4d12feca2decd61307b4ac655d0))
* **web:** hide the gh-app bot env from the /code picker (claude.ai parity) ([6fec919](https://github.com/ulrichando/jarvis/commit/6fec9190722f6e5f2943bc823570b71213d1f7a6))
* **web:** installer verifies checksum on macOS + flags a broken binary ([9d64a3c](https://github.com/ulrichando/jarvis/commit/9d64a3ca6130a53beafb376d8737c9addb74b372))
* **web:** installer verifies checksum on macOS + flags a broken binary ([ba2172d](https://github.com/ulrichando/jarvis/commit/ba2172ddc8f8d26a78dce1302fd37408b3aebb13))
* **web:** make Settings font size + density actually app-wide ([f1028a2](https://github.com/ulrichando/jarvis/commit/f1028a2a0d4e17d4d1a84f5a16c7ef73867e266e))
* **web:** mint a proxy JWT for the headless /code CLI's model calls ([4a04baa](https://github.com/ulrichando/jarvis/commit/4a04baa62846a95db6d359c08aa5082731896dd4))
* **web:** mint a proxy JWT for the headless /code CLI's model calls (gap [#7](https://github.com/ulrichando/jarvis/issues/7)) ([0de55f4](https://github.com/ulrichando/jarvis/commit/0de55f469e8997cdf8bfc1dd57a623c2dcff54a0))
* **web:** normal /code sessions reach the git-proxy internally (not just bot jobs) ([48567b6](https://github.com/ulrichando/jarvis/commit/48567b6b30ac28d249d40fc18ffba85f63cecc26))
* **web:** normal /code sessions reach the git-proxy internally too (not just bot jobs) ([e725e11](https://github.com/ulrichando/jarvis/commit/e725e11ee30c43dfd266d2c64409e33d8a08e153))
* **web:** paginate GitHub repo list — show all repos, not just the first 100 ([3685d9e](https://github.com/ulrichando/jarvis/commit/3685d9e9b57b4518fda7dacfa93e306d1d9ba310))
* **web:** paginate the GitHub repo list so ALL repos show, not just the first 100 ([819ed02](https://github.com/ulrichando/jarvis/commit/819ed0272da7f68092e8e1ae1e7900741068c3ce))
* **web:** path-containment barriers in the knowledge stores (CodeQL js/path-injection) ([0260311](https://github.com/ulrichando/jarvis/commit/02603119342a3f16722527a73bc7d832faf28c12))
* **web:** persist ~/.jarvis across deploys (connector + /code sessions no longer wiped) ([29b39f6](https://github.com/ulrichando/jarvis/commit/29b39f6033811d0011222cc6349ea65c454b993a))
* **web:** persist ~/.jarvis across deploys (GitHub connector + /code sessions no longer wiped) ([7e1a639](https://github.com/ulrichando/jarvis/commit/7e1a6398593cad1f3f2aa89612497221fde083f8))
* **web:** pre-create ~/.jarvis owned by the app user so the volume mounts writable ([c4ad659](https://github.com/ulrichando/jarvis/commit/c4ad65943ddc9da155270975f8400691b3286784))
* **web:** proxy accepts per-user bridge tokens on self-authenticating CLI routes ([47c3003](https://github.com/ulrichando/jarvis/commit/47c300333e555204a12214d26ff8c040cd8a189b))
* **web:** proxy accepts per-user bridge tokens on self-authenticating CLI routes ([9bcbb31](https://github.com/ulrichando/jarvis/commit/9bcbb313d1f2d8fed557dfe9672704d1db5de4f1))
* **web:** raise provider-test probe cap to 512 tokens so reasoning models return content ([f516cc5](https://github.com/ulrichando/jarvis/commit/f516cc5accb150431e917c55ce0f2db1f05aa5bc))
* **web:** remote-control worker routes reach their own auth past the proxy gate ([7ed6ff1](https://github.com/ulrichando/jarvis/commit/7ed6ff1d22d41227d0e6b79c050292b8f23e7984))
* **web:** remote-control worker routes reach their own auth past the proxy gate ([63d174f](https://github.com/ulrichando/jarvis/commit/63d174fc9ccbee78af6e668db2fdb0bdff6ecdad))
* **web:** remote-control worker routes reach their own auth past the proxy gate ([6ad82f2](https://github.com/ulrichando/jarvis/commit/6ad82f232b20e1068f4d70206542860edd0c2efe))
* **web:** REPL /remote-control session routes reach in-handler auth (wave 2) ([b79dc80](https://github.com/ulrichando/jarvis/commit/b79dc80b9a83fe0329e8a0fd7e11c3e38558dbb1))
* **web:** REPL /remote-control session routes reach in-handler auth (wave 2) ([e3209a3](https://github.com/ulrichando/jarvis/commit/e3209a35cfebcd0ad8ee9b35423f2ee871f171d1))
* **web:** repo picker re-fetches on open + after connecting (was mount-only) ([cc7ef0a](https://github.com/ulrichando/jarvis/commit/cc7ef0a6ae4dc361de1e216233441f43f7fa2838))
* **web:** repo picker re-fetches on open + after connecting GitHub (was mount-only) ([a6277b3](https://github.com/ulrichando/jarvis/commit/a6277b3280a3a0aca723141acbe190f8a9064625))
* **web:** self-healing settings + wire every Settings→General control ([1e9cbce](https://github.com/ulrichando/jarvis/commit/1e9cbce7d69ace5459debb99ab6c613203f79eb1))
* **web:** ship the /api/design/build route — .gitignore 'build/' had swallowed it ([65b5b31](https://github.com/ulrichando/jarvis/commit/65b5b31b6ecf8224df72c9fc8b386cd7157e2771))
* **web:** ship the /api/design/build route — .gitignore 'build/' had swallowed it ([f46848a](https://github.com/ulrichando/jarvis/commit/f46848ad9452065804013e4cc9362072de7dd08b))
* **web:** stop new chat forking a fresh conversation per message ([b3d62eb](https://github.com/ulrichando/jarvis/commit/b3d62ebb2657c89c76be3701963130ac9bf996c0))
* **web:** sync validRepoFullName traversal guard from master onto cli-feature-unlock ([9ca7001](https://github.com/ulrichando/jarvis/commit/9ca7001df0c3e65725ac37d1758c1ba63573b2f0))
* **web:** teleport session list shows last-activity time, not creation time ([#110](https://github.com/ulrichando/jarvis/issues/110)) ([bcd652e](https://github.com/ulrichando/jarvis/commit/bcd652e0f5b8d301d3952eee84cf1052bebb0b49))
* **web:** thread the App installation token to pr-status + merge for external sessions ([c9d8b5a](https://github.com/ulrichando/jarvis/commit/c9d8b5ad569d919d3f6551ee75d5c6b4f171959b))

## [2.1.108] — 2026-06-29

### Added
- **Hub Gateway** — the CLI's `:4000` proxy promoted to a VPS-hosted,
  multi-provider LLM gateway at `proxy.0wlan.com` (a container behind the
  cloudflared tunnel). Adds an OpenAI-shaped ingress (`POST /v1/chat/completions`,
  passthrough) + a `/config` diagnostic on top of the Anthropic-shaped
  `/v1/messages`. Clients route LLM traffic through it with a login JWT; provider
  keys live only on the VPS.
- **Conversation modes (backend)** — named presets bundling a voice LLM + CLI
  model + TTS voice + tool allowlist, applied as one set (`~/.jarvis/modes.json`;
  `/modes` + `/mode[/create|update|delete]` endpoints; built-ins DeepSeek /
  Claude / Local).

### Removed
- **Groq** — removed entirely across voice-agent, web, and CLI (LLM / STT / TTS
  provider + cost tables + health probe). DeepSeek is the default rung.

### Changed
- Version sources reconciled (`start.sh` / `cli.tsx` / `package.json`) so
  `jarvis --version` matches for source-run and binary.

---

## [Unreleased] — SDLC review pass (2026-06-11)

Follow-ups from the full lifecycle review (CI un-red + docs truth):

### Fixed

- CI: `cmudict` was a missing **runtime** dependency of the viseme engine
  (`lipsync/phonemize.py`) — added to `requirements.txt`; fresh installs of
  the voice-client would have crashed the lipsync path.
- CI: `test_dtln_integration.py` import-crashed without the PortAudio C
  library; CI now installs `libportaudio2` and the test module skips
  gracefully on minimal environments.
- CI: bumped `actions/checkout`→v6, `setup-node`→v6, `setup-python`→v6,
  `setup-java`→v5 ahead of GitHub's forced Node 24 switch (2026-06-16).
- cli: `npm audit fix` — resolved the critical `shell-quote` advisory
  (GHSA-w7jw-789q-3m8p) and `ws` memory disclosure; the remaining
  @opentelemetry HIGH pair is accepted + tracked in
  `docs/decisions-pending.md` (gate moved to `--audit-level=critical`).
- `bin/jarvis-mode-resume`: a slow `jarvis-mode` at login (exit 124) left
  the unit `failed` for days — mode restore is best-effort now (warn + exit 0).

### Docs

- Truth pass over `ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `docs/runbook/jarvis-voice.md`, and `.claude/rules/` — removed the
  deleted hub/extractor/consolidator and `jarvis-proxy`/`jarvis-hub` units,
  fixed log paths, test counts, monkey-patch count, snapshot + escalation
  targets.
- New `docs-truth` CI job (lint workflow) greps orientation docs for
  tombstoned systems so this class of drift fails the build.
- New `docs/decisions-pending.md` — single tracked home for findings that
  await a maintainer decision.

---

## [Unreleased] — production-hardening pass (2026-05)

This section summarises the `chore/production-hardening` pass. Items are
grouped by domain; individual commits carry conventional-commit prefixes.

### Security

- Removed a committed secret file that had been accidentally tracked in git;
  confirmed `.gitignore` coverage for all `*.env` and `~/.jarvis/*.env` paths.
- Locked filesystem permissions on `.env` files to mode 600 per install script
  and runbook guidance.
- Added bridge bearer-token enforcement (`JARVIS_REQUIRE_LOCAL_AUTH=1`);
  `~/.jarvis/local-api-token.env` generated by installer.
- Added `SECURITY.md` (threat model, reporting path, secret-handling policy).

### Cleanup

- Untracked 354 MB of Android NDK build artifacts (`src/android/app/.cxx/`)
  that were not covered by `.gitignore`; added the pattern.

### Dependencies

- Applied `setuptools` CVE fix (pinned safe floor in `requirements.txt`).
- Tauri 2.10.3→2.11.2 via `Cargo.lock` (`cargo update`; release rebuild deferred).
- Bounded `litert` version ceiling to prevent silent ABI breaks in the
  Android on-device inference path.

### Bug fixes

- Voice agent: offloaded blocking event-loop calls to the thread executor to
  prevent LiveKit frame-processing stalls.
- Web app: added rehype-sanitize sanitisation on chat message render paths to close
  a stored-XSS vector in the markdown renderer.
- Desktop UI: fixed a chat-panel spinner that never resolved when the bridge
  was unreachable; added a 60 s timeout with a user-visible error state.

### Docs

- Added `ARCHITECTURE.md` — one-page multi-process overview + data-flow
  diagram; links to `CLAUDE.md` and the repo map for deeper detail.
- Added `CONTRIBUTING.md` — per-subtree build/test commands, commit
  conventions, and pointer to regression-prevention rules.
- Added `CHANGELOG.md` (this file).
- Added `SECURITY.md` — threat model, reporting path, secret-handling policy.
- Added `docs/env-reference.md` — complete manifest of all `JARVIS_*`
  environment variables, grouped by subsystem, with required keys called out.
- Added `docs/superpowers/specs/README.md` — chronological index of design
  specs; tombstoned specs marked.

---

## [Prior]

All history prior to this hardening pass is in `git log`. Key milestones:

- **2026-05-28** — French/English code-switch support; kiosk mode v2.
- **2026-05-27** — Out-of-process `dispatch_agent` tool; post-tool reply gate;
  kiosk mode v1; pre-TTS confab gate pattern coverage.
- **2026-05-24** — Auto-mod loop (gated, audited, reversible); pre-TTS confab
  gate; tray chat panel.
- **2026-05-20** — Hermes-style soul extraction; self-improvement rebuild;
  echo-aware barge-in gate; between-turn scheduler; skill loop.
- **2026-05-18** — Barge-in interrupt fix (VAD-direct + Deepgram primary STT +
  TTS upstream-cancel); computer-use parity; CUA password-check fail-open.
- **2026-05-16** — 10-domain global security + architecture review.
- **2026-05-12** — Self-evolution design.
- **2026-05-10** — 10/10 refactor: `jarvis_agent.py` shrunk 38%; LangGraph
  alt-supervisor deleted.
- **2026-05-08** — 4-layer memory fix; token-aware pruning; memory
  consolidator; anti-gaslighting denial detector.
- **2026-05-04** — Voice resilience; VAD threshold tuned; confab detector.
- **2026-04-30** — Browser extension control; voice-intelligence rubric.
- **2026-04-23** — Voice-like-Claude design; app-builder UI.

For per-commit detail: `git log --oneline`.
