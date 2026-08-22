from __future__ import annotations

from dataclasses import dataclass

from toolkit_web.serialize import serialize


@dataclass(frozen=True, slots=True)
class _Item:
    quantity: int
    unit_value: int

    @property
    def total_value(self) -> int:
        return self.quantity * self.unit_value


@dataclass(frozen=True, slots=True)
class _Bundle:
    name: str
    items: tuple[_Item, ...]

    @property
    def item_count(self) -> int:
        return len(self.items)


def test_stored_fields_come_through():
    assert serialize(_Item(quantity=3, unit_value=10)) == {
        "quantity": 3,
        "unit_value": 10,
        "total_value": 30,
    }


def test_a_top_level_property_is_included_even_though_it_is_not_a_field():
    """dataclasses.asdict on its own would drop total_value entirely — it only knows about
    stored fields, and a property is not one."""
    result = serialize(_Item(quantity=3, unit_value=10))

    assert result["total_value"] == 30


def test_a_property_on_a_nested_dataclass_is_not_lost():
    """The failure mode plain asdict() has: it flattens nested dataclasses to bare dicts
    before this code would ever get a chance to add their properties. This has to recurse
    itself rather than delegate to asdict for exactly that reason."""
    bundle = _Bundle(name="loadout", items=(_Item(2, 5), _Item(1, 100)))

    result = serialize(bundle)

    assert result["items"] == [
        {"quantity": 2, "unit_value": 5, "total_value": 10},
        {"quantity": 1, "unit_value": 100, "total_value": 100},
    ]
    assert result["item_count"] == 2


def test_a_tuple_becomes_a_json_safe_list():
    result = serialize(_Bundle(name="x", items=()))
    assert result["items"] == []


def test_plain_values_pass_through_unchanged():
    assert serialize(42) == 42
    assert serialize("text") == "text"
    assert serialize(None) is None


def test_a_dict_value_is_walked_too():
    assert serialize({"a": 1, "b": 2}) == {"a": 1, "b": 2}


def test_a_frozenset_becomes_a_list():
    assert sorted(serialize(frozenset({1, 2, 3}))) == [1, 2, 3]
