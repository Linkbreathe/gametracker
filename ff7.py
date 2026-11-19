"""Read chapter-level progress from a Windows FFVII Remake save."""

from pathlib import Path

from i18n import translate
from story import ff7_story


SAVE_SIZE = 0x8FF680
COMMON_HEADER = b"RESD" * 4
END_RECORD = bytes.fromhex("04 00 01 00") + bytes(12)

# Common record fields documented in Kingdom Save Editor's layout notes.
# https://github.com/Xeeynamo/KingdomSaveEditor/tree/master/KHSave.LibFf7Remake/Chunks
COMMON_DATA_OFFSET = 0x28
CHAPTER_OFFSET = COMMON_DATA_OFFSET + 0x42F5D

# Game Tools Collection documents the file-level play time in seconds.
# https://github.com/RyudoSynbios/game-tools-collection/tree/master/src/lib/templates/final-fantasy-vii-remake/saveEditor
PLAY_TIME_OFFSET = 0x2B0

def read_save(save_path: Path, install_path: Path | None = None, *, language: str = "en") -> dict:
    """Return read-only progress facts from the supported PC save layout."""
    with save_path.open("rb") as save:
        save.seek(0, 2)
        if save.tell() != SAVE_SIZE:
            raise ValueError("Unsupported FFVII Remake save size. Select a numbered save slot.")
        save.seek(0x10)
        if save.read(len(COMMON_HEADER)) != COMMON_HEADER:
            raise ValueError("Missing FFVII Remake common record.")
        save.seek(-len(END_RECORD), 2)
        if save.read(len(END_RECORD)) != END_RECORD:
            raise ValueError("Invalid FFVII Remake end record.")

        save.seek(CHAPTER_OFFSET)
        chapter = int.from_bytes(save.read(1), "little")
        save.seek(PLAY_TIME_OFFSET)
        play_seconds = int.from_bytes(save.read(4), "little")

    if 1 <= chapter <= 18:
        story = "Main story"
    elif chapter in (22, 23):
        story = "Episode INTERmission"
        chapter -= 21
    else:
        raise ValueError("Unrecognized FFVII Remake chapter.")

    hours, remainder = divmod(play_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    facts = {
        "Story": story,
        "Chapter": chapter,
        "Play time": f"{hours}:{minutes:02d}:{seconds:02d}",
    }
    recap = []
    if story == "Main story":
        title, recap = ff7_story(chapter, language)
        if title:
            facts["Chapter title"] = title
    return {
        "facts": facts,
        "quests": [],
        "chapter_recap": recap,
        "note": translate(language, "The exact objective within the saved chapter is unavailable."),
    }
