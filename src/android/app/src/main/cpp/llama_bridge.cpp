/**
 * llama_bridge.cpp
 *
 * JNI bridge between Kotlin and llama.cpp.
 *
 * Lifecycle per-session:
 *   nativeLoadModel()      → allocates llama_context, returns opaque handle (jlong)
 *   nativeRunInference()   → runs inference; fires tokenCallback for each token
 *   nativeStopInference()  → sets cancel flag; inference loop checks it each token
 *   nativeGetModelInfo()   → JSON string: {name, params, quant, context_len, size_mb}
 *   nativeUnloadModel()    → frees all resources for this handle
 *
 * Thread safety:
 *   Each session is a separate LlamaSession and can run concurrently.
 *   The global model (llama_model*) is reference-counted and shared across
 *   sessions that load the same path — avoids double-loading 4GB weights.
 *
 * Memory layout:
 *   Global:  modelCache  (path → SharedModel, ref-counted)
 *   Per-ctx: LlamaSession (llama_context + sampler + cancel flag)
 *
 * Token streaming:
 *   Calls back into Java on the SAME thread that called nativeRunInference.
 *   The Kotlin side must not block in the callback or the inference stalls.
 *   Use a Channel/Flow on the Kotlin side to decouple.
 */

#include <jni.h>
#include <android/log.h>

#include <atomic>
#include <cstring>
#include <memory>
#include <mutex>
#include <algorithm>
#include <string>
#include <unordered_map>
#include <vector>

// llama.cpp public C API. These headers are found via the include dirs
// configured in CMakeLists.txt (${LLAMA_DIR}/include + ${LLAMA_DIR}/ggml/include).
#include "llama.h"
#include "ggml.h"
#include "ggml-backend.h"

#if JARVIS_HAS_OPENCL
// Direct reference to the OpenCL backend registrar. Having this declaration
// and calling it in nativeInit() forces the linker to keep ggml-opencl's
// static-constructor object files — otherwise the whole archive gets
// stripped and GPU inference is silently disabled.
extern "C" ggml_backend_reg_t ggml_backend_opencl_reg(void);
#endif

#define TAG     "JarvisLlama"
#define LOGI(...)  __android_log_print(ANDROID_LOG_INFO,  TAG, __VA_ARGS__)
#define LOGW(...)  __android_log_print(ANDROID_LOG_WARN,  TAG, __VA_ARGS__)
#define LOGE(...)  __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

// ── Constants ────────────────────────────────────────────────────────────────

// 2048 was too small in practice — our chat history builder produces prompts
// of 1200-1500 tokens before the user even sees a turn. Bump the default so
// a typical chat has room for a few rounds before hitting the KV cache wall.
static constexpr int  DEFAULT_N_CTX      = 4096;
static constexpr int  DEFAULT_N_THREADS  = 4;
// llama_decode aborts via ggml_abort when batch.n_tokens > n_batch. We
// prefill in DEFAULT_N_BATCH-sized chunks (see nativeRunInference) so this
// can stay at the llama.cpp default rather than scaling with the prompt.
static constexpr int  DEFAULT_N_BATCH    = 512;
static constexpr float DEFAULT_TEMP      = 0.8f;
static constexpr int  DEFAULT_TOP_K      = 40;
static constexpr float DEFAULT_TOP_P     = 0.95f;
static constexpr float DEFAULT_REPEAT_PENALTY = 1.1f;

// ── Shared model cache (avoids loading same weights twice) ───────────────────

struct SharedModel {
    llama_model * model     = nullptr;
    std::string   path;
    int           refCount  = 0;
};

static std::mutex                                   gModelCacheMutex;
static std::unordered_map<std::string, SharedModel> gModelCache;

// ── Per-session state ────────────────────────────────────────────────────────

struct LlamaSession {
    llama_model   * model   = nullptr;   // borrowed from SharedModel
    llama_context * ctx     = nullptr;
    llama_sampler * sampler = nullptr;
    std::string     modelPath;
    std::atomic<bool> cancelled { false };
};

// Handle → session map
static std::mutex                                          gSessionMutex;
static std::unordered_map<jlong, std::unique_ptr<LlamaSession>> gSessions;
static std::atomic<jlong>                                  gNextHandle { 1 };

