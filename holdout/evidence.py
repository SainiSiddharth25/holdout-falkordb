"""Bounded, non-executing evidence inspection for HOLDOUT.

Only provenance paths returned by the graph are assessed here.  This module does
not discover ancestry by traversing the fixture's links in Python.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


MAX_ARTIFACT_BYTES = 256 * 1024
MAX_CONTENT_CHARS = 12_000
SOLUTION_ROLES = {"solution", "answer", "solution_bundle"}


class EvidenceError(ValueError):
    """An input cannot be safely or unambiguously inspected."""


def _contained(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise EvidenceError("Artifact paths must be nonempty, relative paths.")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise EvidenceError("Artifact path escapes the fixture directory.") from exc
    if not candidate.is_file():
        raise EvidenceError(f"Artifact does not exist: {value}")
    return candidate


def _read(root: Path, value: str) -> bytes:
    path = _contained(root, value)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise EvidenceError("Artifact exceeds the inspection size limit.")
    data = path.read_bytes()
    if len(data) > MAX_ARTIFACT_BYTES:
        raise EvidenceError("Artifact exceeds the inspection size limit.")
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_fixture(path: str | Path | None = None) -> dict[str, Any]:
    """Load one local fixture and bind it to an absolute artifact root."""
    candidate = Path(path) if path is not None else Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "dataset.json"
    if candidate.is_dir():
        candidate = candidate / "dataset.json"
    root = candidate.absolute().parent.resolve()
    manifest_path = _contained(root, candidate.name)
    try:
        fixture = json.loads(_read(root, manifest_path.name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("Fixture must be UTF-8 JSON.") from exc
    if not isinstance(fixture, dict) or not isinstance(fixture.get("records"), list):
        raise EvidenceError("Fixture must contain a records list.")
    fixture["_root"] = str(root)
    for key in ("entities", "links", "candidates"):
        if not isinstance(fixture.setdefault(key, []), list):
            raise EvidenceError(f"Fixture {key} must be a list.")
    records: dict[str, dict[str, Any]] = {}
    for record in fixture["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise EvidenceError("Every record needs a string ID.")
        if record["id"] in records:
            raise EvidenceError("Record IDs must be unique.")
        if record.get("split") not in {"train", "eval"}:
            raise EvidenceError("Record split must be train or eval.")
        if isinstance(record.get("cost"), bool) or not isinstance(record.get("cost"), int) or record["cost"] < 0:
            raise EvidenceError("Record exclusion cost must be a nonnegative integer.")
        if not isinstance(record.get("protected", False), bool):
            raise EvidenceError("Record protected must be a boolean.")
        record.setdefault("protected", False)
        record["content_sha256"] = _sha(_read(root, record.get("content_path", "")))
        symbols = record.get("solution_symbols", [])
        if not isinstance(symbols, list) or any(not isinstance(symbol, str) for symbol in symbols):
            raise EvidenceError("solution_symbols must be a list of names.")
        records[record["id"]] = record
    entity_ids: set[str] = set()
    for entity in fixture["entities"]:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or entity["id"] in entity_ids:
            raise EvidenceError("Entity IDs must be unique strings.")
        entity_ids.add(entity["id"])
        if entity.get("path"):
            entity["content_sha256"] = _sha(_read(root, entity["path"]))
    known_ids = set(records) | entity_ids
    for link in fixture["links"]:
        if link.get("source") not in known_ids or link.get("target") not in known_ids:
            raise EvidenceError("Every link endpoint must identify a fixture entity.")
        if link.get("relation") not in {"DERIVED_FROM", "CONTAINS", "USES_SCAFFOLD"}:
            raise EvidenceError("Unsupported provenance relation.")
        if link.get("evidence_path"):
            _read(root, link["evidence_path"])
    seen_candidates: set[str] = set()
    for candidate in fixture["candidates"]:
        if not isinstance(candidate.get("id"), str) or candidate["id"] in seen_candidates:
            raise EvidenceError("Candidate IDs must be unique strings.")
        seen_candidates.add(candidate["id"])
        if records.get(candidate.get("train_id"), {}).get("split") != "train" or records.get(candidate.get("eval_id"), {}).get("split") != "eval":
            raise EvidenceError("Candidates must join a train record to an eval record.")
        if candidate.get("status", "unresolved") not in {"confirmed", "rejected", "unresolved"}:
            raise EvidenceError("Unsupported candidate status.")
        candidate.setdefault("status", "unresolved")
    return fixture


def _record(fixture: dict[str, Any], record_id: str) -> dict[str, Any]:
    for record in fixture["records"]:
        if record["id"] == record_id:
            return record
    raise EvidenceError(f"Unknown record ID: {record_id}")


def _entity(fixture: dict[str, Any], entity_id: str) -> dict[str, Any]:
    # A record is also a graph entity.  Its artifact contract takes precedence.
    for record in fixture["records"]:
        if record["id"] == entity_id:
            return record
    for entity in fixture["entities"]:
        if entity["id"] == entity_id:
            return entity
    raise EvidenceError(f"Unknown entity ID: {entity_id}")


def _entity_bytes(fixture: dict[str, Any], entity_id: str) -> bytes | None:
    entity = _entity(fixture, entity_id)
    path = entity.get("content_path") or entity.get("path")
    return _read(Path(fixture["_root"]), path) if path else None


def inspect_entity(fixture: dict[str, Any], entity_id: str) -> dict[str, Any]:
    entity = _entity(fixture, entity_id)
    data = _entity_bytes(fixture, entity_id)
    result = dict(entity)
    result["available"] = data is not None
    if data is None:
        result.update(content=None, reason="The fixture has a reference, but no captured artifact.")
        return result
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("Only UTF-8 text artifacts are supported.") from exc
    result.update(content=content[:MAX_CONTENT_CHARS], content_sha256=_sha(data), byte_length=len(data), truncated=len(content) > MAX_CONTENT_CHARS)
    return result


def inspect_record(fixture: dict[str, Any], record_id: str) -> dict[str, Any]:
    _record(fixture, record_id)
    result = inspect_entity(fixture, record_id)
    result["available_methods"] = ["exact", "normalized_ast", "provenance"]
    return result


def _answer_units(record: dict[str, Any], text: str) -> tuple[list[tuple[str, ast.AST, str]], str | None]:
    if record.get("content_role") not in SOLUTION_ROLES:
        return [], "This artifact does not declare directly inspectable answer code."
    symbols = record.get("solution_symbols", [])
    if not symbols:
        return [], "No independently solution-bearing code symbols are declared."
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return [], "The artifact is not supported Python source."
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    lines = text.splitlines(keepends=True)
    result = []
    for symbol in symbols:
        node = definitions.get(symbol)
        if node is None:
            return [], f"Declared solution symbol {symbol!r} is absent."
        start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]) - 1
        source = "".join(lines[start:node.end_lineno]).rstrip("\r\n")
        result.append((symbol, node, source))
    return result, None


def _normalized(node: ast.AST) -> str | None:
    """Alpha-normalize a bounded single-function Python subset.

    Only the function's own name, parameters, and local bindings are renamed.
    Constants, operators, attributes, imports, and unbound/global names are never
    erased.  Constructs introducing another lexical scope are unsupported.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    unsupported = (ast.ClassDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom, ast.NamedExpr)
    for child in ast.walk(node):
        if isinstance(child, unsupported) or (isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node) or (hasattr(ast, "Match") and isinstance(child, ast.Match)):
            return None
    clone = copy.deepcopy(node)
    bindings: dict[str, str] = {node.name: "__function__"}
    args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    if node.args.vararg:
        args += [node.args.vararg]
    if node.args.kwarg:
        args += [node.args.kwarg]
    for arg in args:
        if arg.arg not in bindings:
            bindings[arg.arg] = f"__bound_{len(bindings)}__"
    for statement in node.body:
        for child in ast.walk(statement):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)) and child.id not in bindings:
                bindings[child.id] = f"__bound_{len(bindings)}__"
            if isinstance(child, ast.ExceptHandler) and child.name and child.name not in bindings:
                bindings[child.name] = f"__bound_{len(bindings)}__"

    class RenameBody(ast.NodeTransformer):
        def visit_Name(self, item: ast.Name) -> ast.Name:
            if item.id in bindings:
                item.id = bindings[item.id]
            return item

        def visit_ExceptHandler(self, item: ast.ExceptHandler) -> ast.ExceptHandler:
            if item.name in bindings:
                item.name = bindings[item.name]
            return self.generic_visit(item)

    clone.name = "__function__"
    clone_args = clone.args.posonlyargs + clone.args.args + clone.args.kwonlyargs
    if clone.args.vararg:
        clone_args += [clone.args.vararg]
    if clone.args.kwarg:
        clone_args += [clone.args.kwarg]
    for arg in clone_args:
        arg.arg = bindings[arg.arg]
    # Defaults, decorators and annotations are evaluated outside this local body.
    clone.body = [RenameBody().visit(statement) for statement in clone.body]
    return ast.dump(clone, annotate_fields=True, include_attributes=False)


