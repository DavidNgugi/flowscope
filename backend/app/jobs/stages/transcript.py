"""Transcript cleanup shared by captions, Whisper, the API, and alignment.

YouTube automatic captions are commonly emitted as rolling cues: each cue
repeats the end of the previous cue before adding a few new words.  Treating
those cues as independent transcript lines makes both the transcript and the
screen context repeat themselves.  This module turns them into a monotonic
stream while preserving the timing of the cue that introduced each phrase.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

_WORD_RE = re.compile(r"\S+")
_EDGE_PUNCTUATION_RE = re.compile(r"(^[^\w]+|[^\w]+$)")
_MAX_OVERLAP_WORDS = 80


def _word_key(word: str) -> str:
    return _EDGE_PUNCTUATION_RE.sub("", word).casefold()


def _remove_rolling_prefix(text: str, emitted_words: list[str]) -> str:
    matches = list(_WORD_RE.finditer(text))
    words = [match.group(0) for match in matches]
    keys = [_word_key(word) for word in words]
    history_keys = [_word_key(word) for word in emitted_words[-_MAX_OVERLAP_WORDS:]]

    max_overlap = min(len(keys), len(history_keys))
    overlap = 0
    for size in range(max_overlap, 1, -1):
        if history_keys[-size:] == keys[:size]:
            overlap = size
            break

    # A fully repeated one-word cue is safe to discard, while a single word at
    # a normal sentence boundary may be intentional ("very very", for example).
    if not overlap and len(keys) == 1 and history_keys[-1:] == keys:
        overlap = 1

    if overlap == len(words):
        return ""
    if overlap:
        return text[matches[overlap].start():].strip()
    return text


def normalize_transcript_segments(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return ordered, whitespace-normalized segments without rolling repetition.

    Cues with identical timing are collapsed first.  If their text is
    cumulative, the longest version wins; otherwise their unique text is
    combined.  Overlap between consecutive cues is then removed word-for-word.
    """
    ordered = sorted(
        (dict(segment) for segment in segments),
        key=lambda segment: (int(segment["start_ms"]), int(segment["end_ms"])),
    )

    by_timing: list[dict[str, Any]] = []
    for segment in ordered:
        text = " ".join(str(segment.get("text") or "").split())
        if not text:
            continue
        segment["text"] = text

        if (
            by_timing
            and segment["start_ms"] == by_timing[-1]["start_ms"]
            and segment["end_ms"] == by_timing[-1]["end_ms"]
        ):
            existing = by_timing[-1]
            existing_key = existing["text"].casefold()
            new_key = text.casefold()
            if existing_key in new_key:
                existing["text"] = text
            elif new_key not in existing_key:
                existing["text"] = f'{existing["text"]} {text}'
            continue
        by_timing.append(segment)

    normalized: list[dict[str, Any]] = []
    emitted_words: list[str] = []
    for segment in by_timing:
        text = _remove_rolling_prefix(segment["text"], emitted_words)
        if not text:
            continue
        segment["text"] = text
        emitted_words.extend(_WORD_RE.findall(text))

        # The UI displays timestamps to the second. Combine fragments that
        # start within that same displayed second so it never shows several
        # different transcript rows carrying an apparently identical time.
        if normalized and segment["start_ms"] // 1000 == normalized[-1]["start_ms"] // 1000:
            normalized[-1]["text"] = f'{normalized[-1]["text"]} {text}'
            normalized[-1]["end_ms"] = max(normalized[-1]["end_ms"], segment["end_ms"])
        else:
            normalized.append(segment)

    return normalized
