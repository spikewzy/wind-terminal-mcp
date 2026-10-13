"""Build the relay overlay only: no existing MCP core, SDK, data or credentials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def payload(root=ROOT):
    exact = ["relay_gateway.py", "relay_worker.py", "relay_client.py", "relay_setup.py",
             "relay_demo.py", "generate_relay_contract.py", "build_relay_trial.py", "verify_relay_offline.py",
             "requirements.lock.txt", "LICENSE", "references/relay-tool-contract.json",
             "docs/RELAY_TRIAL.md", "docs/RELAY_WORKBUDDY_PROMPT.md", "tests/test_relay.py",
             "verification/relay-mock-acceptance.json", "verification/relay-loopback-acceptance.json",
             "verification/relay-offline-suite.json", "verification/relay-quota-regression.json",
             "verification/relay-deployed-native-acceptance.json",
             "verification/relay-deployed-isolation-acceptance.json",
             "verification/relay-deployed-client-reconnect-acceptance.json"]
    files = {root / name for name in exact}
    files.update((root / "wind_relay").glob("*.py"))
    return sorted(files)


def main():
    contract = json.loads((ROOT / "references/relay-tool-contract.json").read_text())
    worker_sha = hashlib.sha256((ROOT / "wind_relay/worker.py").read_bytes()).hexdigest()
    evidence = {kind: json.loads((ROOT / ("verification/relay-deployed-" + kind + "-acceptance.json")).read_text())
                for kind in ("native", "isolation", "client-reconnect")}
    native = evidence["native"]
    verified_native = (native.get("ok") is True
                       and native.get("base_server_sha256") == contract["server_sha256"]
                       and native.get("worker_module_sha256") == worker_sha)
    verified_isolation = (verified_native and evidence["isolation"].get("ok") is True
                          and evidence["isolation"].get("worker_module_sha256") == worker_sha)
    verified_reconnect = (verified_native and evidence["client-reconnect"].get("ok") is True
                          and evidence["client-reconnect"].get("worker_module_sha256") == worker_sha)
    manifest = {"kind": "relay_trial_overlay", "protocol_version": contract["protocol_version"],
                "package_revision": 2,
                "base_mcp_version": contract["mcp_version"], "base_server_sha256": contract["server_sha256"],
                "cloud_or_uos_deployed": verified_native, "live_wind_verified": verified_native,
                "two_client_isolation_verified": verified_isolation,
                "client_disconnect_reconnect_verified": verified_reconnect,
                "execution_host_disconnect_recovery_verified": False,
                "current_codex_or_workbuddy_tool_registration_verified": False,
                "validation_scope": "Recorded deployment samples only; not acceptance of a new installation or production stability",
                "excluded": ["server.py", "wind_bridge/", "Wind SDK", "runtime/", ".venv/", ".relay/", "credentials"],
                "files": {}}
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / "wind-terminal-mcp-relay-trial-v2.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in payload():
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                raise ValueError("Refusing to package a symlink: " + relative)
            value = path.read_bytes()
            manifest["files"][relative] = {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            output.writestr(relative, value)
        output.writestr("relay-trial-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{sha256}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "sha256": sha256, "files": len(manifest["files"]),
                      "bytes": archive.stat().st_size, "live_wind_verified": verified_native,
                      "two_client_isolation_verified": verified_isolation}, ensure_ascii=False))


if __name__ == "__main__":
    main()
