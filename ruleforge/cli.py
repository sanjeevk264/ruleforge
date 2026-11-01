"""RuleForge command line.

    ruleforge build  POLICY --dict DICT --out DIR [--cassette FILE | --record FILE]
    ruleforge check  RULES  --dict DICT
    ruleforge test   RULES  --cases CASES
    ruleforge decide RULES  --claim JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import conflicts, mcdc, mutation
from .compiler import compile_policy
from .dictionary import DataDictionary
from .export_drl import to_drl
from .ir import RuleSet, decide
from .llm import AnthropicProvider, Recorder, ReplayProvider


def _report_md(rs: RuleSet, cov: mcdc.CoverageReport, findings, attempts: int,
               mut: mutation.MutationResult) -> str:
    lines = [f"# RuleForge report: `{rs.policy_id}`", "",
             f"- Rules compiled: **{len(rs.rules)}** (valid after {attempts} attempt(s))",
             f"- MC/DC coverage: **{cov.covered}/{cov.total} conditions ({cov.pct:.0f}%)**",
             f"- Generated test cases: **{len(cov.cases)}**",
             f"- Mutation score: **{mut.killed}/{mut.total - len(mut.equivalent)} non-equivalent mutants killed "
             f"({mut.pct:.0f}%)**, {len(mut.equivalent)} proven equivalent", ""]
    counts = {}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    lines.append("## Static analysis")
    lines.append("")
    if not findings:
        lines.append("No conflicts, dead or shadowed rules.")
    for kind in ["UNRESOLVED_CONFLICT", "DEAD_RULE", "SHADOWED_RULE", "RESOLVED_BY_PRIORITY"]:
        group = [f for f in findings if f.kind == kind]
        if not group:
            continue
        lines += [f"### {kind.replace('_', ' ').title()} ({len(group)})", ""]
        for f in group:
            lines.append(f"- **{' vs '.join(f.rules)}**: {f.detail}")
            if f.witness:
                w = ", ".join(f"`{k}={v}`" for k, v in f.witness.items())
                lines.append(f"  - witness claim: {w}")
        lines.append("")
    if cov.uncoverable:
        lines += ["## Conditions with no independent effect", ""]
        for rid, c in cov.uncoverable:
            lines.append(f"- {rid}: `{c}`")
        lines.append("")
    if mut.survivors or mut.equivalent:
        lines += ["## Mutation testing", ""]
        for m in mut.survivors:
            lines.append(f"- SURVIVED: `{m}` (add a test that distinguishes it)")
        for m in mut.equivalent:
            lines.append(f"- equivalent: `{m}` (no claim can tell it apart from the original)")
        lines.append("")
    lines += ["## Rules", "", "| id | priority | decision | reason | source |", "|---|---|---|---|---|"]
    for r in rs.ordered():
        lines.append(f"| {r.id} | {r.priority} | {r.decision} | {r.reason_code} | {r.source_quote[:70]}… |")
    return "\n".join(lines) + "\n"


def cmd_build(a) -> int:
    dd = DataDictionary.load(a.dict)
    policy = Path(a.policy).read_text()
    if a.cassette:
        provider = ReplayProvider(a.cassette)
    else:
        provider = AnthropicProvider(model=a.model)
        if a.record:
            provider = Recorder(provider, a.record)
    res = compile_policy(policy, dd, provider, max_attempts=a.max_attempts)
    if not res.ok:
        print("compilation failed after repair attempts", file=sys.stderr)
        return 2
    rs = res.ruleset
    cov = mcdc.generate(rs, dd)
    findings = conflicts.analyze(rs, dd)
    mut = mutation.run(rs, [c.to_dict() for c in cov.cases], dd)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rules.json").write_text(json.dumps(rs.to_dict(), indent=2))
    (out / "rules.drl").write_text(to_drl(rs, dd))
    (out / "test_cases.json").write_text(json.dumps([c.to_dict() for c in cov.cases], indent=2))
    (out / "analysis.json").write_text(json.dumps([f.to_dict() for f in findings], indent=2))
    (out / "REPORT.md").write_text(_report_md(rs, cov, findings, res.attempts, mut))
    unresolved = sum(f.kind == "UNRESOLVED_CONFLICT" for f in findings)
    print(f"MC/DC {cov.covered}/{cov.total} | {len(cov.cases)} tests | mutation score {mut.pct:.0f}% | "
          f"{unresolved} unresolved conflict(s) | wrote {out}/")
    return 1 if (a.strict and unresolved) else 0


def cmd_check(a) -> int:
    dd = DataDictionary.load(a.dict)
    rs = RuleSet.from_dict(json.loads(Path(a.rules).read_text()))
    findings = conflicts.analyze(rs, dd)
    for f in findings:
        print(f"{f.kind:22} {' vs '.join(f.rules):10} {f.detail}")
    return 1 if any(f.kind == "UNRESOLVED_CONFLICT" for f in findings) else 0


def cmd_test(a) -> int:
    rs = RuleSet.from_dict(json.loads(Path(a.rules).read_text()))
    cases = json.loads(Path(a.cases).read_text())
    fails = mcdc.run_cases(rs, cases)
    for f in fails:
        print("FAIL", f)
    print(f"{len(cases) - len(fails)}/{len(cases)} passed")
    return 1 if fails else 0


def cmd_decide(a) -> int:
    rs = RuleSet.from_dict(json.loads(Path(a.rules).read_text()))
    claim = json.loads(a.claim)
    dec, rid = decide(rs, claim)
    rule = next((r for r in rs.rules if r.id == rid), None)
    print(json.dumps({"decision": dec, "rule": rid,
                      "reason_code": rule.reason_code if rule else None,
                      "policy_text": rule.source_quote if rule else None}, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ruleforge", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="compile a policy into rules, tests, DRL and a report")
    b.add_argument("policy")
    b.add_argument("--dict", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--cassette", help="replay recorded model responses (offline)")
    b.add_argument("--record", help="save live model responses to this cassette")
    b.add_argument("--model")
    b.add_argument("--max-attempts", type=int, default=3)
    b.add_argument("--strict", action="store_true", help="exit 1 on unresolved conflicts (for CI)")
    b.set_defaults(fn=cmd_build)
    c = sub.add_parser("check", help="static analysis of an existing rules.json")
    c.add_argument("rules"); c.add_argument("--dict", required=True); c.set_defaults(fn=cmd_check)
    t = sub.add_parser("test", help="replay golden test cases against rules.json")
    t.add_argument("rules"); t.add_argument("--cases", required=True); t.set_defaults(fn=cmd_test)
    d = sub.add_parser("decide", help="evaluate one claim and explain the decision")
    d.add_argument("rules"); d.add_argument("--claim", required=True); d.set_defaults(fn=cmd_decide)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
