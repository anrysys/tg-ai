"""Style measurement for a Dialog Persona - pure, language-agnostic, offline.

This module is deliberately as pure as ``safety.py``: no network, no database,
no Telethon. That is not tidiness, it is the only way the guarantee this module
carries can be proven. The guarantee is:

    Nothing here ever emits message text.

A Dialog Persona is derived from chat content and is then re-injected into a
model's context on every read, which is exactly the shape ``RISK-06`` warns
about. Keeping the measured half to numbers and Unicode script names means the
measured half is structurally incapable of carrying a payload (SPEC-PSN-002).
The qualitative half is written by the agent, sanitised by ``safety.py``, and
capped.

Everything here also has to work without knowing what language the archive is
in. The archive is multilingual by design (ADR-0003), and a per-language
lexicon of greetings or formality markers would put non-English literals in a
``.py`` file, which AGENTS.md rule 1 forbids and ``just docs-english`` fails on.
So formality and addressing form are left to the agent, and this module
measures only what is true in every script (SPEC-PSN-003).

Specified by SPEC-PSN-002, SPEC-PSN-003 and SPEC-PSN-005.
"""

from __future__ import annotations

import itertools
import re
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: A message at or under this length counts as a short reply. Reporting the
#: share of these matters more than any average: a person who answers "ok" and
#: then writes four paragraphs has no meaningful mean, and drafting to that
#: mean produces a uniform middle register that reads more synthetic than not
#: trying at all.
SHORT_MESSAGE_CHARS = 25

#: Two of the account's own messages closer together than this are one burst.
#: "Sends three short lines instead of one long one" is a strong human tell.
BURST_GAP_SECONDS = 90.0

#: Below this many archived outgoing messages, a style summary is guesswork.
#: The tools return the samples and a warning instead of a confident answer.
MIN_SAMPLE = 20

#: Default and maximum outgoing messages pulled for one analysis.
DEFAULT_SAMPLE_LIMIT = 400
MAX_SAMPLE_LIMIT = 2000

#: Pattern Drift thresholds (SPEC-PSN-005). Volume is in messages archived
#: since the analysis; age is in days; the metric thresholds are absolute
#: differences in the ratio or, for length, a proportional change.
STALE_AFTER_NEW_OUTGOING = 50
STALE_AFTER_DAYS = 90
DRIFT_RATIO_DELTA = 0.20
DRIFT_LENGTH_FACTOR = 2.0

#: Unicode blocks that carry emoji and pictographs. Ranges rather than a list
#: of literals, so the detector works on emoji nobody thought to enumerate and
#: the source stays ASCII.
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1FAFF),  # pictographs, emoticons, transport, symbols, extended
    (0x1F000, 0x1F0FF),  # mahjong, dominoes, playing cards
    (0x2600, 0x27BF),  # misc symbols and dingbats
    (0x2B00, 0x2BFF),  # arrows and geometric shapes used as emoji
    (0xFE00, 0xFE0F),  # variation selectors
    (0x1F1E6, 0x1F1FF),  # regional indicators (flags)
)

_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?…]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class OutgoingSample:
    """One message the account itself sent, as read from the Archive."""

    message_id: int
    text: str
    date: datetime


