from osrs_toolkit.app import _JOURNAL_STATUS_ORDER, _table_sort_value


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


def test_price_ages_sort_across_units() -> None:
    values = ["2 hr", "59 min", "51 sec", "3 min"]
    assert sorted(values, key=_table_sort_value) == ["51 sec", "3 min", "59 min", "2 hr"]


def test_empty_cells_remain_sortable() -> None:
    assert _table_sort_value("") == (1, "")


def test_journal_profit_variants_sort_by_amount() -> None:
    values = [
        "Est. +80,000 gp",
        "+1,234 gp • 7 left",
        "-21,600 gp",
        "Est. +8,028 gp",
    ]

    assert sorted(values, key=_table_sort_value, reverse=True) == [
        "Est. +80,000 gp",
        "Est. +8,028 gp",
        "+1,234 gp • 7 left",
        "-21,600 gp",
    ]


def test_journal_status_order_follows_the_trade_lifecycle_not_alphabet() -> None:
    """Regression: sorting by Status must not interleave terminal states between active ones."""
    ranked = sorted(_JOURNAL_STATUS_ORDER, key=_JOURNAL_STATUS_ORDER.get)
    assert ranked == [
        # A plan and a placed offer are the same stage of the lifecycle, so they share a rank
        # and fall back to sorting by name — the table must not separate them.
        "Planned",
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Completed (manual)",
        "Cancelled",
        "Supplies",
    ]
    assert _JOURNAL_STATUS_ORDER["Cancelled"] > _JOURNAL_STATUS_ORDER["Partially sold"]
