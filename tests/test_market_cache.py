"""The disk cache is a fallback for the *next* run, not part of whether this one succeeded."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

import pytest

from osrs_toolkit.market import MarketDataError, WikiMarketClient


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _serve(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda _request, timeout=None: _Response(payload)
    )


def _fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_request: object, timeout: float | None = None) -> object:
        raise OSError("no network")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


def test_a_fetched_payload_is_cached_for_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = WikiMarketClient(cache_dir=tmp_path / "cache")
    _serve(monkeypatch, {"data": {"1": {"high": 10}}})

    assert client._get("latest") == {"data": {"1": {"high": 10}}}
    assert client.used_cache is False
    assert json.loads((tmp_path / "cache" / "latest.json").read_text(encoding="utf-8")) == {
        "data": {"1": {"high": 10}}
    }


def test_fresh_prices_survive_a_cache_write_that_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk, an antivirus scanner, or a second copy of the app holding the file open
    must not throw away prices that downloaded perfectly well — nor quietly answer with the
    older ones already on disk."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "latest.json").write_text(json.dumps({"data": "yesterday"}), encoding="utf-8")
    client = WikiMarketClient(cache_dir=cache_dir)
    _serve(monkeypatch, {"data": "today"})
    monkeypatch.setattr(
        Path, "write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked"))
    )

    assert client._get("latest") == {"data": "today"}
    assert client.used_cache is False


def test_a_failed_fetch_still_falls_back_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "latest.json").write_text(json.dumps({"data": "yesterday"}), encoding="utf-8")
    client = WikiMarketClient(cache_dir=cache_dir)
    _fail(monkeypatch)

    assert client._get("latest") == {"data": "yesterday"}
    assert client.used_cache is True


def test_a_failed_fetch_with_no_cache_reports_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = WikiMarketClient(cache_dir=tmp_path / "cache")
    _fail(monkeypatch)

    with pytest.raises(MarketDataError):
        client._get("latest")
