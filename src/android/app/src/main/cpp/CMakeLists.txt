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

    # GPU acceleration via OpenCL (Adreno on Snapdragon, Mali on Exynos).
    #
    # The previous attempt linked libjarvis_llm.so against the vendor's
    # libOpenCL.so directly, which dragged libcutils.so into our DT_NEEDED.
    # Android 11+ namespace isolation blocks that for app code:
    #   dlopen failed: library "libcutils.so" not found
    # (see https://developer.android.com/about/versions/11/behavior-changes-11
    # under "Non-public native libraries restricted").
    #
    # The fix — same pattern mybigday/llama.rn uses in production — is to link
    # against the Khronos OpenCL ICD loader built as a tiny libOpenCL.so stub.
    # The stub's only cl_* symbols are wrappers that dlopen the vendor driver
    # lazily, so libcutils stays out of our app's link graph. The manifest
    # already carries <uses-native-library libOpenCL.so/>, which is what lets
    # the ICD loader find /vendor/lib64/libOpenCL.so at runtime.
    #
    # The stub is produced by src/android/scripts/build-opencl-stub.sh and
    # lands in opencl-stub/${ANDROID_ABI}/libOpenCL.so. If it's missing we
    # fall back to CPU-only — so the build never fails because of it, and
    # the fallback matches the previous behaviour.
    set(OPENCL_STUB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/opencl-stub/${ANDROID_ABI}")
    set(OPENCL_STUB_LIB "${OPENCL_STUB_DIR}/libOpenCL.so")

    if(EXISTS "${OPENCL_STUB_LIB}")
        set(GGML_OPENCL ON  CACHE BOOL "" FORCE)
        set(JARVIS_HAS_OPENCL TRUE)
        message(STATUS "JARVIS: OpenCL ON for ${ANDROID_ABI} (stub: ${OPENCL_STUB_LIB})")
    else()
        set(GGML_OPENCL OFF CACHE BOOL "" FORCE)
        set(JARVIS_HAS_OPENCL FALSE)
        message(STATUS "JARVIS: OpenCL OFF for ${ANDROID_ABI} — stub not built yet")
        message(STATUS "        Run: src/android/scripts/build-opencl-stub.sh")
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
        # Point the linker at our ICD-loader stub before pulling in ggml-opencl.
        # ggml-opencl's CMake config adds "OpenCL" to its link line — resolving
        # that to our stub's libOpenCL.so (not the vendor's) is what keeps
        # libcutils out of DT_NEEDED.
        target_link_directories(jarvis_llm PRIVATE ${OPENCL_STUB_DIR})

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
