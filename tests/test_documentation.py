import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wind_bridge.common import Problem
from wind_bridge.documentation import chunk_sections, import_help, notebook_text, search_help, sha256


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def payload(self, source, cell_type="markdown", parent="doc-id"):
        notebook = {"nbformat": 4, "cells": [{"cell_type": cell_type, "source": source, "outputs": [{"text": "not-source"}]}]}
        wrapper = {"content": json.dumps(notebook)}
        return {"code": 0, "data": [{"id": "record-id", "parentId": parent, "jsonData": json.dumps(wrapper), "textData": "ignored duplicate representation"}]}

    def import_fixture(self):
        source = self.root / "download"
        source.mkdir()
        files = [("python", "manual", "doc-id", "Python 手册", "python.json"),
                 ("case", "case", "case-id", "Python 案例", "case.json")]
        (source / "python.json").write_text(json.dumps(self.payload(["### WSD 参数\n", "Days=Alldays;Period=Q\n", "### WSS 参数\n", "fields: oper_rev\n"])), encoding="utf-8")
        (source / "case.json").write_text(json.dumps(self.payload('w.wset("indexhistory", "field=tradedate,tradecode")', "code", parent="case-id")), encoding="utf-8")
        destination = self.root / "help"
        with patch("wind_bridge.documentation.SOURCES", files):
            result = import_help(source, destination)
        return destination / "index.json", result

    def test_notebook_code_is_text_and_outputs_are_not_documentation(self):
        marker = self.root / "should-not-exist"
        code = f"open({str(marker)!r}, 'w').write('executed')"
        text, records = notebook_text(self.payload(code, "code"), "doc-id")
        self.assertIn(code, text)
        self.assertNotIn("not-source", text)
        self.assertFalse(marker.exists())
        self.assertEqual(records, ["record-id"])

    def test_rejects_failed_misdirected_or_duplicate_documents(self):
        failed = self.payload("text")
        failed["code"] = False
        duplicate = self.payload("text")
        duplicate["data"] *= 2
        malformed = self.payload("text")
        malformed["data"][0]["jsonData"] = "not JSON"
        for payload in [failed, duplicate, malformed, self.payload("text", parent="other-id"), self.payload("text", cell_type="unsupported")]:
            with self.subTest(payload=payload):
                with self.assertRaises(Problem):
                    notebook_text(payload, "doc-id")

    def test_sections_preserve_all_lines_and_do_not_split_at_code_comments(self):
        text = "# Manual\n### Function\n```python\n# This is a code comment\nprint('x')\n```\n### Options\nA\nB\n"
        chunks = chunk_sections(text, max_chars=35)
        self.assertEqual("".join(row["text"] for row in chunks), text)
        self.assertFalse(any("This is a code comment" in row["heading_path"] for row in chunks))
        self.assertEqual(chunks[-1]["heading_path"], ["Manual", "Options"])
        self.assertEqual(chunks[-1]["line_end"], len(text.splitlines()))
        self.assertTrue(any(row["parts"] > 1 for row in chunks))

    def test_import_preserves_source_receipts_and_text_hashes(self):
        path, result = self.import_fixture()
        self.assertEqual(result["metadata_source_id"], "wind_api_official_help")
        self.assertFalse(result["code_executed"])
        for row in result["documents"]:
            self.assertEqual(sha256((path.parent / "raw" / f"{row['document']}.json").read_bytes()), row["raw_sha256"])
            self.assertEqual(sha256((path.parent / f"{row['document']}.md").read_bytes()), row["text_sha256"])

    def test_search_scope_and_pagination_keep_reference_status(self):
        path, _ = self.import_fixture()
        result = search_help("Days Alldays", "python", path=path)
        self.assertEqual(result["data_source_id"], "wind_terminal_api")
        self.assertEqual(len(result["results"]), 1)
        self.assertFalse(result["executed"])
        self.assertFalse(result["remote_freshness_checked"])
        self.assertFalse(result["full_field_dictionary"])
        self.assertEqual(result["results"][0]["heading_path"], ["WSD 参数"])
        first = search_help("Python", limit=1, path=path)
        second = search_help("Python", limit=1, offset=first["next_offset"], path=path)
        self.assertNotEqual(first["results"][0]["line_start"], second["results"][0]["line_start"])
        self.assertIn("indexhistory", search_help("indexhistory", document="case", path=path)["results"][0]["text"])

    def test_local_copy_changes_are_distinct_from_remote_freshness(self):
        path, _ = self.import_fixture()
        file = path.parent / "raw/python.json"
        self.assertTrue(search_help("Days", path=path)["results"][0]["local_raw_copy_matches_import"])
        file.write_bytes(b"changed")
        result = search_help("Days", path=path)
        self.assertFalse(result["results"][0]["local_raw_copy_matches_import"])
        self.assertFalse(result["remote_freshness_checked"])
        file.unlink()
        self.assertIsNone(search_help("Days", path=path)["results"][0]["local_raw_copy_matches_import"])

    def test_invalid_search_parameters(self):
        path, _ = self.import_fixture()
        for kwargs in [{"document": "../../other"}, {"document": []}, {"limit": True}, {"limit": 21}, {"offset": -1}]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(Problem):
                    search_help("Wind", path=path, **kwargs)
        with self.assertRaises(Problem):
            search_help(" ", path=path)

    def test_fresh_install_reports_missing_optional_reference(self):
        with self.assertRaises(Problem) as caught:
            search_help("wss", path=self.root / "absent.json")
        self.assertEqual(caught.exception.code, "LOCAL_REFERENCE_NOT_INSTALLED")
        self.assertFalse(caught.exception.details["reference_available"])
        self.assertFalse(caught.exception.details["executed"])
