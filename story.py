"""Select original offline recaps using progress evidence from a save."""

from functools import lru_cache
import json
from pathlib import Path


PACKS = Path(__file__).resolve().parent / "story_packs"


@lru_cache(maxsize=None)
def _load_pack(filename):
    try:
        with (PACKS / filename).open(encoding="utf-8") as pack:
            return json.load(pack)
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not read bundled story pack {filename}: {error}") from error


def ff7_story(chapter: int, language: str = "en") -> tuple[str | None, list[dict]]:
    """Return the current title and summaries only for earlier main-story chapters."""
    title = None
    earlier = []
    translated = (
        {entry["chapter"]: entry for entry in _load_pack("ff7-remake.zh.json")}
        if language == "zh" else {}
    )
    for entry in _load_pack("ff7-remake.json"):
        entry = translated.get(entry["chapter"], entry)
        if entry["chapter"] == chapter:
            title = entry["title"]
        elif entry["chapter"] < chapter:
            earlier.append({
                "chapter": entry["chapter"],
                "title": entry["title"],
                "text": entry["summary"],
                "beats": entry["beats"],
                "why_it_matters": entry["why_it_matters"],
            })
    return title, earlier


def bg3_story(quest_id: str, unlocked_steps: list[str], language: str = "en") -> list[dict]:
    """Match a quest's written context to the specific steps recorded in this save."""
    if not unlocked_steps:
        return []
    step_order = {step: index for index, step in enumerate(unlocked_steps)}
    known_beats = _load_pack("bg3-quests.json").get(quest_id, ())
    translated = (
        {tuple(beat["requires"]): beat for beat in _load_pack("bg3-quests.zh.json").get(quest_id, ())}
        if language == "zh" else {}
    )
    matched = [
        (max(step_order[step] for step in beat["requires"]), translated.get(tuple(beat["requires"]), beat))
        for beat in known_beats
        if beat["requires"] and all(step in step_order for step in beat["requires"])
    ]
    return [
        {"title": beat["title"], "text": beat["text"]}
        for _, beat in sorted(matched, key=lambda item: item[0])
    ]


def bg3_labels(language: str) -> dict:
    """Return curated quest labels used if the installed locale lacks a translation."""
    return _load_pack("bg3-labels.zh.json") if language == "zh" else {}