// ── Helpers ───────────────────────────────────────────────────────────────────

static LlamaSession * getSession(jlong handle) {
    std::lock_guard<std::mutex> lock(gSessionMutex);
    auto it = gSessions.find(handle);
    return (it != gSessions.end()) ? it->second.get() : nullptr;
}

static void releaseModel(const std::string& path) {
    std::lock_guard<std::mutex> lock(gModelCacheMutex);
    auto it = gModelCache.find(path);
    if (it == gModelCache.end()) return;
    SharedModel& sm = it->second;
    sm.refCount--;
    if (sm.refCount <= 0) {
        LOGI("Freeing model: %s", path.c_str());
        llama_model_free(sm.model);
        gModelCache.erase(it);
    }
}

// Converts a llama token to its UTF-8 string piece.
// As of b4631 the token-to-piece function takes a `llama_vocab *` rather than
// a `llama_model *` — we grab it via llama_model_get_vocab.
static std::string tokenToPiece(const llama_context * ctx, llama_token token) {
    const llama_model * model = llama_get_model(ctx);
    const llama_vocab * vocab = llama_model_get_vocab(model);
    char buf[256];
    int  n = llama_token_to_piece(vocab, token, buf, sizeof(buf), 0, true);
    if (n < 0) {
        // Buffer too small — use a heap-allocated buffer
        std::vector<char> heap(-(n) + 1);
        llama_token_to_piece(vocab, token, heap.data(),
                             static_cast<int>(heap.size()), 0, true);
        return std::string(heap.data());
    }
    return std::string(buf, n);
}

// ── JNI implementation ────────────────────────────────────────────────────────

extern "C" {

/**
 * One-time init called by LlamaJNI.init().
 * Sets llama backend logging to Android logcat.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeInit(
        JNIEnv * /* env */,
        jclass  /* cls */)
{
    llama_log_set([](ggml_log_level level, const char * text, void * /*ud*/) {
        switch (level) {
            case GGML_LOG_LEVEL_ERROR: LOGE("%s", text); break;
            case GGML_LOG_LEVEL_WARN:  LOGW("%s", text); break;
            default:                   LOGI("%s", text); break;
        }
    }, nullptr);

    llama_backend_init();

#if JARVIS_HAS_OPENCL
    // Touch ggml_backend_opencl_reg so the linker keeps the ggml-opencl
    // archive. ggml's own registry constructor (compiled with GGML_USE_OPENCL)
    // handles the actual registration; we just need to prevent dead-code
    // stripping of the symbol it depends on.
    ggml_backend_reg_t opencl_reg = ggml_backend_opencl_reg();
    if (opencl_reg) {
        LOGI("ggml-opencl backend symbol resolved: %s",
             ggml_backend_reg_name(opencl_reg));
    } else {
        LOGW("ggml-opencl reg returned null");
    }
#endif
    // LLAMA_BUILD_NUMBER is only defined when llama.cpp is built via its own
    // Makefile (which stamps the number). Our CMake-driven build doesn't
    // define it, so we log a placeholder instead. Not worth wiring a custom
    // define for one log line.
    LOGI("llama backend initialised");
}

/**
 * Load (or ref-count share) a GGUF model from disk.
 *
 * @param modelPath  Absolute path to .gguf file on device storage
 * @param nGpuLayers Number of transformer layers to offload to GPU (Vulkan).
 *                   0 = CPU only; -1 = all layers on GPU.
 * @param contextSize KV-cache context window (tokens).
 * @return           Session handle (opaque jlong > 0), or -1 on failure.
 */
