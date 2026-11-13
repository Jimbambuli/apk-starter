#!/usr/bin/env python3
"""Build APK Starter's components one after the other into a staging tree, as flatpak-builder does with the
modules of io.github.Jimbambuli.ApkStarter.yml, and copy that tree into the prefix (native/meson.build).

  build.py build --source NATIVE_DIR --build BUILD_DIR --stage STAGE --prefix PREFIX --jdk JDK --stamp FILE
  build.py install --stage STAGE --prefix PREFIX        (meson install; honours DESTDIR)
  build.py fetch-jars --source NATIVE_DIR --dest DIR    (packaging/collect-sources.sh)

Each component is installed into STAGE/PREFIX; the next one finds it there (CPATH, LIBRARY_PATH, pkg-config files
rewritten to the staging tree). A component is built again only when its commit, the prefix or the JDK changed.
"""
import argparse
import configparser
import hashlib
import re
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# The configure options of wolfSSL in the Flatpak manifest (module «wolfssl»).
WOLFSSL_OPTS = ["--enable-shared", "--disable-opensslall", "--disable-opensslextra", "--enable-aescbc-length-checks",
                "--enable-curve25519", "--enable-ed25519", "--enable-ed25519-stream", "--enable-oldtls",
                "--enable-base64encode", "--enable-tlsx", "--enable-scrypt", "--disable-examples", "--enable-crl",
                "--enable-jni", "--enable-sessioncerts"]


def log(msg: str) -> None:
    print(f"build.py: {msg}", flush=True)


def run(cmd: list, env: dict, cwd: Path | None = None) -> None:
    log(" ".join(map(str, cmd)))
    subprocess.run([str(c) for c in cmd], env=env, cwd=cwd, check=True)


