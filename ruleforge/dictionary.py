"""Data dictionary: the contract between policy language and the claim schema.

Every field a rule may reference must be declared here with a type and domain.
The validator rejects anything else, which is what stops an LLM from inventing
fields like `patient_age` when the schema says `member_age`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TYPES = {"int", "bool", "enum", "code_list"}


@dataclass
class FieldSpec:
    name: str
    type: str
    description: str = ""
    min: int | None = None
    max: int | None = None
    values: list[str] | None = None  # enum domain
    default: Any = None

    def baseline(self) -> Any:
        if self.default is not None:
            return self.default
        if self.type == "int":
            return self.min if self.min is not None else 0
        if self.type == "bool":
            return False
        if self.type == "enum":
            return self.values[0] if self.values else ""
        if self.type == "code_list":
            return []
        raise ValueError(self.type)


class DataDictionary:
    def __init__(self, fields: dict[str, FieldSpec]):
        self.fields = fields

    @classmethod
    def load(cls, path: str | Path) -> "DataDictionary":
        raw = json.loads(Path(path).read_text())
        fields = {}
        for name, spec in raw["fields"].items():
            if spec["type"] not in TYPES:
                raise ValueError(f"field {name}: unsupported type {spec['type']}")
            fields[name] = FieldSpec(name=name, **spec)
        return cls(fields)

    def __contains__(self, name: str) -> bool:
        return name in self.fields

    def __getitem__(self, name: str) -> FieldSpec:
        return self.fields[name]

    def describe(self) -> str:
        """Compact description used inside the LLM prompt."""
        lines = []
        for f in self.fields.values():
            dom = ""
            if f.type == "int":
                dom = f" range [{f.min}, {f.max}]"
            elif f.type == "enum":
                dom = f" one of {f.values}"
            lines.append(f"- {f.name} ({f.type}{dom}): {f.description}")
        return "\n".join(lines)
