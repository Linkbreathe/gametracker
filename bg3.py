"""Read quest journal snapshots from Baldur's Gate 3 LSPK v18 saves.

Format references: Norbyte/lslib (PackageCommon and LSFReader) and
DanFinchUK/bg3-savefile-parser/FORMAT.md (journal and localization).
"""

import io
import json
from pathlib import Path
import struct
from typing import BinaryIO
from xml.etree import ElementTree
import zlib

import lz4.block
import lz4.frame
import zstandard

from i18n import translate
from story import bg3_labels, bg3_story


_PACKAGE_ENTRY = struct.Struct("<256sIHBBII")
_LSF_HEADER = struct.Struct("<4sIQ10IB3xI")
_NODE = struct.Struct("<Iii")
_ATTRIBUTE = struct.Struct("<IIi")
_LOCA_ENTRY = struct.Struct("<64sHI")
_MAX_SECTION = 128 * 1024 * 1024
_GUSTAV_JOURNAL = "mods/gustavdev/story/journal"
_JOURNAL_FIELDS = {
    "Quest": {"ObjectiveID", "QuestUnlocked", "QuestDisabled"},
    "QuestsProgress": {"MapKey"},
    "QuestUnlockedSteps": {"QuestUnlockedSteps"},
}


def _read(stream: BinaryIO, length: int) -> bytes:
    if not 0 <= length <= _MAX_SECTION:
        raise ValueError("Baldur's Gate 3 resource size exceeds the supported limit.")
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("Baldur's Gate 3 resource is truncated.")
    return data


def _inflate(data: bytes, method: int, expected: int, *, framed: bool = False) -> bytes:
    if not 0 <= expected <= _MAX_SECTION:
        raise ValueError("Baldur's Gate 3 resource size exceeds the supported limit.")
    if method == 0:
        output = data
    elif method == 1:
        reader = zlib.decompressobj()
        output = reader.decompress(data, expected + 1)
        if not reader.eof:
            raise ValueError("Invalid compressed resource.")
    elif method == 2:
        if framed:
            reader = lz4.frame.LZ4FrameDecompressor()
            output = reader.decompress(data, max_length=expected + 1)
            if not reader.eof:
                raise ValueError("Invalid framed LZ4 resource.")
        else:
            output = lz4.block.decompress(data, uncompressed_size=expected)
    elif method == 3:
        frame_size = zstandard.frame_content_size(data)
        if frame_size not in (-1, expected):
            raise ValueError("Invalid Zstandard resource size.")
        output = zstandard.ZstdDecompressor().decompress(data, max_output_size=expected)
    else:
        raise ValueError(f"Unsupported Baldur's Gate 3 compression mode {method}.")
    if len(output) != expected:
        raise ValueError("Baldur's Gate 3 resource size does not match the archive.")
    return output


def _package_resources(path: Path, wanted: set[str]) -> dict[str, bytes]:
    """Read specified archive members by offset without extracting to disk."""
    resources = {}
    with path.open("rb") as stream:
        magic, version, table_offset, _ = struct.unpack("<4sIQI", _read(stream, 20))
        if magic != b"LSPK" or version != 18:
            raise ValueError("Expected a Baldur's Gate 3 version-18 LSPK archive.")
        stream.seek(table_offset)
        entry_count, stored_length = struct.unpack("<II", _read(stream, 8))
        table = _inflate(_read(stream, stored_length), 2, entry_count * _PACKAGE_ENTRY.size)
        for raw_name, low, high, part, flags, disk_size, raw_size in _PACKAGE_ENTRY.iter_unpack(table):
            name = raw_name.split(b"\0", 1)[0].decode().casefold()
            if name not in wanted:
                continue
            if name in resources or part != 0:
                raise ValueError("Ambiguous or split Baldur's Gate 3 archive resource.")
            stream.seek(low | (high << 32))
            method = flags & 15
            payload = _read(stream, disk_size)
            resources[name] = _inflate(payload, method, raw_size if method else disk_size)
    return resources


