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

    # OpenCL GPU acceleration — arm64 Android only (the emulator build has no
    # GPU driver to link against).
    #
    # Strategy: the Khronos ICD loader's clGetPlatformIDs() returns 0 platforms
    # on Samsung because /vendor/etc/OpenCL/vendors/*.icd isn't populated.
    # Instead we link directly against the vendor's libOpenCL.so (pulled once
    # from the device into cpp/opencl-stub/) — at runtime Android's dynamic
    # linker resolves cl* symbols against /vendor/lib64/libOpenCL.so which is
    # Qualcomm's driver implementation directly.
    #
    # Headers still come from Khronos OpenCL-Headers via FetchContent — they
    # are the authoritative C API declarations matching our .so ABI.
    if (CMAKE_ANDROID_ARCH_ABI STREQUAL "arm64-v8a")
        include(FetchContent)

        FetchContent_Declare(
            OpenCL-Headers
            GIT_REPOSITORY https://github.com/KhronosGroup/OpenCL-Headers.git
            GIT_TAG        v2024.10.24
        )
        FetchContent_MakeAvailable(OpenCL-Headers)

        set(OpenCL_INCLUDE_DIR "${opencl-headers_SOURCE_DIR}" CACHE PATH "" FORCE)
        set(OpenCL_LIBRARY
            "${CMAKE_CURRENT_SOURCE_DIR}/opencl-stub/libOpenCL.so"
            CACHE FILEPATH "" FORCE)

        set(GGML_OPENCL                    ON  CACHE BOOL "" FORCE)
        set(GGML_OPENCL_USE_ADRENO_KERNELS ON  CACHE BOOL "" FORCE)
        set(GGML_OPENCL_EMBED_KERNELS      ON  CACHE BOOL "" FORCE)
        message(STATUS "JARVIS: OpenCL ON (arm64, Adreno kernels + embedded)")
        set(JARVIS_HAS_OPENCL TRUE)
    else()
        set(GGML_OPENCL OFF CACHE BOOL "" FORCE)
        message(STATUS "JARVIS: OpenCL OFF (${CMAKE_ANDROID_ARCH_ABI} — CPU only)")
        set(JARVIS_HAS_OPENCL FALSE)
    endif()

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
