"""Mutation testing: measure whether the generated tests would catch real edits.

Each mutant is the rule set with one small, plausible change, the kind of
off-by-one or wrong-operator slip a human (or a model) makes when editing a
rule. A mutant is "killed" if at least one generated test case fails on it.
The kill rate is a more honest quality signal than coverage alone.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .dictionary import DataDictionary
from .ir import RuleSet, decide, leaves
from .mcdc import run_cases
from .solver import fields_of, full_record, search

OP_SWAPS = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==",
            "in": "not_in", "not_in": "in", "includes_any": "includes_none", "includes_none": "includes_any"}
FLIP_DECISION = {"APPROVE": "DENY", "DENY": "APPROVE", "PEND_REVIEW": "APPROVE"}


@dataclass
class MutationResult:
    total: int
    killed: int
    survivors: list[str]
    equivalent: list[str]

    @property
    def pct(self) -> float:
        """Kill rate over non-equivalent mutants."""
        live = self.total - len(self.equivalent)
        return 100.0 * self.killed / live if live else 100.0


def mutants(rs: RuleSet):
    base = rs.to_dict()
    for ri, rule in enumerate(base["rules"]):
        for li, _ in enumerate(leaves(rule["when"])):
            def edit(fn, label, ri=ri, li=li):
                m = copy.deepcopy(base)
                leaf = list(leaves(m["rules"][ri]["when"]))[li]
                desc = f"{m['rules'][ri]['id']}: {leaf['field']} {leaf['op']} {leaf['value']} -> "
                fn(leaf)
                return desc + f"{leaf['op']} {leaf['value']}", RuleSet.from_dict(m)
            yield edit(lambda l: l.update(op=OP_SWAPS[l["op"]]), "op")
            v = list(leaves(rule["when"]))[li]["value"]
            if isinstance(v, int) and not isinstance(v, bool):
                yield edit(lambda l: l.update(value=l["value"] + 1), "+1")
                yield edit(lambda l: l.update(value=l["value"] - 1), "-1")
            if isinstance(v, list) and len(v) > 1:
                for k in range(len(v)):
                    yield edit(lambda l, k=k: l.update(value=[c for j, c in enumerate(l["value"]) if j != k]), "drop")
        m = copy.deepcopy(base)
        m["rules"][ri]["then"]["decision"] = FLIP_DECISION[rule["then"]["decision"]]
        yield f"{rule['id']}: decision -> {m['rules'][ri]['then']['decision']}", RuleSet.from_dict(m)


def is_equivalent(a: RuleSet, b: RuleSet, dd: DataDictionary) -> bool:
    """True if no claim gets a different decision under a and b (exhaustive over candidate domains)."""
    conds = [r.when for r in a.rules] + [r.when for r in b.rules]
    flds = fields_of(conds)

    def differs(rec):
        if len(rec) < len(flds):
            return None
        full = full_record(rec, dd)
        return decide(a, full) != decide(b, full)
    return search(differs, flds, dd, conds) is None


def run(rs: RuleSet, cases: list[dict], dd: DataDictionary) -> MutationResult:
    total, killed, survivors, equivalent = 0, 0, [], []
    for label, m in mutants(rs):
        total += 1
        if run_cases(m, cases):
            killed += 1
        elif is_equivalent(rs, m, dd):
            equivalent.append(label)
        else:
            survivors.append(label)
    return MutationResult(total, killed, survivors, equivalent)
