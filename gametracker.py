"""Discover local Windows saves and report progress without editing game files."""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    steam_id: str
    executable: Path
    windows_folder_id: int
    fallback_folder: Path
    save_subdirectory: Path
    filename: re.Pattern[str]
    save_patterns: tuple[str, ...]

    def save_root(self) -> Path:
        base = known_folder(self.windows_folder_id, Path.home() / self.fallback_folder)
        return base / self.save_subdirectory


GAMES = (
    Game(
        "ff7", "Final Fantasy VII Remake Intergrade", "1462040",
        Path("ff7remake.exe"), 5, Path("Documents"),
        Path("My Games/FINAL FANTASY VII REMAKE"),
        re.compile(r"ff7remake(?:plus)?[0-9]+\.sav", re.IGNORECASE),
        ("*.sav", "*/*.sav", "*/*/*.sav"),
    ),
    Game(
        "bg3", "Baldur's Gate 3", "1086940",
        Path("bin/bg3.exe"), 28, Path("AppData/Local"),
        Path("Larian Studios/Baldur's Gate 3/PlayerProfiles"),
        re.compile(r"[^/\\]+\.lsv", re.IGNORECASE),
        ("*.lsv", "*/*.lsv", "Savegames/Story/*/*.lsv", "*/Savegames/Story/*/*.lsv"),
    ),
)


@dataclass(frozen=True)
class Installation:
    path: Path
    steam_build: str | None


@dataclass(frozen=True)
class Save:
    path: Path
    size: int
    modified_ns: int

    @property
    def modified(self) -> str:
        return datetime.fromtimestamp(self.modified_ns / 1_000_000_000).astimezone().isoformat(
            timespec="seconds"
        )


def known_folder(folder_id: int, fallback: Path) -> Path:
    if os.name == "nt":
        path = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, folder_id, None, 0, path) == 0:
            return Path(path.value)
    return fallback


def vdf_value(contents: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s+"([^"]*)"', contents, re.IGNORECASE)
    return match.group(1) if match else None


def steamapps_dir(root: Path) -> Path:
    return root if root.name.casefold() == "steamapps" else root / "steamapps"


def steam_libraries(root: Path) -> list[Path]:
    if root.name.casefold() == "common":
        return [root.parent]
    primary = steamapps_dir(root)
    library_file = primary / "libraryfolders.vdf"
    if not library_file.is_file():
        return [primary]

    contents = library_file.read_text(encoding="utf-8-sig", errors="replace")
    saved_paths = re.findall(r'"path"\s+"([^"]+)"', contents, re.IGNORECASE)
    libraries = [primary]
    for saved_path in saved_paths:
        library = Path(saved_path.replace("\\\\", "\\"))
        if library.is_absolute():
            libraries.append(library / "steamapps")
    return list(dict.fromkeys(libraries))


def steam_installation(game: Game, steamapps: Path) -> Installation | None:
    manifest = steamapps / f"appmanifest_{game.steam_id}.acf"
    if not manifest.is_file():
        return None

    contents = manifest.read_text(encoding="utf-8-sig", errors="replace")
    folder = vdf_value(contents, "installdir")
    if not folder or folder in {".", ".."} or Path(folder).name != folder:
        return None
    game_dir = steamapps / "common" / folder
    if not (game_dir / game.executable).is_file():
        return None
    return Installation(game_dir, vdf_value(contents, "buildid"))


def locate_installation(game: Game, source: Path, libraries: list[Path]) -> Installation | None:
    if (source / game.executable).is_file():
        manifest_install = steam_installation(game, source.parent.parent)
        if manifest_install and manifest_install.path == source:
            return manifest_install
        return Installation(source, None)
    for library in libraries:
        installation = steam_installation(game, library)
        if installation:
            return installation
    return None


def find_saves(game: Game, source: Path) -> list[Save]:
    if source.is_file():
        candidates = {source}
    else:
        candidates = {path for pattern in game.save_patterns for path in source.glob(pattern)}

    matches = []
    for path in candidates:
        if not game.filename.fullmatch(path.name) or not path.is_file():
            continue
        try:
            details = path.stat()
        except FileNotFoundError:
            continue
        matches.append(Save(path, details.st_size, details.st_mtime_ns))
    return sorted(matches, key=lambda save: (-save.modified_ns, str(save.path).casefold()))