def compare_records(fixture: dict[str, Any], train_id: str, eval_id: str, method: str = "exact") -> dict[str, Any]:
    if method not in {"exact", "normalized_ast"}:
        raise EvidenceError("Comparison method must be exact or normalized_ast.")
    train = _record(fixture, train_id)
    evaluation = _record(fixture, eval_id)
    if train["split"] != "train" or evaluation["split"] != "eval":
        raise EvidenceError("Comparison requires train_id and eval_id in their respective pools.")
    train_data, eval_data = _entity_bytes(fixture, train_id), _entity_bytes(fixture, eval_id)
    assert train_data is not None and eval_data is not None
    try:
        train_text, eval_text = train_data.decode("utf-8"), eval_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("Code comparison supports UTF-8 source only.") from exc
    train_units, train_error = _answer_units(train, train_text)
    eval_units, eval_error = _answer_units(evaluation, eval_text)
    evidence: list[dict[str, Any]] = [{"kind": "artifact_hashes", "train_id": train_id, "eval_id": eval_id, "train_sha256": _sha(train_data), "eval_sha256": _sha(eval_data)}]
    for train_name, train_node, train_source in train_units:
        for eval_name, eval_node, eval_source in eval_units:
            if method == "exact":
                equal = train_source == eval_source
                digest = _sha(train_source.encode("utf-8"))
            else:
                train_normal, eval_normal = _normalized(train_node), _normalized(eval_node)
                equal = train_normal is not None and train_normal == eval_normal
                digest = _sha((train_normal or "").encode("utf-8"))
            if equal:
                evidence.append({"kind": "solution_unit_match", "train_symbol": train_name, "eval_symbol": eval_name, "comparison_sha256": digest, "scope": "declared independently solution-bearing functions"})
                reason = "A declared answer function occurs exactly in both captured artifacts." if method == "exact" else "Declared answer functions have equal ASTs after renaming only local bindings and the function name; constants, operators, attributes and global names are preserved. This is a bounded structural match, not a claim of arbitrary semantic equivalence."
                return {"verdict": "confirmed", "method": method, "reason": reason, "evidence": evidence}
    reasons = [reason for reason in (train_error, eval_error) if reason]
    evidence.append({"kind": "comparison_coverage", "train_symbols": [name for name, _, _ in train_units], "eval_symbols": [name for name, _, _ in eval_units], "supported_train_symbols": [name for name, node, _ in train_units if method == "exact" or _normalized(node) is not None], "supported_eval_symbols": [name for name, node, _ in eval_units if method == "exact" or _normalized(node) is not None], "notes": reasons})
    return {"verdict": "unresolved", "method": method, "reason": "No supported answer match was established. A mismatch alone cannot establish independence; provenance or another investigation may still show leakage.", "evidence": evidence}


