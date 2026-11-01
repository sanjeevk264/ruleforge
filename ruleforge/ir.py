"""Rule intermediate representation (IR) and three-valued evaluation.

A policy is a list of rules. Each rule has a condition tree (`when`) built from
leaves ({"field", "op", "value"}) and combinators ({"all": [...]}, {"any": [...]},
{"not": {...}}), plus a decision (`then`) and a priority.

Evaluation is three-valued: a leaf whose field is missing from a (partial)
record evaluates to None ("unknown"). The constraint solver uses this to prune
its search early.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

NUMERIC_OPS = {"<", "<=", ">", ">="}
EQUALITY_OPS = {"==", "!="}
SET_OPS = {"in", "not_in"}
LIST_OPS = {"includes_any", "includes_none"}
ALL_OPS = NUMERIC_OPS | EQUALITY_OPS | SET_OPS | LIST_OPS
DECISIONS = {"APPROVE", "DENY", "PEND_REVIEW"}


@dataclass
class Rule:
    id: str
    description: str
    source_quote: str
    when: dict
    decision: str
    reason_code: str
    priority: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        then = d.get("then", {})
        return cls(
            id=d["id"],
            description=d.get("description", ""),
            source_quote=d.get("source_quote", ""),
            when=d["when"],
            decision=then.get("decision", ""),
            reason_code=then.get("reason_code", ""),
            priority=int(d.get("priority", 0)),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "source_quote": self.source_quote,
            "priority": self.priority,
            "when": self.when,
            "then": {"decision": self.decision, "reason_code": self.reason_code},
        }


@dataclass
class RuleSet:
    policy_id: str
    default_decision: str
    rules: list[Rule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "RuleSet":
        return cls(
            policy_id=d.get("policy_id", "policy"),
            default_decision=d.get("default_decision", "PEND_REVIEW"),
            rules=[Rule.from_dict(r) for r in d.get("rules", [])],
        )

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "default_decision": self.default_decision,
            "rules": [r.to_dict() for r in self.rules],
        }

    def ordered(self) -> list[Rule]:
        """Rules in firing order: highest priority first, ties keep source order."""
        return sorted(self.rules, key=lambda r: -r.priority)


def is_leaf(node: dict) -> bool:
    return "field" in node


def leaves(node: dict) -> Iterator[dict]:
    if is_leaf(node):
        yield node
    elif "all" in node:
        for c in node["all"]:
            yield from leaves(c)
    elif "any" in node:
        for c in node["any"]:
            yield from leaves(c)
    elif "not" in node:
        yield from leaves(node["not"])


def eval_leaf(leaf: dict, record: dict) -> bool | None:
    f, op, v = leaf["field"], leaf["op"], leaf["value"]
    if f not in record:
        return None
    x = record[f]
    if op == "==":
        return x == v
    if op == "!=":
        return x != v
    if op == "<":
        return x < v
    if op == "<=":
        return x <= v
    if op == ">":
        return x > v
    if op == ">=":
        return x >= v
    if op == "in":
        return x in v
    if op == "not_in":
        return x not in v
    if op == "includes_any":
        return any(c in x for c in v)
    if op == "includes_none":
        return not any(c in x for c in v)
    raise ValueError(f"unknown op {op!r}")


def eval3(node: dict, record: dict) -> bool | None:
    """Kleene three-valued evaluation of a condition tree."""
    if is_leaf(node):
        return eval_leaf(node, record)
    if "all" in node:
        unknown = False
        for c in node["all"]:
            r = eval3(c, record)
            if r is False:
                return False
            if r is None:
                unknown = True
        return None if unknown else True
    if "any" in node:
        unknown = False
        for c in node["any"]:
            r = eval3(c, record)
            if r is True:
                return True
            if r is None:
                unknown = True
        return None if unknown else False
    if "not" in node:
        r = eval3(node["not"], record)
        return None if r is None else (not r)
    raise ValueError(f"malformed condition node: {node!r}")


def decide(ruleset: RuleSet, record: dict) -> tuple[str, str | None]:
    """First-match-by-priority evaluation. Returns (decision, rule_id)."""
    for rule in ruleset.ordered():
        if eval3(rule.when, record) is True:
            return rule.decision, rule.id
    return ruleset.default_decision, None