def read_stable_save(
    game: Game, save: Save, installation: Installation | None, *, language: str = "en",
) -> dict:
    try:
        reader = importlib.import_module(game.key).read_save
        progress = reader(save.path, installation.path if installation else None, language=language)
    except ModuleNotFoundError as error:
        raise ValueError(f"Missing parser dependency {error.name}. Install requirements.txt.") from error

    after = save.path.stat()
    if (after.st_size, after.st_mtime_ns) != (save.size, save.modified_ns):
        raise ValueError("The game changed this save during import. Run the scan again.")
    return progress


def save_record(save: Save, slot: int) -> dict:
    return {"slot": slot, "modified": save.modified, "size": save.size, "path": str(save.path)}


def game_report(
    game: Game, saves: list[Save], installation: Installation | None, args: argparse.Namespace
) -> dict:
    report = {"game": game.name, "game_id": game.key, "save_count": len(saves)}
    if installation:
        report["install_path"] = str(installation.path)
        report["steam_build"] = installation.steam_build
    if not saves:
        return {**report, "status": "no_saves"}
    if args.list_saves:
        return {**report, "status": "listed", "saves": [
            save_record(save, slot) for slot, save in enumerate(saves, 1)
        ]}
    if args.slot and args.slot > len(saves):
        return {**report, "status": "unreadable", "errors": ["No such slot in --list-saves."]}

    selected = [(args.slot, saves[args.slot - 1])] if args.slot else enumerate(saves, 1)
    errors = []
    for slot, save in selected:
        try:
            progress = read_stable_save(game, save, installation, language=getattr(args, "language", "en"))
        except (OSError, ValueError) as error:
            errors.append(f"Slot {slot}: {error}")
            continue
        return {
            **report, "status": "read",
            "selected_save": save_record(save, slot), "progress": progress, "errors": errors,
        }

    return {**report, "status": "unreadable", "errors": errors}


def inspect(source: Path, args: argparse.Namespace) -> list[dict]:
    reports = []
    libraries = steam_libraries(source) if source.is_dir() else []
    for game in GAMES:
        if args.game and args.game != game.key:
            continue
        installation = locate_installation(game, source, libraries) if source.is_dir() else None
        saves = find_saves(game, game.save_root() if installation else source)
        if installation or saves:
            reports.append(game_report(game, saves, installation, args))
    return reports


def print_report(report: dict) -> None:
    print(f"\n{report['game']} · {report['save_count']} save(s)")
    if report["status"] == "no_saves":
        print("  Game detected. No readable local save files found in its save directory.")
        return
    for entry in report.get("saves", []):
        print(f"  [{entry['slot']}] {entry['modified']}  {Path(entry['path']).name}")
    for error in report.get("errors", []):
        print(f"  {error}")
    if report["status"] != "read":
        return

    entry = report["selected_save"]
    print(f"  Save [{entry['slot']}] · {entry['modified']}  {Path(entry['path']).name}")
    progress = report["progress"]
    for name, value in progress["facts"].items():
        print(f"  {name}: {value}")
    for quest in progress["quests"]:
        if quest.get("journal_visible") is False:
            continue
        title = quest.get("title") or quest["id"]
        print(f"  [{quest['state']}] {title}")
        for objective in quest.get("objectives", []):
            print(f"    {objective.get('title') or objective['id']}")
    if progress.get("note"):
        print(f"  {progress['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read progress from local Steam installations or save files."
    )
    parser.add_argument("path", type=Path, help="Steam library, game installation, save folder or save file")
    parser.add_argument("--game", choices=[game.key for game in GAMES], help="Filter to one supported game")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--list-saves", action="store_true", help="List available saves without parsing")
    selection.add_argument("--slot", type=int, help="Use the number shown by --list-saves")
    parser.add_argument("--json", action="store_true", help="Write machine-readable results")
    args = parser.parse_args()
    if args.slot is not None and args.slot < 1:
        parser.error("--slot must be 1 or greater")
    if args.slot and not args.game:
        parser.error("--slot requires --game")
    if not args.path.exists():
        parser.error("The supplied path does not exist.")

    try:
        reports = inspect(args.path.resolve(), args)
    except OSError as error:
        parser.exit(1, f"Could not read the supplied location: {error}\n")
    if args.json:
        print(json.dumps(reports, ensure_ascii=True, indent=2))
    elif not reports:
        print("No supported installation or save found at that location.")
    else:
        for report in reports:
            print_report(report)
    return 0 if any(report["status"] in {"read", "listed"} for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