def wrap(source: Path, name: str) -> tuple[Path, str]:
    """The checkout of subprojects/NAME.wrap and its pinned commit; refuses a checkout at another commit."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(source / "subprojects" / f"{name}.wrap", encoding="utf-8")
    d = source / "subprojects" / cp["wrap-git"]["directory"]
    rev = cp["wrap-git"]["revision"]
    pinned = d / ".pinned-revision"                  # the offline source bundle (packaging/) carries no .git
    if pinned.is_file():
        head = pinned.read_text(encoding="utf-8").strip()
    else:
        head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != rev:
        sys.exit(f"build.py: {d} is at {head or 'nothing'}, {name}.wrap wants {rev}: "
                 f"run `meson subprojects update --reset` in {source}")
    return d, rev


def read_jars(source: Path) -> list[tuple[str, str, str]]:
    """tool-jars.txt: NAME URL SHA256 per line (the jars the manifest's module jni-upcall-deps ships)."""
    out = []
    for line in (source / "tool-jars.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, url, sha = line.split()
            out.append((name, url, sha))
    return out


def fetch_jar(url: str, sha: str, dest: Path, local: Path | None = None) -> None:
    """DEST from the bundle's jars directory (LOCAL, offline) or else from URL; always checked against SHA."""
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
        return
    if local is not None and local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == sha:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dest)
        return
    log(f"download {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        sys.exit(f"build.py: {url}: sha256 mismatch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def apply_patch(tree: Path, patch: Path, level: str, env: dict) -> None:
    """Apply once: skipped when the patch is in already (it reverses cleanly)."""
    rev = subprocess.run(["patch", level, "-R", "--dry-run", "-s", "-f", "-i", patch], cwd=tree,
                         capture_output=True)
    if rev.returncode != 0:
        run(["patch", level, "-N", "-i", patch], env, cwd=tree)


def rewrite_pc(stage_prefix: Path, prefix: str, pcdir: Path) -> None:
    """The pkg-config files of the staging tree, pointing into it (ATL takes art's libdir from art-standalone.pc)."""
    pcdir.mkdir(parents=True, exist_ok=True)
    for pc in (stage_prefix / "lib/pkgconfig").glob("*.pc"):
        lines = [f"prefix={stage_prefix}" if line.strip() == f"prefix={prefix}" else line
                 for line in pc.read_text(encoding="utf-8").splitlines()]
        (pcdir / pc.name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def meson_component(src: Path, bdir: Path, prefix: str, stage: Path, env: dict, opts: list[str] = ()) -> None:
    if not (bdir / "build.ninja").is_file():
        run(["meson", "setup", bdir, src, f"--prefix={prefix}", "--libdir=lib", "--buildtype=release", *opts], env)
    run(["meson", "compile", "-C", bdir], env)
    run(["meson", "install", "-C", bdir, "--destdir", stage, "--no-rebuild"], env)


def build(a: argparse.Namespace) -> None:
    source, bld, stage, prefix, jdk = Path(a.source), Path(a.build), Path(a.stage), a.prefix, a.jdk
    repo = source.parent                             # the starter's own repository
    sp = stage / prefix.lstrip("/")                  # STAGE/PREFIX
    jobs = str(len(os.sched_getaffinity(0)))
    stamps, pcdir = bld / "stamps", bld / "pkgconfig"
    stamps.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        JAVA_HOME=jdk,
        PATH=os.pathsep.join([f"{jdk}/bin", str(sp / "bin"), env.get("PATH", "")]),
        CPATH=str(sp / "include"),
        LIBRARY_PATH=str(sp / "lib"),
        LD_LIBRARY_PATH=str(sp / "lib"),
        PKG_CONFIG_PATH=os.pathsep.join(filter(None, [str(pcdir), env.get("PKG_CONFIG_PATH")])),
    )

    def step(name: str, rev: str, work) -> None:
        key = f"{rev} {prefix} {jdk}\n"
        stamp = stamps / name
        if stamp.is_file() and stamp.read_text(encoding="utf-8") == key:
            log(f"{name}: up to date")
            return
        log(f"{name}: building {rev}")
        work()
        rewrite_pc(sp, prefix, pcdir)
        stamp.write_text(key, encoding="utf-8")

    wolfssl, wolfssl_rev = wrap(source, "wolfssl")
    bionic, bionic_rev = wrap(source, "bionic_translation")
    opensles, opensles_rev = wrap(source, "libopensles-standalone")
    art, art_rev = wrap(source, "art_standalone")
    atl, atl_rev = wrap(source, "android_translation_layer")
    doctor, doctor_rev = wrap(source, "atl-doctor")

    def do_wolfssl() -> None:
        apply_patch(wolfssl, repo / "build-aux/wolfssl-ticket-decline.patch", "-p1", env)
        if not (wolfssl / "configure").is_file():
            run(["./autogen.sh"], env, cwd=wolfssl)
        b = bld / "wolfssl"
        b.mkdir(exist_ok=True)
        run([wolfssl / "configure", f"--prefix={prefix}", f"--libdir={prefix}/lib", *WOLFSSL_OPTS], env, cwd=b)
        run(["make", f"-j{jobs}"], env, cwd=b)
        run(["make", f"DESTDIR={stage}", "install"], env, cwd=b)

    def do_art() -> None:
        run(["make", f"-j{jobs}", f"____PREFIX={prefix}", "____LIBDIR=lib"], env, cwd=art)
        # the .pc file was made with the real prefix above; install copies it as it is
        run(["make", f"____PREFIX={sp}", "____LIBDIR=lib", "install"], env, cwd=art)
        apply_patch(art, doctor / "tools/openjdkjvmti-gcc14.diff", "-p0", env)
        run(["bash", doctor / "tools/build-jvmti-plugin.sh", art, sp], dict(env, J=jobs))
        shutil.copy2(art / "jvmti-out/libopenjdkjvmti.so", sp / "lib/art/libopenjdkjvmti.so")
        # the script links with -rpath <staging>/lib/art; the plugin sits in that directory: $ORIGIN says the same
        run(["patchelf", "--set-rpath", "$ORIGIN", sp / "lib/art/libopenjdkjvmti.so"], env)

    def do_atl() -> None:
        jars = {}
        for name, url, sha in read_jars(source):
            jars[name] = bld / "jars" / name
            fetch_jar(url, sha, jars[name], source / "jars" / name)
        # inside its source tree, as flatpak-builder does: ninja runs a javac command line with every source file,
        # and with paths relative to a build directory elsewhere it passes the kernel's 128 KiB limit (E2BIG)
        bdir = atl / f"_build-{hashlib.sha256(str(bld).encode()).hexdigest()[:8]}"
        meson_component(atl, bdir, prefix, stage, env, [
            "-Ddownload_tool_deps=false", f"-Dasm_jar_path={jars['asm.jar']}",
            f"-Dasm_tree_jar_path={jars['asm-tree.jar']}", f"-Dgetopt_jar_path={jars['java-getopt.jar']}"])

    def do_doctor() -> None:
        b = bld / "atl-doctor"
        if not (b / "build.ninja").is_file():
            run(["meson", "setup", b, doctor / "agent", f"--prefix={prefix}", f"-Djdk={jdk}"], env)
        run(["ninja", "-C", b, "libatl_doctor_agent.so", "atl-doctor-tracecount"], env)
        (sp / "lib/atl-doctor").mkdir(parents=True, exist_ok=True)
        for f in ("libatl_doctor_agent.so", "atl-doctor-tracecount"):
            shutil.copy2(b / f, sp / "lib/atl-doctor" / f)
        share = sp / "share/apk-starter"
        shutil.rmtree(share / "atl_doctor", ignore_errors=True)
        shutil.copytree(doctor / "atl_doctor", share / "atl_doctor", ignore=shutil.ignore_patterns("__pycache__"))
        lic = sp / "share/licenses/io.github.Jimbambuli.ApkStarter"
        lic.mkdir(parents=True, exist_ok=True)
        shutil.copy2(doctor / "LICENSE", lic / "atl-doctor-LICENSE")
        code = ("import pathlib, sys; from atl_doctor.sources.stubs import build_stub_list, save_stub_tsv; "
                "jars = sorted(pathlib.Path(sys.argv[2]).glob('*.jar')); "
                "save_stub_tsv(build_stub_list(pathlib.Path(sys.argv[1]), jars), pathlib.Path(sys.argv[3]))")
        run([sys.executable, "-c", code, atl, sp / "lib/java/dex/android_translation_layer", share / "stubs.tsv"],
            dict(env, PYTHONPATH=str(share)))

    step("wolfssl", wolfssl_rev, do_wolfssl)
    step("bionic_translation", bionic_rev, lambda: meson_component(bionic, bld / "bionic", prefix, stage, env))
    step("libopensles-standalone", opensles_rev,
         lambda: meson_component(opensles, bld / "opensles", prefix, stage, env))
    step("art_standalone", art_rev, do_art)
    step("android_translation_layer", atl_rev, do_atl)
    step("atl-doctor", f"{doctor_rev} {atl_rev}", do_doctor)
    if a.strip:
        strip_tree(sp, env)
    install_starter(repo, sp, prefix, jdk, env)
    Path(a.stamp).write_text("ok\n", encoding="utf-8")


def strip_tree(sp: Path, env: dict) -> None:
    """Without debug information (meson option strip, the default): every ELF library and program of the staging
    tree; ART's compiled boot image (.oat/.odex/.art) stays as it is. Done again each build, it is quick."""
    for f in sorted(sp.rglob("*")):
        if f.is_symlink() or not f.is_file() or f.suffix in (".oat", ".odex", ".vdex", ".art"):
            continue
        with open(f, "rb") as fh:
            if fh.read(4) != b"\x7fELF":
                continue
        subprocess.run(["strip", "--strip-unneeded", f], env=env, check=True)


def desktop_exec_arg(s: str) -> str:
    """An Exec argument quoted as the Desktop Entry spec says (as portal.py does for the apps' entries)."""
    return '"' + re.sub(r'([\\"`$])', r'\\\1', s).replace("\\", "\\\\").replace("%", "%%") + '"'


def install_starter(repo: Path, sp: Path, prefix: str, jdk: str, env: dict) -> None:
    """The manifest's module «apk-starter», every time (it takes a second); menu entry with absolute paths."""
    share = sp / "share/apk-starter"
    (sp / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "apk-starter", sp / "bin/apk-starter")
    shutil.rmtree(share / "apk_starter", ignore_errors=True)
    shutil.copytree(repo / "src/apk_starter", share / "apk_starter", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(repo / "data/build-info", share / "build-info")
    shutil.copyfile(Path(jdk) / "lib/security/cacerts", share / "cacerts")
    for lang in (repo / "po/LINGUAS").read_text(encoding="utf-8").split():
        d = sp / "share/locale" / lang / "LC_MESSAGES"
        d.mkdir(parents=True, exist_ok=True)
        run(["msgfmt", "--check", "-o", d / "apk-starter.mo", repo / "po" / f"{lang}.po"], env)
    app = "io.github.Jimbambuli.ApkStarter"
    icon = sp / "share/icons/hicolor/scalable/apps" / f"{app}.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "data/icons" / f"{app}.svg", icon)
    for kind, src, dest in (("--desktop", "desktop.in", f"applications/{app}.desktop"),
                            ("--xml", "metainfo.xml.in", f"metainfo/{app}.metainfo.xml")):
        out = sp / "share" / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["msgfmt", kind, f"--template={repo / 'data' / f'{app}.{src}'}", "-d", repo / "po", "-o", out], env)
    # not in a Flatpak: the desktop has neither the command in PATH nor the icon in its theme
    entry = sp / f"share/applications/{app}.desktop"
    real_icon = f"{prefix}/share/icons/hicolor/scalable/apps/{app}.svg"
    lines = [f"Exec={desktop_exec_arg(prefix + '/bin/apk-starter')} %f" if line.startswith("Exec=") else
             f"Icon={real_icon}" if line.startswith("Icon=") else line
             for line in entry.read_text(encoding="utf-8").splitlines()]
    entry.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install(a: argparse.Namespace) -> None:
    sp = Path(a.stage) / a.prefix.lstrip("/")
    dest = Path(os.environ.get("MESON_INSTALL_DESTDIR_PREFIX", a.prefix))
    log(f"install {sp} -> {dest}")
    # over an earlier install: each file and link is replaced (copytree cannot overwrite a symlink)
    for root, _dirs, files in os.walk(sp):
        out = dest / Path(root).relative_to(sp)
        out.mkdir(parents=True, exist_ok=True)
        for f in files:
            src, dst = Path(root) / f, out / f
            if dst.is_symlink() or src.is_symlink():
                dst.unlink(missing_ok=True)
            if src.is_symlink():
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst)


def fetch_jars(a: argparse.Namespace) -> None:
    """For the offline source bundle: every jar of tool-jars.txt into DEST (sha256 checked)."""
    source = Path(a.source)
    for name, url, sha in read_jars(source):
        fetch_jar(url, sha, Path(a.dest) / name)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    for opt in ("--source", "--build", "--stage", "--prefix", "--jdk", "--stamp"):
        b.add_argument(opt, required=True)
    b.add_argument("--strip", action="store_true", help="install without debug information")
    i = sub.add_parser("install")
    i.add_argument("--stage", required=True)
    i.add_argument("--prefix", required=True)
    f = sub.add_parser("fetch-jars")
    f.add_argument("--source", required=True)
    f.add_argument("--dest", required=True)
    a = p.parse_args()
    {"build": build, "install": install, "fetch-jars": fetch_jars}[a.cmd](a)


if __name__ == "__main__":
    main()
