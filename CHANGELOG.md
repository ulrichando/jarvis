# Changelog

All notable changes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

For the full commit-level history, run `git log --oneline`.

---

## [0.2.2](https://github.com/ulrichando/jarvis/compare/v0.2.1...v0.2.2) (2026-07-02)


### Features

* **automod:** 6-provider council on the highest model of each ([cdcb6eb](https://github.com/ulrichando/jarvis/commit/cdcb6eb97ba948f6ca148de17a01409b7cb20afe))
* **automod:** auto-collapse redundant retry-failure records ([ab04d2f](https://github.com/ulrichando/jarvis/commit/ab04d2f054972ccf91d4e74e77b80fa4e2621112))
* **automod:** council-&gt;rework routing (gated, off by default) ([c901e12](https://github.com/ulrichando/jarvis/commit/c901e1252c32622f42340dcbddac9310094112ee))
* **automod:** dedup queue at enqueue (retry-exempt) ([1b66fbd](https://github.com/ulrichando/jarvis/commit/1b66fbd83b2d781f9b6d477ac5ac003bc1d6bf4a))
* **automod:** expand review council with 3 advisory lenses ([ec36a77](https://github.com/ulrichando/jarvis/commit/ec36a77ab9b6e14b5c486a7be0b7dbbbb945d129))
* **automod:** pre-build research stage (grounds the offline build) ([2f1e025](https://github.com/ulrichando/jarvis/commit/2f1e02583d60074856a72d8e46e6ba126ad05bd0))
* **automod:** stress-gate differential dual-run via git worktree ([7c7f4cb](https://github.com/ulrichando/jarvis/commit/7c7f4cb795fb06ae1d812612f167184733382d62))
* **automod:** stress-test gate — differential edge-case verification ([079f3a4](https://github.com/ulrichando/jarvis/commit/079f3a4d7dbe582f534d5876d6024dd0bc959389))
* **automod:** wire research into the build + optional build-network ([66ea9e6](https://github.com/ulrichando/jarvis/commit/66ea9e671c4458059bc547b91cbb464709423a85))
* **automod:** wire stress-test gate into finalize (off by default) ([513e6b3](https://github.com/ulrichando/jarvis/commit/513e6b36cda25696bbae3cf284a1e3eef6e9e48d))
* CCR-compat backend + plan approval for /ultraplan (Phase B) ([1c5fdcf](https://github.com/ulrichando/jarvis/commit/1c5fdcfb9578c77287ae2dbe1b692b38693fc1e2))
* **cli-gateway:** remote model-gateway wiring — web gatewayUrl + persist + binary bootstrap ([eb0d51b](https://github.com/ulrichando/jarvis/commit/eb0d51b4063a02d0ee072265f03e4290298349dd))
* **cli,web:** 'jarvis keys pull' — sync provider keys from the server ([088ae36](https://github.com/ulrichando/jarvis/commit/088ae369c24d1ac6d102217bc7ccd2cb021eba7a))
* **cli:** /workflows listing, slash commands, permission + detail dialogs ([a9861b9](https://github.com/ulrichando/jarvis/commit/a9861b9eb9f9499c3378896c65c6cce011364bd0))
* **cli:** add bg.ts session manager + 21 more feature flags ([0cf821e](https://github.com/ulrichando/jarvis/commit/0cf821eb3990adb617d1fc01e9ae95c7a1e10052))
* **cli:** add bg.ts session manager + 21 more feature flags ([dd13a7c](https://github.com/ulrichando/jarvis/commit/dd13a7ce5ce3865b59d5a21fc7678ac56ffb9b57))
* **cli:** batch workflow progress into task state + sdk events ([f6740f6](https://github.com/ulrichando/jarvis/commit/f6740f688df58762b3f36ec86c59ce54b7b07d25))
* **cli:** computer-use bridge MCP server + register Playwright (registry) ([4a79c21](https://github.com/ulrichando/jarvis/commit/4a79c21e686811aad7ff3b104b12fe8d489403e9))
* **cli:** dynamic Ollama discovery + gpt-oss /effort + saner local default ([db610bf](https://github.com/ulrichando/jarvis/commit/db610bf5d22dd278fbbc9cb5a2efae5445712f35))
* **cli:** dynamic-workflows + history-snip engines + SearXNG web_search backend ([baafa7b](https://github.com/ulrichando/jarvis/commit/baafa7be5959bb6105623db5d05c7e9cc0e062c9))
* **cli:** enable /files, /version, ConfigTool for JARVIS ([d08e9f5](https://github.com/ulrichando/jarvis/commit/d08e9f53315c8b8a57658795fb98e18a1fb99932))
* **cli:** enable /files, /version, ConfigTool for JARVIS (was ant-only) ([3d3137c](https://github.com/ulrichando/jarvis/commit/3d3137c71f2410b07b4fedaddc601c7032176448))
* **cli:** enable /files, /version, ConfigTool for JARVIS (was ant-only) ([ae2c8d6](https://github.com/ulrichando/jarvis/commit/ae2c8d636e9dec5699fa7f48f59a55013278f093))
* **cli:** enable HISTORY_SNIP feature flag ([11be424](https://github.com/ulrichando/jarvis/commit/11be4247db9e13efc2a3f82a58fdb8d4cc55d8c9))
* **cli:** enable WORKFLOW_SCRIPTS feature flag ([d1ca7d0](https://github.com/ulrichando/jarvis/commit/d1ca7d02f90c3a8fc6d5b5f155c550b364fc6c16))
* **cli:** gh-agent config loader + author allowlist gate ([a61a64a](https://github.com/ulrichando/jarvis/commit/a61a64a6526a2ccd3ff8b6cb03a9ebce33419e4f))
* **cli:** gh-agent gh wrappers (listMentions, postComment) ([d5edfab](https://github.com/ulrichando/jarvis/commit/d5edfabf7d77dcf2d092b93e906b29565dc87613))
* **cli:** gh-agent one-sweep loop (poll, gate, ack, cursor) ([c5c6de1](https://github.com/ulrichando/jarvis/commit/c5c6de170aed6f0c76747eb4e7f6e159156e8c0d))
* **cli:** gh-agent per-repo cursor (no-replay marker) ([f75af7a](https://github.com/ulrichando/jarvis/commit/f75af7ae6e2fb000dc967de3b15c29a61931cbb9))
* **cli:** history-snip runtime (queue, nudge pacing, boundary insert) ([a60dae3](https://github.com/ulrichando/jarvis/commit/a60dae34762a1014d5c78ad12ede9e1162e544ca))
* **cli:** id-anchored Snip tool + boundary message render ([bf03024](https://github.com/ulrichando/jarvis/commit/bf030241e120316c184fea1c7ef279ca461dc341))
* **cli:** jarvis uninstall — self-uninstall subcommand (idiomatic, like rustup self uninstall) ([fda15e5](https://github.com/ulrichando/jarvis/commit/fda15e5a2bfef740cd65ea7fede6e80530da2ad3))
* **cli:** list local Ollama models (qwen3-30b-a3b, gpt-oss-120b) in the model picker ([43cba17](https://github.com/ulrichando/jarvis/commit/43cba17cf7c67924f99e16d0567bb173f2c98d3a))
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
* **code:** clone/push via git proxy; drop real PAT + GH_TOKEN from container ([4628360](https://github.com/ulrichando/jarvis/commit/462836049d6908eccbb670c126b376d054b9d8f2))
* **code:** git proxy route — cap-token auth, repo-scope gate (403+audit), 503 no-PAT ([d415f19](https://github.com/ulrichando/jarvis/commit/d415f19a24f59f70b553e17f14ee9f498759e4b4))
* **code:** git-proxy policy + forwarder (parse smart-HTTP, repo allowlist, inject PAT host-side) ([b8703b3](https://github.com/ulrichando/jarvis/commit/b8703b344c1043043871d76e46830d468f926e68))
* **code:** open/merge PRs host-side via REST (no GitHub token in container) ([f7ae5e4](https://github.com/ulrichando/jarvis/commit/f7ae5e4348859c2f2a30c07346db0752977fc774))
* **code:** persist per-session git scope + cap token in container_json ([c50043e](https://github.com/ulrichando/jarvis/commit/c50043e184e720b00a43e0a59b16ae06309e3a4d))
* **computer-use:** auditable activity timeline component ([f4e884e](https://github.com/ulrichando/jarvis/commit/f4e884e821f649bdb08fe57eccd9b80afad7bbf7))
* **computer-use:** expose snapshot() handle on NoVNCView for per-step thumbnails ([a7c8373](https://github.com/ulrichando/jarvis/commit/a7c8373b31d207e530a83553907fa7b59e0cb368))
* **computer-use:** extract token-themed PermissionCard ([46f7493](https://github.com/ulrichando/jarvis/commit/46f74933604a8bfa1af1cc338b2e38784467ded3))
* **computer-use:** framed desktop stage with control overlay + states ([2484785](https://github.com/ulrichando/jarvis/commit/2484785a663a0368681013c348663630690a0eb0))
* **computer-use:** full-width command bar ([e8ae65a](https://github.com/ulrichando/jarvis/commit/e8ae65a1ab1b775eb2d15027e67e5376e51d2a48))
* **computer-use:** mission-control app bar (segmented mode, overflow) ([0065a8b](https://github.com/ulrichando/jarvis/commit/0065a8b07c996720ada78441083454bdcd426842))
* **computer-use:** multi-provider web computer use (Claude/GPT-5.5/Gemini) + UI ([8a371f4](https://github.com/ulrichando/jarvis/commit/8a371f4e9b3503178e57e84be41889fe5c11c475))
* **computer-use:** pure timeline event mapper + format helpers ([13f795f](https://github.com/ulrichando/jarvis/commit/13f795f1708214e40125faa3858f85441541aa13))
* **computer-use:** wire mission-control layout in the page orchestrator ([2f68d23](https://github.com/ulrichando/jarvis/commit/2f68d237ee55e0f2d9cb1a4bd4f894ddb6a1cfea))
* continuous VPS deploy pipeline + settings/gh-agent/desktop wave ([37b3483](https://github.com/ulrichando/jarvis/commit/37b3483e97f299fafcd3b9259de93bed66a599c2))
* **deploy:** continuous deploy — VPS polls origin/master and self-updates ([0f9cff6](https://github.com/ulrichando/jarvis/commit/0f9cff6ef6e4542eea6fef9a2d36771889cc6630))
* **desktop:** "Local (on-device)" as a 4th Conversation mode (replaces the toggle) ([90eb3ba](https://github.com/ulrichando/jarvis/commit/90eb3baf8811b6a7892e4fc0b97cab008540c7f7))
* **desktop:** "Restart all services" button on the Keys settings page ([d5ba241](https://github.com/ulrichando/jarvis/commit/d5ba241e92d4b8e6b2976313df81729bc7f820a0))
* **desktop:** add local Ollama speech models to the tray Speech-model menu ([55bd145](https://github.com/ulrichando/jarvis/commit/55bd14574f3d653c157489fdd8f345a47e8181c4))
* **desktop:** chat-panel account/CLI/restart controls + stall recovery ([480eb50](https://github.com/ulrichando/jarvis/commit/480eb50946fff5ac067fabdf534e299d6f976485))
* **desktop:** DeepSeek in tray model menus + detection-driven menu ([a3bab50](https://github.com/ulrichando/jarvis/commit/a3bab50b3956ef549fbd66cc6da25f26d8a08d43))
* **desktop:** enable bundling + stage the voice stack as resources (Phase 2 Tasks 2-3) ([704da7f](https://github.com/ulrichando/jarvis/commit/704da7f2d0073b3309937696d5dcbc215f67d08b))
* **desktop:** flag-gated process supervisor for the unified app (default OFF) ([8038b6b](https://github.com/ulrichando/jarvis/commit/8038b6b131d88ed634fbfb677fa417682dd14d18))
* **desktop:** make the voice-UI sign-in state-aware (Sign in ⇄ Signed in · &lt;server&gt; · Sign out) ([8bede50](https://github.com/ulrichando/jarvis/commit/8bede503af97f94ca744067f3731166989161fb8))
* **desktop:** MCP Connectors panel in settings (reflect new MCP work) ([a6c56ad](https://github.com/ulrichando/jarvis/commit/a6c56adf5902d9b90f7b836a492358e754554b24))
* **desktop:** move Sign-in/CLI shortcuts to the voice-agent UI ([fd4f0c9](https://github.com/ulrichando/jarvis/commit/fd4f0c919ad2aec8ecd1c31d475e3a2416433ae6))
* **desktop:** open deployed web, fall back to local when the VPS is down ([b5f7562](https://github.com/ulrichando/jarvis/commit/b5f75620615bbbeea87463616759e462559ee68b))
* **desktop:** resource-dir-aware asset_root for bundled installs (Phase 2 Task 1) ([7bbe978](https://github.com/ulrichando/jarvis/commit/7bbe978bff9e60bb5138c1f35eb1f48af37b739b))
* **desktop:** run-manifest for the supervised voice stack (SFU + voice-agent) ([cfb2206](https://github.com/ulrichando/jarvis/commit/cfb22061bd31b03b0642de98f04950ef3d813d4b))
* **desktop:** ship-polish — supervisor default-ON when bundled + real JARVIS logo icon (was placeholder green dot) ([a823aa7](https://github.com/ulrichando/jarvis/commit/a823aa7badfc2a4e4c469ed0f8ca72d5fef0bd7f))
* **desktop:** show Claude + DeepSeek as conversation modes; notify real LLM ([760746a](https://github.com/ulrichando/jarvis/commit/760746a4013df3ceb834dcae0cab81f06a25f9ef))
* **desktop:** tray audio device picker (Microphone / Speaker submenus) ([a7d3f6f](https://github.com/ulrichando/jarvis/commit/a7d3f6f6c2b79db7fc80964b6c99639ba4432812))
* **desktop:** tray voice controls — speech-rate presets, live ✓ sync, model-pick preservation ([1a95fdf](https://github.com/ulrichando/jarvis/commit/1a95fdf75823686b46818321bdc337354137f0ad))
* evolution-loop hardening, ops/health control plane, security fixes, voice vision tool ([64a0baf](https://github.com/ulrichando/jarvis/commit/64a0baf4c95af477b5831e27c55af21c00a069b8))
* **evolution:** 3-lens review council on pending proposals ([e6e97f3](https://github.com/ulrichando/jarvis/commit/e6e97f3b6653c50a9626469bb2432ed97e2eac79))
* **evolution:** AutoData-informed queue admission, retry feedback + fitness learnability ([c1d3066](https://github.com/ulrichando/jarvis/commit/c1d30665702d35e1e8e12508e66eab5a7948f35c))
* **evolution:** batch review council — review all pending at once ([8eff9fc](https://github.com/ulrichando/jarvis/commit/8eff9fcd972044808ca58392ed46027ffb3528a3))
* **evolution:** build tick every 4h (was every 30min) ([d667201](https://github.com/ulrichando/jarvis/commit/d667201b3fb5a21e58f326fea97a6748fd881e47))
* **evolution:** bump experience signal on correction/confab turns ([a4c91e1](https://github.com/ulrichando/jarvis/commit/a4c91e1cb53b5ddd3a076b14380cb9156138046d))
* **evolution:** bump experience signal when a new fact is learned ([a39fef3](https://github.com/ulrichando/jarvis/commit/a39fef36bd0476bdd1c47afcd5519274bb5bab3b))
* **evolution:** capture build cost + per-build budget cap ([760549b](https://github.com/ulrichando/jarvis/commit/760549ba7c6a9ff46fb80893c18bd13a81c8754e))
* **evolution:** changed-line coverage gate + build test venv fix ([7f994d6](https://github.com/ulrichando/jarvis/commit/7f994d6571b44ab65ae0c566ecbebf19ef8a70fd))
* **evolution:** event-driven _automod_loop (waits on experience signal, mode-gated build) ([ac9c12b](https://github.com/ulrichando/jarvis/commit/ac9c12b8d5025c6ed5aaf255551a557cb7594377))
* **evolution:** gate on idle+budget+cooldown, demote count cap to backstop ([8316e4a](https://github.com/ulrichando/jarvis/commit/8316e4a44de012e4609fefd69ed5e3712010f2bf))
* **evolution:** incremental review-all — background run + live progress ([7ddd98c](https://github.com/ulrichando/jarvis/commit/7ddd98c8b0e5cb65db61fb979d6ec6df6435e1f9))
* **evolution:** lived-experience shadow trial + loop heartbeat ([c635a0b](https://github.com/ulrichando/jarvis/commit/c635a0b4f5206193cbd13a6ac83b0cdab37a792e))
* **evolution:** multi-model review council + categorized queue ([91c2e41](https://github.com/ulrichando/jarvis/commit/91c2e41ddacf3d3eddbf3efe3a1eadf31cb76ccb))
* **evolution:** per-day cost ledger (the spend brake) ([a490bb1](https://github.com/ulrichando/jarvis/commit/a490bb112aa69c41cc3b04dbba668eaa39bc1a74))
* **evolution:** publish confirmed deploys to GitHub (push origin/master + a closed Issue), gated by JARVIS_EVOLUTION_GITHUB_DEPLOY ([c07360e](https://github.com/ulrichando/jarvis/commit/c07360e0f74bba8fb1a743c1e99cdc2d1b122046))
* **evolution:** reflect auto-rollbacks on GitHub — deploy symmetry ([#29](https://github.com/ulrichando/jarvis/issues/29)) ([c1bf23b](https://github.com/ulrichando/jarvis/commit/c1bf23b7c6714b84c46778257128689934f30ca0))
* **evolution:** SDLC design stage — 2-agent plan fusion + gate before build ([2406a9b](https://github.com/ulrichando/jarvis/commit/2406a9b97e286792f004d6fdd76631cd11b91ac6))
* **evolution:** self-assessment auto-queue + learn-and-retry build cycle + P0-P3 priority ([e795e32](https://github.com/ulrichando/jarvis/commit/e795e32f3d1238be8b68bd86b4f5ec3303087418))
* **evolution:** thread-safe experience signal for event-driven trigger ([abef45c](https://github.com/ulrichando/jarvis/commit/abef45c08fe7d6b7d81621b5d1232653a006a945))
* **hub:** /config diagnostic endpoint (provider key-presence + default route) ([e226282](https://github.com/ulrichando/jarvis/commit/e22628218ed73f953a6dfdd9d016b9a6a8ce1465))
* **hub:** Dockerfile for the VPS hub gateway (Bun proxy, auth-required) ([3443c30](https://github.com/ulrichando/jarvis/commit/3443c30ad4d0ee6119bfff986f0f4846cf4aa37e))
* **hub:** OpenAI-shaped ingress on the proxy (/v1/chat/completions passthrough) ([93bb782](https://github.com/ulrichando/jarvis/commit/93bb78218ffe388458ebeeb0ddafd04c26a0e7ce))
* **hub:** wire hub container into web compose + Caddy /hub route ([8d8014f](https://github.com/ulrichando/jarvis/commit/8d8014f07ee50f6b6703152fae840e270882c47b))
* local install/uninstall toolkit (CLI/voice/desktop/web channels) ([ec4ed61](https://github.com/ulrichando/jarvis/commit/ec4ed610b794f0850df66c78a4da9171af2c330d))
* **notifications:** read OS notifications via D-Bus (no vision) ([718634e](https://github.com/ulrichando/jarvis/commit/718634e7b096addeb0b819f31f613ef3bfa9c0aa))
* **ops:** API-key age rotation reminder ([bbc804e](https://github.com/ulrichando/jarvis/commit/bbc804e1e9f68b00598df9d5257e7d332478a71d))
* **ops:** automated health probe — alert on dead keys / services ([a84ba14](https://github.com/ulrichando/jarvis/commit/a84ba14dbbae0844b55a30a9b2eb831f94ebfb00))
* **ops:** health-gated voice-agent deploy (jarvis-voice-deploy) ([3d2bb64](https://github.com/ulrichando/jarvis/commit/3d2bb6410fb0051f71fea0853bf0d016e7759a39))
* **ops:** jarvis-health + jarvis-restart-all stack control plane ([8c9cdf0](https://github.com/ulrichando/jarvis/commit/8c9cdf01419b57f0078e54c9315ef2523696cfdf))
* **ops:** jarvis-trace — reconstruct one voice turn end-to-end ([8a514fd](https://github.com/ulrichando/jarvis/commit/8a514fd9aa657e3c39eb3abda8224f0f5e5eb5ad))
* **ops:** off-box encrypted backup + tested restore ([eb874da](https://github.com/ulrichando/jarvis/commit/eb874dabf2a0cf8b15ffc69e1c164e18615fbd90))
* **ops:** off-box push alerting (jarvis-notify) ([42b92e2](https://github.com/ulrichando/jarvis/commit/42b92e2284939ad9ae5d15b230a0497f3cbed6da))
* **ops:** SLO threshold alerts on turn telemetry ([4f959bb](https://github.com/ulrichando/jarvis/commit/4f959bbc1a4ac3317330f374d8e8d1af882edb9d))
* **ops:** SRE hardening — durability, off-box alerting, SLOs, deploy safety ([be50b0c](https://github.com/ulrichando/jarvis/commit/be50b0c2608f69b4608c2ae7782993131837d074))
* **ops:** systemd OnFailure= crash alerts on core units ([a14a9fa](https://github.com/ulrichando/jarvis/commit/a14a9fae1d0927e63a319df0f80bf2ddcdfdc83a))
* **ops:** telemetry status dashboard (jarvis-status) ([cf0a0f9](https://github.com/ulrichando/jarvis/commit/cf0a0f976b500c0080b1eb524b44772d4d014802))
* **voice-agent:** add gpt-oss-120b + qwen3-30b-a3b as selectable local (Ollama) speech models ([0d17791](https://github.com/ulrichando/jarvis/commit/0d177917353fb59204bfa2e9f9dc124359031ec4))
* **voice-agent:** evolution/automod — criteria, fault-boundary, on-demand cycle + tests ([27e496d](https://github.com/ulrichando/jarvis/commit/27e496de2407b63a0e0befb58721208540cc6019))
* **voice-agent:** JARVIS_LOCAL_STT_PRIMARY - promote local faster-whisper to primary STT ([490dfd1](https://github.com/ulrichando/jarvis/commit/490dfd15c61dfe8ca82415f79a31dbbbf0a1ef5b))
* **voice-agent:** JARVIS_LOCAL_TTS_PRIMARY - promote local TTS (Kokoro/Piper) to primary ([358786e](https://github.com/ulrichando/jarvis/commit/358786e1f0beb0c8b1d5b9b6b74816aa0b33b181))
* **voice-agent:** nightly self-evolution trigger (Phase 3) ([6b1ded2](https://github.com/ulrichando/jarvis/commit/6b1ded2119f0d04e20f104cc069f9afd003e14a9))
* **voice-agent:** self-evolution deploy watchdog + auto-rollback (Phase 1) ([66c4e9a](https://github.com/ulrichando/jarvis/commit/66c4e9a150995f51fd133919d369a7a2ca1a6106))
* **voice-agent:** self-evolution proposal summary generator (Phase 2) ([fc82f81](https://github.com/ulrichando/jarvis/commit/fc82f81a5fb08a213401f615e6c2b4c1f2eeefb1))
* **voice-agent:** self-evolution publish — push branch + open PR (Phase 2) ([e90f655](https://github.com/ulrichando/jarvis/commit/e90f65556e3a349757dc5fe0539972a4a6c9e8c4))
* **voice:** /modes + /mode HTTP endpoints (select/create/update/delete) ([c909a12](https://github.com/ulrichando/jarvis/commit/c909a12931d11357cd69a395901206ccda023098))
* **voice+desktop:** Gemini Live + OpenAI Realtime modes on Windows ([0b43e7f](https://github.com/ulrichando/jarvis/commit/0b43e7f9fd747dc8eb83546a8c13e8ae8137c215))
* **voice+desktop:** tray "Voice brain: Local / Cloud" toggle flips STT+LLM+TTS together ([4cdf070](https://github.com/ulrichando/jarvis/commit/4cdf07087194a73b736da641a3ee22889c162450))
* **voice+desktop:** tray pickers for local STT model + Kokoro voice ([580bc16](https://github.com/ulrichando/jarvis/commit/580bc16a90a554d650df320f5ce2dbeb271b5087))
* **voice:** conversation_modes resolve + apply (writes setting files) ([0b0c4ac](https://github.com/ulrichando/jarvis/commit/0b0c4ac1db1509a400db8f8d35859b96ec408413))
* **voice:** conversation_modes store — schema + seed + load ([b9edf3c](https://github.com/ulrichando/jarvis/commit/b9edf3c49919dc3c0c86c630c9b01633be7eebe0))
* **voice:** English-only persona — switch language ONLY on explicit request ([944ec71](https://github.com/ulrichando/jarvis/commit/944ec7125db26721174c97985ab975ad5f3b0825))
* **voice:** online TTS voices (Orpheus + Edge) + spec-driven engine + tray grouping ([f5298f2](https://github.com/ulrichando/jarvis/commit/f5298f2e9ddf558467b13767e348401767681a50))
* **voice:** per-mode tool allowlist filter in load_all_livekit_tools ([779d5bc](https://github.com/ulrichando/jarvis/commit/779d5bceedf53d0e2eaf762fba8ddf25ff350889))
* **voice:** provider-error classifier — explicit spoken/notified errors (out-of-credits, rate-limit, auth, quota, …) not raw HTTP ([d8a9494](https://github.com/ulrichando/jarvis/commit/d8a9494c2badafc4d4c013e5e681ae6585ca8a2e))
* **voice:** strictly-local STT/TTS + truthful provider labels ([ba59dbb](https://github.com/ulrichando/jarvis/commit/ba59dbb0ee884b3abb208a535ca96275b427758e))
* **voice:** vision tool — describe ctx images via Gemini so a text-only brain can see ([d1a32d6](https://github.com/ulrichando/jarvis/commit/d1a32d6f106f7f4592edf96fa3f83ff6016f3101))
* **voice:** wire provider-error classifier into session error/close handlers — speak the specific error + notify; recoverable gate supersedes _UNRECOVERABLE_LLM_ERR_RE ([404e036](https://github.com/ulrichando/jarvis/commit/404e0368bea4f14eb255787d37af18bf0c96570f))
* **web-auth:** add twoFactor plugin + otplib + reset/2fa schema ([7fd214a](https://github.com/ulrichando/jarvis/commit/7fd214a68bd8ee39a0075b414bf1387c72575a4e))
* **web-auth:** bin/jarvis-web-account CLI (seed + emergency reset-password) ([1b9ba7b](https://github.com/ulrichando/jarvis/commit/1b9ba7b0fd0523467268bff9752ae0671289342d))
* **web-auth:** forgot-password page (TOTP reset) + login link ([5d621d2](https://github.com/ulrichando/jarvis/commit/5d621d2482066111b1b7b799a4eb49fe6e52b14b))
* **web-auth:** getUserId returns string|null + requireUserId/withUser ([89dd6b1](https://github.com/ulrichando/jarvis/commit/89dd6b1db85f6222bc48a8f51dfd713737da62c9))
* **web-auth:** lock public signup at the proxy ([04ffcc9](https://github.com/ulrichando/jarvis/commit/04ffcc9136fda60a85f48e66843cfd9aca379f36))
* **web-auth:** OWASP session policy — 30-min idle + 8-hour absolute cap ([e3c0d44](https://github.com/ulrichando/jarvis/commit/e3c0d44e3ad1434f504ed4c99635317872475f52))
* **web-auth:** sliding 7-day idle session + 30-day absolute cap helper ([df86b21](https://github.com/ulrichando/jarvis/commit/df86b21db4fb9152820f2cbf16a3b4e81cab7d01))
* **web-auth:** standalone TOTP/backup verify ([9b6d59d](https://github.com/ulrichando/jarvis/commit/9b6d59dcfaaf02f45c89b62d5a67702e411da126))
* **web-auth:** TOTP enrollment UI in account security settings ([6fe7965](https://github.com/ulrichando/jarvis/commit/6fe796538464ffa8b3cf5ca6441bfc2ef330c7cd))
* **web-auth:** TOTP-authorized password reset endpoints ([d2e75f3](https://github.com/ulrichando/jarvis/commit/d2e75f3d2bb983ceaf10d7924a405ddbd76b5c53))
* **web:** /chat read-aloud uses local Kokoro TTS ($0), Groq Orpheus fallback ([c740234](https://github.com/ulrichando/jarvis/commit/c740234604e18e5e05cb6d6c29bae9fbc14e37f8))
* **web/code:** GET /environments reaps stale sandboxes + marks machine online ([59b5245](https://github.com/ulrichando/jarvis/commit/59b52458f7b27b9afb4e1f5089f172fd363335b0))
* **web/code:** liveness-gate URL session restore; don't reopen dead sessions ([89ed015](https://github.com/ulrichando/jarvis/commit/89ed0154fd1d291e4d479307aa00526af9b31274))
* **web/code:** online status dot on the connected machine ([8cdfbca](https://github.com/ulrichando/jarvis/commit/8cdfbca0520e1edf72d2d16c669b3deadf7914a2))
* **web/code:** TTL reaper for stale cloud sandboxes + online helper ([7c92ba7](https://github.com/ulrichando/jarvis/commit/7c92ba757f3558253892ff864f593f3c16807f9f))
* **web:** /evolution review + approve surface (Phase 2) ([0d084ef](https://github.com/ulrichando/jarvis/commit/0d084efa6399556c8d825727525d24ed43dbf823))
* **web/evolution:** clarify review-council UX — re-run button, auto-ran hints, empty-state mention ([ad7d3f5](https://github.com/ulrichando/jarvis/commit/ad7d3f599f6372d650f26c125570db5de2fb3cff))
* **web/evolution:** enabling Auto kicks off a build cycle immediately, not only at the nightly ([1038622](https://github.com/ulrichando/jarvis/commit/1038622de0d15b3fd6168ccae756f6888af75b9d))
* **web/evolution:** unify pipeline+tabs into one nav, split Queue/Review, dedupe card text, clamp coverage ([5c9215e](https://github.com/ulrichando/jarvis/commit/5c9215e09a8cdc53aa0affcc6f46461ad5ad9165))
* **web+cli:** Phase 2/3 — claude-style installer served from jarvis web ([b4251d4](https://github.com/ulrichando/jarvis/commit/b4251d4b1b59d827a56616c307d63747b5a5a2cb))
* **web+cli:** Phase 2/3 — claude-style installer served from jarvis web ([1c7b43d](https://github.com/ulrichando/jarvis/commit/1c7b43d95744c053ecda7c2f0881d3e8e742c4af))
* **web:** add Cookbook to Settings — embed the local Cookbook sidecar ([31f9ee7](https://github.com/ulrichando/jarvis/commit/31f9ee7c5b8b5b66c76c370a036af9d946814c08))
* **web:** add local Ollama provider + models to the web model picker ([878e841](https://github.com/ulrichando/jarvis/commit/878e841b9db9525dd024d571ded64802a371ae74))
* **web:** add self-hosted SearXNG service to deploy stack (JARVIS web_search backend; JSON API enabled) ([1cb2dba](https://github.com/ulrichando/jarvis/commit/1cb2dbabe443a871a8aaf25ea4b9388c9c155335))
* **web:** auto-discover installed Ollama models in the model picker ([0a9d873](https://github.com/ulrichando/jarvis/commit/0a9d8738c3e7573aa16da78a2bf163f666ae7ed3))
* **web:** claude.ai-parity artifacts + fix MCP tool-schema chat crash ([60d91ed](https://github.com/ulrichando/jarvis/commit/60d91ed511afbad5c08af35587b058d0c5ea0c7e))
* **web:** delete Ollama models from Settings ([c8a2de8](https://github.com/ulrichando/jarvis/commit/c8a2de8923117f486c142752ff3883a07ee9724f))
* **web:** enforce login + TOTP password reset + OWASP session policy ([8586159](https://github.com/ulrichando/jarvis/commit/85861595aefd3b0889b0cfe85e92e5c3401dc33b))
* **web:** enterprise deploy phase 1 — hardened sandbox executor + containerization ([e8bc8a7](https://github.com/ulrichando/jarvis/commit/e8bc8a74d265d60abb3f179ec2cbc6ac73f9a486))
* **web:** enterprise deploy phase 2 — per-session auth on the /code PTY socket ([70a0430](https://github.com/ulrichando/jarvis/commit/70a04305529993ba74cae505d19850df4ed5a9d2))
* **web:** enterprise deploy phase 4 — Cloudflare front door as Terraform ([3f1593d](https://github.com/ulrichando/jarvis/commit/3f1593dddce5657b3cdea5cc305afd6c0969854c))
* **web:** evolution console redesign — status card, tab bar, single run-state control ([bf9bf69](https://github.com/ulrichando/jarvis/commit/bf9bf69e493343fd60ff8af177f592cfe5302787))
* **web:** Evolution nav badge + Recents Star / Add-to-project ([19dde92](https://github.com/ulrichando/jarvis/commit/19dde9282e3629bee0082eb205da1ef33bb82273))
* **web:** let users reset/remove the 2FA authenticator in Settings → Security ([30fedde](https://github.com/ulrichando/jarvis/commit/30fedde8378fb4d4e6d9275f634d418c1433ad60))
* **web:** make Settings → Jarvis in Chrome real (persisted prefs + live status) ([8cde290](https://github.com/ulrichando/jarvis/commit/8cde2906cd6aa9cf74d2c594e0e94c959b57bace))
* **web:** mobile-responsive shell + settings; UI cleanups ([ce26ed9](https://github.com/ulrichando/jarvis/commit/ce26ed9fcebbd7c53616f1c55a3fdad05cc4a69b))
* **web:** Ollama connection + model management in Settings (Open WebUI-style) ([f9aa31f](https://github.com/ulrichando/jarvis/commit/f9aa31f1bb2583952e1c323404be4096efbee1a1))
* **web:** online CLI uninstaller (curl 0wlan.com/uninstall.sh | bash), client-side only ([3de5639](https://github.com/ulrichando/jarvis/commit/3de5639081c8072b118d81d7956f67568ef6988d))
* **web:** real MCP OAuth sign-in for connectors (Vercel/Notion/…) ([a5fdb6d](https://github.com/ulrichando/jarvis/commit/a5fdb6d8b42fc311233330f070ae7a0606702381))
* **web:** rebuild rich /evolution UI (review + health + history) ([b2d177f](https://github.com/ulrichando/jarvis/commit/b2d177f95486b788113096f4824c359b40714d3a))
* **web:** restore /evolution API layer (12 routes) ([f7eee55](https://github.com/ulrichando/jarvis/commit/f7eee55947772ecd678d733a98101db52c214b9b))
* **web:** restore the global Knowledge store, API, and Settings tab ([bf0c84e](https://github.com/ulrichando/jarvis/commit/bf0c84ede7fe235fbe304d2ff9c6a3c0984df0e2))
* **web:** show signed-in identity + Sign out in Settings → Account ([d1a623b](https://github.com/ulrichando/jarvis/commit/d1a623b902650321874a19db0fc47ba0cec371fe))
* **web:** surface shared keys.env provider keys in Settings ([55e6a91](https://github.com/ulrichando/jarvis/commit/55e6a9100f1a90a82ef4c45de9586befcce1b2cd))
* **windows:** native deploy — cross-platform voice-agent, desktop, installer fixes ([4567db9](https://github.com/ulrichando/jarvis/commit/4567db9b652008dca17f196f7f910f5649c807ac))


### Bug Fixes

* **automod,ops:** pause suppresses proposal notifications; jarvis-health port-checks launcher services ([1c07490](https://github.com/ulrichando/jarvis/commit/1c07490e3ce3c71099e4e0b9e8f2b00d97693ceb))
* **automod:** close diff-path-extraction blocklist bypasses (rename/quoted/..) ([58b69a8](https://github.com/ulrichando/jarvis/commit/58b69a8a0c9ef5b522971e69f80e332b6f324984))
* **automod:** council uses the CURRENT top model IDs (researched + live-verified) ([63e024c](https://github.com/ulrichando/jarvis/commit/63e024c7d0df7908a5d55d6d948ebd32242deb8f))
* **automod:** deflate all-P0 priority inflation ([cf89275](https://github.com/ulrichando/jarvis/commit/cf8927535a8019b6315b8947c9ba63225a742c98))
* **automod:** deploy orphan proposals by head_sha when branch is reaped ([c030d13](https://github.com/ulrichando/jarvis/commit/c030d137453c4c1929c823c0f49d8e8ce0ca2b59))
* **automod:** derive build-prompt blocklist from HARD_BLOCKLIST_PATHS ([55d1f40](https://github.com/ulrichando/jarvis/commit/55d1f40f37e76c61513a85d0d57daaab0a44d049))
* **automod:** stop the assessment re-queuing already-built goals ([4b921c3](https://github.com/ulrichando/jarvis/commit/4b921c3be2026ab82a3ae10eb809e96ced9552d2))
* **automod:** suppress proposal notifications under pytest (tests spammed real popups) ([fd3f00c](https://github.com/ulrichando/jarvis/commit/fd3f00ce61b4176e7192c095095e82eb6e45f1d7))
* **automod:** watchdog re-verifies health after rollback before clearing marker ([1be7d48](https://github.com/ulrichando/jarvis/commit/1be7d48bc84dbdd48e740d3bd13f88ea01f6d330))
* **cli:** /effort applies the selected level, not one behind ([b80f7d8](https://github.com/ulrichando/jarvis/commit/b80f7d86b984a8214121de6dfdda29856cb03e13))
* **cli:** /version never surfaced — move out of INTERNAL_ONLY_COMMANDS ([ccebd2b](https://github.com/ulrichando/jarvis/commit/ccebd2bd32bc917a2ac4a10da4710ab75410bd51))
* **cli:** /version never surfaced — move out of INTERNAL_ONLY_COMMANDS ([f1f791b](https://github.com/ulrichando/jarvis/commit/f1f791b7444ab829aff0fdd1a5b3c1fa09ee3c37))
* **cli:** add gh-agent to start.sh commander-subcommand skip-list ([a69a0b0](https://github.com/ulrichando/jarvis/commit/a69a0b0f43980893b399d647bca492787d3bee83))
* **cli:** add runSkillGenerator.ts stub to prevent crash when RUN_SKILL_GENERATOR enabled ([42306c4](https://github.com/ulrichando/jarvis/commit/42306c4973b79199f4c070f95b124f3c3e4c9863))
* **cli:** add runSkillGenerator.ts stub to prevent crash when RUN_SKILL_GENERATOR enabled ([5b40ecd](https://github.com/ulrichando/jarvis/commit/5b40ecd332d8c9c73dcf3e4937e8e0e282112ebb))
* **cli:** auth login detects edge-gate redirect instead of crashing silently ([2a55b12](https://github.com/ulrichando/jarvis/commit/2a55b1224146698ed72b54f9fc0e4825bd004e0e))
* **cli:** canonicalize OAuth loopback to 127.0.0.1 (RFC 8252) ([b62372e](https://github.com/ulrichando/jarvis/commit/b62372e68e4ca504fce1a4590038f95f38742ad6))
* **cli:** claude-api skill crashed on invoke — graceful live-docs fallback ([ef22cf4](https://github.com/ulrichando/jarvis/commit/ef22cf442df70ed67bc0a49bbe6f1f55d5b9c168))
* **cli:** drop phantom HISTORY_SNIP flag — was exposing a dead Snip tool ([8dca0e6](https://github.com/ulrichando/jarvis/commit/8dca0e68ae0df0e58024f92b2f68550865dc69c0))
* **cli:** drop phantom HISTORY_SNIP flag — was exposing a dead Snip tool ([8d12b3a](https://github.com/ulrichando/jarvis/commit/8d12b3aef7d32f7ec2821cc22284677b3e10031a))
* **cli:** drop phantom WORKFLOW_SCRIPTS — WorkflowTool has no engine ([0c1740c](https://github.com/ulrichando/jarvis/commit/0c1740ca66af2639a43617000a3eed1d9fc024e4))
* **cli:** drop phantom WORKFLOW_SCRIPTS — WorkflowTool has no engine ([34ab42a](https://github.com/ulrichando/jarvis/commit/34ab42a0c9a6b3800a86ecc02f946b453374d26a))
* **cli:** effort indicator never showed bottom-right (Infinity timeout clamped to 1ms) ([726f125](https://github.com/ulrichando/jarvis/commit/726f12594b518f5fa4dd585e4e6ad62397d7a7b4))
* **cli:** effort indicator transient, not persistent — my prior fix starved notifications ([ed29599](https://github.com/ulrichando/jarvis/commit/ed2959953f2b592e794553f0bc6a55fe773d762c))
* **cli:** gh-agent command preserves module exit code (failed poll → nonzero) ([eba3c95](https://github.com/ulrichando/jarvis/commit/eba3c952e545bbf3ebce799109ee796e0c4fe513))
* **cli:** gh-agent no-replay by comment-id dedupe + monotonic cursor ([c48fb81](https://github.com/ulrichando/jarvis/commit/c48fb81f96b1fa63da1c225e2072ba473e03007e))
* **cli:** harden gh-agent — slurp pagination, dry-run no-writes, fail-safe acks, self-marker, window advance ([799d7b0](https://github.com/ulrichando/jarvis/commit/799d7b033e95118f38e0ac22a4a96f2dea5970ee))
* **cli:** jarvis auth login prefers your real server, falls back to local only if it's not responding ([a7abe6e](https://github.com/ulrichando/jarvis/commit/a7abe6ec4b285721a120bc67792dc82c355ee730))
* **cli:** jarvis uninstall --purge refuses to wipe shared provider keys (voice/web) ([52898e5](https://github.com/ulrichando/jarvis/commit/52898e5df3749592c67b9486b15d155c1cbce008))
* **cli:** make the :4000 proxy immortal (systemd service + crash guards) ([c6e071e](https://github.com/ulrichando/jarvis/commit/c6e071e8a610d08db5e98ff7a5533a65d2bb6ddb))
* **cli:** repair Claude 400s and refresh model registry to latest ([9f2715b](https://github.com/ulrichando/jarvis/commit/9f2715b627a097e338c4d9f4bb8a39306ba42e64))
* **cli:** restore autonomous interactive mode (bypass) with full Shift+Tab carousel ([7f057ae](https://github.com/ulrichando/jarvis/commit/7f057aec41673dd632d39f4ad7ba6adec190417c))
* **cli:** restore the interactive REPL — binary for TUI, IS_DEMO removed ([48277ee](https://github.com/ulrichando/jarvis/commit/48277ee4d48953396491aaae2be7ac9b6158e31e))
* **cli:** root-cause the interactive deadlock — break the bootstrap/state import cycle ([eb65fd2](https://github.com/ulrichando/jarvis/commit/eb65fd29d17aea01d8cb99a567bdf96e5c0e362f))
* **cli:** subcommand fast-paths never fired — args ordering + bg session env/name ([30c2e9d](https://github.com/ulrichando/jarvis/commit/30c2e9d93e981ddae2c3eabe5a5fee135d9acdb7))
* **cli:** subcommand fast-paths never fired — args ordering + bg session env/name ([da9a600](https://github.com/ulrichando/jarvis/commit/da9a6002eb7c0b52a147acd949f94f909d34ea67))
* **cli:** sync src/cli to master — restore /ultraplan + /swarm (Phase B) ([a6db7f4](https://github.com/ulrichando/jarvis/commit/a6db7f41284483426a774dd682b6aa40943d6ec4))
* **cli:** true root cause of the blank interactive REPL — gated module-level require()s ([2131d8a](https://github.com/ulrichando/jarvis/commit/2131d8a6c4c9182c9a55b727961df54f2435f27d))
* **cli:** two strays imported npm 'ink' instead of the vendored ink ([5a9dd90](https://github.com/ulrichando/jarvis/commit/5a9dd902d55f868ad238fc4ed5b82684b0e99a5e))
* **cli:** unblock headless/tool path — taskSummary.ts, drop REVIEW_ARTIFACT, silence proxy banner ([a66ed23](https://github.com/ulrichando/jarvis/commit/a66ed231fc8e1249c9936d5780a8a375c44dcb80))
* **cli:** unblock headless/tool path — taskSummary.ts, drop REVIEW_ARTIFACT, silence proxy banner ([2342d79](https://github.com/ulrichando/jarvis/commit/2342d7955673c1a592f458d8df874912d4993d37))
* **cli:** wire the 5 workflow parity stubs (nested workflow, custom agentType, worktree isolation, real budget.spent, resume journal read-back) ([2888b48](https://github.com/ulrichando/jarvis/commit/2888b48264704277b42ae0f4f2dbaef0660d371b))
* **cli:** workflow dispatch canUseTool must echo tool input — empty updatedInput clobbered Bash's command (undefined.includes crash) in workflow agents ([40da326](https://github.com/ulrichando/jarvis/commit/40da326a71ad244096de0ad04808ce00b17bd1ef))
* **code:** git proxy 401 must send WWW-Authenticate so real git resends the cap token ([e981433](https://github.com/ulrichando/jarvis/commit/e981433309e11bc69054bfba069b3917448b2212))
* **code:** squid egress allowlist bungled config — never start (isolated level dead) ([4e8b956](https://github.com/ulrichando/jarvis/commit/4e8b95697258dd89e83bb46fb11cd68b0e3f3bea))
* **computer-use:** generate sessionId client-only to fix SSR hydration mismatch ([a940d21](https://github.com/ulrichando/jarvis/commit/a940d21a9c73ec899e5734bf9cba93d9c2a6a3d2))
* **computer-use:** reduced-motion gating, status-chip casing, ReactNode import (review fixes) ([0e87176](https://github.com/ulrichando/jarvis/commit/0e8717657b773cd8b8f7e14b5d65d21bca112dbb))
* **deps:** remediate 228 Dependabot alerts (226 fixed, 2 won't-fix) ([4d221e1](https://github.com/ulrichando/jarvis/commit/4d221e1047ab73155c2872a658a336ea5152d339))
* **desktop:** align @tauri-apps/{api,cli} to 2.11.x (match Rust crate) so tauri build's version gate passes (Phase 2) ([74963e7](https://github.com/ulrichando/jarvis/commit/74963e7141755b9b585abba8ea90a399cd0afe65))
* **desktop:** bump vite 8.0-&gt;8.1 + esbuild 0.28.0-&gt;0.28.1 ([866536b](https://github.com/ulrichando/jarvis/commit/866536b28f46d108570419184bef9d59221dc219))
* **desktop:** bundle .deb only (AppImage's linuxdeploy fails + unneeded on Debian) ([df648fe](https://github.com/ulrichando/jarvis/commit/df648fe87e51572f3715c1df889c51a77e75ab8d))
* **desktop:** don't fight jarvis-proxy.service for :4000 ([f79b76b](https://github.com/ulrichando/jarvis/commit/f79b76b03cf9b6afdc9df71676a5bf5e5122f551))
* **desktop:** let jarvis-proxy.service reclaim :4000 after the stale-proxy pkill ([b9f18c3](https://github.com/ulrichando/jarvis/commit/b9f18c36e7056b0abeb2ecffba1e9d65170199a1))
* **desktop:** repo-root marker-walk + tray ✓ on active speech/tool model ([3d32a50](https://github.com/ulrichando/jarvis/commit/3d32a505318ea3f9d22002af49ea1698d6d47c51))
* **desktop:** restyle the API Keys window ([fdebe89](https://github.com/ulrichando/jarvis/commit/fdebe89ea4f95810ad8c9de1e113533cc9a36c87))
* **desktop:** single-instance, no console windows, non-blocking model-switch, browser-open paths ([368c2c1](https://github.com/ulrichando/jarvis/commit/368c2c167147acc37eccb676aa91bb4f238235b1))
* **desktop:** tray CLI opens in ~/Jarvis, not the repo ([e842dd0](https://github.com/ulrichando/jarvis/commit/e842dd0ae5483d82033ebe64ccf3f14a87a69c8b))
* **evolution/review:** force JSON-only output via system prompt ([d4d728b](https://github.com/ulrichando/jarvis/commit/d4d728b215515a571780e9c33a092319f9e7c999))
* **evolution/ui:** don't crash on an expired-session 401 ([dfb8b13](https://github.com/ulrichando/jarvis/commit/dfb8b134eb287ae1b963fdd93117bae405ec94fc))
* **evolution/ui:** render structured self-assessment items ([7fc1a72](https://github.com/ulrichando/jarvis/commit/7fc1a726a523ba97e65acaacef11366529da270b))
* **evolution/ui:** show all 7 categories in filter + chip History cards ([8317b12](https://github.com/ulrichando/jarvis/commit/8317b12b6494c3a995175be6b22c0cdbd862ba54))
* **evolution:** build worktree from local master, not stale origin/master ([ec9763d](https://github.com/ulrichando/jarvis/commit/ec9763d5631ec9739360e0357293a775c00ef3ec))
* **evolution:** correct 4h OnCalendar (explicit hour list, was bad step syntax) ([acdb029](https://github.com/ulrichando/jarvis/commit/acdb0296d80b6d278dbe838917ad7577d15f671d))
* **evolution:** deploy merges via cherry-pick (survives master moving past base) ([42cc461](https://github.com/ulrichando/jarvis/commit/42cc461a2c393731841997f5a5558112613798b1))
* **evolution:** deploy tolerates an unrelated-dirty tree ([f1d57b4](https://github.com/ulrichando/jarvis/commit/f1d57b4002c3722f88be4a917f8ecf33e80a554e))
* **evolution:** drop concurrent session's full-suite-failing watchdog tests ([479ecf2](https://github.com/ulrichando/jarvis/commit/479ecf257c907f93e66158e8dd183aa8c39a1d14))
* **evolution:** evolution-review/introspect bins self-load keys.env so council/introspect always have provider keys ([8e3d020](https://github.com/ulrichando/jarvis/commit/8e3d020a8b085fda06981339c232c8d62c122cb5))
* **evolution:** hermetic env for the automod fitness gate ([efaf8e3](https://github.com/ulrichando/jarvis/commit/efaf8e33d7eb5294af9d791b69ca001233a021c1))
* **evolution:** isolate tests from the build's injected automod env vars ([1983e10](https://github.com/ulrichando/jarvis/commit/1983e107fc3c4a5bdcca5f313df8782f1e05ab00))
* **evolution:** manual mode no longer autonomously builds ([e21ef80](https://github.com/ulrichando/jarvis/commit/e21ef807f0ef2f05f09f6a4ffceff6e9a637d1f0))
* **evolution:** pin diff base to worktree SHA + cap counts only reviewable ([fde79e2](https://github.com/ulrichando/jarvis/commit/fde79e2a33f749b69f9c9d2e4bc40326647bc784))
* **evolution:** retry circuit-breaker (cap 2, skip blocklist) + hermetic agent pre-commit pytest ([3f585a5](https://github.com/ulrichando/jarvis/commit/3f585a55678227b9defb24b1ab866daebb69910b))
* **evolution:** un-break committed master after Phase 1 (signal shadow + _state helpers) ([b8ab05d](https://github.com/ulrichando/jarvis/commit/b8ab05dbc49cc89385be3ae61f0d2cad58989ca0))
* **hub:** exclude .env* from image context + null-guard stream passthrough ([4ce7a7a](https://github.com/ulrichando/jarvis/commit/4ce7a7adcc07315037824ea1bc96d941f06ea472))
* **ops:** jarvis-mode-resume exit 124 at boot ([ac9267c](https://github.com/ulrichando/jarvis/commit/ac9267c48cadedc76b33fc081213718f66e41a3b))
* **ops:** service-review hardening pass ([c58f172](https://github.com/ulrichando/jarvis/commit/c58f17224a8d28b2c5ccfd5976857e9c58144f50))
* **ops:** stage backup plaintext in 700 dir, not world-readable /tmp ([22db12e](https://github.com/ulrichando/jarvis/commit/22db12e7b5d005708a757e73f4460f9b7deef7bc))
* **ops:** stop jarvis-mode-resume failing with exit 124 at boot ([e04d31c](https://github.com/ulrichando/jarvis/commit/e04d31c87d97035c59856861ecce286e3f11d9e6))
* **security:** bump cli deps to clear ~88 Trivy CVEs (incl. critical shell-quote) ([c9d8cf6](https://github.com/ulrichando/jarvis/commit/c9d8cf6995c8123dbcdf34cb29c0fcd1d827b020))
* **security:** bump dep CVEs across cli/web/desktop-tauri (Trivy) ([986c1fd](https://github.com/ulrichando/jarvis/commit/986c1fd4e5451b03b20346f8358e6c51c6f143a5))
* **security:** bump remaining dep CVEs (cli/web/desktop-tauri) ([8a78f4b](https://github.com/ulrichando/jarvis/commit/8a78f4ba4a57327c70a325feee9f3058b364be2f))
* **security:** bump web hono 4.12.18-&gt;4.12.25 + brace-expansion/picomatch (Trivy) ([1d4c63f](https://github.com/ulrichando/jarvis/commit/1d4c63f9938bd7b8352991a9b5b159913d7f9f8f))
* **security:** bump web image docker CLI 27.5.1 -&gt; 29.6.1 (Go stdlib CVEs) ([0f30b7f](https://github.com/ulrichando/jarvis/commit/0f30b7f2390ab5796e60c94043fa5ba0215f31dd))
* **security:** clear 4 web CodeQL highs (randomness, log fmt, codegen escape, ReDoS) ([c51403d](https://github.com/ulrichando/jarvis/commit/c51403da2a166747e63893cb7a1526be4cca790c))
* **security:** cli webSearch — decode &amp; last + loop tag-strip (CodeQL) ([317c9a9](https://github.com/ulrichando/jarvis/commit/317c9a97ff3425024b140c328ad13d2332e931da))
* **security:** sanitize repo/number/branch in GitHub connector URLs (SSRF) ([9c7f9f7](https://github.com/ulrichando/jarvis/commit/9c7f9f748472f1cd9eea3299d571c61ab26db0b5))
* **security:** stop sensitive data in voice-agent logs/SSE (CodeQL) ([095c3b7](https://github.com/ulrichando/jarvis/commit/095c3b7d662817f4c6bf3c6f999bcebd3468d25e))
* **security:** strip drizzle-kit build tools from web runtime image (esbuild CVEs) ([0abeb71](https://github.com/ulrichando/jarvis/commit/0abeb71958dcb00cd3b79009fd9b686b7450658f))
* **security:** validate workspace id at the path chokepoint (path-injection) ([7d8bee9](https://github.com/ulrichando/jarvis/commit/7d8bee9f73e4611b9f972949729211b6dc2c3502))
* **security:** voice-agent CodeQL — clear-text logging + stack-trace exposure ([8e81c08](https://github.com/ulrichando/jarvis/commit/8e81c08b13408a47d44bc76168311970d9d0a2d6))
* **security:** web hono 4.12.25 + brace-expansion/picomatch ([819eac3](https://github.com/ulrichando/jarvis/commit/819eac3a76c0d1bcce0ca2060807c51a689bd1a3))
* **security:** web image base bookworm-&gt;trixie (clears ~144 no-fix Debian CVEs) ([c782953](https://github.com/ulrichando/jarvis/commit/c78295355cd49f45d86fc7e8313df8191c0527e8))
* **security:** web image base Debian -&gt; Alpine (eliminate OS-CVE floor) ([aca3957](https://github.com/ulrichando/jarvis/commit/aca39579c16e9a4608f278f16a4b234b9e7449cc))
* **security:** web image base Debian/trixie -&gt; Alpine (eliminate the OS-CVE floor) ([98673a2](https://github.com/ulrichando/jarvis/commit/98673a210e4c82c302fbbaba93a8c328e17c30ba))
* **security:** web image node 22-&gt;24-alpine + cli uuid override (Trivy) ([511872f](https://github.com/ulrichando/jarvis/commit/511872fb7e9279d766322627753b95f0a1b50f68))
* **security:** web image node24-alpine + cli uuid (clears 5 dep CVEs) ([3acc8a4](https://github.com/ulrichando/jarvis/commit/3acc8a44f9ae4de788424a1101e151d2371b30d8))
* **voice-agent:** cross-platform fcntl locking + audio device selection on Windows ([c38c795](https://github.com/ulrichando/jarvis/commit/c38c795672f5d44ff4a8765a52854da877044d87))
* **voice-agent:** cross-platform shell for the terminal tool (Windows PowerShell) ([e0af263](https://github.com/ulrichando/jarvis/commit/e0af2634dc056fb42ed17ba528f9f9c2c8926c71))
* **voice-agent:** make search_files + write_file sensitive-path guard work on Windows ([dbf959d](https://github.com/ulrichando/jarvis/commit/dbf959d9ee9fe6660bc60d23d0299d7119463890))
* **voice-agent:** nightly restores the working-tree branch after a spawn ([628846a](https://github.com/ulrichando/jarvis/commit/628846a4c2e952aa831d63b9fde71e5b1293aee5))
* **voice-agent:** nightly stash-guards uncommitted work before a spawn ([79d6687](https://github.com/ulrichando/jarvis/commit/79d6687f8761442bf12f66567f641e93d774e319))
* **voice-agent:** repair ax_tree.py SyntaxError — escape inner triple-quotes ([4dd7d96](https://github.com/ulrichando/jarvis/commit/4dd7d9699edfa1c86a5d07d1c69a6453373a0670))
* **voice-client:** stop SIGABRT on service stop (PortAudio write/close race) ([4dd0a78](https://github.com/ulrichando/jarvis/commit/4dd0a7849b37ea8c8212b1debe6e8a0d6d9f36d8))
* **voice/evolution:** fuse 2-agent test hardening + missing-intent crash guard ([3ca3c44](https://github.com/ulrichando/jarvis/commit/3ca3c44c96d0c1d36e1d3f7a3c5d92f37afc0bf6))
* **voice/evolution:** harden automod loop reliability ([afa62fa](https://github.com/ulrichando/jarvis/commit/afa62fa0f191b0da24c7884bee96068f5fda3c67))
* **voice+desktop:** Kokoro voice hot-swaps with no restart ([4a7976d](https://github.com/ulrichando/jarvis/commit/4a7976d3a5f97cd9403e1e40f60ddbb5b9f4eab5))
* **voice+desktop:** Models reflects local mode; drop the STT-model picker ([7ddb87c](https://github.com/ulrichando/jarvis/commit/7ddb87ca0d0c374656d22692d6b08f8d488228fb))
* **voice:** gate ambient audio — answer only when addressed ([419e67a](https://github.com/ulrichando/jarvis/commit/419e67a2c076bbd85d5236caeb92e1ee6071fdc5))
* **voice:** log background agent-restart task failures ([f1f71d0](https://github.com/ulrichando/jarvis/commit/f1f71d0ffa7aaea9165bad70438483886c4bb823))
* **voice:** pin DeepSeek voice models to explicit deepseek-v4-flash + forced non-thinking (extra_body thinking:disabled) — the bare deepseek-chat alias is discontinued 2026-07-24 and V4 defaults to thinking (slow TTFT + tool_choice=required 400s) ([45f43ad](https://github.com/ulrichando/jarvis/commit/45f43ada509dafeedccf73d1dd6ff18d21d210f2))
* **voice:** pin fallback rung + dispatcher DeepSeek non-thinking (outage audit) ([3dfe607](https://github.com/ulrichando/jarvis/commit/3dfe607901b91f0f544bc22d5033382b13d4404b))
* **voice:** require web corroboration before stating looked-up facts ([c22c90d](https://github.com/ulrichando/jarvis/commit/c22c90d6cee7d7ecef5199293b93622d934f2a69))
* **voice:** silence ambient backchannel fillers — DISCRETION enforced in code ([4f313b7](https://github.com/ulrichando/jarvis/commit/4f313b7b9b8d069104cbf25e0d430b77fa54d23a))
* **voice:** strip CJK/Cyrillic from TTS output (DeepSeek leak) ([463b9b5](https://github.com/ulrichando/jarvis/commit/463b9b5e137f46cfe34616ea564c2c10015879cf))
* **voice:** strip empty text blocks before the Anthropic request (supervisor wedge) ([d9bde62](https://github.com/ulrichando/jarvis/commit/d9bde62cca4c64ba312b49c613247dd9e8a6ff1f))
* **voice:** strip image content for text-only models — stop the "acks then never returns" wedge ([c03d794](https://github.com/ulrichando/jarvis/commit/c03d794ffb9f8ea36cfc45fe14d98883cdcaf092))
* **voice:** web_search block-fallback points to browser_task, not the removed transfer_to_browser ([f0b945b](https://github.com/ulrichando/jarvis/commit/f0b945b80a037efb015680fc5ad09409e157716f))
* **web-auth:** document two-layer gate + harden same-origin carve-out ([7b3faeb](https://github.com/ulrichando/jarvis/commit/7b3faeb23b033b99b3fcd2856f016a9717702ff7))
* **web-auth:** rate-limit password reset per-email, not per-IP ([06c62f7](https://github.com/ulrichando/jarvis/commit/06c62f77d8eebfaf0bbf88d0c3defba0baf12ae4))
* **web/auth:** lengthen session to 24h idle / 30d absolute cap for the single-user box ([eb60d79](https://github.com/ulrichando/jarvis/commit/eb60d79ae26d28ef3c505b0bb7ccf67c9a372a32))
* **web/code:** machine identity keys on (user, machine_name), not directory ([516a9eb](https://github.com/ulrichando/jarvis/commit/516a9eb352eeceb1339bfbba9dd2b8542893abc7))
* **web/evolution:** nav badge shows real queue depth, not proposals count ([3331ab6](https://github.com/ulrichando/jarvis/commit/3331ab6f0c0b398b483c36cba63aa5443472352f))
* **web:** canonical loopback host — end the localhost/127.0.0.1 session split ([f2e5dda](https://github.com/ulrichando/jarvis/commit/f2e5dda41f017806a5a3e11b186394327221ad41))
* **web:** close conversations IDOR + tie /api same-origin carve-out to a session ([97f43a1](https://github.com/ulrichando/jarvis/commit/97f43a19bb6c726f2e76ddc914d510a512ea8cad))
* **web:** commit the live tunnel-shaped Caddyfile + searx.0wlan.com route ([d825838](https://github.com/ulrichando/jarvis/commit/d825838f6f4bde6cabefae5723bd9f1775ad5482))
* **web:** Cookbook section — live health state + tighter iframe sandbox ([f01237a](https://github.com/ulrichando/jarvis/commit/f01237af50b79958c3c1de4f1559339f6c58cb4a))
* **web:** declare imageAvailable on ComposerProps to unblock web-tests CI ([0c39d7f](https://github.com/ulrichando/jarvis/commit/0c39d7fbba112e709fb4d15eaee9c9ce26f21bf5))
* **web:** enterprise deploy phase 3 — make the containerized stack actually boot ([1552411](https://github.com/ulrichando/jarvis/commit/1552411c5fdac62ca833a9f81dbbc98048b254cc))
* **web:** harden artifact sandbox + stop preview flicker/truncation + dup-chat ([b9331bb](https://github.com/ulrichando/jarvis/commit/b9331bb59b55ca62d1cf3036e7b44db69ef253b8))
* **web:** make Settings → Connectors real (no more 'coming soon'); rewire GitHub MCP ([460fb0e](https://github.com/ulrichando/jarvis/commit/460fb0e4bd440f58e44d4f9e68bddeaa437f0705))
* **web:** make Settings font size + density actually app-wide ([f1028a2](https://github.com/ulrichando/jarvis/commit/f1028a2a0d4e17d4d1a84f5a16c7ef73867e266e))
* **web:** make the vitest suite hermetic + enforce it as a CI gate ([#28](https://github.com/ulrichando/jarvis/issues/28)) ([5566709](https://github.com/ulrichando/jarvis/commit/5566709fb4d3dcc379824506a266cc149075d5e1))
* **web:** minimal stubs for concurrent-session-deleted chat deps ([316bd36](https://github.com/ulrichando/jarvis/commit/316bd367278efd75d32004c1065991183f0fd2fd))
* **web:** path-containment barriers in the knowledge stores (CodeQL js/path-injection) ([0260311](https://github.com/ulrichando/jarvis/commit/02603119342a3f16722527a73bc7d832faf28c12))
* **web:** postinstall-rebuild better-sqlite3 so its native ABI matches local node ([d66e80d](https://github.com/ulrichando/jarvis/commit/d66e80dac2ead683ddede25a991fec45992bc88d))
* **web:** raise provider-test probe cap to 512 tokens so reasoning models return content ([f516cc5](https://github.com/ulrichando/jarvis/commit/f516cc5accb150431e917c55ce0f2db1f05aa5bc))
* **web:** self-healing settings + wire every Settings→General control ([1e9cbce](https://github.com/ulrichando/jarvis/commit/1e9cbce7d69ace5459debb99ab6c613203f79eb1))
* **web:** SSRF-guard ollama base URL + stop previewing keys.env secrets ([6e07073](https://github.com/ulrichando/jarvis/commit/6e0707354e029fc0f14afc117bb6d352c490c04e))
* **web:** stop new chat forking a fresh conversation per message ([b3d62eb](https://github.com/ulrichando/jarvis/commit/b3d62ebb2657c89c76be3701963130ac9bf996c0))
* **web:** store settings under ~/.jarvis with legacy cwd migration ([1c742f6](https://github.com/ulrichando/jarvis/commit/1c742f6ddf35998009e4bd8e73113900a6de2f6c))
* **web:** validate provider/ollama baseURL as a URL ([01a677b](https://github.com/ulrichando/jarvis/commit/01a677ba0a2848a3bb4f484a7440d4d3d0c62f50))


### Performance Improvements

* **voice:** add EMOTIONAL sync fast-path to skip classifier for short emotional turns ([bd41aed](https://github.com/ulrichando/jarvis/commit/bd41aeda0e1a16c1cb659179643686172ac5e2b6))

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