def _names(data: bytes) -> list[list[str]]:
    with io.BytesIO(data) as stream:
        count, = struct.unpack("<I", _read(stream, 4))
        if count > (len(data) - 4) // 2:
            raise ValueError("Invalid LSF name table.")
        result = []
        for _ in range(count):
            chain_length, = struct.unpack("<H", _read(stream, 2))
            chain = []
            for _ in range(chain_length):
                length, = struct.unpack("<H", _read(stream, 2))
                chain.append(_read(stream, length).decode())
            result.append(chain)
        return result


def _journal(globals_lsf: bytes) -> tuple[list[dict], dict[str, list[str]]]:
    """Read the quest state and the saved narrative step IDs within each quest."""
    with io.BytesIO(globals_lsf) as stream:
        magic, version, _, *metadata = _LSF_HEADER.unpack(_read(stream, _LSF_HEADER.size))
        sizes, flags, layout = metadata[:10], metadata[-2], metadata[-1]
        if magic != b"LSOF" or version != 7 or layout not in (0, 2):
            raise ValueError("Unsupported Baldur's Gate 3 journal resource layout.")

        sections = []
        # Header records key sizes second. Actual keys, if any, follow values.
        for section in (0, 2, 3, 4):
            raw_size, disk_size = sizes[2 * section:2 * section + 2]
            compressed = flags & 15 if disk_size else 0
            payload = _read(stream, disk_size if compressed else raw_size)
            sections.append(_inflate(payload, compressed, raw_size, framed=section != 0))
    strings, node_bytes, attr_bytes, values = sections
    name_table = _names(strings)

    def name_at(index: int) -> str:
        return name_table[index >> 16][index & 0xFFFF]

    journal_found = False
    journal_nodes = {}
    for index, (name_index, _, parent) in enumerate(_NODE.iter_unpack(node_bytes)):
        if parent == -1 and name_at(name_index) == "Journal":
            journal_found = True
            journal_nodes[index] = (parent, "Journal")
        elif parent in journal_nodes:
            journal_nodes[index] = (parent, name_at(name_index))
    if not journal_found:
        raise ValueError("Baldur's Gate 3 save has no journal region.")

    attributes = {}
    value_offset = 0
    for field_index, packed, owner in _ATTRIBUTE.iter_unpack(attr_bytes):
        span, kind = packed >> 6, packed & 63
        end = value_offset + span
        if end > len(values):
            raise ValueError("Invalid attribute offset in BG3 journal resource.")
        node = journal_nodes.get(owner)
        if node and node[1] in _JOURNAL_FIELDS:
            field = name_at(field_index)
            if field in _JOURNAL_FIELDS[node[1]]:
                raw = values[value_offset:end]
                if field in ("QuestUnlocked", "QuestDisabled") and kind == 19 and len(raw) == 1:
                    value = raw[0] != 0
                elif field in ("MapKey", "ObjectiveID", "QuestUnlockedSteps") and kind in (20, 21, 22, 23):
                    value = raw.decode().rstrip("\0")
                else:
                    raise ValueError(f"Unexpected type for BG3 journal field {field}.")
                attributes.setdefault(owner, {})[field] = value
        value_offset = end

    steps_by_node = {}
    for index, (parent, node_name) in journal_nodes.items():
        if node_name == "QuestUnlockedSteps":
            step_id = attributes.get(index, {}).get("QuestUnlockedSteps")
            if step_id:
                steps_by_node.setdefault(parent, []).append(step_id)

    quests = []
    unlocked_steps = {}
    for index, (parent, node_name) in journal_nodes.items():
        if node_name != "Quest":
            continue
        fields = attributes.get(index, {})
        if fields.get("QuestUnlocked") is not True:
            continue
        while parent in journal_nodes and journal_nodes[parent][1] != "QuestsProgress":
            parent = journal_nodes[parent][0]
        quest_id = attributes.get(parent, {}).get("MapKey")
        if not quest_id:
            raise ValueError("Unlocked BG3 quest has no journal identifier.")
        if "QuestDisabled" not in fields:
            raise ValueError("Unlocked BG3 quest has no disabled status.")
        disabled = fields["QuestDisabled"]
        objective = fields.get("ObjectiveID")
        quests.append({
            "id": quest_id,
            "state": "disabled" if disabled else "unlocked",
            "objectives": [{"id": objective}] if objective and not disabled else [],
        })
        unlocked_steps[quest_id] = list(dict.fromkeys(steps_by_node.get(index, ())))
    return quests, unlocked_steps