@dataclass(frozen=True, slots=True)
class StyleMetrics:
    """Language-agnostic measurements of how the account writes in one Dialog.

    Every field is a number, a timestamp or a Unicode script name. No field
    can hold message text, which is what makes this half of a Dialog Persona
    safe to re-inject on every read (SPEC-PSN-002).
    """

    sample_size: int
    first_date: datetime | None
    last_date: datetime | None
    chars_median: float
    chars_p90: float
    chars_max: int
    words_median: float
    sentences_median: float
    short_reply_rate: float
    multiline_rate: float
    lowercase_start_rate: float
    trailing_stop_rate: float
    question_rate: float
    exclamation_rate: float
    ellipsis_rate: float
    emoji_rate: float
    link_rate: float
    burst_rate: float
    scripts: tuple[tuple[str, float], ...]

    def as_dict(self) -> dict[str, Any]:
        """Render as a JSON-serialisable snapshot for the ``metrics`` column.

        Timestamps become ISO strings because this goes through ``json.dumps``
        into a JSONB column; ``datetime`` is not JSON.
        """
        return {
            "sample_size": self.sample_size,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "chars_median": self.chars_median,
            "chars_p90": self.chars_p90,
            "chars_max": self.chars_max,
            "words_median": self.words_median,
            "sentences_median": self.sentences_median,
            "short_reply_rate": self.short_reply_rate,
            "multiline_rate": self.multiline_rate,
            "lowercase_start_rate": self.lowercase_start_rate,
            "trailing_stop_rate": self.trailing_stop_rate,
            "question_rate": self.question_rate,
            "exclamation_rate": self.exclamation_rate,
            "ellipsis_rate": self.ellipsis_rate,
            "emoji_rate": self.emoji_rate,
            "link_rate": self.link_rate,
            "burst_rate": self.burst_rate,
            "scripts": [list(entry) for entry in self.scripts],
        }


#: What an analysis of nothing returns. Every ratio is zero and every date is
#: absent, so a caller can render it without a single None check and no
#: statistics function is ever handed an empty sequence.
EMPTY_METRICS = StyleMetrics(
    sample_size=0,
    first_date=None,
    last_date=None,
    chars_median=0.0,
    chars_p90=0.0,
    chars_max=0,
    words_median=0.0,
    sentences_median=0.0,
    short_reply_rate=0.0,
    multiline_rate=0.0,
    lowercase_start_rate=0.0,
    trailing_stop_rate=0.0,
    question_rate=0.0,
    exclamation_rate=0.0,
    ellipsis_rate=0.0,
    emoji_rate=0.0,
    link_rate=0.0,
    burst_rate=0.0,
    scripts=(),
)


def is_emoji(character: str) -> bool:
    """Whether one codepoint belongs to an emoji or pictograph block."""
    point = ord(character)
    return any(low <= point <= high for low, high in _EMOJI_RANGES)


def _script_of(character: str) -> str | None:
    """Name the writing system of one cased character, or ``None``.

    The name comes from the Unicode database at runtime - ``LATIN``,
    ``CYRILLIC``, ``GREEK`` - never from a literal in this file. That is how a
    Persona can record "writes Cyrillic to this person" while the source stays
    ASCII and ``just docs-english`` passes by construction (SPEC-PSN-003).
    """
    if not character.isalpha():
        return None
    try:
        name = unicodedata.name(character)
    except ValueError:  # unnamed codepoint
        return None
    return name.split()[0]


def _ratio(count: int, total: int) -> float:
    """Share of ``total``, rounded, and zero when there is nothing to divide."""
    if total <= 0:
        return 0.0
    return round(count / total, 4)


