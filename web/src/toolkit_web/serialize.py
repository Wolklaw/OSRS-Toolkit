"""Turn a domain dataclass into plain JSON, properties included.

``dataclasses.asdict`` only ever gives back stored fields — it has no way to know that
``TrackedTrade.estimated_profit`` or ``LoadoutItem.total_value`` exist, because they are
``@property`` methods, not fields. Those properties are exactly the numbers a caller wants —
they are the same ones the desktop window reads to draw the Journal — so this walks both:
every stored field and every public property, recursing into nested dataclasses so a property
on something nested (an item's own value, say) is not lost the way a plain ``asdict`` would
lose it by flattening that item to a bare dict first.
"""

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
