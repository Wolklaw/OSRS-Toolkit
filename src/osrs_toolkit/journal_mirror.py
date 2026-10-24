from __future__ import annotations

from dataclasses import dataclass

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.web_source import ToolkitWebClient

VERSION_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class MirrorResult:
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
    def __init__(self, repository: JournalRepository, client: ToolkitWebClient) -> None:
        self.repository = repository
        self.client = client
        self.remote_version: str | None = None
        self.local_version: str | None = None

    def reset(self) -> None:
        self.remote_version = None
        self.local_version = None

    def sync(self) -> MirrorResult:
        if not self.client.configured:
            return MirrorResult(reached=False)

        remote = self.client.get("/api/journal/version", timeout=VERSION_PROBE_TIMEOUT_SECONDS)
        if not isinstance(remote, dict):
            return MirrorResult(reached=False)
        remote_version = str(remote.get("version") or "")

        local = self.repository.sync_version()
        local_version = str(local.get("version") or "")

        nothing_there = self.remote_version is not None and remote_version == self.remote_version
        nothing_here = self.local_version is not None and local_version == self.local_version
        if nothing_there and nothing_here:
            return MirrorResult(reached=True, checked_only=True)

        received, pulled = (True, 0) if nothing_there else self._pull()
        delivered, pushed = (True, 0) if nothing_here else self._push()

        if received:
            self.remote_version = self._remote_version()
        if delivered:
            self.local_version = str(self.repository.sync_version().get("version") or "")
        return MirrorResult(reached=True, pulled=pulled, pushed=pushed)

    def _pull(self) -> tuple[bool, int]:
        params = {"since": self.remote_version} if self.remote_version else {}
        payload = self.client.get("/api/journal/pull", **params)
        if not isinstance(payload, dict):
            return (False, 0)
        applied = self.repository.sync_apply(payload)
        return (True, applied["inserted"] + applied["updated"])

    def _push(self) -> tuple[bool, int]:
        payload = self.repository.sync_export(since=self.local_version)
        if not any(payload.get(table) for table in payload):
            return (True, 0)
        answer = self.client.post("/api/journal/push", payload)
        if not isinstance(answer, dict):
            return (False, 0)
        return (True, int(answer.get("inserted", 0)) + int(answer.get("updated", 0)))

    def _remote_version(self) -> str | None:
        remote = self.client.get("/api/journal/version", timeout=VERSION_PROBE_TIMEOUT_SECONDS)
        return str(remote.get("version") or "") if isinstance(remote, dict) else None
