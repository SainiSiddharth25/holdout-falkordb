"""Evidence correctness and containment checks; never agent input."""

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from holdout.evidence import EvidenceError, assess_lineage, compare_records, inspect_entity, inspect_record, load_fixture


ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "demo"
TRAIN_LINEAGE = {"paths": [{"nodes": ["t103", "capture-31", "source-83"], "relations": ["DERIVED_FROM", "DERIVED_FROM"]}]}
EVAL_LINEAGE = {"paths": [{"nodes": ["e203", "source-83"], "relations": ["DERIVED_FROM"]}]}
TRAIN_SCAFFOLD = {"paths": [{"nodes": ["t104", "source-42"], "relations": ["USES_SCAFFOLD"]}]}
EVAL_SCAFFOLD = {"paths": [{"nodes": ["e204", "source-42"], "relations": ["USES_SCAFFOLD"]}]}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture()

    def _copy_fixture(self, destination):
        target = Path(destination) / "fixture"
        shutil.copytree(ROOT, target)
        return target

    def _pair_fixture(self, directory, train_source, eval_source, train_symbol="run", eval_symbol="run"):
        root = Path(directory)
        (root / "train.py").write_text(train_source)
        (root / "eval.py").write_text(eval_source)
        fixture = {"id": "unit-pair", "records": [
            {"id": "t", "split": "train", "cost": 1, "content_path": "train.py", "content_role": "solution", "solution_symbols": [train_symbol]},
            {"id": "e", "split": "eval", "cost": 1, "content_path": "eval.py", "content_role": "solution", "solution_symbols": [eval_symbol]},
        ]}
        (root / "dataset.json").write_text(json.dumps(fixture))
        return load_fixture(root)

    def test_fixture_contains_no_preclassified_candidates(self):
        self.assertEqual(len(self.fixture["records"]), 14)
        self.assertEqual(len(self.fixture["candidates"]), 8)
        self.assertTrue(all(item["status"] == "unresolved" for item in self.fixture["candidates"]))
        self.assertEqual({record["id"] for record in self.fixture["records"] if record["protected"]}, {"t106", "e203"})
        serialized = json.dumps(self.fixture)
        for field in ("expected_verdict", "golden", "ground_truth", "expected_label"):
            self.assertNotIn(field, serialized)

    def test_exact_solution_inside_training_bundle(self):
        for evaluation in ("e201", "e202"):
            result = compare_records(self.fixture, "t101", evaluation, "exact")
            self.assertEqual(result["verdict"], "confirmed")
            self.assertEqual(result["evidence"][1]["kind"], "solution_unit_match")

    def test_identical_complex_function_still_matches_exactly(self):
        self.assertEqual(compare_records(self.fixture, "t106", "e206", "exact")["verdict"], "confirmed")
        self.assertEqual(compare_records(self.fixture, "t106", "e206", "normalized_ast")["verdict"], "unresolved")

    def test_local_renaming_match_preserves_globals(self):
        self.assertEqual(compare_records(self.fixture, "t102", "e202", "exact")["verdict"], "unresolved")
        self.assertEqual(compare_records(self.fixture, "t102", "e202", "normalized_ast")["verdict"], "confirmed")
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pair_fixture(directory, "def run(values):\n    return min(values)\n", "def run(items):\n    return max(items)\n")
            self.assertEqual(compare_records(fixture, "t", "e", "normalized_ast")["verdict"], "unresolved")

    def test_constants_operators_and_attributes_are_not_erased(self):
        variants = [
            ("def run(value):\n    return value + 1\n", "def run(item):\n    return item + 2\n"),
            ("def run(value):\n    return value < 2\n", "def run(item):\n    return item <= 2\n"),
            ("def run(value):\n    return value.strip()\n", "def run(item):\n    return item.lower()\n"),
            ("def run(value):\n    return value + 'x'\n", "def run(item):\n    return item + 'y'\n"),
        ]
        for train, evaluation in variants:
            with self.subTest(train=train), tempfile.TemporaryDirectory() as directory:
                fixture = self._pair_fixture(directory, train, evaluation)
                self.assertEqual(compare_records(fixture, "t", "e", "normalized_ast")["verdict"], "unresolved")
        self.assertEqual(compare_records(self.fixture, "t107", "e201", "normalized_ast")["verdict"], "unresolved")

    def test_decorators_cannot_disappear_from_exact_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pair_fixture(directory, "@cache\ndef run(value):\n    return value + 1\n", "@trace\ndef run(value):\n    return value + 1\n")
            self.assertEqual(compare_records(fixture, "t", "e", "exact")["verdict"], "unresolved")
            self.assertEqual(compare_records(fixture, "t", "e", "normalized_ast")["verdict"], "unresolved")

    def test_module_source_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            source = f"open({str(marker)!r}, 'w').write('bad')\ndef run(value):\n    return value + 1\n"
            fixture = self._pair_fixture(directory, source, source)
            self.assertEqual(compare_records(fixture, "t", "e", "exact")["verdict"], "confirmed")
            inspect_record(fixture, "t")
            self.assertFalse(marker.exists())

    def test_lineage_requires_graph_supplied_paths(self):
        self.assertEqual(assess_lineage(self.fixture, "t103", "e203", TRAIN_LINEAGE, EVAL_LINEAGE)["verdict"], "confirmed")
        self.assertEqual(assess_lineage(self.fixture, "t103", "e203", {"paths": []}, EVAL_LINEAGE)["verdict"], "unresolved")
        self.assertEqual(assess_lineage(self.fixture, "t103", "e203", TRAIN_LINEAGE, {"paths": []})["verdict"], "unresolved")

    def test_tampering_breaks_capture_hash_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_fixture(directory)
            fixture = load_fixture(root)
            note = root / "records" / "t103.md"
            note.write_text(note.read_text() + "\nChanged after capture.\n")
            self.assertEqual(assess_lineage(fixture, "t103", "e203", TRAIN_LINEAGE, EVAL_LINEAGE)["verdict"], "unresolved")

    def test_manifests_alone_do_not_prove_preservation(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_fixture(directory)
            note = root / "records" / "t103.md"
            note.write_text("The source exists, but this note does not contain its answer.\n")
            manifest_path = root / "manifests" / "edge-01.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["source_sha256"] = hashlib.sha256(note.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            fixture = load_fixture(root)
            self.assertEqual(assess_lineage(fixture, "t103", "e203", TRAIN_LINEAGE, EVAL_LINEAGE)["verdict"], "unresolved")

    def test_only_scaffold_candidate_is_rejected(self):
        result = assess_lineage(self.fixture, "t104", "e204", TRAIN_SCAFFOLD, EVAL_SCAFFOLD)
        self.assertEqual(result["verdict"], "rejected")
        self.assertIn("not every possible", result["reason"])
        fixture = copy.deepcopy(self.fixture)
        next(candidate for candidate in fixture["candidates"] if candidate["id"] == "c405")["basis"] = "source_retrieval"
        self.assertEqual(assess_lineage(fixture, "t104", "e204", TRAIN_SCAFFOLD, EVAL_SCAFFOLD)["verdict"], "unresolved")

    def test_missing_source_capture_remains_unresolved(self):
        train = {"paths": [{"nodes": ["t105", "source-91"], "relations": ["DERIVED_FROM"]}]}
        evaluation = {"paths": [{"nodes": ["e205", "source-91"], "relations": ["DERIVED_FROM"]}]}
        self.assertFalse(inspect_entity(self.fixture, "source-91")["available"])
        self.assertEqual(assess_lineage(self.fixture, "t105", "e205", train, evaluation)["verdict"], "unresolved")

    def test_fabricated_scaffold_paths_cannot_clear_a_candidate(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["links"] = [link for link in fixture["links"] if not (link["source"] == "t104" and link["target"] == "source-42")]
        self.assertEqual(assess_lineage(fixture, "t104", "e204", TRAIN_SCAFFOLD, EVAL_SCAFFOLD)["verdict"], "unresolved")
        fabricated = {"paths": [{"nodes": ["t104", "nonexistent", "source-42"], "relations": ["DERIVED_FROM", "USES_SCAFFOLD"]}]}
        self.assertEqual(assess_lineage(self.fixture, "t104", "e204", fabricated, EVAL_SCAFFOLD)["verdict"], "unresolved")

    def test_artifact_traversal_absolute_paths_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_fixture(directory)
            external = Path(directory) / "outside.txt"
            external.write_text("outside")
            manifest = json.loads((root / "dataset.json").read_text())
            for bad_path in ("../outside.txt", str(external)):
                with self.subTest(path=bad_path):
                    manifest["records"][0]["content_path"] = bad_path
                    (root / "dataset.json").write_text(json.dumps(manifest))
                    with self.assertRaises(EvidenceError):
                        load_fixture(root)
            (root / "link.txt").symlink_to(external)
            manifest["records"][0]["content_path"] = "link.txt"
            (root / "dataset.json").write_text(json.dumps(manifest))
            with self.assertRaises(EvidenceError):
                load_fixture(root)

    def test_mixed_repair_has_independent_exhaustive_certificate(self):
        records = {record["id"]: record for record in self.fixture["records"]}
        vertices = ["t101", "t102", "e201", "e202"]
        conflicts = [("t101", "e201"), ("t101", "e202"), ("t102", "e202")]
        choices = []
        for mask in range(1 << len(vertices)):
            excluded = {vertex for bit, vertex in enumerate(vertices) if mask & (1 << bit)}
            if all(train in excluded or evaluation in excluded for train, evaluation in conflicts):
                choices.append((sum(records[vertex]["cost"] for vertex in excluded), excluded))
        best = min(choices, key=lambda entry: entry[0])
        self.assertEqual(best, (3, {"t101", "e202"}))
        self.assertLess(best[0], sum(records[vertex]["cost"] for vertex in ("t101", "t102")))
        self.assertLess(best[0], sum(records[vertex]["cost"] for vertex in ("e201", "e202")))


if __name__ == "__main__":
    unittest.main()