JNIEXPORT jlong JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeLoadModel(
        JNIEnv * env,
        jclass  /* cls */,
        jstring  modelPath,
        jint     nGpuLayers,
        jint     contextSize)
{
    const char * rawPath = env->GetStringUTFChars(modelPath, nullptr);
    std::string  path(rawPath);
    env->ReleaseStringUTFChars(modelPath, rawPath);

    LOGI("nativeLoadModel: %s (gpu_layers=%d, ctx=%d)",
         path.c_str(), nGpuLayers, contextSize);

    // ── Load or reuse model weights ───────────────────────────────────────
    llama_model * model = nullptr;
    {
        std::lock_guard<std::mutex> lock(gModelCacheMutex);
        auto it = gModelCache.find(path);
        if (it != gModelCache.end()) {
            it->second.refCount++;
            model = it->second.model;
            LOGI("Reusing cached model (refCount=%d)", it->second.refCount);
        } else {
            llama_model_params mparams = llama_model_default_params();
            mparams.n_gpu_layers = nGpuLayers;

            model = llama_load_model_from_file(path.c_str(), mparams);
            if (!model) {
                LOGE("Failed to load model: %s", path.c_str());
                return -1L;
            }

            SharedModel sm;
            sm.model    = model;
            sm.path     = path;
            sm.refCount = 1;
            gModelCache[path] = sm;
            LOGI("Model loaded: %s", path.c_str());
        }
    }

    // ── Create inference context ──────────────────────────────────────────
    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx       = static_cast<uint32_t>(contextSize > 0 ? contextSize : DEFAULT_N_CTX);
    cparams.n_batch     = DEFAULT_N_BATCH;
    cparams.n_threads   = DEFAULT_N_THREADS;

    // Quantize the KV cache to Q8_0 (1 byte/value) instead of the default f16
    // (2 bytes/value). This halves the KV cache footprint — for a 7B model at
    // n_ctx=4096 that's 1 GB instead of 2 GB. Q8_0 KV quality is
    // indistinguishable from f16 in practice; Q4_0 is more aggressive but has
    // noticeable quality loss on some shapes, so Q8_0 is the recommended
    // default for on-device inference on phones with < 16 GB RAM.
    cparams.type_k = GGML_TYPE_Q8_0;
    cparams.type_v = GGML_TYPE_Q8_0;

    // llama.cpp requires flash attention whenever V is quantized — the default
    // attention kernel only reads f16 V tensors, so without it context
    // creation fails with "V cache quantization requires flash_attn".
    // As of llama.cpp b8000+ the bool `flash_attn` was replaced with an enum
    // `flash_attn_type` (AUTO / DISABLED / ENABLED). Force ENABLED here for
    // the same reason the old bool was true.
    cparams.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;

    llama_context * ctx = llama_new_context_with_model(model, cparams);
    if (!ctx) {
        LOGE("Failed to create llama context");
        releaseModel(path);
        return -1L;
    }

    // ── Build sampler chain ───────────────────────────────────────────────
    llama_sampler * sampler = llama_sampler_chain_init(
            llama_sampler_chain_default_params());
    llama_sampler_chain_add(sampler,
            llama_sampler_init_top_k(DEFAULT_TOP_K));
    llama_sampler_chain_add(sampler,
            llama_sampler_init_top_p(DEFAULT_TOP_P, 1));
    llama_sampler_chain_add(sampler,
            llama_sampler_init_temp(DEFAULT_TEMP));
    llama_sampler_chain_add(sampler,
            llama_sampler_init_penalties(
                llama_n_ctx(ctx),   // penalty_last_n
                DEFAULT_REPEAT_PENALTY,
                0.0f,               // freq_penalty
                0.0f                // presence_penalty
            ));
    llama_sampler_chain_add(sampler,
            llama_sampler_init_dist(LLAMA_DEFAULT_SEED));

    // ── Register session ──────────────────────────────────────────────────
    auto session        = std::make_unique<LlamaSession>();
    session->model      = model;
    session->ctx        = ctx;
    session->sampler    = sampler;
    session->modelPath  = path;

    jlong handle = gNextHandle.fetch_add(1);
    {
        std::lock_guard<std::mutex> lock(gSessionMutex);
        gSessions[handle] = std::move(session);
    }

    LOGI("Session created: handle=%lld", (long long)handle);
    return handle;
}

