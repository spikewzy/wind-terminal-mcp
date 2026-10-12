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
             "verification/relay-offline-suite.json"]
    files = {root / name for name in exact}
    files.update((root / "wind_relay").glob("*.py"))
    return sorted(files)


def main():
    contract = json.loads((ROOT / "references/relay-tool-contract.json").read_text())
    manifest = {"kind": "relay_trial_overlay", "protocol_version": contract["protocol_version"],
                "base_mcp_version": contract["mcp_version"], "base_server_sha256": contract["server_sha256"],
                "cloud_or_uos_deployed": False, "live_wind_verified": False,
                "excluded": ["server.py", "wind_bridge/", "Wind SDK", "runtime/", ".venv/", ".relay/", "credentials"],
                "files": {}}
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / "wind-terminal-mcp-relay-trial-v1.zip"
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
                      "bytes": archive.stat().st_size, "live_wind_verified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
