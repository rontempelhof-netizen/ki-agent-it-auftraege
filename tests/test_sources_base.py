from __future__ import annotations

from src.sources.base import FetchResult, SourceConnector, safe_fetch


class BrokenConnector(SourceConnector):
    name = "broken"

    def fetch(self) -> FetchResult:
        raise ConnectionError("Portal nicht erreichbar")


class EmptyConnector(SourceConnector):
    name = "empty"

    def fetch(self) -> FetchResult:
        return FetchResult(connector=self.name)


def test_safe_fetch_isolates_errors_per_source():
    results = [safe_fetch(c) for c in (BrokenConnector(), EmptyConnector())]

    assert [r.ok for r in results] == [False, True]
    assert results[0].error == "ConnectionError: Portal nicht erreichbar"
    assert results[0].connector == "broken"


def test_acknowledge_is_optional_noop():
    connector = EmptyConnector()
    connector.acknowledge(connector.fetch())