def _prototype_handles(data: bytes, node_type: str, text_field: str) -> dict:
    result = {}
    for node in ElementTree.fromstring(data).iterfind(f".//node[@id='{node_type}']"):
        fields = {field.get("id"): field for field in node.findall("attribute")}
        quest_id = fields["QuestID"].get("value")
        identity = (quest_id, fields["ObjectiveID"].get("value")) if node_type == "Objective" else quest_id
        translated = fields[text_field]
        handle = translated.get("handle")
        if handle:
            result[identity] = handle
    return result


def _journal_step_handles(data: bytes) -> tuple[dict[tuple[str, str], str], set[str]]:
    """Index player-facing descriptions for explicitly visible quest prototypes."""
    handles = {}
    ambiguous = set()
    visible_ids = set()
    for quest in ElementTree.fromstring(data).iterfind(".//node[@id='Quest']"):
        fields = {field.get("id"): field for field in quest.findall("attribute")}
        if fields["QuestVisiblity"].get("value") != "true":
            continue
        quest_id = fields["QuestID"].get("value")
        if not quest_id:
            continue
        visible_ids.add(quest_id)
        for step in quest.findall("./children/node[@id='QuestStep']"):
            fields = {field.get("id"): field for field in step.findall("attribute")}
            step_id = fields["ID"].get("value")
            handle = fields["Description"].get("handle")
            if not (quest_id and step_id and handle):
                continue
            key = (quest_id, step_id)
            existing = handles.setdefault(key, handle)
            if existing != handle:
                ambiguous.add(key)
    return {identity: handle for identity, handle in handles.items() if identity not in ambiguous}, visible_ids


def _localized_strings(data: bytes, handles: set[str]) -> dict[str, str]:
    magic, count, text_offset = struct.unpack_from("<4sII", data)
    if magic != b"LOCA" or text_offset != 12 + count * _LOCA_ENTRY.size:
        raise ValueError("Invalid BG3 localization table.")
    result = {}
    for key, _, size in _LOCA_ENTRY.iter_unpack(memoryview(data)[12:text_offset]):
        stop = text_offset + size
        if stop > len(data):
            raise ValueError("Truncated BG3 localization text.")
        handle = key.split(b"\0", 1)[0].decode()
        if handle in handles:
            result[handle] = data[text_offset:stop].decode().rstrip("\0")
        text_offset = stop
    return result


def _game_locale(data_path: Path, language: str, handles: set[str]) -> dict[str, str]:
    name = "Chinese" if language == "zh" else "English"
    resource = f"localization/{name.casefold()}/{name.casefold()}.loca"
    directory = data_path / "Localization"
    for archive in (directory / f"{name}.pak", directory / name / f"{name}.pak"):
        if archive.is_file():
            payload = _package_resources(archive, {resource}).get(resource)
            if payload:
                return _localized_strings(payload, handles)
    return {}


