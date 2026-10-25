from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from osrs_toolkit.cancellations import cancellation_groups, summarize_cancellations
from osrs_toolkit.drops import drop_table, npc_summaries
from osrs_toolkit.fill_times import fill_observations, fill_time_groups
from osrs_toolkit.formatting import compact_items, synced_trade_label
from osrs_toolkit.holdings import untracked_holdings
from osrs_toolkit.journal import JournalRepository, LoadoutSnapshot
from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.net_worth_attribution import attribute_change, idle_stacks
from osrs_toolkit.progression import level_unlocks, skill_progress
from osrs_toolkit.quests import (
    all_quests,
    assess_quests,
    best_for_skill,
    counted_quest_names,
    do_next,
    nearly_ready,
    quest_points,
    ready_miniquests,
)
from osrs_toolkit.slippage import by_item_slippage, slippage_fills, summarize_slippage
from osrs_toolkit.trips import build_trips

ACTIVITY_WINDOW_DAYS = 90
TRIP_WINDOW_DAYS = 30
NET_WORTH_WINDOW_DAYS = 30
IDLE_DAYS = 14
ALL_TIME = "All time"
CLOSED_STATUSES = frozenset({"Completed", "Cancelled", "Supplies"})


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    hint: str = ""
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    profit_columns: frozenset[int] = frozenset()
    empty: str = "Nothing to show yet."


@dataclass(slots=True)
class ReportContext:
    journal: JournalRepository
    snapshot: LoadoutSnapshot | None
    mappings: dict[int, ItemMapping]
    points: list[MarketPoint]
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def account_hash(self) -> str | None:
        return self.snapshot.account_hash if self.snapshot is not None else None


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    title: str
    subtitle: str
    build: Callable[[ReportContext, str | None], list[Section]]
    choices: Callable[[ReportContext], list[str]] | None = None


def _n(value: float) -> str:
    return f"{value:,.0f}"


def _signed(value: float) -> str:
    return f"{value:+,.0f}"


def _minutes(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} min"


def _day(stamp: str) -> str:
    return stamp[:16].replace("T", " ")


def _no_character() -> list[Section]:
    return [
        Section(
            "No character yet",
            "Connect RuneLite and open your bank in game, then refresh. "
            "Per-character pages read from your latest snapshot.",
        )
    ]


def overview(context: ReportContext, _choice: str | None = None) -> list[Section]:
    journal = context.journal
    tracked = journal.list_tracked(context.account_hash)
    open_positions = [trade for trade in tracked if trade.status not in CLOSED_STATUSES]
    history = journal.list_net_worth_history(context.account_hash)
    sections: list[Section] = []
    stats: list[tuple[str, ...]] = [
        ("Open positions", _n(len(open_positions))),
        ("Capital in open positions", f"{_n(sum(trade.invested for trade in open_positions))} gp"),
        (
            "Estimated profit on open positions",
            f"{_signed(sum(trade.estimated_profit for trade in open_positions))} gp",
        ),
    ]
    if history:
        stats.insert(0, ("Net worth", f"{_n(history[-1].total_value)} gp"))
        if len(history) > 1:
            stats.insert(
                1,
                (
                    "Change since first record",
                    f"{_signed(history[-1].total_value - history[0].total_value)} gp",
                ),
            )
    sections.append(Section("Overview", headers=("Measure", "Value"), rows=tuple(stats)))
    held = untracked_holdings(context.snapshot, tracked, context.mappings, context.points)
    sections.append(
        Section(
            "Held but not tracked",
            "Inventory and bank items with no tracked purchase behind them, at the market's "
            f"suggested sell price after tax. Worth {_n(sum(row.net_total for row in held))} gp "
            "in all.",
            ("Item", "Quantity", "Suggested sell", "After tax, each", "After tax, total"),
            tuple(
                (
                    row.item_name,
                    _n(row.quantity),
                    _n(row.suggested_sell),
                    _n(row.net_each),
                    _n(row.net_total),
                )
                for row in held[:40]
            ),
            empty="Every item you hold is accounted for by a tracked purchase.",
        )
    )
    if context.account_hash is not None:
        summary = journal.session_summary(context.account_hash)
        if summary is not None:
            sections.append(_session_section(summary))
    return sections


