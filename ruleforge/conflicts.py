"""Static analysis of a rule set: conflicts, shadowing and dead rules.

For every pair of rules with different decisions we ask the solver for a claim
that triggers both. If one exists:

- equal priority  -> UNRESOLVED conflict (outcome depends on rule order: a
                     policy ambiguity a human has to settle)
- unequal priority -> RESOLVED by priority, reported so reviewers can confirm
                      the precedence is intended

Each finding carries a concrete witness claim, which is what makes the report
actionable for a policy owner rather than a list of abstract warnings.
"""
from __future__ import annotations

from dataclasses import dataclass

from .dictionary import DataDictionary
from .ir import Rule, RuleSet, decide, eval3
from .solver import fields_of, full_record, search


@dataclass
class Finding:
    kind: str  # UNRESOLVED_CONFLICT | RESOLVED_BY_PRIORITY | DEAD_RULE | SHADOWED_RULE
    rules: tuple[str, ...]
    detail: str
    witness: dict | None = None

    def to_dict(self):
        return {"kind": self.kind, "rules": list(self.rules), "detail": self.detail, "witness": self.witness}


def _both(a: Rule, b: Rule):
    def goal(rec):
        x, y = eval3(a.when, rec), eval3(b.when, rec)
        if x is False or y is False:
            return False
        return True if (x and y) else None
    return goal


def analyze(ruleset: RuleSet, dd: DataDictionary) -> list[Finding]:
    findings: list[Finding] = []
    rules = ruleset.rules

    for r in rules:
        conds = [r.when]
        if search(lambda rec, r=r: eval3(r.when, rec), fields_of(conds), dd, conds) is None:
            findings.append(Finding("DEAD_RULE", (r.id,), "no claim can satisfy this rule's conditions"))

    for i, a in enumerate(rules):
        for b in rules[i + 1:]:
            if a.decision == b.decision:
                continue
            conds = [a.when, b.when]
            hit = search(_both(a, b), fields_of(conds), dd, conds)
            if hit is None:
                continue
            witness = full_record(hit, dd)
            if a.priority == b.priority:
                findings.append(Finding(
                    "UNRESOLVED_CONFLICT", (a.id, b.id),
                    f"{a.id} -> {a.decision} and {b.id} -> {b.decision} both fire at priority {a.priority}; "
                    f"outcome depends on rule order", witness))
            else:
                hi, lo = (a, b) if a.priority > b.priority else (b, a)
                findings.append(Finding(
                    "RESOLVED_BY_PRIORITY", (hi.id, lo.id),
                    f"{hi.id} ({hi.decision}, p={hi.priority}) overrides {lo.id} ({lo.decision}, p={lo.priority})",
                    witness))

    # A rule is shadowed if it can be satisfied but never wins.
    dead = {f.rules[0] for f in findings if f.kind == "DEAD_RULE"}
    all_conds = [r.when for r in rules]
    flds = fields_of(all_conds)
    for r in rules:
        if r.id in dead:
            continue
        def wins(rec, r=r):
            if eval3(r.when, rec) is False:
                return False
            for other in ruleset.ordered():
                if other is r:
                    break
                if eval3(other.when, rec) is True:
                    return False
            if len(rec) < len(flds):
                return None
            return decide(ruleset, rec)[1] == r.id
        if search(wins, flds, dd, all_conds) is None:
            findings.append(Finding("SHADOWED_RULE", (r.id,), "satisfiable, but a higher-priority rule always fires first"))
    return findings
