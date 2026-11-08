#!/usr/bin/env python3
"""Stand-in for ART's dalvikvm and dex2oat in tests (by program name).

Names the dalvik-cache files the way ART does (GetDalvikCacheFilename): drop the leading '/', append
'/classes.dex' unless the location ends in .dex/.art/.oat, replace '/' by '@'.
FAKE_ART_RECORD: append one JSON line per call. FAKE_ART_FAIL=dalvikvm|dex2oat: that program fails.
FAKE_ART_CHECK=PATH: dex2oat records whether PATH existed when it ran.
FAKE_ART_TICKS=PATH: dex2oat works for 1.5 s and appends a dot to PATH every 0.1 s (a stopped process adds none)."""
import json
import os
import sys
import time
from pathlib import Path

prog = Path(sys.argv[0]).name
args = sys.argv[1:]
cache = Path(os.environ["ANDROID_DATA"]) / "dalvik-cache" if os.environ.get("ANDROID_DATA") else \
    Path(os.environ["XDG_CACHE_HOME"]) / "art"
cache = cache / "x86_64"


def name(location: str) -> str:
    n = location[1:]
    if not n.endswith((".dex", ".art", ".oat")):
        n += "/classes.dex"
    return n.replace("/", "@")


rec = os.environ.get("FAKE_ART_RECORD")
if rec:
    check = os.environ.get("FAKE_ART_CHECK")
    with open(rec, "a") as f:
        f.write(json.dumps({"prog": prog, "args": args, "check": check and os.path.exists(check),
                            "sched_idle": os.sched_getscheduler(0) == getattr(os, "SCHED_IDLE", -1)}) + "\n")
if os.environ.get("FAKE_ART_FAIL") == prog:
    sys.stderr.write(f"{prog}: something went wrong\n")
    sys.exit(1)
if prog == "dalvikvm":
    image = next(a.split(":", 1)[1] for a in args if a.startswith("-Ximage:"))
    cp = args[args.index("-cp") + 1].split(":")
    cache.mkdir(parents=True, exist_ok=True)
    for ext in (".art", ".oat", ".vdex"):
        (cache / name(image[:-4] + ext)).write_bytes(b"image")
    (cache / name(cp[0])).write_bytes(b"oat")
    (cache / (name(cp[0])[:-4] + ".vdex")).write_bytes(b"vdex")
    sys.stderr.write("Class name required\n")
    sys.exit(1)
if prog == "dex2oat":
    ticks = os.environ.get("FAKE_ART_TICKS")
    for _i in range(15 if ticks else 0):
        time.sleep(0.1)
        with open(ticks, "a") as f:
            f.write(".")
    oat = Path(next(a.split("=", 1)[1] for a in args if a.startswith("--oat-file=")))
    oat.write_bytes(b"oat " + " ".join(args).encode())
    oat.with_suffix(".vdex").write_bytes(b"vdex")
    sys.exit(0)
sys.exit(2)
