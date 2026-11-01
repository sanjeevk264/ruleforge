"""MC/DC test generation (unique-cause, boundary-biased).

Modified Condition/Decision Coverage is the avionics (DO-178C) standard for
proving every condition in a decision independently affects its outcome. For
each leaf condition we search for a pair of records that differ only in that
leaf's field, where the leaf flips and the rule's outcome flips with it.

A leaf with no such pair is reported as uncoverable: it can never change the
rule's result, which usually means the policy text (or the extraction) is
redundant or contradictory.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dfield

from .dictionary import DataDictionary
from .ir import RuleSet, decide, eval3, eval_leaf, leaves
from .solver import candidate_values, fields_of, full_record, search


def describe_leaf(l: dict) -> str:
    return f"{l['field']} {l['op']} {l['value']}"


@dataclass
class TestCase:
    id: str
    rule_id: str
    covers: str
    record: dict
    rule_fires: bool
    expected_decision: str
    expected_rule: str | None

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class CoverageReport:
    cases: list[TestCase] = dfield(default_factory=list)
    covered: int = 0
    total: int = 0
    uncoverable: list[tuple[str, str]] = dfield(default_factory=list)

    @property
    def pct(self) -> float:
        return 100.0 * self.covered / self.total if self.total else 100.0


def generate(ruleset: RuleSet, dd: DataDictionary) -> CoverageReport:
    report = CoverageReport()
    seen_records: set[str] = set()
    n = 0

    def add(rule_id, covers, partial, fires):
        nonlocal n
        rec = full_record(partial, dd)
        key = repr(sorted(rec.items()))
        dec, rid = decide(ruleset, rec)
        if key in seen_records:
            return
        seen_records.add(key)
        n += 1
        report.cases.append(TestCase(f"TC{n:03d}", rule_id, covers, rec, fires, dec, rid))

    all_conds = [r.when for r in ruleset.rules]
    all_fields = fields_of(all_conds)

    for rule in ruleset.rules:
        conds = [rule.when]
        flds = fields_of(conds)
        for leaf in leaves(rule.when):
            report.total += 1
            f = leaf["field"]
            domain = candidate_values(f, dd, all_conds)
            prefer = {}
            if dd[f].type == "int":
                # boundary-value bias: try values nearest the threshold first
                near = lambda v, t=leaf["value"]: abs(v - t)
                domain = sorted(domain, key=near)
                prefer[f] = near
            siblings = [l for l in leaves(rule.when) if l["field"] == f and l is not leaf]

            def flip_value(rec, leaf=leaf, f=f, domain=domain, siblings=siblings):
                for v in domain:
                    trial = dict(rec)
                    trial[f] = v
                    # unique cause: only this condition may change truth value
                    if any(eval_leaf(s, trial) != eval_leaf(s, rec) for s in siblings):
                        continue
                    if eval_leaf(leaf, trial) is False and eval3(rule.when, trial) is False:
                        return v
                return None

            def goal(rec, leaf=leaf):
                a = eval3(rule.when, rec)
                b = eval_leaf(leaf, rec)
                if a is False or b is False:
                    return False
                return True if (a is True and b is True) else None

            def observable(rec):
                """The pair is visible at the policy level: rule wins, and the flip changes the decision."""
                v = flip_value(rec)
                if v is None or decide(ruleset, full_record(rec, dd))[1] != rule.id:
                    return False
                return decide(ruleset, full_record({**rec, f: v}, dd))[1] != rule.id

            # Prefer a pair whose effect is observable through the whole rule set;
            # fall back to rule-local independence (still valid MC/DC for the rule).
            hit = search(goal, all_fields, dd, all_conds, complete=observable, prefer=prefer)
            if hit is None:
                hit = search(goal, flds, dd, conds, complete=lambda r: flip_value(r) is not None, prefer=prefer)
            if hit is None:
                report.uncoverable.append((rule.id, describe_leaf(leaf)))
                continue
            report.covered += 1
            flipped = dict(hit)
            flipped[f] = flip_value(hit)
            add(rule.id, f"{describe_leaf(leaf)} = TRUE", hit, True)
            add(rule.id, f"{describe_leaf(leaf)} = FALSE", flipped, False)
            if leaf["op"] == "includes_any":
                # member coverage: every listed code must trigger the rule on its own
                for code in leaf["value"]:
                    add(rule.id, f"{f} includes {code}", {**hit, f: [code]}, True)
    return report


def run_cases(ruleset: RuleSet, cases: list[dict]) -> list[str]:
    """Replay golden cases against a rule set; returns failure messages."""
    failures = []
    for c in cases:
        dec, rid = decide(ruleset, c["record"])
        if dec != c["expected_decision"] or rid != c["expected_rule"]:
            failures.append(
                f"{c['id']}: expected {c['expected_decision']} via {c['expected_rule']}, got {dec} via {rid}")
    return failures