def _session_section(summary) -> Section:
    rows: list[tuple[str, ...]] = [
        ("Window", f"{_day(summary.started_at)} → {_day(summary.ended_at)}"),
        ("Duration", f"{summary.duration_minutes} min"),
        ("Loot", f"{_n(summary.loot_value)} gp"),
        ("Deaths", f"{summary.death_count} ({_n(summary.value_lost)} gp carried)"),
        ("Trades", f"{summary.trade_count} ({_signed(summary.trade_net_value)} gp)"),
    ]
    if summary.net_worth_change is not None:
        rows.append(("Net worth change", f"{_signed(summary.net_worth_change)} gp"))
    for skill, gained in sorted(summary.xp_gained.items()):
        levels = summary.levels_gained.get(skill, 0)
        suffix = f" (+{levels} levels)" if levels else ""
        rows.append((f"{skill} XP", f"+{_n(gained)}{suffix}"))
    return Section("Latest session", headers=("Measure", "Value"), rows=tuple(rows))


def session(context: ReportContext, _choice: str | None = None) -> list[Section]:
    if context.account_hash is None:
        return _no_character()
    summary = context.journal.session_summary(context.account_hash)
    if summary is None:
        return [Section("Session", empty="No recent activity to summarise.")]
    sections = [_session_section(summary)]
    sections.append(
        Section(
            "Loot by monster",
            headers=("Monster", "Kills", "Value"),
            rows=tuple((npc.npc_name, _n(npc.kills), _n(npc.value)) for npc in summary.loot_by_npc),
            empty="No loot recorded in this window.",
        )
    )
    return sections


def activity(context: ReportContext, _choice: str | None = None) -> list[Section]:
    since = context.now - timedelta(days=ACTIVITY_WINDOW_DAYS)
    events = context.journal.list_synced_trades(since=since, account_hash=context.account_hash)
    return [
        Section(
            "Activity",
            f"Every Grand Exchange fill and player trade from the last {ACTIVITY_WINDOW_DAYS} days.",
            ("When", "What", "Gave", "Received", "Difference"),
            tuple(
                (
                    _day(event.occurred_at),
                    synced_trade_label(event),
                    compact_items(event.given),
                    compact_items(event.received),
                    _signed(event.estimated_difference),
                )
                for event in events
            ),
            frozenset({4}),
            "No synced trades in this window.",
        )
    ]


def counterparties(context: ReportContext, _choice: str | None = None) -> list[Section]:
    trades = context.journal.list_synced_trades("player_trade", account_hash=context.account_hash)
    grouped: dict[str, list] = {}
    for trade in trades:
        grouped.setdefault(trade.counterparty or "Unknown", []).append(trade)
    rows = sorted(
        grouped.items(), key=lambda pair: max(t.occurred_at for t in pair[1]), reverse=True
    )
    return [
        Section(
            "Counterparties",
            "Everyone you have traded with, and how each trade came out by estimated value.",
            ("Player", "Trades", "Gave", "Received", "Net", "Last traded"),
            tuple(
                (
                    name,
                    _n(len(with_them)),
                    _n(sum(t.given_value for t in with_them)),
                    _n(sum(t.received_value for t in with_them)),
                    _signed(sum(t.estimated_difference for t in with_them)),
                    _day(max(t.occurred_at for t in with_them)),
                )
                for name, with_them in rows
            ),
            frozenset({4}),
            "No player trades recorded.",
        )
    ]


