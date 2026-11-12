# Native build of APK Starter without Flatpak: the same components at the same commits as
# io.github.Jimbambuli.ApkStarter.yml (subprojects/*.wrap, tools/check-native-pins.py keeps them equal).
#
#   meson setup build --prefix ~/.local/opt/apk-starter && meson install -C build
#
# The components need each other installed while they are configured (ATL reads art-standalone.pc and
# looks for libdl_bio at configure time), so they cannot be plain subproject() calls in one configure run.
# build.py builds them one after the other into a staging tree inside the build directory, in the order of
# the Flatpak manifest; `meson install` copies that tree into the prefix.
project('apk-starter-native', version: '0.8.7', meson_version: '>=1.1', default_options: ['libdir=lib'])

if get_option('libdir') != 'lib'
  error('the starter expects its libraries in <prefix>/lib: configure with -Dlibdir=lib (the default here)')
endif

# What the distribution has to provide (README «Native build»): checked here, so a missing package is
# named before the long build starts.
deps = [
  # bionic_translation
  'egl', 'libelf', 'libbsd',
  # libopensles-standalone
  'sdl2', 'sndfile',
  # art_standalone
  'icu-uc', 'icu-i18n', 'libcrypto', 'expat', 'liblz4', 'liblzma', 'libcap', 'zlib',
  # android_translation_layer
  'vulkan', 'openxr', 'fontconfig', 'gl', 'wayland-client', 'libportal', 'sqlite3', 'libavfilter', 'libavformat',
  'libavutil', 'libswscale', 'libdrm', 'gudev-1.0', 'webkitgtk-6.0', 'libsecret-1', 'pangoft2', 'libwebp', 'alsa',
]
versioned = {'libunwind': '>=1.8.0', 'gtk4': '>=4.14', 'wayland-protocols': '>=1.12', 'libavcodec': '>=59'}
foreach d : deps
  dependency(d)
endforeach
foreach d, v : versioned
  dependency(d, version: v)
endforeach

foreach p : ['git', 'make', 'patch', 'autoreconf', 'libtoolize', 'javac', 'java', 'jar', 'ninja', 'wayland-scanner',
             'msgfmt', 'zip', 'glib-genmarshal', 'gdbus-codegen', 'glib-compile-resources']
  find_program(p)
endforeach

python = import('python').find_installation('python3', modules: ['gi'])

jdk = get_option('jdk')
if jdk == ''
  jdk = run_command(python, '-c',
    'import os, shutil; print(os.environ.get("JAVA_HOME") or os.path.dirname(os.path.dirname(os.path.realpath(shutil.which("javac")))))',
    check: true).stdout().strip()
endif
if not import('fs').is_file(jdk / 'include' / 'jni.h')
  error('no JDK at ' + jdk + ' (no include/jni.h): set -Djdk=DIR or JAVA_HOME')
endif

# Fetch the components at the pinned commits (does nothing for those already there).
run_command(find_program('meson'), 'subprojects', 'download', '--sourcedir', meson.project_source_root(),
            check: true)

stage = meson.current_build_dir() / 'stage'
custom_target('components',
  output: 'components.stamp',
  command: [python, files('build.py'), 'build', '--source', meson.project_source_root(), '--build',
            meson.current_build_dir(), '--stage', stage, '--prefix', get_option('prefix'), '--jdk', jdk,
            '--stamp', '@OUTPUT@'],
  build_by_default: true,
  build_always_stale: true,
  console: true)

meson.add_install_script(python, files('build.py'), 'install', '--stage', stage, '--prefix',
                         get_option('prefix'))
