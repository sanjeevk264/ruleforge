"""Policy text -> validated rule set, with a bounded self-repair loop."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .dictionary import DataDictionary
from .ir import RuleSet
from .llm import Provider
from .validate import Issue, validate

SYSTEM = """You convert healthcare payer policy documents into executable decision rules.

Output ONLY a JSON object in a ```json fenced block, with this shape:
{
  "policy_id": str,
  "default_decision": "APPROVE" | "DENY" | "PEND_REVIEW",
  "rules": [{
    "id": "R1",
    "description": short plain-English summary,
    "source_quote": the exact sentence(s) from the policy this rule implements, copied verbatim,
    "priority": int (higher fires first; use the policy's stated precedence),
    "when": condition tree,
    "then": {"decision": "APPROVE" | "DENY" | "PEND_REVIEW", "reason_code": "UPPER_SNAKE_CASE"}
  }]
}

Condition trees: a leaf is {"field": name, "op": op, "value": v}; combine with
{"all": [...]}, {"any": [...]}, {"not": {...}}.
Ops: == != < <= > >= (int, bool, enum), in / not_in (enum, list value),
includes_any / includes_none (code_list fields, list value).

Hard requirements:
- Use ONLY fields from the data dictionary. Never invent fields.
- Every numeric threshold must come from the rule's source_quote.
- Do not encode anything the policy does not say. If the policy is ambiguous,
  encode it literally; a separate analyzer reports ambiguities.
"""


@dataclass
class CompileResult:
    ruleset: RuleSet | None
    attempts: int
    history: list[list[Issue]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.ruleset is not None


def extract_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def compile_policy(policy_text: str, dd: DataDictionary, provider: Provider,
                   max_attempts: int = 3, log=print) -> CompileResult:
    messages = [{
        "role": "user",
        "content": f"DATA DICTIONARY:\n{dd.describe()}\n\nPOLICY DOCUMENT:\n<policy>\n{policy_text}\n</policy>",
    }]
    history: list[list[Issue]] = []
    for attempt in range(1, max_attempts + 1):
        reply = provider.complete(SYSTEM, messages)
        try:
            candidate = extract_json(reply)
            issues = validate(candidate, dd, policy_text)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            candidate, issues = None, [Issue("*", "structure", f"could not parse JSON: {e}")]
        history.append(issues)
        if not issues:
            log(f"attempt {attempt}: valid ({len(candidate['rules'])} rules)")
            return CompileResult(RuleSet.from_dict(candidate), attempt, history)
        log(f"attempt {attempt}: {len(issues)} issue(s)")
        for i in issues:
            log(f"    {i}")
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content":
                "The rule set failed validation. Fix every issue below and return the complete "
                "corrected JSON. Do not change rules that had no issues.\n\n"
                + "\n".join(f"- {i}" for i in issues)},
        ]
    return CompileResult(None, max_attempts, history)