def net_worth(context: ReportContext, _choice: str | None = None) -> list[Section]:
    if context.account_hash is None or context.snapshot is None:
        return _no_character()
    journal = context.journal
    latest = context.snapshot
    latest_at = datetime.fromisoformat(latest.captured_at)
    start = journal.get_loadout_snapshot_at(
        latest.account_hash,
        (latest_at - timedelta(days=NET_WORTH_WINDOW_DAYS)).isoformat(),
        fall_back_to_earliest=True,
    )
    sections: list[Section] = []
    if start is None or start.captured_at == latest.captured_at:
        sections.append(Section("Where your wealth moved", empty="Needs two snapshots to compare."))
    else:
        since = datetime.fromisoformat(start.captured_at)
        result = attribute_change(
            start,
            latest,
            journal.list_npc_loot_events(since=since, account_hash=latest.account_hash),
            journal.list_player_death_events(since=since, account_hash=latest.account_hash),
        )
        sections.append(
            Section(
                "Where your wealth moved",
                f"{_day(result.started_at)} → {_day(result.ended_at)}: "
                f"{_n(result.start_value)} → {_n(result.end_value)} gp ({_signed(result.change)}).",
                ("Cause", "Effect"),
                (
                    ("Price movement on holdings", f"{_signed(result.price_movement)} gp"),
                    ("Loot", f"{_signed(result.loot)} gp"),
                    ("Deaths", f"{_signed(result.deaths)} gp"),
                    ("Everything else", f"{_signed(result.other)} gp"),
                ),
                frozenset({1}),
            )
        )
        sections.append(
            Section(
                "Biggest price movers",
                headers=("Item", "Held", "Before", "After", "Effect"),
                rows=tuple(
                    (
                        mover.item_name,
                        _n(mover.quantity),
                        _n(mover.price_before),
                        _n(mover.price_after),
                        _signed(mover.effect),
                    )
                    for mover in result.price_movers[:10]
                ),
                profit_columns=frozenset({4}),
                empty="No price movement on holdings.",
            )
        )
        sections.append(
            Section(
                "Biggest changes in holdings",
                headers=("Item", "Change", "Effect"),
                rows=tuple(
                    (mover.item_name, _signed(mover.quantity_change), _signed(mover.effect))
                    for mover in result.quantity_movers[:10]
                ),
                profit_columns=frozenset({2}),
                empty="Nothing came or went.",
            )
        )
    reference = journal.get_loadout_snapshot_at(
        latest.account_hash, (latest_at - timedelta(days=IDLE_DAYS)).isoformat()
    )
    idle = idle_stacks(latest, reference) if reference is not None else []
    sections.append(
        Section(
            "Idle capital",
            f"Stacks that have not moved in {IDLE_DAYS} days. "
            f"{_n(sum(stack.value for stack in idle))} gp sitting still.",
            ("Item", "Quantity", "Value"),
            tuple((stack.item_name, _n(stack.quantity), _n(stack.value)) for stack in idle[:15]),
            empty=f"Needs a snapshot from {IDLE_DAYS} days ago, or nothing has sat still.",
        )
    )
    return sections


def trips(context: ReportContext, _choice: str | None = None) -> list[Section]:
    if context.account_hash is None:
        return _no_character()
    journal = context.journal
    since = context.now - timedelta(days=TRIP_WINDOW_DAYS)
    margin = timedelta(hours=6)
    found = build_trips(
        journal.list_npc_loot_events(since=since, account_hash=context.account_hash),
        journal.list_player_death_events(since=since, account_hash=context.account_hash),
        [
            point
            for point in journal.list_skills_history(context.account_hash)
            if datetime.fromisoformat(point.captured_at) >= since - margin
        ],
        journal.list_loadout_snapshots(context.account_hash, since=(since - margin).isoformat()),
    )
    return [
        Section(
            "Trip ledger",
            f"What each trip paid over the last {TRIP_WINDOW_DAYS} days: loot, minus what left "
            "your account between the bank visits either side.",
            ("Trip", "When", "Minutes", "Loot", "Supplies", "Net", "Net / hr"),
            tuple(
                (
                    trip.title,
                    _day(trip.started_at),
                    _n(trip.duration_minutes),
                    _n(trip.loot_value),
                    "—" if trip.supplies_value is None else _n(trip.supplies_value),
                    "—" if trip.net_profit is None else _signed(trip.net_profit),
                    "—" if trip.net_per_hour is None else _signed(trip.net_per_hour),
                )
                for trip in found
            ),
            frozenset({5, 6}),
            "No trips in this window.",
        )
    ]


def _drop_choices(context: ReportContext) -> list[str]:
    if context.account_hash is None:
        return []
    events = context.journal.list_npc_loot_events(account_hash=context.account_hash)
    return [entry.npc_name for entry in npc_summaries(events)]


def drops(context: ReportContext, choice: str | None = None) -> list[Section]:
    if context.account_hash is None:
        return _no_character()
    events = context.journal.list_npc_loot_events(account_hash=context.account_hash)
    summaries = npc_summaries(events)
    sections = [
        Section(
            "Monsters",
            headers=("Monster", "Kills", "Loot", "First kill", "Last kill"),
            rows=tuple(
                (
                    entry.npc_name,
                    _n(entry.kills),
                    _n(entry.value),
                    _day(entry.first_kill_at),
                    _day(entry.last_kill_at),
                )
                for entry in summaries
            ),
            empty="No kills recorded.",
        )
    ]
    names = {entry.npc_name for entry in summaries}
    chosen = choice if choice in names else (summaries[0].npc_name if summaries else None)
    if chosen is not None:
        table = drop_table(events, chosen)
        sections.append(
            Section(
                f"{table.npc_name} drops",
                f"{_n(table.kills)} kills recorded.",
                ("Item", "Kills with", "Quantity", "Rate", "One in", "Since last", "Longest dry"),
                tuple(
                    (
                        row.item_name,
                        _n(row.kills_with),
                        _n(row.quantity),
                        f"{row.rate_percent:.2f}%",
                        f"{row.one_in:,.1f}",
                        _n(row.kills_since),
                        _n(row.longest_dry),
                    )
                    for row in table.rows
                ),
            )
        )
    return sections


