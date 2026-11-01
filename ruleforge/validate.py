"""Validation gates applied to every LLM-produced rule set.

Four layers, each producing machine-readable errors that are fed back to the
model during the repair loop:

1. structure  - IR shape, known ops, known decisions, unique ids
2. schema     - fields exist in the data dictionary and values match their type
3. grounding  - every rule quotes the policy verbatim, and every numeric
                threshold it uses literally appears in that quote
4. codes      - every code value appears somewhere in the policy text
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .dictionary import DataDictionary
from .ir import ALL_OPS, DECISIONS, LIST_OPS, NUMERIC_OPS, SET_OPS, leaves


@dataclass
class Issue:
    rule_id: str
    layer: str
    message: str

    def __str__(self) -> str:
        return f"[{self.layer}] {self.rule_id}: {self.message}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _check_node(node, rule_id, issues, path="when"):
    if not isinstance(node, dict):
        issues.append(Issue(rule_id, "structure", f"{path} is not an object"))
        return
    keys = set(node)
    if "field" in keys:
        missing = {"field", "op", "value"} - keys
        if missing:
            issues.append(Issue(rule_id, "structure", f"{path} leaf missing {sorted(missing)}"))
        elif node["op"] not in ALL_OPS:
            issues.append(Issue(rule_id, "structure", f"{path} unknown op {node['op']!r}"))
        return
    combos = keys & {"all", "any", "not"}
    if len(combos) != 1:
        issues.append(Issue(rule_id, "structure", f"{path} must be a leaf or exactly one of all/any/not"))
        return
    k = combos.pop()
    if k == "not":
        _check_node(node["not"], rule_id, issues, f"{path}.not")
    else:
        if not isinstance(node[k], list) or not node[k]:
            issues.append(Issue(rule_id, "structure", f"{path}.{k} must be a non-empty list"))
            return
        for i, c in enumerate(node[k]):
            _check_node(c, rule_id, issues, f"{path}.{k}[{i}]")


def _check_leaf_types(leaf, dd: DataDictionary, rule_id, issues):
    f, op, v = leaf["field"], leaf["op"], leaf["value"]
    if f not in dd:
        known = ", ".join(dd.fields)
        issues.append(Issue(rule_id, "schema", f"unknown field {f!r}; valid fields: {known}"))
        return
    spec = dd[f]
    if spec.type == "int":
        if op in SET_OPS | LIST_OPS:
            issues.append(Issue(rule_id, "schema", f"op {op} not valid for int field {f}"))
        elif not isinstance(v, int) or isinstance(v, bool):
            issues.append(Issue(rule_id, "schema", f"{f} expects an integer, got {v!r}"))
        elif (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max):
            issues.append(Issue(rule_id, "schema", f"{f} threshold {v} outside domain [{spec.min}, {spec.max}]"))
    elif spec.type == "bool":
        if op not in {"==", "!="} or not isinstance(v, bool):
            issues.append(Issue(rule_id, "schema", f"{f} is bool: use ==/!= with true/false"))
    elif spec.type == "enum":
        vals = v if op in SET_OPS else [v]
        if op in NUMERIC_OPS | LIST_OPS:
            issues.append(Issue(rule_id, "schema", f"op {op} not valid for enum field {f}"))
        elif op in SET_OPS and not isinstance(v, list):
            issues.append(Issue(rule_id, "schema", f"{op} expects a list for {f}"))
        else:
            bad = [x for x in vals if x not in (spec.values or [])]
            if bad:
                issues.append(Issue(rule_id, "schema", f"{f} values {bad} not in domain {spec.values}"))
    elif spec.type == "code_list":
        if op not in LIST_OPS or not isinstance(v, list) or not v:
            issues.append(Issue(rule_id, "schema", f"{f} is a code list: use includes_any/includes_none with a non-empty list"))


def validate(ruleset_dict: dict, dd: DataDictionary, policy_text: str) -> list[Issue]:
    issues: list[Issue] = []
    rules = ruleset_dict.get("rules")
    if not isinstance(rules, list) or not rules:
        return [Issue("*", "structure", "top-level 'rules' must be a non-empty list")]
    if ruleset_dict.get("default_decision") not in DECISIONS:
        issues.append(Issue("*", "structure", f"default_decision must be one of {sorted(DECISIONS)}"))

    norm_policy = _norm(policy_text)
    seen = set()
    for i, r in enumerate(rules):
        rid = r.get("id") or f"rules[{i}]"
        if rid in seen:
            issues.append(Issue(rid, "structure", "duplicate rule id"))
        seen.add(rid)
        if "when" not in r:
            issues.append(Issue(rid, "structure", "missing 'when'"))
            continue
        then = r.get("then", {})
        if then.get("decision") not in DECISIONS:
            issues.append(Issue(rid, "structure", f"then.decision must be one of {sorted(DECISIONS)}"))
        if not then.get("reason_code"):
            issues.append(Issue(rid, "structure", "then.reason_code is required"))
        if not isinstance(r.get("priority", 0), int):
            issues.append(Issue(rid, "structure", "priority must be an integer"))

        before = len(issues)
        _check_node(r["when"], rid, issues)
        if len(issues) > before:
            continue  # tree is malformed; deeper checks would be noise

        for leaf in leaves(r["when"]):
            _check_leaf_types(leaf, dd, rid, issues)

        # --- grounding -------------------------------------------------------
        quote = r.get("source_quote", "")
        nq = _norm(quote)
        if len(nq) < 15:
            issues.append(Issue(rid, "grounding", "source_quote must be a verbatim sentence from the policy"))
            continue
        if nq not in norm_policy:
            issues.append(Issue(rid, "grounding", f"source_quote not found verbatim in policy: {quote[:80]!r}"))
            continue
        quote_numbers = set(re.findall(r"\d+", quote))
        for leaf in leaves(r["when"]):
            v = leaf["value"]
            if isinstance(v, int) and not isinstance(v, bool) and str(v) not in quote_numbers:
                # allow the off-by-one encodings of "more than N" / "at least N"
                if not ({str(v - 1), str(v + 1)} & quote_numbers):
                    issues.append(Issue(rid, "grounding",
                        f"threshold {leaf['field']} {leaf['op']} {v} does not appear in its source_quote"))
            if isinstance(v, list) and leaf["field"] in dd and dd[leaf["field"]].type == "code_list":
                for code in v:
                    if _norm(code) not in norm_policy:
                        issues.append(Issue(rid, "codes", f"code {code!r} does not appear in the policy text"))
    return issues
