"""Keeps this machine's journal and the website's journal in sync.

The merge logic lives in :mod:`osrs_toolkit.journal`; this module only handles the
conversation around it — when to ask, what to send, and what to remember between passes.
The cheap check runs on a timer, and the expensive pull/push only happens when it changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.web_source import ToolkitWebClient


@dataclass(frozen=True, slots=True)
class MirrorResult:
    """What one pass did, in terms a status line can say out loud."""

    reached: bool = False
    pulled: int = 0
    pushed: int = 0
    checked_only: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.pulled or self.pushed)

    def describe(self) -> str:
        if not self.reached:
            return "Could not reach the website"
        if self.checked_only:
            return "Up to date"
        parts = []
        if self.pulled:
            parts.append(f"{self.pulled} in")
        if self.pushed:
            parts.append(f"{self.pushed} out")
        return f"Synced {', '.join(parts)}" if parts else "Up to date"


class JournalMirror:
    """One account's journal, kept level with its journal on the website.

    Holds two watermarks that aren't interchangeable: ``remote_version`` only compares
    against the website's clock, ``local_version`` only against this machine's.
    """

    def __init__(self, repository: JournalRepository, client: ToolkitWebClient) -> None:
        self.repository = repository
        self.client = client
        self.remote_version: str | None = None
        self.local_version: str | None = None

    def reset(self) -> None:
        """Forget both watermarks, so the next pass exchanges everything.

        For when the account behind the credential changes: what the old one had synced says
        nothing about what the new one holds.
        """
        self.remote_version = None
        self.local_version = None

    def sync(self) -> MirrorResult:
        """One pass: pull what changed there, push what changed here.

        A pass that cannot reach the website changes nothing and remembers nothing, so the
        next one asks exactly what this one did rather than skipping past a gap.
        """
        if not self.client.configured:
            return MirrorResult(reached=False)

        remote = self.client.get("/api/journal/version")
        if not isinstance(remote, dict):
            return MirrorResult(reached=False)
        remote_version = str(remote.get("version") or "")

        local = self.repository.sync_version()
        local_version = str(local.get("version") or "")

        nothing_there = self.remote_version is not None and remote_version == self.remote_version
        nothing_here = self.local_version is not None and local_version == self.local_version
        if nothing_there and nothing_here:
            # The common case, and the reason the version route exists: one small query
            # answered it, and neither journal had to be read or serialized.
            return MirrorResult(reached=True, checked_only=True)

        received, pulled = (True, 0) if nothing_there else self._pull()
        delivered, pushed = (True, 0) if nothing_here else self._push()

        # A watermark only advances for the direction that actually got through — advancing
        # one after a failed exchange is how rows go missing. Read back rather than reusing
        # what was measured above, since both sides may have just changed.
        if received:
            self.remote_version = self._remote_version()
        if delivered:
            self.local_version = str(self.repository.sync_version().get("version") or "")
        return MirrorResult(reached=True, pulled=pulled, pushed=pushed)

    def _pull(self) -> tuple[bool, int]:
        """(whether the website answered, how many rows that changed anything)."""
        params = {"since": self.remote_version} if self.remote_version else {}
        payload = self.client.get("/api/journal/pull", **params)
        if not isinstance(payload, dict):
            return (False, 0)
        applied = self.repository.sync_apply(payload)
        return (True, applied["inserted"] + applied["updated"])

    def _push(self) -> tuple[bool, int]:
        """(whether the website took them, how many rows that changed anything).

        Nothing to send counts as delivered: there is no gap to come back for.
        """
        payload = self.repository.sync_export(since=self.local_version)
        if not any(payload.get(table) for table in payload):
            return (True, 0)
        answer = self.client.post("/api/journal/push", payload)
        if not isinstance(answer, dict):
            # It may well have landed, but reports failure anyway so the next pass offers
            # these rows again — the far side skips what it already has, so sending twice
            # is free and guessing wrong isn't.
            return (False, 0)
        return (True, int(answer.get("inserted", 0)) + int(answer.get("updated", 0)))

    def _remote_version(self) -> str | None:
        remote = self.client.get("/api/journal/version")
        return str(remote.get("version") or "") if isinstance(remote, dict) else None
