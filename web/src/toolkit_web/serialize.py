from __future__ import annotations

import dataclasses
from collections.abc import Mapping


def serialize(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result: dict[str, object] = {}
        for field in dataclasses.fields(value):
            result[field.name] = serialize(getattr(value, field.name))
        for name in dir(type(value)):
            if name.startswith("_") or name in result:
                continue
            if isinstance(getattr(type(value), name, None), property):
                result[name] = serialize(getattr(value, name))
        return result
    if isinstance(value, Mapping):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        return [serialize(item) for item in value]
    return value
