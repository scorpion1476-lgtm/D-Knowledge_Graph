"""Plugin manifest validator.

We deliberately do NOT depend on a JSON Schema library at runtime. The
validator implements the subset of Draft-07 we actually use: required keys,
type checks, pattern matches, min/max items, and additionalProperties.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from ..core.errors import ValidationError


def load_schema() -> dict:
    return json.loads(
        resources.files("dkg.plugins").joinpath("schema.json").read_text(encoding="utf-8")
    )


def validate_manifest(obj: Any, schema: dict | None = None) -> None:
    schema = schema or load_schema()
    _validate(obj, schema, path="$")


def load_manifest(path: Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_manifest(raw)
    return raw


# --- minimal validator ---------------------------------------------


def _validate(value: Any, schema: dict, *, path: str) -> None:
    if "type" in schema:
        expected = schema["type"]
        if not _type_matches(value, expected):
            raise ValidationError(f"{path}: expected type {expected}, got {type(value).__name__}")
    if "required" in schema and isinstance(value, dict):
        for k in schema["required"]:
            if k not in value:
                raise ValidationError(f"{path}: missing required key {k!r}")
    if "properties" in schema and isinstance(value, dict):
        for k, sub in schema["properties"].items():
            if k in value:
                _validate(value[k], sub, path=f"{path}.{k}")
    if schema.get("additionalProperties") is False and isinstance(value, dict):
        allowed = set(schema.get("properties", {}).keys())
        pat_props = schema.get("patternProperties", {})
        for k in value.keys():
            if k in allowed:
                continue
            if any(re.match(p, k) for p in pat_props):
                continue
            raise ValidationError(f"{path}: unexpected key {k!r}")
    if "patternProperties" in schema and isinstance(value, dict):
        for pat, sub in schema["patternProperties"].items():
            regex = re.compile(pat)
            for k, v in value.items():
                if regex.match(k):
                    _validate(v, sub, path=f"{path}.{k}")
    if "pattern" in schema and isinstance(value, str):
        if not re.match(schema["pattern"], value):
            raise ValidationError(f"{path}: value does not match pattern {schema['pattern']!r}")
    if "maxLength" in schema and isinstance(value, str):
        if len(value) > int(schema["maxLength"]):
            raise ValidationError(f"{path}: value exceeds max length {schema['maxLength']}")
    if "items" in schema and isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValidationError(f"{path}: array shorter than minItems={schema['minItems']}")
        for i, item in enumerate(value):
            _validate(item, schema["items"], path=f"{path}[{i}]")


def _type_matches(value: Any, expected: str | list) -> bool:
    if isinstance(expected, list):
        return any(_type_matches(value, e) for e in expected)
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]
