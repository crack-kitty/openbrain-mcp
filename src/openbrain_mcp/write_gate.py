from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass
class GateResult:
    ok: bool
    error: str | None = None


def validate_kind(kind: str) -> GateResult:
    if kind not in ("rule", "fact", "incident", "task"):
        return GateResult(False, f"invalid kind: {kind!r} (must be rule/fact/incident/task)")
    return GateResult(True)


def validate_severity(kind: str, severity: str | None) -> GateResult:
    if kind != "rule":
        return GateResult(True)
    if severity not in ("BLOCKER", "PATTERN"):
        return GateResult(False, "rules require severity=BLOCKER or PATTERN")
    return GateResult(True)


def validate_headline(headline: str | None, settings: Settings) -> GateResult:
    if not headline or not headline.strip():
        return GateResult(False, "headline is required")
    word_count = len(headline.split())
    if word_count > settings.headline_max_words:
        return GateResult(
            False,
            f"headline must be ≤{settings.headline_max_words} words (got {word_count})",
        )
    return GateResult(True)


def validate_body(body: str | None, settings: Settings) -> GateResult:
    if body is None:
        body = ""
    word_count = len(body.split())
    if word_count > settings.body_max_words:
        return GateResult(
            False,
            f"body must be ≤{settings.body_max_words} words (got {word_count})",
        )
    return GateResult(True)


def validate_all(
    *,
    kind: str,
    headline: str,
    body: str,
    severity: str | None,
    settings: Settings,
) -> GateResult:
    for check in (
        validate_kind(kind),
        validate_severity(kind, severity),
        validate_headline(headline, settings),
        validate_body(body, settings),
    ):
        if not check.ok:
            return check
    return GateResult(True)
