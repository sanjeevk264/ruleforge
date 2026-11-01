import copy
import json
from pathlib import Path

import pytest

from ruleforge import conflicts, mcdc, mutation
from ruleforge.compiler import compile_policy
from ruleforge.dictionary import DataDictionary
from ruleforge.export_drl import to_drl
from ruleforge.ir import RuleSet, decide, eval3
from ruleforge.llm import ReplayProvider
from ruleforge.validate import validate

EX = Path(__file__).parent.parent / "examples" / "lumbar_mri"
DD = DataDictionary.load(EX / "data_dictionary.json")
POLICY = (EX / "policy.md").read_text()
CASSETTE = EX / "cassettes" / "demo.json"


@pytest.fixture(scope="module")
def ruleset():
    res = compile_policy(POLICY, DD, ReplayProvider(CASSETTE), log=lambda *_: None)
    assert res.ok
    return res.ruleset


def good_dict():
    return json.loads(json.loads(CASSETTE.read_text())["responses"][1].split("```json")[1].split("```")[0])


# --- three-valued logic -----------------------------------------------------

def test_eval3_unknown_propagates():
    node = {"all": [{"field": "a", "op": ">", "value": 1}, {"field": "b", "op": "==", "value": True}]}
    assert eval3(node, {"a": 5}) is None
    assert eval3(node, {"a": 0}) is False  # short-circuits despite unknown b
    assert eval3({"any": [{"field": "a", "op": ">", "value": 1}]}, {}) is None
    assert eval3({"not": {"field": "a", "op": ">", "value": 1}}, {"a": 0}) is True


# --- validation gates --------------------------------------------------------

def test_repair_loop_fixes_all_three_error_types():
    res = compile_policy(POLICY, DD, ReplayProvider(CASSETTE), log=lambda *_: None)
    assert res.ok and res.attempts == 2
    layers = sorted(i.layer for i in res.history[0])
    assert layers == ["codes", "grounding", "schema"]


def test_rejects_quote_not_in_policy():
    d = good_dict()
    d["rules"][1]["source_quote"] = "Members under 18 always need a specialist review first."
    issues = validate(d, DD, POLICY)
    assert any(i.layer == "grounding" and i.rule_id == "R2" for i in issues)


def test_rejects_enum_value_outside_domain():
    d = good_dict()
    d["rules"][5]["when"]["value"] = "OUT_OF_NETWORK"
    assert any(i.layer == "schema" for i in validate(d, DD, POLICY))


def test_accepts_clean_ruleset():
    assert validate(good_dict(), DD, POLICY) == []


def test_unparseable_output_triggers_retry():
    class Junk:
        def __init__(self): self.n = 0
        def complete(self, s, m):
            self.n += 1
            return "sorry, I can't" if self.n == 1 else json.loads(CASSETTE.read_text())["responses"][1]
    res = compile_policy(POLICY, DD, Junk(), log=lambda *_: None)
    assert res.ok and res.attempts == 2


# --- decisions ---------------------------------------------------------------

@pytest.mark.parametrize("claim,expected", [
    ({"member_age": 40, "diagnosis_codes": ["G83.4"], "conservative_therapy_weeks": 0,
      "symptom_duration_weeks": 1, "months_since_last_lumbar_mri": 2, "new_neuro_deficit": False,
      "provider_network": "OUT"}, ("APPROVE", "R1")),
    ({"member_age": 40, "diagnosis_codes": [], "conservative_therapy_weeks": 8,
      "symptom_duration_weeks": 9, "months_since_last_lumbar_mri": 6, "new_neuro_deficit": False,
      "provider_network": "IN"}, ("DENY", "R3")),
    ({"member_age": 40, "diagnosis_codes": [], "conservative_therapy_weeks": 8,
      "symptom_duration_weeks": 9, "months_since_last_lumbar_mri": 6, "new_neuro_deficit": True,
      "provider_network": "IN"}, ("APPROVE", "R4")),
])
def test_decisions(ruleset, claim, expected):
    assert decide(ruleset, claim) == expected


