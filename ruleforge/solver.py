"""A small finite-domain search over claim records.

Rules only compare fields against constants, so each field's interesting values
are finite: every threshold and its neighbours, every enum value, every code
mentioned. We enumerate those candidates and run depth-first search, pruning a
branch as soon as three-valued evaluation proves the goal impossible.

This is what powers both MC/DC test generation and conflict detection, and it
always returns a concrete witness record rather than a yes/no answer.
"""
from __future__ import annotations

from typing import Callable, Iterable

from .dictionary import DataDictionary
from .ir import RuleSet, leaves

Goal = Callable[[dict], "bool | None"]


def candidate_values(field: str, dd: DataDictionary, conditions: Iterable[dict]) -> list:
    spec = dd[field]
    relevant = [l for c in conditions for l in leaves(c) if l["field"] == field]
    if spec.type == "bool":
        return [False, True]
    if spec.type == "enum":
        vals = list(spec.values or [])
        return vals
    if spec.type == "code_list":
        codes = []
        for l in relevant:
            for c in l["value"]:
                if c not in codes:
                    codes.append(c)
        cands = [[]] + [[c] for c in codes]
        cands.append(["Z00.00"])  # a code that matches nothing in the rules
        return cands
    if spec.type == "int":
        lo = spec.min if spec.min is not None else -10**9
        hi = spec.max if spec.max is not None else 10**9
        pts = set()
        for l in relevant:
            v = l["value"]
            pts.update({v - 1, v, v + 1})
        if not pts:
            pts.add(spec.baseline())
        pts.update({lo, hi})
        return sorted(p for p in pts if lo <= p <= hi)
    raise ValueError(spec.type)


def fields_of(conditions: Iterable[dict]) -> list[str]:
    out: list[str] = []
    for c in conditions:
        for l in leaves(c):
            if l["field"] not in out:
                out.append(l["field"])
    return out


def search(goal: Goal, fields: list[str], dd: DataDictionary, conditions: list[dict],
           complete: Callable[[dict], bool] | None = None,
           prefer: dict[str, Callable] | None = None) -> dict | None:
    """Find a record (over `fields`) where goal(record) is True.

    `complete` is an optional extra check on a fully assigned record (used when
    the goal involves something three-valued pruning can't see, like a flip).
    `prefer` maps a field to a sort key, e.g. to try boundary values first.
    """
    domains = {f: candidate_values(f, dd, conditions) for f in fields}
    for f, key in (prefer or {}).items():
        if f in domains:
            domains[f] = sorted(domains[f], key=key)
    record: dict = {}

    def dfs(i: int) -> dict | None:
        status = goal(record)
        if status is False:
            return None
        if i == len(fields):
            if status is True and (complete is None or complete(record)):
                return dict(record)
            return None
        f = fields[i]
        for v in domains[f]:
            record[f] = v
            hit = dfs(i + 1)
            if hit is not None:
                return hit
        del record[f]
        return None

    return dfs(0)


def full_record(partial: dict, dd: DataDictionary) -> dict:
    """Fill fields the rules don't constrain with dictionary baselines."""
    rec = {name: spec.baseline() for name, spec in dd.fields.items()}
    rec.update(partial)
    return rec