/**
 * Run autoregressive inference, streaming each token back via a Java callback.
 *
 * @param handle         Session handle from nativeLoadModel.
 * @param prompt         Full prompt string (already formatted for the model).
 * @param maxNewTokens   Hard cap on generated tokens.
 * @param callbackObj    Java object implementing TokenCallback interface.
 *
 * The Java signature expected on callbackObj:
 *   interface TokenCallback {
 *     fun onToken(piece: String): Boolean  // return false to stop early
 *   }
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeRunInference(
        JNIEnv * env,
        jclass  /* cls */,
        jlong    handle,
        jstring  prompt,
        jint     maxNewTokens,
        jobject  callbackObj)
{
    LlamaSession * session = getSession(handle);
    if (!session) {
        LOGE("nativeRunInference: invalid handle %lld", (long long)handle);
        return;
    }

    session->cancelled.store(false);

    const char * rawPrompt = env->GetStringUTFChars(prompt, nullptr);
    std::string  promptStr(rawPrompt);
    env->ReleaseStringUTFChars(prompt, rawPrompt);

    llama_context * ctx   = session->ctx;
    llama_model   * model = session->model;

    // ── Tokenise prompt ───────────────────────────────────────────────────
    // b4631+ moved token ops off of llama_model and onto llama_vocab; we
    // grab the vocab once and reuse it for tokenize / eos / is-eog below.
    const llama_vocab * vocab = llama_model_get_vocab(model);
    (void) llama_n_vocab(vocab);   // keep tokeniser in cache (deprecated call)
    std::vector<llama_token> promptTokens(promptStr.size() + 8);
    int nTokens = llama_tokenize(
            vocab,
            promptStr.c_str(),
            static_cast<int32_t>(promptStr.size()),
            promptTokens.data(),
            static_cast<int32_t>(promptTokens.size()),
            /*add_special=*/true,
            /*parse_special=*/true);

    if (nTokens < 0) {
        // Buffer was too small — resize and retry
        promptTokens.resize(-nTokens + 8);
        nTokens = llama_tokenize(
                vocab,
                promptStr.c_str(),
                static_cast<int32_t>(promptStr.size()),
                promptTokens.data(),
                static_cast<int32_t>(promptTokens.size()),
                true, true);
    }

    if (nTokens <= 0) {
        LOGE("Tokenisation failed (nTokens=%d)", nTokens);
        return;
    }
    promptTokens.resize(nTokens);
    LOGI("Prompt tokenised: %d tokens", nTokens);

    // ── Prepare callback method IDs ───────────────────────────────────────
    jclass   cbClass   = env->GetObjectClass(callbackObj);
    jmethodID onToken  = env->GetMethodID(cbClass, "onToken",
                                          "(Ljava/lang/String;)Z");
    if (!onToken) {
        LOGE("TokenCallback.onToken method not found");
        return;
    }

    // ── Reset context & sampler ───────────────────────────────────────────
    // b8000+ replaced llama_kv_cache_clear(ctx) with a generic memory API.
    // Passing `true` wipes both metadata and the backing tensors so each
    // generate() call starts from a clean KV — same semantics as the old call.
    llama_memory_clear(llama_get_memory(ctx), /*data=*/true);
    llama_sampler_reset(session->sampler);

    // ── Decode prompt (prefill) ───────────────────────────────────────────
    //
    // Build the batch explicitly via llama_batch_init (b4631 flagged
    // llama_batch_get_one as "avoid using" — its null seq_id/logits arrays
    // triggered ggml_abort inside llama_decode on the S26).
    //
    // Critically, llama_decode aborts when batch.n_tokens exceeds n_batch
    // (the context's per-decode batch cap — 512 by default). Chat prompts
    // routinely exceed that (the system prompt alone can be 1000+ tokens),
    // so we split the prefill into n_batch-sized chunks and only request
    // logits on the very last token of the final chunk.
    const int32_t n_batch_cap = DEFAULT_N_BATCH;
    llama_batch batch = llama_batch_init(n_batch_cap, /*embd=*/0, /*n_seq_max=*/1);

    const int32_t promptLen = static_cast<int32_t>(promptTokens.size());
    int32_t n_past = 0;

    for (int32_t offset = 0; offset < promptLen; /* advanced below */) {
        const int32_t chunk = std::min(n_batch_cap, promptLen - offset);
        const bool    isLast = (offset + chunk) == promptLen;
        batch.n_tokens = chunk;
        for (int32_t i = 0; i < chunk; i++) {
            batch.token[i]     = promptTokens[offset + i];
            batch.pos[i]       = n_past + i;
            batch.n_seq_id[i]  = 1;
            batch.seq_id[i][0] = 0;
            // Only the very last prefill token needs logits (for sampling).
            batch.logits[i]    = isLast && (i == chunk - 1);
        }
        if (llama_decode(ctx, batch) != 0) {
            LOGE("Prompt decode failed at offset %d/%d", offset, promptLen);
            llama_batch_free(batch);
            return;
        }
        offset += chunk;
        n_past += chunk;
    }

    // ── Autoregressive generation loop ────────────────────────────────────
    //
    // The vocab-based API (b4631+) means eos / is_eog take a vocab* now.
    const llama_token eotToken = llama_token_eos(vocab);
    (void) eotToken;   // reserved for stopping heuristics
    int generated = 0;

    while (generated < maxNewTokens && !session->cancelled.load()) {
        llama_token token = llama_sampler_sample(session->sampler, ctx, -1);
        llama_sampler_accept(session->sampler, token);

        if (llama_token_is_eog(vocab, token)) break;

        // Convert token → string piece and fire callback
        std::string piece = tokenToPiece(ctx, token);
        if (!piece.empty()) {
            jstring jPiece = env->NewStringUTF(piece.c_str());
            jboolean cont  = env->CallBooleanMethod(callbackObj, onToken, jPiece);
            env->DeleteLocalRef(jPiece);
            if (!cont) break;   // Kotlin returned false → stop
        }

        // Decode the new token for next-step KV cache update. Reuse the
        // same allocated batch — we just rewrite index 0.
        batch.n_tokens      = 1;
        batch.token[0]      = token;
        batch.pos[0]        = n_past;
        batch.n_seq_id[0]   = 1;
        batch.seq_id[0][0]  = 0;
        batch.logits[0]     = true;

        if (llama_decode(ctx, batch) != 0) {
            LOGE("Decode failed at token %d", generated);
            break;
        }
        n_past++;
        generated++;
    }

    llama_batch_free(batch);
    LOGI("Generation complete: %d new tokens", generated);
}

