#!/usr/bin/env bash
#
# build-opencl-stub.sh
#
# Builds the Khronos OpenCL ICD loader as libOpenCL.so per Android ABI, to be
# linked against jarvis_llm. The ICD loader is a minimal stub whose cl_* symbols
# resolve to the device's vendor GPU driver at runtime via dlopen — not at link
# time. Doing it this way keeps libcutils.so (and the rest of the vendor driver
# chain) out of libjarvis_llm.so's DT_NEEDED entries, which is what Android 11+
# namespace isolation would otherwise block.
#
# One-time per NDK upgrade. Output lands at:
#   src/android/app/src/main/cpp/opencl-stub/<abi>/libOpenCL.so
#
# Prereqs:
#   - ANDROID_HOME pointing at an Android SDK install that has NDK 27.2.12479018
#     (matching ndkVersion in app/build.gradle.kts)
#   - cmake on PATH, or at $ANDROID_HOME/cmake/<version>/bin/cmake
#   - The OpenCL-Headers and OpenCL-ICD-Loader submodules initialised:
#       git submodule update --init --recursive \
#         src/android/app/src/main/cpp/third_party/OpenCL-Headers \
#         src/android/app/src/main/cpp/third_party/OpenCL-ICD-Loader
#
# Based on the approach used by mybigday/llama.rn (scripts/build-opencl.sh).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CPP_DIR="$ANDROID_DIR/app/src/main/cpp"
THIRD_PARTY="$CPP_DIR/third_party"
HEADERS_DIR="$THIRD_PARTY/OpenCL-Headers"
ICD_DIR="$THIRD_PARTY/OpenCL-ICD-Loader"
OUT_DIR="$CPP_DIR/opencl-stub"

NDK_VERSION="${JARVIS_NDK_VERSION:-27.2.12479018}"
ANDROID_PLATFORM="${JARVIS_ANDROID_PLATFORM:-android-26}"
ABIS=("arm64-v8a" "x86_64")

# ── Sanity checks ────────────────────────────────────────────────────────────

if [[ -z "${ANDROID_HOME:-}" ]]; then
    echo "error: ANDROID_HOME is not set" >&2
    exit 1
fi

NDK_ROOT="$ANDROID_HOME/ndk/$NDK_VERSION"
if [[ ! -d "$NDK_ROOT" ]]; then
    echo "error: NDK $NDK_VERSION not found at $NDK_ROOT" >&2
    echo "       available: $(ls "$ANDROID_HOME/ndk" 2>/dev/null | tr '\n' ' ')" >&2
    echo "       install:   \$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager \"ndk;$NDK_VERSION\"" >&2
    exit 1
fi
TOOLCHAIN_FILE="$NDK_ROOT/build/cmake/android.toolchain.cmake"

if [[ ! -d "$HEADERS_DIR/CL" || ! -f "$ICD_DIR/CMakeLists.txt" ]]; then
    echo "error: Khronos submodules missing. Run:" >&2
    echo "  git submodule update --init --recursive \\" >&2
    echo "    $HEADERS_DIR \\" >&2
    echo "    $ICD_DIR" >&2
    exit 1
fi

# Resolve cmake — prefer the one bundled with the Android SDK so the toolchain
# file and the cmake version line up on the NDK's expectations.
if command -v cmake >/dev/null 2>&1; then
    CMAKE_BIN="$(command -v cmake)"
elif [[ -d "$ANDROID_HOME/cmake" ]]; then
    LATEST_CMAKE="$(ls "$ANDROID_HOME/cmake" | sort -V | tail -n1)"
    CMAKE_BIN="$ANDROID_HOME/cmake/$LATEST_CMAKE/bin/cmake"
else
    echo "error: cmake not found on PATH or in \$ANDROID_HOME/cmake" >&2
    exit 1
fi

N_JOBS="$(nproc 2>/dev/null || sysctl -n hw.logicalcpu 2>/dev/null || echo 4)"

# ── Build per ABI ────────────────────────────────────────────────────────────

build_abi() {
    local abi="$1"
    local build_dir="$ICD_DIR/build/$abi"
    local out_abi_dir="$OUT_DIR/$abi"

    echo "==> Building libOpenCL.so for $abi"
    rm -rf "$build_dir"
    mkdir -p "$build_dir" "$out_abi_dir"

    "$CMAKE_BIN" -S "$ICD_DIR" -B "$build_dir" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN_FILE" \
        -DANDROID_ABI="$abi" \
        -DANDROID_PLATFORM="$ANDROID_PLATFORM" \
        -DANDROID_STL=c++_shared \
        -DOPENCL_ICD_LOADER_HEADERS_DIR="$HEADERS_DIR"

    "$CMAKE_BIN" --build "$build_dir" --config Release -j "$N_JOBS"

    local built="$build_dir/libOpenCL.so"
    if [[ ! -f "$built" ]]; then
        echo "error: expected $built not produced" >&2
        return 1
    fi

    cp "$built" "$out_abi_dir/libOpenCL.so"
    echo "    -> $out_abi_dir/libOpenCL.so"
}

for abi in "${ABIS[@]}"; do
    build_abi "$abi"
done

echo
echo "Done. Stubs:"
for abi in "${ABIS[@]}"; do
    echo "  $OUT_DIR/$abi/libOpenCL.so"
done
echo
echo "Next: rebuild the app. CMake will auto-detect the stubs and enable GGML_OPENCL."