def _supplied_paths(provenance: Any, start: str) -> list[dict[str, list[str]]]:
    raw = provenance.get("paths", []) if isinstance(provenance, dict) else provenance
    if not isinstance(raw, list):
        return []
    result = []
    for path in raw:
        if not isinstance(path, dict):
            continue
        if not isinstance(path.get("nodes"), list) or not isinstance(path.get("relations"), list):
            continue
        nodes = [node.get("id") if isinstance(node, dict) else node for node in path["nodes"]]
        relations = path.get("relations", [])
        if nodes and nodes[0] == start and len(relations) == len(nodes) - 1 and all(isinstance(item, str) for item in nodes + relations):
            result.append({"nodes": nodes, "relations": relations})
    return result


def _verify_path(fixture: dict[str, Any], path: dict[str, list[str]], solution_id: str) -> tuple[bool, list[dict[str, Any]]]:
    """Validate an already supplied graph path, without discovering new paths."""
    solution = _entity(fixture, solution_id)
    if solution.get("kind") != "solution":
        return False, []
    payload = _entity_bytes(fixture, solution_id)
    if payload is None or len(payload.strip()) < 30:
        return False, []
    proof = []
    for source, target, relation in zip(path["nodes"], path["nodes"][1:], path["relations"]):
        if relation not in {"DERIVED_FROM", "CONTAINS"}:
            return False, proof
        links = [link for link in fixture["links"] if (link["source"], link["target"], link["relation"]) == (source, target, relation)]
        if len(links) != 1 or not links[0].get("evidence_path"):
            return False, proof
        manifest_path = links[0]["evidence_path"]
        manifest_bytes = _read(Path(fixture["_root"]), manifest_path)
        try:
            manifest = json.loads(manifest_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False, proof
        if not isinstance(manifest, dict):
            return False, proof
        source_bytes, target_bytes = _entity_bytes(fixture, source), _entity_bytes(fixture, target)
        if source_bytes is None or target_bytes is None:
            return False, proof
        expected = {"source_id": source, "target_id": target, "relation": relation, "preserves_solution": True, "source_sha256": _sha(source_bytes), "target_sha256": _sha(target_bytes), "solution_id": solution_id, "solution_sha256": _sha(payload)}
        if any(manifest.get(key) != value for key, value in expected.items()):
            return False, proof
        # Manifests alone are insufficient: both captured artifacts must contain
        # the exact solution payload claimed by this link's capture contract.
        if payload.strip() not in source_bytes or payload.strip() not in target_bytes:
            return False, proof
        proof.append({"kind": "verified_lineage_link", "source": source, "target": target, "relation": relation, "manifest_path": manifest_path, "manifest_sha256": _sha(manifest_bytes), "source_sha256": _sha(source_bytes), "target_sha256": _sha(target_bytes), "solution_sha256": _sha(payload)})
    return bool(proof), proof


def assess_lineage(fixture: dict[str, Any], train_id: str, eval_id: str, train_provenance: Any, eval_provenance: Any) -> dict[str, Any]:
    """Assess graph-returned paths and the source evidence supporting each hop.

    A common ancestor or an asserted preserves_solution flag is insufficient.
    Confirmed lineage requires captured answer bytes and hash-bound evidence on
    every supplied hop.  Scaffold rejection concerns that candidate's stated
    basis only, and never certifies complete dataset independence.
    """
    _record(fixture, train_id)
    _record(fixture, eval_id)
    trains, evaluations = _supplied_paths(train_provenance, train_id), _supplied_paths(eval_provenance, eval_id)
    # A stored path receipt is input, not authority. Match each supplied hop to
    # the captured catalog before either solution or scaffold reasoning. This
    # only checks paths already returned; it never discovers additional paths.
    known_ids = {item["id"] for item in fixture["records"] + fixture["entities"]}
    known_edges = {(link["source"], link["target"], link["relation"]) for link in fixture["links"]}

    def captured_path(path: dict[str, list[str]]) -> bool:
        return all(node in known_ids for node in path["nodes"]) and all(
            (source, target, relation) in known_edges
            for source, target, relation in zip(path["nodes"], path["nodes"][1:], path["relations"])
        )

    trains = [path for path in trains if captured_path(path)]
    evaluations = [path for path in evaluations if captured_path(path)]
    common_scaffolds: set[str] = set()
    evidence: list[dict[str, Any]] = []
    for train_path in trains:
        for eval_path in evaluations:
            if train_path["nodes"][-1] != eval_path["nodes"][-1]:
                continue
            shared_id = train_path["nodes"][-1]
            shared = _entity(fixture, shared_id)
            if shared.get("kind") == "scaffold" and "USES_SCAFFOLD" in train_path["relations"] and "USES_SCAFFOLD" in eval_path["relations"]:
                common_scaffolds.add(shared_id)
                continue
            train_valid, train_proof = _verify_path(fixture, train_path, shared_id)
            eval_valid, eval_proof = _verify_path(fixture, eval_path, shared_id)
            if train_valid and eval_valid:
                return {"verdict": "confirmed", "method": "provenance", "reason": "Both graph-returned paths lead to the same captured solution. Every hop has matching artifact hashes and both endpoints contain the exact solution payload.", "evidence": [{"kind": "shared_solution", "solution_id": shared_id, "train_path": train_path, "eval_path": eval_path}] + train_proof + eval_proof}
            evidence.append({"kind": "unverified_common_source", "source_id": shared_id, "train_path": train_path, "eval_path": eval_path, "train_verified": train_valid, "eval_verified": eval_valid})
    candidate = next((item for item in fixture["candidates"] if item["train_id"] == train_id and item["eval_id"] == eval_id), {})
    if common_scaffolds and candidate.get("basis") == "shared_scaffold":
        exact_comparison = compare_records(fixture, train_id, eval_id, "exact")
        if exact_comparison["verdict"] == "confirmed":
            return exact_comparison
        comparison = compare_records(fixture, train_id, eval_id, "normalized_ast")
        if comparison["verdict"] == "confirmed":
            return comparison
        coverage = next((item for item in comparison["evidence"] if item.get("kind") == "comparison_coverage"), {})
        if coverage.get("supported_train_symbols") and coverage.get("supported_eval_symbols"):
            return {"verdict": "rejected", "method": "provenance", "reason": "This candidate was raised solely for a shared scaffold. The graph-supplied common artifact is scaffold, and the declared answer functions were inspected without an exact or normalized match. This rejects the scaffold-based claim, not every possible undiscovered leakage relationship.", "evidence": [{"kind": "shared_scaffold", "entity_ids": sorted(common_scaffolds), "candidate_basis": "shared_scaffold"}] + comparison["evidence"]}
    return {"verdict": "unresolved", "method": "provenance", "reason": "The supplied graph paths do not establish preserved solution lineage. Missing captures, shared references, or absent paths cannot be treated as evidence of independence.", "evidence": evidence or [{"kind": "path_coverage", "train_paths": len(trains), "eval_paths": len(evaluations)}]}
