#!/bin/bash
# build-bundle.sh AOSP_TREE OUT_DIR: builds the Digitalis bundle for the Flatpak module «digitalis» (x86_64 only)
# from a Digitalis AOSP tree (repo init -u https://github.com/DigitalisX64/manifest -b android16, see Digitalis'
# README; about 170 GB) and writes OUT_DIR/digitalis-bundle-<binary_translation commit>.tar.gz.
#
# The tree must have the commits named in PINNED below checked out (the Jimbambuli forks carry the changes ATL
# needs on a glibc host). The bundle is Digitalis' own prebuilt package (scripts/build-and-package-prebuilts.sh:
# libberberis_arm64.so, the proxy libraries and the ARM64 guest system) plus the licence texts of the projects it
# is built from (NOTICES/) and the exact source revisions (BUILD-INFO: repo manifest -r).
set -euo pipefail
TREE=$(realpath "$1"); OUT=$(realpath "$2")

# project path, commit
PINNED=(
	"frameworks/libs/binary_translation e199616"
	"digitalis 81c34d5"
)
# project files with the licences of what the bundle contains
NOTICES=(
	digitalis/LICENSE                       # Digitalis, Berberis, native_bridge_support (Apache-2.0)
	bionic/libc/NOTICE bionic/linker/NOTICE
	external/zlib/LICENSE external/icu/LICENSE external/boringssl/NOTICE external/sqlite/LICENSE
	external/libcxx/LICENSE.TXT external/libcxxabi/LICENSE.TXT external/compiler-rt/LICENSE.TXT
	libnativehelper/NOTICE system/libbase/NOTICE system/logging/liblog/NOTICE system/core/libcutils/NOTICE
	frameworks/native/NOTICE
)
# the ARM64 guest modules (NATIVE_BRIDGE_PRODUCT_PACKAGES of native_bridge_support.mk without RenderScript);
# build-and-package-prebuilts.sh builds them only with --full (a whole system image)
GUEST_LIBS="libandroidicu.bootstrap libcompiler_rt libcrypto libcutils libdl.bootstrap libdl_android.bootstrap
	libicu.bootstrap liblog libsqlite libssl libstdc++ libsync libutils libz"
GUEST_STUBS="libaaudio libamidi libandroid libandroid_runtime libbinder_ndk libc libcamera2ndk libEGL libGLESv1_CM
	libGLESv2 libGLESv3 libjnigraphics libm libmediandk libnativehelper libnativewindow libneuralnetworks libOpenMAXAL
	libOpenSLES libvulkan libwebviewchromium_plat_support"

cd "$TREE"
for p in "${PINNED[@]}"; do
	set -- $p
	have=$(git -C "$1" rev-parse --short=7 HEAD)
	[ "$have" = "$2" ] || { echo "$1 is at $have, the bundle is pinned to $2" >&2; exit 1; }
done

MODULES="libnative_bridge_vdso.native_bridge native_bridge_guest_app_process.native_bridge native_bridge_guest_linker.native_bridge"
for m in $GUEST_LIBS; do MODULES="$MODULES $m.native_bridge"; done
for m in $GUEST_STUBS; do MODULES="$MODULES libnative_bridge_guest_$m.native_bridge"; done

# userdebug: the guest linker then honours LD_CONFIG_FILE, which Berberis sets for a system tree outside /system
export TARGET_PRODUCT=sdk_phone64_x86_64_digitalis TARGET_RELEASE=trunk_staging TARGET_BUILD_VARIANT=userdebug \
	BUILD_USERNAME=digitalis-build
bash digitalis/scripts/build-and-package-prebuilts.sh --username digitalis-build
set +u; source build/envsetup.sh >/dev/null; set -u
m $MODULES
bash digitalis/scripts/build-and-package-prebuilts.sh --collect-only --username digitalis-build
bash digitalis/scripts/verify-digitalis-prebuilts.sh

PKG=digitalis/dist/digitalis-prebuilts
# the host side (libberberis_arm64.so, the proxies) needs the platform's libc++ next to it: on a device it is in
# /system/lib64, on a glibc host with bionic_translation nothing else provides it
cp "$(get_build_var PRODUCT_OUT)/system/lib64/libc++.so" "$PKG/system/lib64/"
cp digitalis/LICENSE "$PKG/LICENSE"
cat > "$PKG/NOTICE" <<NOTICE
Digitalis bundle for APK Starter (ATL native bridge for arm64-v8a apps on x86_64)

Digitalis: https://github.com/DigitalisX64/digitalis, licensed under the Apache License 2.0 (LICENSE).
Built from the Android Open Source Project (Berberis binary translator, native_bridge_support, bionic and
the libraries in system/lib64/arm64) with the changes in https://github.com/Jimbambuli/digitalis and
https://github.com/Jimbambuli/platform_frameworks_libs_binary_translation, branch atl-digitalis-3.
Exact source revisions: BUILD-INFO. Licences of the projects the files are built from: NOTICES/.
NOTICE
mkdir -p "$PKG/NOTICES"
for f in "${NOTICES[@]}"; do cp "$f" "$PKG/NOTICES/$(echo "$f" | tr / _)"; done
"${REPO:-repo}" manifest -r > "$PKG/BUILD-INFO"
NAME=digitalis-bundle-$(git -C frameworks/libs/binary_translation rev-parse --short=7 HEAD)
mkdir -p "$OUT"
# reproducible archive: fixed order, owner and times
tar -C digitalis/dist --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -cf - digitalis-prebuilts \
	| gzip -n > "$OUT/$NAME.tar.gz"
(cd "$OUT" && sha256sum "$NAME.tar.gz" | tee "$NAME.tar.gz.sha256")
