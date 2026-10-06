"""Recognize narrowly verified SDK limitations without guessing permissions.

Only exact installed-module/library fingerprints and architecture are matched.
An update invalidates this evidence and returns the methods to unverified state.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform

from .common import MODULE_DIR, ROOT, Problem

EVIDENCE = ROOT / "verification/native-news-availability.json"


def inspect_native_support():
    result = {"verified_build_match": False, "unavailable_methods": [],
              "authentication_checked": False, "evidence": str(EVIDENCE),
              "other_methods": "not_certified_by_this_check"}
    try:
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        if (platform.system(), platform.machine()) != (evidence["platform"], evidence["architecture"]):
            return {**result, "reason": "different_platform_or_architecture"}
        if MODULE_DIR is None:
            return {**result, "reason": "module_not_found"}
        module = MODULE_DIR / "WindPy.py"
        library = Path(evidence["library_path"])
        module_sha = hashlib.sha256(module.read_bytes()).hexdigest()
        library_sha = hashlib.sha256(library.read_bytes()).hexdigest()
        if module_sha != evidence["module_sha256"] or library_sha != evidence["library_sha256"]:
            return {**result, "reason": "sdk_fingerprint_changed"}
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, "reason": "matching_evidence_unavailable"}
    return {**result, "verified_build_match": True, "reason": "verified_null_return_stubs",
            "unavailable_methods": sorted(evidence["methods"]), "app_version": evidence["app_version"],
            "app_build": evidence["app_build"], "library_sha256": library_sha,
            "scope": "Only the inspected SDK binary and architecture; not an account-permission diagnosis."}


def require_native_method(method):
    support = inspect_native_support()
    if method in support["unavailable_methods"]:
        raise Problem("WIND_SDK_METHOD_UNAVAILABLE", "This exact installed Wind SDK implements the method as an unconditional NULL return",
                      method=method, native_support=support, authentication_checked=False,
                      next_step="Obtain an official supported SDK or a verified alternative read interface. Changing this method's parameters cannot implement its missing native function.")
