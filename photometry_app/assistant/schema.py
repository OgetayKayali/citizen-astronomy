from __future__ import annotations

from collections.abc import Mapping


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def validate_arguments(schema: Mapping[str, object], arguments: object) -> list[str]:
    """Check tool arguments against the JSON Schema subset CAst tools declare."""
    errors: list[str] = []
    _validate(schema, arguments, "arguments", errors)
    return errors


def _validate(schema: Mapping[str, object], value: object, where: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(expected, value):
        errors.append(f"{where} must be {_article(expected)} {expected}.")
        return
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        options = ", ".join(repr(option) for option in enum)
        errors.append(f"{where} must be one of {options}.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{where} must be at least {minimum}.")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{where} must be at most {maximum}.")
    if isinstance(value, str):
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{where} must be at most {max_length} characters.")
    if isinstance(value, (list, tuple)):
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{where} must have at most {max_items} items.")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _validate(items, item, f"{where}[{index}]", errors)
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for name in required:
            if name not in value:
                errors.append(f"{where}.{name} is required.")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, Mapping):
                _validate(child, item, f"{where}.{name}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}.{name} is not a known argument.")


def _matches_type(expected: str, value: object) -> bool:
    if expected in {"integer", "number"} and isinstance(value, bool):
        return False
    if expected == "integer" and isinstance(value, float):
        return value.is_integer()
    python_types = _JSON_TYPES.get(expected)
    return True if python_types is None else isinstance(value, python_types)


def _article(type_name: str) -> str:
    return "an" if type_name[:1] in "aeiou" else "a"