def loadout(context: ReportContext, _choice: str | None = None) -> list[Section]:
    snapshot = context.snapshot
    if snapshot is None:
        return [
            Section(
                "No snapshot yet",
                "Turn on gear and bank sync in the plugin's settings, then open your bank in game.",
            )
        ]
    bank = sorted(snapshot.bank, key=lambda item: item.total_value, reverse=True)
    return [
        Section(
            "Skills",
            f"Total level {sum(snapshot.skills.values()):,}.",
            ("Skill", "Level", "Experience"),
            tuple(
                (skill, str(level), _n(snapshot.xp.get(skill, 0)))
                for skill, level in snapshot.skills.items()
            ),
        ),
        Section(
            "Bank",
            f"{len(bank):,} stacks worth {_n(sum(item.total_value for item in bank))} gp. "
            "Top 40 by value.",
            ("Item", "Quantity", "Value"),
            tuple((item.item_name, _n(item.quantity), _n(item.total_value)) for item in bank[:40]),
            empty="Nothing in the bank as of the last sync.",
        ),
    ]


def _quest_rows(guides) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            guide.quest.name,
            guide.quest.difficulty,
            guide.quest.length,
            ", ".join(f"{gap.skill} {gap.have}/{gap.need}" for gap in guide.skill_gaps) or "—",
            ", ".join(guide.missing_quests) or "—",
            _n(guide.unlocks),
        )
        for guide in guides
    )


_QUEST_HEADERS = ("Quest", "Difficulty", "Length", "Skills short", "Quests missing", "Unlocks")


def quests(context: ReportContext, _choice: str | None = None) -> list[Section]:
    if context.account_hash is None or context.snapshot is None:
        return _no_character()
    completed = context.journal.list_completed_quests(context.account_hash)
    guides = assess_quests(context.snapshot.skills, completed)
    counted = counted_quest_names()
    done = len(completed & counted)
    sections = [
        Section(
            "Quest progress",
            f"{done} of {len(counted)} quests done, {quest_points(completed)} quest points. "
            f"{len(all_quests())} quests and miniquests known. Tick quests off on the website.",
            ("Measure", "Value"),
            (
                ("Ready to start", _n(sum(1 for guide in guides if guide.state == "Ready"))),
                ("Quest points", _n(quest_points(completed))),
            ),
        ),
        Section("Do these next", headers=_QUEST_HEADERS, rows=_quest_rows(do_next(guides))),
        Section(
            "Nearly ready",
            "A few levels short.",
            _QUEST_HEADERS,
            _quest_rows(nearly_ready(guides)),
        ),
        Section(
            "Ready miniquests", headers=_QUEST_HEADERS, rows=_quest_rows(ready_miniquests(guides))
        ),
    ]
    picks: list[tuple[str, ...]] = []
    for skill in sorted({s for guide in guides for s in guide.quest.experience}):
        for guide in best_for_skill(guides, skill, 3):
            picks.append((skill, guide.quest.name, _n(guide.quest.experience[skill])))
    sections.append(
        Section("Best quest XP by skill", headers=("Skill", "Quest", "XP"), rows=tuple(picks))
    )
    return sections


def skill_goals(context: ReportContext, _choice: str | None = None) -> list[Section]:
    if context.account_hash is None or context.snapshot is None:
        return _no_character()
    snapshot = context.snapshot
    journal = context.journal
    history = journal.list_skills_history(context.account_hash)
    rows = skill_progress(snapshot.skills, snapshot.xp, history, context.now)
    completed = journal.list_completed_quests(context.account_hash)
    unlocks = level_unlocks(assess_quests(snapshot.skills, completed), snapshot.skills, snapshot.xp)
    return [
        Section(
            "Skill goals",
            f"{sum(1 for row in rows if row.level >= 99)} skills at 99. "
            "Days to max uses your recent experience rate.",
            ("Skill", "Level", "XP to next", "XP to 99", "Progress", "XP / day", "Days to 99"),
            tuple(
                (
                    row.skill,
                    str(row.level),
                    "—" if row.xp_to_next is None else _n(row.xp_to_next),
                    _n(row.xp_to_max),
                    f"{row.percent_to_max:.1f}%",
                    "—" if row.xp_per_day is None else _n(row.xp_per_day),
                    "—" if row.days_to_max is None else _n(row.days_to_max),
                )
                for row in rows
            ),
        ),
        Section(
            "Levels that unlock quests",
            headers=("Skill", "Now", "Target", "XP needed", "Quests"),
            rows=tuple(
                (
                    row.skill,
                    str(row.current),
                    str(row.target),
                    _n(row.xp_needed),
                    ", ".join(row.quests[:4]) + (" …" if len(row.quests) > 4 else ""),
                )
                for row in unlocks[:12]
            ),
        ),
    ]


