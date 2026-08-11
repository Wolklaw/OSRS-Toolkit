from osrs_toolkit.app import _table_sort_value


def test_formatted_numbers_sort_numerically() -> None:
    values = ["97 gp", "9 gp", "893 gp", "8,021 gp"]
    assert sorted(values, key=_table_sort_value, reverse=True) == [
        "8,021 gp",
        "893 gp",
        "97 gp",
        "9 gp",
    ]


def test_percentages_and_volumes_sort_numerically() -> None:
    assert _table_sort_value("83.69%") > _table_sort_value("9.1%")
    assert _table_sort_value("33,469") > _table_sort_value("204")