/**
 * Format a {system?, user} pair into the model's own chat template, as stored
 * in the GGUF's `tokenizer.chat_template` metadata. Replaces the hardcoded
 * Gemma `<start_of_turn>` wrapping that was baking wrong control tokens into
 * every non-Gemma model's prompt (and producing incoherent replies).
 *
 * Returns the formatted prompt string, ready to be tokenised and fed to
 * nativeRunInference. If the model has no template metadata or something else
 * goes wrong, returns an empty string and the caller is expected to fall back
 * to a sensible default.
 */
JNIEXPORT jstring JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeApplyChatTemplate(
        JNIEnv * env,
        jclass  /* cls */,
        jlong    handle,
        jstring  systemPrompt,
        jstring  userPrompt)
{
    LlamaSession * session = getSession(handle);
    if (!session) return env->NewStringUTF("");

    const char * sys = env->GetStringUTFChars(systemPrompt, nullptr);
    const char * usr = env->GetStringUTFChars(userPrompt,   nullptr);
    std::string sysStr(sys ? sys : "");
    std::string usrStr(usr ? usr : "");
    env->ReleaseStringUTFChars(systemPrompt, sys);
    env->ReleaseStringUTFChars(userPrompt,   usr);

    // Build the message list. llama_chat_apply_template holds borrowed C
    // pointers into these strings, so they must outlive the call.
    std::vector<llama_chat_message> messages;
    if (!sysStr.empty()) {
        messages.push_back({"system", sysStr.c_str()});
    }
    messages.push_back({"user", usrStr.c_str()});

    // NULL name → default template
    const char * tmpl = llama_model_chat_template(session->model, nullptr);
    if (!tmpl) {
        LOGW("Model has no chat template metadata — returning empty");
        return env->NewStringUTF("");
    }

    // First call: ask llama.cpp how big a buffer we need (or let it write as
    // much as fits and return the full size if truncated). Docs suggest
    // 2× total chars of all messages as a safe starting capacity.
    const size_t totalChars = sysStr.size() + usrStr.size() + 256;
    std::vector<char> buf(totalChars * 2 + 128);
    int32_t n = llama_chat_apply_template(
        tmpl,
        messages.data(),
        messages.size(),
        /*add_ass=*/true,   // append the assistant-turn marker so the model picks up from there
        buf.data(),
        static_cast<int32_t>(buf.size()));

    if (n < 0) {
        LOGE("llama_chat_apply_template failed (n=%d)", n);
        return env->NewStringUTF("");
    }
    if (static_cast<size_t>(n) > buf.size()) {
        // Buffer was too small — reallocate exactly and retry.
        buf.assign(n + 1, 0);
        n = llama_chat_apply_template(
            tmpl, messages.data(), messages.size(), true,
            buf.data(), static_cast<int32_t>(buf.size()));
        if (n < 0) return env->NewStringUTF("");
    }

    return env->NewStringUTF(std::string(buf.data(), n).c_str());
}

