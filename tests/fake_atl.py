#!/usr/bin/env python3
"""Stand-in for android-translation-layer in tests. Mode from FAKE_ATL_MODE: exit:N | signal:N | noeol.
FAKE_ATL_CHECK=PATH: records whether PATH existed when ATL started."""
import json
import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
rec = os.environ.get("FAKE_ATL_RECORD")
if rec:
    with open(rec, "w") as f:
        check = os.environ.get("FAKE_ATL_CHECK")
        json.dump({"argv": sys.argv[1:], "data_dir": os.environ.get("ANDROID_APP_DATA_DIR"),
                   "check": bool(check) and os.path.exists(check)}, f)
mode = os.environ.get("FAKE_ATL_MODE", "exit:0")
kind, _, arg = mode.partition(":")
if kind == "noeol":
    sys.stdout.write("tail-ohne-umbruch")
    sys.stdout.flush()
    sys.exit(0)
sys.stdout.write("fake atl output\n")
sys.stdout.flush()
if kind == "signal":
    os.kill(os.getpid(), int(arg))
sys.exit(int(arg or 0))
