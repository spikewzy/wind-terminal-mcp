"""Extract candidate field mappings from a pinned wheel without importing its code."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import zipfile

WHEEL_SHA256 = "50e9511eff2fc7756a68a0b8f49fe973721ca9a77ef488d5a79091e4d9f023e3"
WHEEL_URL = "https://files.pythonhosted.org/packages/a3/9d/83f48e349ec860c7c16fc7d56f4a50a0b5b220078c93dab5a4d8edcd5dae/windget-0.0.7-py3-none-any.whl"
MODULES = ("wss", "wsd", "wsi", "wst", "wsq", "wsee", "wses")


def extract_module(source: str, method: str, source_file: str):
    """Accept only single-return wrappers whose second Wind argument is literal."""
    lines, rows = source.splitlines(), []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("get"):
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            raise ValueError(f"Unexpected wrapper body: {source_file}:{node.lineno}")
        call = node.body[0].value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "w"
                and call.func.attr == method and len(call.args) >= 2
                and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str)
                and re.fullmatch(r"[A-Za-z0-9_/-]{1,200}", call.args[1].value)):
            raise ValueError(f"Nonliteral field or unexpected method: {source_file}:{node.lineno}")
        comments = [line.strip()[1:].strip() for line in lines[node.lineno:call.lineno - 1]
                    if line.strip().startswith("#")]
        if len(comments) != 1 or not comments[0].startswith("获取"):
            raise ValueError(f"Missing unambiguous label: {source_file}:{node.lineno}")
        label = comments[0][2:]
        if method == "wsd" and label.endswith("时间序列"):
            label = label[:-4]
        rows.append({"field": call.args[1].value.lower(), "label": label,
                     "reference": {"file": source_file, "line": node.lineno,
                                   "function": node.name, "method": method,
                                   "literal_field": call.args[1].value,
                                   "original_comment": comments[0]}})
    return rows


def build(wheel: Path, root: Path):
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise ValueError("Wheel differs from the inspected windget 0.0.7 release")
    sources, records, counts = {}, {}, Counter()
    with zipfile.ZipFile(wheel) as archive:
        for method in MODULES:
            filename = f"windget/{method}.py"
            payload = archive.read(filename)
            sources[filename] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for row in extract_module(payload.decode("utf-8-sig"), method, filename):
                entry = records.setdefault(row["field"], {"field": row["field"], "labels": [], "references": []})
                if row["label"] not in entry["labels"]:
                    entry["labels"].append(row["label"])
                entry["references"].append(row["reference"])
                counts[method] += 1
        license_text = archive.read("windget-0.0.7.dist-info/LICENSE").decode().replace("\r\n", "\n")
    output = root / "references/community-fields.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    catalog = {"schema_version": 1, "metadata_source_id": "windget_community_0_0_7",
               "records": [records[key] for key in sorted(records)]}
    output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest = {"schema_version": 1, "metadata_source_id": catalog["metadata_source_id"],
                "project": "windget", "version": "0.0.7", "released_at": "2022-06-23",
                "project_url": "https://pypi.org/project/windget/0.0.7/",
                "documentation_url": "https://windget-doc.readthedocs.io/zh/latest/",
                "wheel_url": WHEEL_URL, "wheel_sha256": WHEEL_SHA256, "source_files": sources,
                "catalog_file": "references/community-fields.json",
                "catalog_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "field_count": len(records), "mapping_count": sum(counts.values()),
                "mappings_by_method": dict(counts), "official_wind_dictionary": False,
                "options_schema_available": False, "upstream_code_executed": False,
                "license": "MIT", "license_file": "references/third-party/windget-LICENSE.md"}
    (root / "references/community-fields-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    notice = root / manifest["license_file"]
    notice.parent.mkdir(exist_ok=True)
    notice.write_text("# windget 0.0.7 notice\n\nCandidate field mappings are extracted from the public wheel, without executing it. "
                      "The following license is reproduced verbatim from that distribution, including its original copyright holder.\n\n" + license_text, encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(build(args.wheel, args.root), ensure_ascii=False))
