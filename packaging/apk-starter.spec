# Version comes from src/apk_starter/cli.py through packaging/ct-build.sh (rpmbuild --define 'apkver X').
# %changelog is appended by that script (one line per version).
%global pfx %{_prefix}/lib/apk-starter
# the private tree is not a set of system libraries: no stripping (it would break ART's boot image, the build has
# stripped the ELF files itself), no debuginfo, no provides from it
%global debug_package %{nil}
%global __strip /bin/true
%global __provides_exclude_from ^%{pfx}/
# no requires on the private libraries (lazy definition: the regex is made from the installed tree)
%define __requires_exclude %(sh %{_builddir}/apk-starter-%{version}/packaging/private-sonames.sh %{buildroot}%{pfx})

Name:           apk-starter
Version:        %{apkver}
Release:        1%{?dist}
Summary:        Run Android apps on the Linux desktop
License:        GPL-3.0-or-later
URL:            https://github.com/Jimbambuli/apk-starter
Source0:        apk-starter-%{version}.tar.xz
ExclusiveArch:  x86_64

%if 0%{?suse_version}
BuildRequires:  ninja
BuildRequires:  java-21-openjdk-devel
BuildRequires:  libxkbcommon-devel
# (no pkgconfig(openxr) in Tumbleweed: the headers from the bundle, see %build)
%else
BuildRequires:  ninja-build
BuildRequires:  pkgconfig(openxr)
BuildRequires:  java-25-openjdk-devel
%endif
BuildRequires:  git meson gcc gcc-c++ make patch autoconf automake libtool gettext zip unzip patchelf
BuildRequires:  python3 python3-gobject valgrind-devel wayland-devel glib2-devel xz
BuildRequires:  pkgconfig(egl) pkgconfig(libelf) pkgconfig(libunwind) pkgconfig(libbsd) pkgconfig(sdl2)
BuildRequires:  pkgconfig(sndfile) pkgconfig(icu-uc) pkgconfig(icu-i18n) pkgconfig(libcrypto) pkgconfig(expat)
BuildRequires:  pkgconfig(liblz4) pkgconfig(liblzma) pkgconfig(libcap) pkgconfig(zlib) pkgconfig(gtk4)
BuildRequires:  pkgconfig(vulkan) pkgconfig(fontconfig) pkgconfig(gl) pkgconfig(wayland-client)
BuildRequires:  pkgconfig(wayland-protocols) pkgconfig(libportal) pkgconfig(sqlite3) pkgconfig(libavcodec)
BuildRequires:  pkgconfig(libavfilter) pkgconfig(libavformat) pkgconfig(libavutil) pkgconfig(libswscale)
BuildRequires:  pkgconfig(libdrm) pkgconfig(gudev-1.0) pkgconfig(webkitgtk-6.0) pkgconfig(libsecret-1)
BuildRequires:  pkgconfig(pangoft2) pkgconfig(libwebp) pkgconfig(alsa)
Requires:       python3
Requires:       python3-gobject
%if 0%{?suse_version}
Requires:       typelib-1_0-Gtk-4_0
%else
Requires:       gtk4
%endif

%description
APK Starter starts Android apps (APK files) with Android Translation Layer,
without an emulator or virtual machine. Everything it needs (ART, ATL and
their libraries) is built from source and installed in /usr/lib/apk-starter.

%prep
%autosetup -n apk-starter-%{version}

%build
%if 0%{?suse_version}
x=$PWD/packaging/openxr; mkdir -p $x/pc
printf 'prefix=%s\nName: openxr\nDescription: OpenXR headers\nVersion: 1.1.65\nCflags: -I${prefix}/include\n' "$x" > $x/pc/openxr.pc
export PKG_CONFIG_PATH=$x/pc${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}
%endif
meson setup build native --prefix=%{pfx} --libdir=lib -Ddownload=false
meson compile -C build

%install
meson install -C build --no-rebuild --destdir %{buildroot}
packaging/install-extras.sh %{buildroot}

%files
%license LICENSE
%{pfx}
%{_bindir}/apk-starter
%{_datadir}/applications/io.github.Jimbambuli.ApkStarter.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.Jimbambuli.ApkStarter.svg
%{_datadir}/metainfo/io.github.Jimbambuli.ApkStarter.metainfo.xml
