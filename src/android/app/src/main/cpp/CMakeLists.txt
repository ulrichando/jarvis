cmake_minimum_required(VERSION 3.22.1)

project("jarvis_native" LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD          17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS        OFF)

# ── Shared compile flags ──────────────────────────────────────────────────────

set(JARVIS_COMMON_FLAGS
    -Wall
    -Wextra
    -Wno-unused-parameter
    -Werror=return-type
    -fvisibility=hidden
    -fstack-protector-strong
    -D_XOPEN_SOURCE=700
    $<$<CONFIG:Release>:-O2 -D_FORTIFY_SOURCE=2>
    $<$<CONFIG:Debug>:-O0 -g>
)

# Android 15 / Samsung S24+ devices run with a 16 KB kernel page size. Shared
# objects compiled for 4 KB page alignment still load but get flagged at
# install time as "not 16 KB compatible" — the warning dialog the user sees.
# Passing -Wl,-z,max-page-size=16384 tells lld to align ELF LOAD segments to
# 16 KB, which is a no-op on 4 KB devices (load just reserves extra padding)
# but satisfies the stricter 16 KB loader.
set(JARVIS_LINK_FLAGS
    "-Wl,-z,max-page-size=16384"
    "-Wl,-z,common-page-size=16384"
)

# ════════════════════════════════════════════════════════════════════════════════
#  LIBRARY 1 — jarvis_pty  (PTY bridge + VT parser — unchanged)
# ════════════════════════════════════════════════════════════════════════════════

add_library(jarvis_pty SHARED
    pty_bridge.cpp
    vt_parser.cpp
)

target_compile_options(jarvis_pty PRIVATE ${JARVIS_COMMON_FLAGS})
target_link_options(jarvis_pty PRIVATE ${JARVIS_LINK_FLAGS})

target_link_libraries(jarvis_pty
    android
    log
)

# ════════════════════════════════════════════════════════════════════════════════
#  LIBRARY 2 — jarvis_llm  (llama.cpp JNI bridge)
#
#  Prerequisites (run once from repo root):
#    git submodule add https://github.com/ggml-org/llama.cpp \
#        clients/android/app/src/main/cpp/llama.cpp
#    git submodule update --init --recursive
#
#  The submodule is pinned to a release tag in .gitmodules.
#  The CMake variable LLAMA_BUILD_TESTS / EXAMPLES / SERVER are turned OFF
#  to avoid compiling the full CLI toolchain for Android.
# ════════════════════════════════════════════════════════════════════════════════

set(LLAMA_DIR "${CMAKE_CURRENT_SOURCE_DIR}/llama.cpp")

if(EXISTS "${LLAMA_DIR}/CMakeLists.txt")

    # ── llama.cpp build flags for Android ────────────────────────────────────

    # Disable everything we don't need
    set(LLAMA_BUILD_TESTS   OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_SERVER  OFF CACHE BOOL "" FORCE)
    set(BUILD_SHARED_LIBS   OFF CACHE BOOL "" FORCE)   # static only

    # Vulkan GPU acceleration — still OFF. ggml-vulkan on Android needs
    # shaderc + Vulkan-Headers plumbed into the NDK build which isn't trivial.
    # OpenCL below gives us Adreno GPU acceleration with a much simpler build.
    set(GGML_VULKAN OFF CACHE BOOL "" FORCE)

    # GPU acceleration — DISABLED.
    #
    # We previously linked OpenCL against a stubbed libOpenCL.so so the runtime
    # linker would resolve cl* symbols against the vendor's GPU driver. That
    # works in theory; in practice libOpenCL.so on every modern Samsung pulls
    # in libcutils.so, which Android 11+ namespace isolation blocks for app
    # code (see https://developer.android.com/about/versions/11/behavior-changes-11
    # under "Non-public native libraries restricted"). Result: libjarvis_llm.so
    # fails to dlopen at all — every GGUF load fails with
    #   dlopen failed: library "libcutils.so" not found
    # which kills the entire on-device backend.
    #
    # CPU-only via NEON SIMD is fast enough for the catalog's smaller models
    # (Qwen 0.5B / 1.5B / TinyLlama / Gemma 1B) and avoids the libcutils trap.
    # If we ever want GPU back, runtime dlopen("libOpenCL.so", RTLD_LAZY) with
    # a CPU fallback is the right pattern — not link-time linking.
    set(GGML_OPENCL OFF CACHE BOOL "" FORCE)
    set(JARVIS_HAS_OPENCL FALSE)
    message(STATUS "JARVIS: OpenCL OFF on all ABIs (CPU/NEON only — see comment)")

    # Use Android-optimised BLAS where available
    # (GGML_BLAS is OFF by default — llama.cpp uses its own SIMD kernels)
    set(GGML_BLAS OFF CACHE BOOL "" FORCE)

    add_subdirectory(${LLAMA_DIR} llama_build EXCLUDE_FROM_ALL)

    # ── JNI bridge library ────────────────────────────────────────────────────

    add_library(jarvis_llm SHARED
        llama_bridge.cpp
    )

    target_compile_options(jarvis_llm PRIVATE
        ${JARVIS_COMMON_FLAGS}
        # llama.cpp itself is noisy; suppress from bridge compilation
        -Wno-error
    )
    target_link_options(jarvis_llm PRIVATE ${JARVIS_LINK_FLAGS})

    target_include_directories(jarvis_llm PRIVATE
        ${LLAMA_DIR}/include    # llama.h
        ${LLAMA_DIR}/ggml/include # ggml.h, ggml-backend.h
    )

    # llama_bridge.cpp references ggml_backend_opencl_reg() at process init —
    # that direct symbol reference keeps the linker from stripping the static
    # ggml-opencl backend (CMake 3.22 on the Android SDK doesn't support the
    # LINK_LIBRARY:WHOLE_ARCHIVE generator expression).
    if (JARVIS_HAS_OPENCL)
        target_link_libraries(jarvis_llm
            llama                                  # llama.cpp core (static)
            ggml-opencl                            # Adreno GPU backend (static)
            ggml                                   # GGML backend meta
            android
            log
        )
        target_compile_definitions(jarvis_llm PRIVATE JARVIS_HAS_OPENCL=1)
    else()
        target_link_libraries(jarvis_llm
            llama
            ggml
            android
            log
        )
    endif()

    message(STATUS "JARVIS: jarvis_llm target configured")

else()
    # Submodule not yet initialised — emit a clear error instead of a silent no-op.
    message(WARNING
        "llama.cpp submodule not found at ${LLAMA_DIR}.\n"
        "Run:  git submodule update --init --recursive\n"
        "The jarvis_llm JNI library will NOT be built."
    )
endif()
