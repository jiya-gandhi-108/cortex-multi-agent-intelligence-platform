"""Convert Pydantic models into JSON Schemas accepted by strict structured output.

Groq strict mode (and OpenAI-compatible strict mode generally) requires:
  * every object has ``additionalProperties: false``
  * every property is listed in ``required`` (optional = union with null)
and supports a core subset of keywords. Validation keywords (lengths, ranges,
patterns) are stripped from the wire schema; the response is re-validated
against the full Pydantic model afterwards, so no constraint is lost.

Rule enforced here: LLM output models must not rely on defaults. A field that
may be absent must be typed ``X | None`` *and* still be required. This avoids
the model sending ``null`` for a field whose Pydantic default isn't ``None``.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_ALLOWED_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "const",
    "anyOf",
    "$ref",
    "$defs",
    "description",
    "additionalProperties",
    "title",
}


class SchemaNotStrictError(TypeError):
    pass


def _walk(node: Any, path: str) -> Any:
    if isinstance(node, list):
        return [_walk(n, path) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED_KEYS:
            continue
        if key == "properties":
            out[key] = {k: _walk(v, f"{path}.{k}") for k, v in value.items()}
        elif key == "$defs":
            out[key] = {k: _walk(v, f"#/$defs/{k}") for k, v in value.items()}
        else:
            out[key] = _walk(value, path)
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        missing = sorted(set(props) - set(out.get("required", [])))
        if missing:
            raise SchemaNotStrictError(
                f"{path}: fields {missing} have defaults; LLM output fields must be required "
                "(use `X | None` without a default for optional values)"
            )
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = list(props)
    return out


def to_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = copy.deepcopy(model.model_json_schema(mode="validation"))
    return _walk(raw, model.__name__)  # type: ignore[no-any-return]
