"""Import public Wind help locally; never execute downloaded examples."""
from pathlib import Path
import argparse
import json
import tempfile
from urllib.request import urlopen

from wind_bridge.documentation import import_help, SITE, SOURCES

MAX_DOCUMENT_BYTES = 5_000_000


def download_help(directory):
    """Fetch the nine documented public endpoints with bounded responses."""
    for _, kind, doc_id, _, filename in SOURCES:
        endpoint = "apiRefHelp" if kind == "manual" else "apiCaseTemplate"
        url = f"{SITE}/{endpoint}/getListByParentId/{doc_id}?local=zh-CN"
        with urlopen(url, timeout=30) as response:
            raw = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"Official help document exceeds 5 MB: {filename}")
        (directory / filename).write_bytes(raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path,
                        help="Directory containing the nine named public-document responses")
    parser.add_argument("--download", action="store_true",
                        help="Fetch public Wind documentation explicitly, without credentials")
    args = parser.parse_args()
    if bool(args.directory) == args.download:
        parser.error("Choose either a saved directory or --download")
    if args.download:
        with tempfile.TemporaryDirectory(prefix="wind-public-help-") as temporary:
            directory = Path(temporary)
            download_help(directory)
            result = import_help(directory)
    else:
        result = import_help(args.directory)
    print(json.dumps({"documents": len(result["documents"]),
                      "chunks": sum(len(d["chunks"]) for d in result["documents"]),
                      "metadata_source_id": result["metadata_source_id"]}, ensure_ascii=False))

