"""Desktop platform policy and SDK discovery; never import or install WindPy."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import shlex
import struct
import sys

MAC_MODULE_DIR = Path("/Applications/Wind API.app/Contents/python")
LINUX_RELEASE_FILES = ("/etc/os-version", "/etc/kylin-release", "/etc/.kyinfo", "/etc/nfs-release")


def support_policy():
    return {
        "supported_desktops": ["macOS", "Windows", "中科方德 5.0", "UOS 20", "银河麒麟 V10 SP1"],
        "unsupported": ["other Linux distributions or versions", "all Linux server editions"],
        "linux_requires_identified_desktop_edition": True,
        "python_minimum": "3.10",
        "sdk_requirement": "Official WindPy and native libraries for the same OS, CPU architecture and Python bitness; licensed desktop Wind login required.",
        "native_sdk_bundled": False,
        "remote_http_deployment": False,
        "policy_match_is_not_native_validation": True,
        "windows_and_xinchuang_native_validation": "not_run_on_target_hardware",
        "reference": "https://wind.com.cn/download.htm",
    }


def read_linux_identity():
    release = {}
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if separator and re.fullmatch(r"[A-Z_]+", key):
                try:
                    pieces = shlex.split(value, comments=True)
                except ValueError:
                    continue
                if len(pieces) == 1:
                    release[key] = pieces[0]
        break
    supplements = []
    for filename in LINUX_RELEASE_FILES:
        try:
            supplements.append(Path(filename).read_text(encoding="utf-8", errors="replace")[:16384])
        except OSError:
            continue
    return release, "\n".join(supplements)


def inspect_platform(*, system=None, machine=None, release=None, supplemental=None):
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    result = {"system": system, "architecture": machine, "python_bits": struct.calcsize("P") * 8,
              "supported": False, "profile": None, "reason": "unsupported_operating_system",
              "policy_match_is_not_native_validation": True}
    if system in {"Darwin", "Windows"}:
        return {**result, "supported": True, "profile": "macos" if system == "Darwin" else "windows",
                "reason": "desktop_platform_in_requested_scope"}
    if system != "Linux":
        return result
    if release is None:
        release, detected_supplemental = read_linux_identity()
        supplemental = detected_supplemental if supplemental is None else supplemental
    supplemental = supplemental or ""
    identity = " ".join(str(release.get(k, "")) for k in
                        ("ID", "NAME", "PRETTY_NAME", "VERSION", "VERSION_ID", "VARIANT", "VARIANT_ID", "EDITION"))
    text = (identity + "\n" + supplemental).lower()
    version = " ".join(str(release.get(k, "")) for k in ("VERSION_ID", "VERSION", "PRETTY_NAME")) + "\n" + supplemental
    version = version.lower()
    result.update(distribution=release.get("ID"), distribution_name=release.get("PRETTY_NAME", release.get("NAME")),
                  version=release.get("VERSION_ID"), reason="unsupported_linux_distribution_or_version")
    # A desktop session installed on a server edition does not change the OS edition.
    if re.search(r"server|服务器", text):
        return {**result, "reason": "linux_server_editions_not_supported"}
    identifier = str(release.get("ID", "")).lower()
    family = None
    if identifier in {"uos", "uniontech"} or re.search(r"统信|\buniontech\b", identity.lower()):
        if re.search(r"(?<!\d)20(?!\d)", version):
            family = "uos20"
    elif identifier in {"nfs", "nfsdesktop", "nfs-desktop"} or re.search(r"方德|\bnfs desktop\b", identity.lower()):
        if re.search(r"(?<!\d)5\.0(?!\d)", version):
            family = "nfs5"
    elif identifier in {"kylin", "kylin-desktop"} or "银河麒麟" in identity:
        v10 = re.search(r"(?<!\d)v?10(?!\d|\.[1-9])", version)
        sp1 = re.search(r"(?<![a-z])sp[-_ ]?1(?!\d)", version)
        other_sp = re.search(r"(?<![a-z])sp[-_ ]?(?:[2-9]|1\d)", version)
        if v10 and sp1 and not other_sp:
            family = "kylin_v10_sp1"
    if family is None:
        return result
    if not re.search(r"desktop|桌面", text):
        return {**result, "profile": family, "reason": "linux_desktop_edition_not_confirmed"}
    return {**result, "supported": True, "profile": family, "reason": "supported_linux_desktop_and_version"}


def require_supported_platform():
    from .common import Problem
    result = inspect_platform()
    if not result["supported"]:
        raise Problem("UNSUPPORTED_PLATFORM", "This OS is outside the supported desktop platforms; other Linux and Linux server editions are not supported.",
                      platform=result, support_policy=support_policy())
    return result


def venv_python(directory, system=None):
    system = platform.system() if system is None else system
    return Path(directory) / ("Scripts/python.exe" if system == "Windows" else "bin/python")


def discover_module_dir(*, environ=None, system=None, search_paths=None):
    environ = os.environ if environ is None else environ
    system = platform.system() if system is None else system
    explicit = environ.get("WIND_TERMINAL_MODULE_DIR")
    if explicit:
        return Path(explicit).expanduser().absolute()
    if system == "Darwin" and (MAC_MODULE_DIR / "WindPy.py").is_file():
        return MAC_MODULE_DIR
    for value in sys.path if search_paths is None else search_paths:
        if value and (Path(value) / "WindPy.py").is_file():
            return Path(value).absolute()
    return MAC_MODULE_DIR if system == "Darwin" else None


def worker_environment(module_dir):
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    library = env.get("WIND_TERMINAL_LIBRARY_DIR")
    directories = [str(Path(library).expanduser().absolute())] if library else []
    if platform.system() == "Windows" and module_dir is not None:
        directories.append(str(module_dir))
    key = "PATH" if platform.system() == "Windows" else "LD_LIBRARY_PATH" if platform.system() == "Linux" else None
    if key and directories:
        env[key] = os.pathsep.join(directories + ([env[key]] if env.get(key) else []))
    return env