# --- MC/DC -------------------------------------------------------------------

def test_mcdc_full_coverage_and_pairs_flip(ruleset):
    cov = mcdc.generate(ruleset, DD)
    assert cov.covered == cov.total == 9
    assert not cov.uncoverable
    assert mcdc.run_cases(ruleset, [c.to_dict() for c in cov.cases]) == []


def test_mcdc_detects_redundant_condition():
    rs = RuleSet.from_dict({"policy_id": "x", "default_decision": "PEND_REVIEW", "rules": [{
        "id": "A", "source_quote": "", "priority": 1,
        "when": {"all": [{"field": "member_age", "op": ">=", "value": 18},
                         {"field": "member_age", "op": ">=", "value": 10}]},
        "then": {"decision": "APPROVE", "reason_code": "X"}}]})
    cov = mcdc.generate(rs, DD)
    assert cov.uncoverable == [("A", "member_age >= 10")]


def test_golden_cases_catch_a_regression(ruleset):
    cases = [c.to_dict() for c in mcdc.generate(ruleset, DD).cases]
    mutated = RuleSet.from_dict(copy.deepcopy(ruleset.to_dict()))
    mutated.rules[3].when["all"][1]["value"] = 4  # someone edits 6 weeks -> 4 weeks
    assert mcdc.run_cases(mutated, cases)


# --- static analysis ---------------------------------------------------------

def test_conflicts_found_with_valid_witnesses(ruleset):
    found = conflicts.analyze(ruleset, DD)
    unresolved = {f.rules for f in found if f.kind == "UNRESOLVED_CONFLICT"}
    assert unresolved == {("R2", "R5"), ("R4", "R6"), ("R5", "R6")}
    by_id = {r.id: r for r in ruleset.rules}
    for f in found:
        if f.witness:
            for rid in f.rules:
                assert eval3(by_id[rid].when, f.witness) is True


def test_dead_and_shadowed_rules():
    rs = RuleSet.from_dict({"policy_id": "x", "default_decision": "PEND_REVIEW", "rules": [
        {"id": "DEAD", "source_quote": "", "priority": 1, "then": {"decision": "DENY", "reason_code": "X"},
         "when": {"all": [{"field": "member_age", "op": "<", "value": 10},
                          {"field": "member_age", "op": ">", "value": 20}]}},
        {"id": "WIDE", "source_quote": "", "priority": 9, "then": {"decision": "APPROVE", "reason_code": "X"},
         "when": {"field": "member_age", "op": ">=", "value": 0}},
        {"id": "HIDDEN", "source_quote": "", "priority": 1, "then": {"decision": "DENY", "reason_code": "X"},
         "when": {"field": "member_age", "op": ">", "value": 65}},
    ]})
    kinds = {(f.kind, f.rules[0]) for f in conflicts.analyze(rs, DD)}
    assert ("DEAD_RULE", "DEAD") in kinds
    assert ("SHADOWED_RULE", "HIDDEN") in kinds


# --- export ------------------------------------------------------------------

def test_drl_export(ruleset):
    drl = to_drl(ruleset, DD)
    assert drl.count('activation-group "policy-decision"') == 6
    assert "monthsSinceLastLumbarMri < 12 && newNeuroDeficit == false" in drl
    assert drl.index('"R1:') < drl.index('"R3:') < drl.index('"R4:')  # salience order


# --- mutation testing --------------------------------------------------------

def test_mutation_score_and_equivalence_proof(ruleset):
    cases = [c.to_dict() for c in mcdc.generate(ruleset, DD).cases]
    res = mutation.run(ruleset, cases, DD)
    assert res.survivors == []
    assert res.pct == 100.0
    # R4 age >= 17 is indistinguishable: a 17-year-old always hits pediatric review (R2) first
    assert res.equivalent == ["R4: member_age >= 18 -> >= 17"]
