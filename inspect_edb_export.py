"""Inspect a deliberately supplied Wind EDB CSV without importing or querying."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from wind_bridge.common import Problem, dumps, failure
from wind_bridge.edb_export import MAX_BYTES, decode_export, parse_export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    try:
        with args.file.open("rb") as stream:
            payload = stream.read(MAX_BYTES + 1)
        text, encoding = decode_export(payload)
        result = parse_export(text, args.evidence)
        result.update(file_sha256=hashlib.sha256(payload).hexdigest(), encoding=encoding)
    except (Problem, OSError) as exc:
        result = failure(exc)
    print(dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
