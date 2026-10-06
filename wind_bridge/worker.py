"""Run native WindPy out of process so logs/crashes/hangs cannot corrupt MCP."""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import ExitStack

from .common import MODULE_DIR, clean, failure, Problem
from .requests import validate
from .native_support import require_native_method
from .platform_support import require_supported_platform


def main():
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    # Redirect Python and native-library stdout to stderr before importing WindPy.
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    os.environ["TZ"] = "Asia/Shanghai"
    if hasattr(time, "tzset"):
        time.tzset()
    api = None
    dll_directories = ExitStack()
    try:
        request = json.load(sys.stdin)
        method = request["method"]
        args = validate(method, request.get("arguments", {})) if method != "__status__" else {}
        require_supported_platform()
        require_native_method(method)
        if MODULE_DIR is None or not (MODULE_DIR / "WindPy.py").is_file():
            raise Problem("WIND_SDK_NOT_FOUND", "Configure WIND_TERMINAL_MODULE_DIR with the directory containing the official WindPy.py for this platform.")
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            for directory in {str(MODULE_DIR), os.environ.get("WIND_TERMINAL_LIBRARY_DIR")} - {None}:
                if not os.path.isdir(directory):
                    raise Problem("WIND_SDK_LOAD_ERROR", "Configured Wind library directory does not exist", directory=directory)
                dll_directories.enter_context(os.add_dll_directory(directory))
        sys.path.insert(0, str(MODULE_DIR))
        try:
            from WindPy import w
        except (ImportError, OSError) as exc:
            raise Problem("WIND_SDK_LOAD_ERROR", "Official WindPy could not load; check SDK location, native libraries and matching OS/CPU/Python bitness.",
                          exception_type=type(exc).__name__, sdk_message=str(exc)) from None
        api = w
        started = w.start(waitTime=15)
        connected = bool(w.isconnected())
        if getattr(started, "ErrorCode", None) != 0 or not connected:
            raise Problem("WIND_CONNECTION_ERROR", "Wind API start failed", start=serialize(started), connected=connected)
        if method == "__status__":
            result = {"ok": True, "connected": connected, "module_path": str(MODULE_DIR / "WindPy.py")}
        else:
            try:
                raw = getattr(w, method)(**args)
            except Exception as exc:
                raise Problem("WIND_SDK_ERROR", "Official WindPy raised an exception", method=method,
                              exception_type=type(exc).__name__, sdk_message=str(exc)) from None
            if raw is None:
                raise Problem("EMPTY_SDK_RESULT", "WindPy returned None")
            result = {"ok": True, "raw": serialize(raw)}
    except Exception as exc:
        result = failure(exc)
    finally:
        if api is not None:
            try:
                api.stop()
            except Exception:
                pass
        dll_directories.close()
    output.write(json.dumps(clean(result), ensure_ascii=False, allow_nan=False) + "\n")
    output.flush()


def serialize(raw):
    return clean({k: getattr(raw, k, None) for k in ["ErrorCode", "Codes", "Fields", "Times", "Data"]})


if __name__ == "__main__":
    main()
