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

# ════════════════════════════════════════════════════════════════════════════════
#  LIBRARY 1 — jarvis_pty  (PTY bridge + VT parser — unchanged)
# ════════════════════════════════════════════════════════════════════════════════

add_library(jarvis_pty SHARED
    pty_bridge.cpp
    vt_parser.cpp
)

target_compile_options(jarvis_pty PRIVATE ${JARVIS_COMMON_FLAGS})

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

    # Vulkan GPU acceleration (available on most Android 10+ devices)
    # Disabled if Vulkan headers are not found — falls back to CPU automatically.
    find_package(Vulkan QUIET)
    if(Vulkan_FOUND)
        set(GGML_VULKAN ON CACHE BOOL "" FORCE)
        message(STATUS "JARVIS: Vulkan found — GPU acceleration enabled")
    else()
        set(GGML_VULKAN OFF CACHE BOOL "" FORCE)
        message(STATUS "JARVIS: Vulkan not found — CPU inference only")
    endif()

    # OpenCL disabled (Vulkan preferred, opencl causes conflicts on some devices)
    set(GGML_OPENCL OFF CACHE BOOL "" FORCE)

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

    target_include_directories(jarvis_llm PRIVATE
        ${LLAMA_DIR}/include    # llama.h
        ${LLAMA_DIR}/ggml/include # ggml.h, ggml-backend.h
    )

    target_link_libraries(jarvis_llm
        llama       # llama.cpp core (static)
        ggml        # GGML backend (static)
        android
        log
    )

    if(Vulkan_FOUND)
        target_link_libraries(jarvis_llm Vulkan::Vulkan)
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
