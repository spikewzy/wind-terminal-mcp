from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "references" / "relay-tool-contract.json"
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_REQUEST_BYTES = 5 * 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024


class RelayError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_config(path):
    path = Path(path).expanduser().absolute()
    value = read_json(path)
    for key in ("database", "data_directory", "mcp_root", "shared_profile"):
        if key in value:
            target = Path(value[key]).expanduser()
            value[key] = str(target.absolute() if target.is_absolute() else (path.parent / target).absolute())
    return value


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as file:
        os.chmod(temporary, 0o600)
        file.write(encode(value) + b"\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def load_contract():
    return read_json(CONTRACT)


def validate_base_url(url, allow_private_http=False):
    parts = urlsplit(url)
    if (not parts.hostname or parts.username or parts.password or parts.query
            or parts.fragment or parts.scheme not in {"https", "http"}):
        raise RelayError("INVALID_URL", "Use an HTTP(S) base URL without credentials, query or fragment")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        if not allow_private_http:
            raise RelayError("HTTPS_REQUIRED", "Remote connections require HTTPS; private encrypted tunnels may explicitly allow HTTP")
    return url.rstrip("/")


def transport_error(code, message, **details):
    return {"ok": False, "code": code, "message": message, "result_ready": False,
            "data_source_id": None, "relay": details}
