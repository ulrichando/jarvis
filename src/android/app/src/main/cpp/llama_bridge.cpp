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
#include <string>
#include <unordered_map>
#include <vector>

// llama.cpp public C API
#include "llama.cpp/include/llama.h"
#include "llama.cpp/include/ggml.h"

#define TAG     "JarvisLlama"
#define LOGI(...)  __android_log_print(ANDROID_LOG_INFO,  TAG, __VA_ARGS__)
#define LOGW(...)  __android_log_print(ANDROID_LOG_WARN,  TAG, __VA_ARGS__)
#define LOGE(...)  __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

// ── Constants ────────────────────────────────────────────────────────────────

static constexpr int  DEFAULT_N_CTX      = 2048;
static constexpr int  DEFAULT_N_THREADS  = 4;
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
static std::string tokenToPiece(const llama_context * ctx, llama_token token) {
    const llama_model * model = llama_get_model(ctx);
    char buf[256];
    int  n = llama_token_to_piece(model, token, buf, sizeof(buf), 0, true);
    if (n < 0) {
        // Buffer too small — use a heap-allocated buffer
        std::vector<char> heap(-(n) + 1);
        llama_token_to_piece(model, token, heap.data(),
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
    LOGI("llama backend initialised — build %d", LLAMA_BUILD_NUMBER);
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
    cparams.flash_attn  = true;   // available on Android GPU via Vulkan

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
    const int vocab = llama_n_vocab(model);
    std::vector<llama_token> promptTokens(promptStr.size() + 8);
    int nTokens = llama_tokenize(
            model,
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
                model,
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
    llama_kv_cache_clear(ctx);
    llama_sampler_reset(session->sampler);

    // ── Decode prompt (prefill) ───────────────────────────────────────────
    llama_batch batch = llama_batch_get_one(
            promptTokens.data(), static_cast<int32_t>(promptTokens.size()));

    if (llama_decode(ctx, batch) != 0) {
        LOGE("Prompt decode failed");
        return;
    }

    // ── Autoregressive generation loop ────────────────────────────────────
    const llama_token eotToken = llama_token_eos(model);
    int generated = 0;

    while (generated < maxNewTokens && !session->cancelled.load()) {
        llama_token token = llama_sampler_sample(session->sampler, ctx, -1);
        llama_sampler_accept(session->sampler, token);

        if (llama_token_is_eog(model, token)) break;

        // Convert token → string piece and fire callback
        std::string piece = tokenToPiece(ctx, token);
        if (!piece.empty()) {
            jstring jPiece = env->NewStringUTF(piece.c_str());
            jboolean cont  = env->CallBooleanMethod(callbackObj, onToken, jPiece);
            env->DeleteLocalRef(jPiece);
            if (!cont) break;   // Kotlin returned false → stop
        }

        // Decode the new token for next-step KV cache update
        llama_batch nextBatch = llama_batch_get_one(&token, 1);
        if (llama_decode(ctx, nextBatch) != 0) {
            LOGE("Decode failed at token %d", generated);
            break;
        }
        generated++;
    }

    LOGI("Generation complete: %d new tokens", generated);
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
