"""Build a public source ZIP with an explicit, relocatable payload."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from wind_bridge import VERSION
from wind_bridge.platform_support import support_policy

ROOT = Path(__file__).resolve().parent


def public_payload(root=ROOT):
    exact = ["server.py", "client_config.py", "install.py", "verify_delivery.py", "build_release.py",
             "INSTALL.md", "README.md", "USER_GUIDE.md", "FUTURES_DEMOS.md", "MIGRATION.md",
             "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore",
             "requirements.in", "requirements.lock.txt", "build_community_fields.py",
             "inspect_edb_export.py", "import_official_help.py", "import_bundle_metadata.py",
             "verification/wind-api-ui-fields.json", "verification/native-news-availability.json"]
    files = {root / name for name in exact}
    files.update((root / "wind_bridge").glob("*.py"))
    files.update(p for p in (root / "tests").rglob("*")
                 if p.is_file() and p.suffix in {".py", ".csv", ".md"})
    # Complete vendor help/dictionaries are created on each user's machine.
    # Only project-authored reference records and licensed candidates are shipped.
    files.update(p for p in (root / "references").glob("*")
                 if p.is_file() and p.suffix in {".json", ".md"}
                 and p.name != "wind-bundle-metadata.json")
    for folder in ("references/third-party", "references/issuer-filings",
                   "references/snapshots", "skills/wind-terminal-api", "examples", "docs"):
        files.update(p for p in (root / folder).rglob("*")
                     if p.is_file() and p.suffix in {".json", ".md", ".pdf", ".png", ".jpg", ".html", ".txt"})
    return sorted(files)


def main():
    files = public_payload()
    manifest = {"version": VERSION, "data_source_id": "wind_terminal_api",
                "platform": "supported_desktops", "platform_policy": support_policy(), "transport": "stdio",
                "excluded": ["Wind SDK/application binaries", "runtime/", ".venv/",
                             "client configurations", "credentials", "full vendor help documents",
                             "full Wind application metadata snapshots"],
                "files": {}}
    archive_root = f"wind-terminal-mcp-{VERSION}"
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / f"{archive_root}-desktop.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                raise ValueError(f"Refusing to package a symlink: {relative}")
            payload = path.read_bytes()
            manifest["files"][relative] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            output.writestr(f"{archive_root}/{relative}", payload)
        output.writestr(f"{archive_root}/release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "sha256": digest, "file_count": len(files),
                      "bytes": archive.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
