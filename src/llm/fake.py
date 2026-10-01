"""Offline-Fake-Provider für Tests und Demos ohne API-Key und ohne Netzwerk."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from src.llm.provider import LLMError, LLMProvider, LLMRequest, LLMResponse, LLMUsage

ScriptedReply = str | dict[str, Any] | LLMError | Callable[[LLMRequest], "str | dict[str, Any]"]


class FakeLLMProvider(LLMProvider):
    """Liefert vorgegebene Antworten der Reihe nach.

    Jede Antwort ist ein JSON-String, ein Dict (wird serialisiert), eine ``LLMError``-
    Instanz (wird geworfen) oder eine Funktion ``request -> str|dict``. Sind alle
    vorgegebenen Antworten verbraucht, wird ``default`` verwendet (Standard: alle
    Merkmale unbekannt).
    """

    name = "fake"

    def __init__(
        self,
        replies: Iterable[ScriptedReply] = (),
        default: ScriptedReply | None = None,
        model: str = "fake-model",
        usage: LLMUsage | None = None,
    ) -> None:
        self._replies = list(replies)
        self._default: ScriptedReply = default if default is not None else {}
        self._model = model
        self._usage = usage
        self.requests: list[LLMRequest] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def calls(self) -> int:
        return len(self.requests)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        reply = self._replies.pop(0) if self._replies else self._default
        if isinstance(reply, LLMError):
            raise reply
        if callable(reply):
            reply = reply(request)
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return LLMResponse(text=text, model=self._model, stop_reason="end_turn", usage=self._usage)
