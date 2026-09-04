"""Chunking must never produce a message Telegram rejects or a broken word.

Proves SPEC-SND-002.
"""

import pytest

from tg_ai.safety import MAX_CHUNK_CHARS, split_message

TELEGRAM_HARD_LIMIT = 4096


def test_short_message_is_one_chunk():
    assert split_message("hello") == ["hello"]


def test_empty_and_whitespace_yield_nothing():
    assert split_message("") == []
    assert split_message("   \n\n  ") == []


def test_message_just_under_the_limit_is_not_split():
    text = "a" * (MAX_CHUNK_CHARS - 1)
    assert split_message(text) == [text]


def test_message_at_the_limit_is_not_split():
    text = "a" * MAX_CHUNK_CHARS
    assert split_message(text) == [text]


def test_every_chunk_fits_telegram_hard_limit():
    text = "Sentence number one is here. " * 900
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert all(len(chunk) < TELEGRAM_HARD_LIMIT for chunk in chunks)


def test_no_word_is_broken_in_half():
    text = " ".join(f"word{i:05d}" for i in range(2000))
    chunks = split_message(text)
    assert len(chunks) > 1
    # Rejoining must reproduce the original token sequence exactly. A word cut
    # in half would produce two different tokens here.
    assert " ".join(chunks).split() == text.split()


def test_paragraph_boundary_is_preferred():
    first = "A" * 3000
    second = "B" * 1500
    chunks = split_message(f"{first}\n\n{second}")
    assert chunks == [first, second]


def test_sentence_boundary_is_used_when_no_newline_exists():
    text = ("x" * 200 + ". ") * 30
    chunks = split_message(text)
    assert len(chunks) > 1
    # Everything but the tail ends on the full stop that closed a sentence.
    assert all(chunk.endswith(".") for chunk in chunks[:-1])


def test_single_oversized_token_is_hard_split_as_a_last_resort():
    text = "z" * 9000
    chunks = split_message(text)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]
    assert "".join(chunks) == text


def test_multilingual_and_emoji_content_survives_intact():
    # Written as escapes so the source stays English-only (AGENTS.md rule 1)
    # while still exercising non-Latin scripts and astral-plane characters.
    # Cyrillic greeting + rocket emoji + Latin text, as a real chat would mix them.
    cyrillic = "\u041f\u0440\u0438\u0432\u0456\u0442, \u0446\u0435 \u0442\u0435\u0441\u0442"
    unit = f"{cyrillic} \U0001f680 with mixed scripts. "
    text = unit * 200
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "").strip()


def test_custom_limit_is_respected():
    chunks = split_message("one two three four five six seven eight", limit=12)
    assert all(len(chunk) <= 12 for chunk in chunks)
    assert " ".join(chunks).split() == "one two three four five six seven eight".split()


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError, match="limit must be positive"):
        split_message("text", limit=0)