def flip_insights(context: ReportContext, _choice: str | None = None) -> list[Section]:
    journal = context.journal
    hash_ = context.account_hash
    fills_trades = journal.list_synced_trades("ge_fill", account_hash=hash_)
    offer_events = journal.list_offer_events(hash_)
    fills = slippage_fills(fills_trades, ALL_TIME, context.now)
    summary = summarize_slippage(fills)
    groups = cancellation_groups(offer_events, ALL_TIME, context.now)
    cancel = summarize_cancellations(groups)
    times = fill_time_groups(fill_observations(offer_events, fills_trades), ALL_TIME, context.now)
    return [
        Section(
            "Observed fill times",
            "How long your offers actually took.",
            ("Group", "Side", "Offers", "First fill", "Complete", "Fastest", "Slowest"),
            tuple(
                (
                    group.label,
                    group.side,
                    _n(group.offers),
                    _minutes(group.median_first_fill_minutes),
                    _minutes(group.median_complete_minutes),
                    _minutes(group.fastest_minutes),
                    _minutes(group.slowest_minutes),
                )
                for group in times
            ),
            empty="Needs more completed offers.",
        ),
        Section(
            "Cancelled offers",
            f"{_n(cancel.opened)} opened, {_n(cancel.cancelled)} cancelled, median wait "
            f"{_minutes(cancel.median_wait_minutes)}.",
            ("Strategy", "Side", "Opened", "Cancelled", "Cancel rate", "Median wait"),
            tuple(
                (
                    group.label,
                    group.side,
                    _n(group.opened),
                    _n(group.cancelled),
                    "—" if group.cancel_rate is None else f"{group.cancel_rate * 100:.0f}%",
                    _minutes(group.median_wait_minutes),
                )
                for group in groups
            ),
            empty="No cancellations recorded.",
        ),
        Section(
            "Slippage by item",
            f"Across {_n(summary.fills)} fills you gained {_signed(summary.total_improvement)} gp "
            "against your own offer prices.",
            ("Item", "Side", "Fills", "Quantity", "Gain", "Per unit"),
            tuple(
                (
                    group.label,
                    group.side,
                    _n(group.fills),
                    _n(group.quantity),
                    _signed(group.improvement),
                    f"{group.improvement_per_unit:+,.1f}",
                )
                for group in by_item_slippage(fills)
            ),
            frozenset({4, 5}),
            "Needs items filled more than once.",
        ),
    ]


REPORTS: dict[str, ReportDefinition] = {
    "Overview": ReportDefinition(
        "Overview",
        "Net worth, open positions, and everything you hold with no tracked purchase behind it.",
        overview,
    ),
    "Session": ReportDefinition(
        "Session", "What your current or latest play session earned.", session
    ),
    "Activity": ReportDefinition(
        "Activity", "Every Grand Exchange fill and player trade the plugin has reported.", activity
    ),
    "Counterparties": ReportDefinition(
        "Counterparties", "Everyone you have traded with directly.", counterparties
    ),
    "Net Worth": ReportDefinition(
        "Net Worth", "Where your wealth moved, and what has been sitting still.", net_worth
    ),
    "Trips": ReportDefinition("Trip Ledger", "What each trip actually paid.", trips),
    "Drops": ReportDefinition(
        "Drop Rates", "Observed drop rates and dry streaks per monster.", drops, _drop_choices
    ),
    "Skills & Bank": ReportDefinition(
        "Skills & Bank", "Skill levels and bank contents from your latest snapshot.", loadout
    ),
    "Quests": ReportDefinition(
        "Quests", "What you can start now, and what is a few levels away.", quests
    ),
    "Skill Goals": ReportDefinition(
        "Skill Goals",
        "How far each skill is from 99, and what its next levels unlock.",
        skill_goals,
    ),
    "Flip Insights": ReportDefinition(
        "Flip Insights",
        "Fill times, cancelled offers and slippage from your own journal.",
        flip_insights,
    ),
}