def _percentile(values: list[int], fraction: float) -> float:
    """Nearest-rank percentile. Total over an empty list, unlike ``statistics``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return float(ordered[index])


def _median(values: list[int]) -> float:
    """Median that returns 0.0 for an empty list.

    ``statistics.median([])`` raises ``StatisticsError``. A metric function
    that can raise would turn an empty Dialog into an ``ERROR:`` from a tool,
    so totality here is what keeps AGENTS.md rule 4 cheap to honour.
    """
    if not values:
        return 0.0
    return round(float(statistics.median(values)), 2)


def analyse_style(samples: Sequence[OutgoingSample]) -> StyleMetrics:
    """Measure how the account writes, from its own messages in one Dialog.

    Args:
        samples: The account's own messages, in any order. Empty is valid and
            yields :data:`EMPTY_METRICS`.

    Returns:
        Numbers and Unicode script names only. Never message text
        (SPEC-PSN-002).
    """
    if not samples:
        return EMPTY_METRICS

    ordered = sorted(samples, key=lambda sample: (sample.date, sample.message_id))
    total = len(ordered)

    lengths: list[int] = []
    word_counts: list[int] = []
    sentence_counts: list[int] = []
    short = multiline = lowercase_start = trailing_stop = 0
    question = exclamation = ellipsis = emoji = link = 0
    script_counts: dict[str, int] = {}

    for sample in ordered:
        text = sample.text
        stripped = text.strip()
        lengths.append(len(stripped))
        word_counts.append(len(stripped.split()))
        sentence_counts.append(max(1, len(_SENTENCE_END_RE.findall(stripped))))

        if len(stripped) <= SHORT_MESSAGE_CHARS:
            short += 1
        if "\n" in text.strip("\n"):
            multiline += 1
        if stripped.endswith((".", "!", "?")):
            trailing_stop += 1
        if "?" in stripped:
            question += 1
        if "!" in stripped:
            exclamation += 1
        if "…" in stripped or "..." in stripped:
            ellipsis += 1
        if _URL_RE.search(stripped):
            link += 1

        # First cased character, so leading punctuation or an emoji does not
        # hide the capitalisation habit. Works in every bicameral script.
        for character in stripped:
            if character.isalpha():
                if character.islower():
                    lowercase_start += 1
                break

        if any(is_emoji(character) for character in text):
            emoji += 1

        for character in text:
            script = _script_of(character)
            if script is not None:
                script_counts[script] = script_counts.get(script, 0) + 1

    burst = 0
    for previous, current in itertools.pairwise(ordered):
        if (current.date - previous.date).total_seconds() <= BURST_GAP_SECONDS:
            burst += 1

    cased_total = sum(script_counts.values())
    scripts = tuple(
        (name, _ratio(count, cased_total))
        for name, count in sorted(script_counts.items(), key=lambda item: -item[1])[:3]
    )

    return StyleMetrics(
        sample_size=total,
        first_date=ordered[0].date,
        last_date=ordered[-1].date,
        chars_median=_median(lengths),
        chars_p90=_percentile(lengths, 0.9),
        chars_max=max(lengths),
        words_median=_median(word_counts),
        sentences_median=_median(sentence_counts),
        short_reply_rate=_ratio(short, total),
        multiline_rate=_ratio(multiline, total),
        lowercase_start_rate=_ratio(lowercase_start, total),
        trailing_stop_rate=_ratio(trailing_stop, total),
        question_rate=_ratio(question, total),
        exclamation_rate=_ratio(exclamation, total),
        ellipsis_rate=_ratio(ellipsis, total),
        emoji_rate=_ratio(emoji, total),
        link_rate=_ratio(link, total),
        # A burst needs a predecessor, so the denominator is one short.
        burst_rate=_ratio(burst, total - 1),
        scripts=scripts,
    )


def _percent(ratio: float) -> int:
    """Render a 0..1 ratio as whole percent, for a line a model reads."""
    return int(round(ratio * 100))


def render_style_constraints(metrics: StyleMetrics) -> list[str]:
    """Turn Style Metrics into imperative lines a drafting model can act on.

    Distributions rather than averages, deliberately: told "your typical reply
    is 90 characters", a model writes 90 characters every time, and uniformity
    is itself the tell. Told the median, the p90 and the short-reply share, it
    can vary the way a person does.
    """
    if metrics.sample_size == 0:
        return ["No archived messages of your own in this dialog, so nothing is measured."]

    lines = [
        f"- Typical message is {int(metrics.chars_median)} characters; "
        f"90% are under {int(metrics.chars_p90)}. Longest archived: {metrics.chars_max}.",
        f"- {_percent(metrics.short_reply_rate)}% of your messages here are "
        f"{SHORT_MESSAGE_CHARS} characters or fewer. Do not pad those.",
        f"- You start {_percent(metrics.lowercase_start_rate)}% of messages in lower case.",
        f"- You end with a full stop, '!' or '?' in "
        f"{_percent(metrics.trailing_stop_rate)}% of messages; "
        f"'?' appears in {_percent(metrics.question_rate)}%, "
        f"'!' in {_percent(metrics.exclamation_rate)}%.",
        f"- Emoji appear in {_percent(metrics.emoji_rate)}% of messages; "
        f"ellipsis in {_percent(metrics.ellipsis_rate)}%.",
        f"- {_percent(metrics.multiline_rate)}% run to more than one line; "
        f"you follow your own message within {int(BURST_GAP_SECONDS)}s "
        f"{_percent(metrics.burst_rate)}% of the time.",
    ]
    if metrics.link_rate:
        lines.append(f"- You share links in {_percent(metrics.link_rate)}% of messages.")
    if metrics.scripts:
        mix = ", ".join(f"{name} {_percent(share)}%" for name, share in metrics.scripts)
        lines.append(f"- Script mix: {mix}. Write in the same script.")
    return lines


def compare_style(stored: dict[str, Any], current: StyleMetrics) -> list[str]:
    """Report where the account's writing has moved since a Persona was written.

    This is the third and best staleness axis (SPEC-PSN-005). Volume alone
    cannot see a style that changed inside the same number of messages, and
    age alone flags Personas that are still perfectly accurate.

    Args:
        stored: A snapshot previously produced by :meth:`StyleMetrics.as_dict`.
            A missing, empty or corrupt snapshot yields no drift lines rather
            than an error - there is simply nothing to compare against.
        current: Metrics measured now.

    Returns:
        One line per axis that moved past its threshold. Empty when the
        Persona still describes the account accurately.
    """
    if not isinstance(stored, dict) or not stored.get("sample_size"):
        return []

    lines: list[str] = []

    def _number(key: str) -> float | None:
        value = stored.get(key)
        return float(value) if isinstance(value, int | float) else None

    was_length = _number("chars_median")
    now_length = current.chars_median
    if was_length and now_length:
        factor = max(was_length, now_length) / min(was_length, now_length)
        if factor >= DRIFT_LENGTH_FACTOR:
            lines.append(
                f"- DRIFT: typical length {int(was_length)} -> {int(now_length)} characters."
            )

    for key, label in (
        ("short_reply_rate", "short-reply rate"),
        ("lowercase_start_rate", "lower-case opening rate"),
        ("emoji_rate", "emoji rate"),
        ("trailing_stop_rate", "sentence-ending punctuation rate"),
        ("burst_rate", "burst rate"),
    ):
        was = _number(key)
        if was is None:
            continue
        now = float(getattr(current, key))
        if abs(now - was) >= DRIFT_RATIO_DELTA:
            lines.append(f"- DRIFT: {label} {_percent(was)}% -> {_percent(now)}%.")

    return lines


def freshness(
    *,
    messages_since: int,
    analysed_at: datetime,
    now: datetime,
    drift_lines: Sequence[str],
) -> tuple[bool, str]:
    """Judge a stored Persona on all three staleness axes.

    Volume alone is not enough: it is bounded above by how far the Sync has
    run, so a Persona written a year ago reports zero new messages - and
    therefore fresh - on an archive nobody has synced since. Age catches that.
    Drift catches a style that changed without the message count moving.

    Returns:
        ``(is_stale, sentence)``. The sentence always names which axis fired,
        so the agent can tell "you have written a lot since" from "this is
        simply old" (SPEC-PSN-005).
    """
    age_days = max(0, (now - analysed_at).days)
    reasons: list[str] = []
    if messages_since >= STALE_AFTER_NEW_OUTGOING:
        reasons.append(f"volume ({messages_since} of your messages archived since)")
    if age_days >= STALE_AFTER_DAYS:
        reasons.append(f"age ({age_days} days old)")
    if drift_lines:
        reasons.append(f"drift ({len(drift_lines)} measurement(s) moved)")

    if not reasons:
        return False, (
            f"FRESH - written {age_days} day(s) ago, "
            f"{messages_since} of your messages archived since."
        )
    return True, "STALE - " + "; ".join(reasons) + ". Re-read the samples and rewrite it."