def _attach_journal_text(
    quests: list[dict], unlocked_steps: dict[str, list[str]], install_path: Path, language: str,
) -> bool:
    data_path = install_path / "Data"
    quest_resource = f"{_GUSTAV_JOURNAL}/quest_prototypes.lsx"
    objective_resource = f"{_GUSTAV_JOURNAL}/objective_prototypes.lsx"
    prototypes = _package_resources(data_path / "Gustav.pak", {quest_resource, objective_resource})
    titles = _prototype_handles(prototypes[quest_resource], "Quest", "QuestTitle")
    objectives = _prototype_handles(prototypes[objective_resource], "Objective", "Description")
    steps, visible_ids = _journal_step_handles(prototypes[quest_resource])
    visible_quests = []
    for quest in quests:
        quest["journal_visible"] = quest["id"] in visible_ids
        if quest["journal_visible"]:
            visible_quests.append(quest)

    wanted_titles = [titles.get(quest["id"]) for quest in visible_quests]
    wanted_objectives = [
        objectives.get((quest["id"], objective["id"]))
        for quest in visible_quests
        for objective in quest["objectives"]
    ]
    wanted_steps = [
        steps.get((quest_id, step_id))
        for quest_id, step_ids in unlocked_steps.items()
        for step_id in step_ids
    ]
    wanted = {handle for handle in wanted_titles + wanted_objectives + wanted_steps if handle}
    if not wanted:
        return True

    selected = _game_locale(data_path, language, wanted)
    alternate = "en" if language == "zh" else "zh"
    missing = {handle for handle in wanted if not selected.get(handle)}
    fallback = _game_locale(data_path, alternate, missing) if missing else {}
    labels = bg3_labels(language)
    translated_titles = labels.get("titles", {})
    translated_objectives = labels.get("objectives", {})

    for quest in visible_quests:
        quest_id = quest["id"]
        title_handle = titles.get(quest_id)
        title = selected.get(title_handle) or translated_titles.get(quest_id) or fallback.get(title_handle)
        if title:
            quest["title"] = title
        updates = []
        for step_id in unlocked_steps.get(quest_id, ()):
            handle = steps.get((quest_id, step_id))
            text = selected.get(handle)
            if text and text.strip():
                updates.append({"id": step_id, "text": text, "language": language})
            else:
                text = fallback.get(handle)
                if text and text.strip():
                    updates.append({"id": step_id, "text": text, "language": alternate})
        if updates:
            quest["journal_updates"] = updates
        objective_labels = translated_objectives.get(quest_id, {})
        for objective in quest["objectives"]:
            objective_id = objective["id"]
            handle = objectives.get((quest_id, objective_id))
            text = selected.get(handle) or objective_labels.get(objective_id) or fallback.get(handle)
            if text:
                objective["title"] = text
    return bool(selected or fallback or labels)


def read_save(save_path: Path, install_path: Path | None = None, *, language: str = "en") -> dict:
    """Read one local save slot. Locked quests are excluded from the output."""
    try:
        resources = _package_resources(save_path, {"globals.lsf", "saveinfo.json"})
        metadata = json.loads(resources["saveinfo.json"])
        if not isinstance(metadata, dict):
            raise ValueError("Invalid BG3 save metadata.")
        quests, unlocked_steps = _journal(resources["globals.lsf"])
    except (IndexError, KeyError, OSError, RuntimeError, struct.error, zlib.error, zstandard.ZstdError) as exc:
        raise ValueError(f"Cannot read Baldur's Gate 3 save: {exc}") from exc

    notes = ["Journal-disabled status does not establish successful completion."]
    if quests and install_path:
        try:
            if not _attach_journal_text(quests, unlocked_steps, install_path, language):
                notes.append("Local journal text could not be resolved.")
        except (IndexError, KeyError, OSError, RuntimeError, ValueError, struct.error,
                ElementTree.ParseError, zlib.error, zstandard.ZstdError):
            notes.append("Local journal text could not be resolved.")
    elif quests:
        notes.append("An installed game folder is needed to resolve journal text.")
    for quest in quests:
        if quest.get("journal_visible") is False:
            continue
        recap = bg3_story(quest["id"], unlocked_steps.get(quest["id"], []), language)
        if recap:
            quest["story_recap"] = recap

    facts = {
        "Game version": metadata.get("Game Version"),
        "Level ID": metadata.get("Current Level"),
        "Difficulty": metadata.get("Difficulty"),
        "Unlocked, enabled quests": sum(quest["state"] == "unlocked" for quest in quests),
        "Journal-disabled quests": sum(quest["state"] == "disabled" for quest in quests),
    }
    return {
        "facts": {label: value for label, value in facts.items() if value is not None},
        "quests": quests,
        "note": " ".join(translate(language, note) for note in notes),
    }
