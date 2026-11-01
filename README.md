# RuleForge

**Turn a written payer policy into executable decision rules, and prove they're right before they ship.**

LLMs are good at reading a clinical policy and drafting rules from it. They're also good at quietly inventing a field name, misreading "12 months" as 18, or slipping an extra diagnosis code into an urgent-approval list. In claims adjudication, every one of those is a wrong payment decision at scale.

RuleForge treats the LLM as an untrusted compiler front-end and wraps it in the verification a rules team would want anyway:

```
policy.md ──► LLM draft ──► validation gates ──► repair loop ──► verified rules.json
                              │                                   │
                              │ schema · grounding · codes        ├─► rules.drl (Drools)
                              └── errors fed back to the model    ├─► MC/DC test suite
                                                                  ├─► mutation score
                                                                  └─► conflict report with witness claims
```

## What it does

| Stage | What it catches | How |
|---|---|---|
| **Schema gate** | Invented fields, wrong types, enum values outside domain | Checks every condition against a data dictionary |
| **Grounding gate** | Hallucinated thresholds, paraphrased or fabricated citations | Each rule must quote the policy *verbatim*; every number it uses must appear in that quote |
| **Code gate** | Diagnosis/procedure codes the policy never mentions | Every code must appear in the source text |
| **Repair loop** | All of the above | Validation errors are fed back to the model as structured feedback (bounded retries) |
| **MC/DC test generation** | Rules that look right but aren't tested | Generates unique-cause MC/DC pairs (the DO-178C avionics standard) with boundary values, and prefers pairs whose effect is visible through the *whole* rule set, not just one rule |
| **Mutation testing** | Weak tests | Applies off-by-one, operator-swap, dropped-code and flipped-decision mutants; **proves** survivors equivalent with the solver instead of hand-waving them |
| **Static analysis** | Policy ambiguity | Finds rule pairs that can fire on the same claim with different decisions, dead rules and shadowed rules, each with a **concrete witness claim** |
| **Drools export** | — | DRL with `salience` + a shared `activation-group`, matching RuleForge's first-match semantics |

Everything is backed by a small finite-domain solver: rules only compare fields to constants, so each field's interesting values (every threshold ±1, every enum value, every listed code) are finite, and a depth-first search with three-valued (Kleene) pruning finds witnesses quickly. That's also why the equivalence proof for mutants is exhaustive rather than sampled.

## Demo (no API key needed)

```bash
pip install -e ".[dev]"
ruleforge build examples/lumbar_mri/policy.md \
  --dict examples/lumbar_mri/data_dictionary.json \
  --cassette examples/lumbar_mri/cassettes/demo.json \
  --out examples/lumbar_mri/output
```

```
attempt 1: 3 issue(s)
    [codes] R1: code 'M54.5' does not appear in the policy text
    [grounding] R3: threshold months_since_last_lumbar_mri < 18 does not appear in its source_quote
    [schema] R4: unknown field 'patient_age'; valid fields: member_age, diagnosis_codes, ...
attempt 2: valid (6 rules)
MC/DC 9/9 | 16 tests | mutation score 100% | 3 unresolved conflict(s) | wrote examples/lumbar_mri/output/
```

The demo cassette is a **scripted fixture**, not a live recording: turn 1 contains three error types typical of LLM rule extraction, turn 2 is the repaired output. It exists so the pipeline is deterministic in CI. To run against a live model and save your own cassette:

```bash
export ANTHROPIC_API_KEY=...
ruleforge build policy.md --dict dict.json --out out/ --record my_session.json
```

### What the analyzer found in the sample policy

The fictional policy [`CP-RAD-014`](examples/lumbar_mri/policy.md) reads reasonably, but it never says how network review interacts with the clinical criteria. RuleForge reports three **unresolved conflicts**, each with a claim that proves it:

- **R4 vs R6**: an adult with 6 weeks of therapy and symptoms, ordered by an out-of-network provider, both meets routine criteria (APPROVE) and requires network review (PEND). The policy doesn't say which wins.
- **R5 vs R6**: insufficient therapy (DENY) vs. out-of-network (PEND).
- **R2 vs R5**: a minor with under 6 weeks of therapy, pediatric review (PEND) vs. denial.

These are exactly the questions a forward-deployed engineer has to take back to the policy owner. The tool turns them from a "we found out in production" incident into a review item with a reproducible example. Full output: [`examples/lumbar_mri/output/REPORT.md`](examples/lumbar_mri/output/REPORT.md).

Mutation testing kills all 29 non-equivalent mutants. The one remaining mutant (`member_age >= 18` → `>= 17`) is **proven equivalent**: every 17-year-old is routed to pediatric review by a rule that fires first, so no claim can distinguish the two.

## Other commands

```bash
ruleforge check  out/rules.json --dict dict.json          # static analysis only; exit 1 on unresolved conflicts
ruleforge test   out/rules.json --cases out/test_cases.json  # replay golden tests (catches regressions on edit)
ruleforge decide out/rules.json --claim '{"member_age": 40, "diagnosis_codes": [], ...}'
```

`decide` returns the decision, the rule that fired and the verbatim policy sentence behind it: an audit trail a reviewer can read.

Use `ruleforge build --strict` in CI to fail the pipeline when a policy change introduces an unresolved conflict.

## Rule IR

```json
{
  "id": "R3",
  "source_quote": "A request is denied when the member had a lumbar MRI within the past 12 months, unless the request documents a new neurological deficit.",
  "priority": 50,
  "when": {"all": [
    {"field": "months_since_last_lumbar_mri", "op": "<", "value": 12},
    {"field": "new_neuro_deficit", "op": "==", "value": false}
  ]},
  "then": {"decision": "DENY", "reason_code": "REPEAT_IMAGING_NOT_INDICATED"}
}
```

Ops: `== != < <= > >=`, `in / not_in` (enums), `includes_any / includes_none` (code lists). Combinators: `all`, `any`, `not`. Field types: `int`, `bool`, `enum`, `code_list`.

## Layout

```
ruleforge/
  ir.py           rule model + three-valued evaluation + first-match decisions
  dictionary.py   data dictionary (the schema contract)
  validate.py     schema / grounding / code gates
  compiler.py     prompt + bounded repair loop
  llm.py          Anthropic provider (stdlib HTTP), replay + record cassettes
  solver.py       finite-domain DFS with Kleene pruning
  mcdc.py         unique-cause, boundary-biased MC/DC generation
  mutation.py     mutation testing with equivalence proofs
  conflicts.py    conflicts, dead rules, shadowed rules
  export_drl.py   Drools DRL export
```

Zero runtime dependencies. Python 3.10+.

## Limitations

- The DRL export is checked by golden tests, not by loading it into a running KIE container.
- Field types cover what prior-auth policies mostly use; dates are modeled as integer durations (e.g. months since last MRI).
- The grounding gate verifies that numbers come from the cited sentence, not that the model interpreted that sentence correctly. That's what the MC/DC suite and human review of the conflict report are for.

## License

MIT