/**
 * Signal the inference loop to stop at the next token boundary.
 * Non-blocking — returns immediately.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeStopInference(
        JNIEnv * /* env */,
        jclass  /* cls */,
        jlong    handle)
{
    LlamaSession * session = getSession(handle);
    if (session) session->cancelled.store(true);
}

/**
 * Returns a JSON object with model metadata.
 * {"name":"…","params":"7B","quant":"Q4_K_M","context_len":4096,"size_mb":4200}
 */
JNIEXPORT jstring JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeGetModelInfo(
        JNIEnv * env,
        jclass  /* cls */,
        jlong    handle)
{
    LlamaSession * session = getSession(handle);
    if (!session) return env->NewStringUTF("{}");

    llama_model * model = session->model;

    // Extract key metadata
    char descBuf[256] = {};
    llama_model_desc(model, descBuf, sizeof(descBuf));

    const int64_t nParams     = llama_model_n_params(model);
    const uint64_t sizeBytes  = llama_model_size(model);
    const int      nCtx       = static_cast<int>(llama_n_ctx(session->ctx));

    // Build JSON manually (avoid nlohmann/rapidjson dep)
    char json[1024];
    snprintf(json, sizeof(json),
        "{\"name\":\"%s\","
        "\"params\":\"%.0fB\","
        "\"size_mb\":%.0f,"
        "\"context_len\":%d,"
        "\"path\":\"%s\"}",
        descBuf,
        static_cast<double>(nParams) / 1e9,
        static_cast<double>(sizeBytes) / (1024.0 * 1024.0),
        nCtx,
        session->modelPath.c_str()
    );

    return env->NewStringUTF(json);
}

/**
 * Free all resources for this session handle.
 * If the model is shared, decrements its ref-count; frees it when it reaches 0.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeUnloadModel(
        JNIEnv * /* env */,
        jclass  /* cls */,
        jlong    handle)
{
    std::unique_ptr<LlamaSession> session;
    {
        std::lock_guard<std::mutex> lock(gSessionMutex);
        auto it = gSessions.find(handle);
        if (it == gSessions.end()) return;
        session = std::move(it->second);
        gSessions.erase(it);
    }

    LOGI("Unloading session handle=%lld", (long long)handle);

    if (session->sampler) {
        llama_sampler_free(session->sampler);
        session->sampler = nullptr;
    }
    if (session->ctx) {
        llama_free(session->ctx);
        session->ctx = nullptr;
    }
    // model is released via the ref-count cache
    releaseModel(session->modelPath);
}

/**
 * Clean shutdown — call from Application.onTerminate() or a DestroyCallback.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_llm_LlamaJNI_nativeDestroy(
        JNIEnv * /* env */,
        jclass  /* cls */)
{
    // Free any remaining sessions
    {
        std::lock_guard<std::mutex> lock(gSessionMutex);
        gSessions.clear();
    }
    // Free remaining cached models
    {
        std::lock_guard<std::mutex> lock(gModelCacheMutex);
        for (auto & kv : gModelCache) {
            llama_model_free(kv.second.model);
        }
        gModelCache.clear();
    }
    llama_backend_free();
    LOGI("llama backend destroyed");
}

} // extern "C"
