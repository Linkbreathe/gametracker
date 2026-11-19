# GameTracker

GameTracker finds saves from a local Steam library or game installation. Its Windows desktop app displays the selected save's progress and unlocks detailed story recaps.

## Run on Windows

Python 3.11 or later, Tkinter and the packages in requirements.txt are required.

~~~powershell
python -m pip install -r requirements.txt
python desktop.py
~~~

Choose a Steam library or a game installation. The app remembers the folder. Select a game and save, then open **Progress** for saved objectives or **Story so far** for the recap. **Refresh** finds new or updated saves. You can also pass a folder directly.

~~~powershell
python desktop.py "E:\SteamLibrary"
~~~

## Language

Select **中文** or **English** in the upper-right corner. The desktop app remembers your language and keeps the selected game, save, and page when switching. Both languages are bundled for the interface and the written recaps.

Baldur's Gate 3 uses the matching installed language data where available. Bundled Chinese labels cover the observed early-game quest titles and objectives. Saved journal lines with no Chinese game text use the installed English original. The app identifies that fallback in the recap.

## How the offline recaps work

- **Final Fantasy VII Remake Intergrade, Steam PC.** The bundled main-story pack covers all 18 chapters with original overviews, expandable key events and explanations of what changes for the characters. The current chapter comes from the selected save. Full plot summaries are shown for chapters before that checkpoint. The exact scene within the current chapter is still pending. The [Square Enix chapter retrospectives](https://www.square-enix-games.com/en_GB/news/final-fantasy-vii-remake-revisited-chapter-1) provide the publisher's story context. See each bundled chapter's source field for its reference. The chapter field is based on the [Kingdom Save Editor format reference](https://github.com/Xeeynamo/KingdomSaveEditor/tree/master/KHSave.LibFf7Remake/Chunks).
- **Baldur's Gate 3, Steam PC.** The bundled pack contains detailed context and events for nine questlines in the opening part of the game. Each passage requires the corresponding step or combination of steps to be present in the selected save. The rest of the game uses the player-visible journal recorded by that save. Current objectives and original saved journal entries use the installed English game data. Inactive status alone does not establish success. See the [Larian journal design guidelines](https://docs.baldursgate3.game/index.php?title=Journal_Design_Guidelines#Designing_Quest_Steps), [BG3 save format reference](https://github.com/DanFinchUK/bg3-savefile-parser/blob/master/FORMAT.md) and [LSLib](https://github.com/Norbyte/lslib).

GameTracker reads its story packs and game data locally. It opens game saves and packages for reading and sends no save data over the network.

## Command line

~~~powershell
python gametracker.py "E:\SteamLibrary"
python gametracker.py "E:\SteamLibrary" --game bg3 --list-saves
python gametracker.py "E:\SteamLibrary" --game bg3 --slot 2 --json
~~~

The CLI also accepts a save folder or an individual save file. Default CLI import tries the newest save first. A numbered slot selects that exact entry in the save list. Slots are sorted by file timestamp, so list them again after adding saves.

## Local validation

The shared Steam library found both installations and read two FFVII Remake saves and 27 BG3 saves. Recap checks use saved chapters and journal steps, including earlier slots with fewer entries. Sampled save files had matching SHA-256 hashes before and after reading. A side-by-side comparison with the live in-game journal display remains pending.
