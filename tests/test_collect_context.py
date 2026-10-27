import json
import os
import tempfile
import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_context import Limits, collect_context, extract_explicit_paths, query_terms


class ContextCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.limits = Limits(
            max_documents=12,
            max_total_bytes=100_000,
            max_file_bytes=20_000,
            max_scan_files=50,
            max_scan_bytes=200_000,
            max_excerpt_chars=2_000,
            max_excerpts_per_file=4,
            excerpt_context_lines=1,
            max_link_depth=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, query):
        return collect_context(query, [self.root], limits=self.limits, cwd=self.root)

    def test_priority_files_and_keyword_provenance(self):
        (self.root / "_hot.md").write_text("# Current\nTax deadline is October 15.\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("Read the resolver before analysis.\n", encoding="utf-8")
        (self.root / "RESOLVER.md").write_text("Prefer fresh evidence.\n", encoding="utf-8")
        (self.root / "tax-notes.md").write_text(
            "Header\nThe CPA can accept a partial tax package.\nFooter\n", encoding="utf-8"
        )

        bundle = self.collect("Should I send the tax package to the CPA now?")
        paths = {Path(document["path"]).name: document for document in bundle["documents"]}

        self.assertTrue({"_hot.md", "AGENTS.md", "RESOLVER.md"}.issubset(paths))
        self.assertIn("tax-notes.md", paths)
        tax_doc = paths["tax-notes.md"]
        self.assertIn("tax", tax_doc["matched_terms"])
        self.assertEqual(64, len(tax_doc["provenance"]["content_sha256"]))
        self.assertGreaterEqual(tax_doc["excerpts"][0]["start_line"], 1)
        self.assertIn("CPA", tax_doc["excerpts"][0]["text"])
        self.assertFalse(bundle["search_scope"]["network_access"])

    def test_skips_sensitive_hidden_and_binary_files_and_redacts_inline_secret(self):
        (self.root / "decision.md").write_text(
            "renewal strategy\napi_key=super-secret-value-123456\n", encoding="utf-8"
        )
        (self.root / ".env.md").write_text("renewal secret", encoding="utf-8")
        (self.root / "credentials.json").write_text('{"renewal": true}', encoding="utf-8")
        (self.root / "binary.txt").write_bytes(b"renewal\x00\x01")
        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        (node_modules / "renewal.md").write_text("should not read", encoding="utf-8")

        bundle = self.collect("renewal strategy")
        names = {Path(document["path"]).name for document in bundle["documents"]}

        self.assertEqual({"decision.md"}, names)
        excerpt = bundle["documents"][0]["excerpts"][0]["text"]
        self.assertNotIn("super-secret-value", excerpt)
        self.assertIn("[REDACTED_SECRET]", excerpt)
        self.assertEqual(1, bundle["documents"][0]["provenance"]["secret_redactions"])
        self.assertGreaterEqual(bundle["stats"]["skip_reasons"].get("sensitive_filename", 0), 1)
        self.assertGreaterEqual(bundle["stats"]["skip_reasons"].get("hidden_path", 0), 1)
        self.assertGreaterEqual(bundle["stats"]["skip_reasons"].get("skipped_directory", 0), 1)
        self.assertGreaterEqual(bundle["stats"]["skip_reasons"].get("unreadable_or_binary", 0), 1)

    def test_follows_local_markdown_link_and_records_source(self):
        source = self.root / "project.md"
        linked = self.root / "details.md"
        source.write_text("Renewal plan: [details](details.md)\n", encoding="utf-8")
        linked.write_text("Stakeholder BATNA and engineering constraints.\n", encoding="utf-8")

        bundle = self.collect("renewal BATNA")
        by_name = {Path(document["path"]).name: document for document in bundle["documents"]}

        self.assertIn("details.md", by_name)
        self.assertIn("linked", by_name["details.md"]["source_kinds"])
        self.assertIn(str(source.resolve()), by_name["details.md"]["linked_from"])

    def test_workspace_link_cannot_escape_configured_root(self):
        outside_dir = Path(self.temp.name).parent / (Path(self.temp.name).name + "-linked-outside")
        outside_dir.mkdir()
        try:
            outside = outside_dir / "private-notes.md"
            outside.write_text("renewal private material\n", encoding="utf-8")
            source = self.root / "project.md"
            source.write_text(f"renewal [outside]({outside})\n", encoding="utf-8")

            bundle = self.collect("renewal")
            paths = {Path(document["path"]) for document in bundle["documents"]}
            self.assertNotIn(outside.resolve(), paths)
        finally:
            for child in outside_dir.iterdir():
                child.unlink()
            outside_dir.rmdir()

    def test_explicit_file_outside_root_is_read_but_directory_is_not_walked(self):
        outside_dir = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside")
        outside_dir.mkdir()
        try:
            explicit = outside_dir / "case.md"
            explicit.write_text("Exact outside context for acquisition decision.\n", encoding="utf-8")
            ignored = outside_dir / "other.md"
            ignored.write_text("acquisition material that must not be discovered.\n", encoding="utf-8")

            bundle = self.collect(f"Analyze acquisition using `{explicit}`")
            paths = {Path(document["path"]) for document in bundle["documents"]}
            self.assertIn(explicit.resolve(), paths)
            self.assertNotIn(ignored.resolve(), paths)
            doc = next(document for document in bundle["documents"] if Path(document["path"]) == explicit.resolve())
            self.assertIn("explicit_path", doc["source_kinds"])
        finally:
            for child in outside_dir.iterdir():
                child.unlink()
            outside_dir.rmdir()

    def test_respects_scan_and_output_limits(self):
        for index in range(20):
            (self.root / f"renewal-{index:02d}.md").write_text(
                ("renewal stakeholder analysis\n" * 20), encoding="utf-8"
            )
        limits = Limits(
            max_documents=3,
            max_total_bytes=10_000,
            max_file_bytes=1_000,
            max_scan_files=5,
            max_scan_bytes=3_000,
            max_excerpt_chars=500,
        )
        bundle = collect_context("renewal", [self.root], limits=limits, cwd=self.root)

        self.assertLessEqual(bundle["stats"]["scanned_files"], 5)
        self.assertLessEqual(bundle["stats"]["scanned_bytes"], 3_000)
        self.assertLessEqual(bundle["stats"]["selected_documents"], 3)
        self.assertLessEqual(bundle["stats"]["selected_bytes"], 10_000)
        excerpt_bytes = sum(
            len(excerpt["text"].encode("utf-8"))
            for document in bundle["documents"]
            for excerpt in document["excerpts"]
        )
        self.assertEqual(bundle["stats"]["selected_bytes"], excerpt_bytes)

    def test_total_output_budget_clips_actual_excerpt_bytes(self):
        (self.root / "_hot.md").write_text("税务材料必须核验后交给 CPA。\n", encoding="utf-8")
        limits = Limits(
            max_documents=3,
            max_total_bytes=10,
            max_file_bytes=1_000,
            max_scan_files=10,
            max_scan_bytes=10_000,
            max_candidate_files=20,
        )
        bundle = collect_context("税务 CPA", [self.root], limits=limits, cwd=self.root)
        excerpt_bytes = sum(
            len(excerpt["text"].encode("utf-8"))
            for document in bundle["documents"]
            for excerpt in document["excerpts"]
        )
        self.assertLessEqual(excerpt_bytes, 10)
        self.assertEqual(bundle["stats"]["selected_bytes"], excerpt_bytes)
        self.assertTrue(any("max_total_bytes" in warning for warning in bundle["warnings"]))

    def test_filename_ranking_reaches_relevant_file_after_large_archive(self):
        archive = self.root / "archive"
        archive.mkdir()
        for index in range(20):
            (archive / f"old-{index:02d}.md").write_text("generic notes\n", encoding="utf-8")
        finance = self.root / "fin"
        finance.mkdir()
        relevant = finance / "tax-coinbase-case.md"
        relevant.write_text("Coinbase tax cost basis is missing.\n", encoding="utf-8")
        limits = Limits(
            max_documents=3,
            max_scan_files=3,
            max_candidate_files=50,
            max_scan_bytes=20_000,
            max_total_bytes=20_000,
            max_file_bytes=5_000,
            max_link_depth=0,
        )

        bundle = collect_context("Coinbase tax basis", [self.root], limits=limits, cwd=self.root)
        paths = {Path(document["path"]) for document in bundle["documents"]}
        self.assertIn(relevant.resolve(), paths)

    def test_chinese_terms_and_explicit_path_parser(self):
        target = self.root / "税务材料.md"
        target.write_text("成本基础仍待确认。\n", encoding="utf-8")
        terms = query_terms("帮我分析税务材料和成本基础")
        paths = extract_explicit_paths(f"请读取 `{target}`", self.root)

        self.assertIn("税务", terms)
        self.assertIn("成本", terms)
        self.assertEqual([target.resolve()], paths)


if __name__ == "__main__":
    unittest.main()
