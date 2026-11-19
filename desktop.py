"""Windows desktop interface for the local GameTracker save reader."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import html
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import tkinter as tk
from tkinter import filedialog, ttk

from gametracker import inspect, known_folder
from i18n import translate


BACKGROUND = "#f4f5ef"
CARD = "#ffffff"
BORDER = "#dbe1d7"
TEXT = "#20322b"
MUTED = "#52665d"
ACCENT = "#20684e"
LIGHT_GREEN = "#e4efe6"
SIDEBAR = "#182b25"
SIDEBAR_TEXT = "#f0f6ed"
SIDEBAR_MUTED = "#aec4b8"
SIDEBAR_SELECTED = "#305749"

BODY = ("Segoe UI", 10)
SMALL = ("Segoe UI", 9)
SEMIBOLD = ("Segoe UI Semibold", 10)
SECTION = ("Segoe UI Semibold", 14)
HEADING = ("Segoe UI Semibold", 24)

SETTINGS_PATH = known_folder(28, Path.home() / "AppData" / "Local") / "GameTracker" / "settings.json"


def clean_text(text: str) -> str:
    """Display plain localized text from the installed game's journal."""
    text = re.sub(r"<br\s*/?>", "\n", html.unescape(text), flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).strip()


def save_label(save: dict, language: str = "en") -> str:
    calendar = "%Y年%m月%d日 %H:%M" if language == "zh" else "%d %b %Y, %H:%M"
    date = datetime.fromisoformat(save["modified"]).strftime(calendar)
    return f"{date}  ·  {Path(save['path']).stem}"


def load_settings() -> dict:
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def last_folder() -> Path | None:
    value = load_settings().get("folder")
    if isinstance(value, str) and value:
        try:
            folder = Path(value)
            if folder.is_dir():
                return folder
        except (OSError, ValueError):
            pass
    return None


class TrackerWindow(tk.Tk):
    def __init__(self, folder: Path | None = None):
        super().__init__()
        self.title("GameTracker")
        self.language = "zh" if load_settings().get("language") == "zh" else "en"
        self.expanded = {}
        self.status_message = ("", {})
        self.placeholder = None
        self.geometry("1160x790")
        self.minsize(860, 600)
        self.configure(bg=BACKGROUND)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.worker = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.library = None
        self.games = {}
        self.selected_game = None
        self.report = None
        self.page = "progress"
        self.wrap_labels = []

        self.folder_text = tk.StringVar(value=self.t("Choose your game library to get started."))
        self.title_text = tk.StringVar(value=self.t("Your game progress"))
        self.status_text = tk.StringVar(value="")

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.build_sidebar()
        self.build_main()
        self.bind_all("<MouseWheel>", self.scroll)

        folder = folder or last_folder()
        if folder:
            self.after_idle(lambda: self.scan(folder))
        else:
            self.show_message("Choose a game folder", "Select a Steam library or an individual game folder.")

    def t(self, message, **values):
        return translate(self.language, message, **values)

    def set_status(self, message, **values):
        self.status_message = (message, values)
        self.status_text.set(self.t(message, **values))

    def remember(self, *, folder=None):
        settings = load_settings()
        settings["language"] = self.language
        if folder is not None:
            settings["folder"] = str(folder)
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=SETTINGS_PATH.parent) as temporary_dir:
                temporary = Path(temporary_dir) / SETTINGS_PATH.name
                temporary.write_text(json.dumps(settings), encoding="utf-8")
                temporary.replace(SETTINGS_PATH)
        except OSError:
            return False
        return True

    def change_language(self):
        self.language = "zh" if self.language == "en" else "en"
        remembered = self.remember()
        self.language_button.configure(text="English" if self.language == "zh" else "中文")
        self.choose_button.configure(text=self.t("Choose game folder…"))
        self.detected_label.configure(text=self.t("Detected games"))
        self.save_field_label.configure(text=self.t("Save"))
        self.refresh_button.configure(text=self.t("Refresh"))
        self.paint_tabs()
        self.paint_games()

        game = self.games.get(self.selected_game)
        self.title_text.set(self.t(game["game"] if game else "Your game progress"))
        if self.library is None:
            self.folder_text.set(self.t("Choose your game library to get started."))
        message, values = self.status_message
        self.set_status(message, **values)

        selected = self.saves.current()
        if game and selected >= 0:
            self.saves.configure(values=[save_label(save, self.language) for save in game["saves"]])
            self.saves.current(selected)
            self.change_save(preserve_view=True)
        elif self.placeholder:
            self.show_message(*self.placeholder)
        if not remembered:
            self.set_status("Language preference could not be saved.")

    def label(self, parent, text="", *, background=BACKGROUND, foreground=TEXT, font=BODY, variable=None):
        return tk.Label(
            parent, text=self.t(text), textvariable=variable, anchor="w", justify="left",
            bg=background, fg=foreground, font=font, bd=0,
        )

    def wrapping_label(self, parent, text, *, background=BACKGROUND, foreground=TEXT, font=BODY, padding=40):
        label = self.label(parent, text, background=background, foreground=foreground, font=font)
        label.configure(wraplength=max(250, self.canvas.winfo_width() - padding))
        self.wrap_labels.append((label, padding))
        return label

    def build_sidebar(self):
        sidebar = tk.Frame(self, bg=SIDEBAR, width=260)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(4, weight=1)

        self.label(sidebar, "GameTracker", background=SIDEBAR, foreground=SIDEBAR_TEXT,
                   font=("Segoe UI Semibold", 19)).grid(row=0, column=0, sticky="ew", padx=23, pady=(23, 18))
        self.choose_button = ttk.Button(
            sidebar, text=self.t("Choose game folder…"), command=self.choose_folder,
        )
        self.choose_button.grid(row=1, column=0, padx=23, sticky="ew", ipady=4)
        folder = self.label(sidebar, background=SIDEBAR, foreground=SIDEBAR_MUTED,
                            font=SMALL, variable=self.folder_text)
        folder.configure(wraplength=210)
        folder.grid(row=2, column=0, sticky="ew", padx=23, pady=(11, 19))
        self.detected_label = self.label(
            sidebar, "Detected games", background=SIDEBAR, foreground=SIDEBAR_MUTED, font=SMALL,
        )
        self.detected_label.grid(row=3, column=0, sticky="ew", padx=23, pady=(3, 8))
        self.game_list = tk.Frame(sidebar, bg=SIDEBAR)
        self.game_list.grid(row=4, column=0, sticky="nsew", padx=13)

    def build_main(self):
        main = tk.Frame(self, bg=BACKGROUND)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        header = tk.Frame(main, bg=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 12))
        header.columnconfigure(0, weight=1)
        header_title = self.label(header, font=HEADING, variable=self.title_text)
        header_title.grid(row=0, column=0, sticky="w")
        self.refresh_button = ttk.Button(header, text=self.t("Refresh"), command=self.refresh, state="disabled")
        self.refresh_button.grid(row=0, column=1, sticky="e", padx=(10, 0), ipady=3)
        self.language_button = ttk.Button(
            header, text="English" if self.language == "zh" else "中文",
            command=self.change_language, width=8,
        )
        self.language_button.grid(row=0, column=2, sticky="e", padx=(8, 0), ipady=3)
        header.bind("<Configure>", lambda event: header_title.configure(wraplength=max(270, event.width - 170)))

        save_bar = tk.Frame(main, bg=BACKGROUND)
        save_bar.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 12))
        save_bar.columnconfigure(1, weight=1)
        self.save_field_label = self.label(save_bar, "Save", font=SEMIBOLD)
        self.save_field_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.saves = ttk.Combobox(save_bar, state="disabled", font=BODY, height=12)
        self.saves.grid(row=0, column=1, sticky="ew", ipady=3)
        self.saves.bind("<<ComboboxSelected>>", self.change_save)

        tabs = tk.Frame(main, bg=BACKGROUND)
        tabs.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 10))
        self.tabs = {}
        for page, title in (("progress", "Progress"), ("story", "Story so far")):
            button = tk.Button(
                tabs, text=self.t(title), command=lambda name=page: self.change_page(name),
                bd=0, font=SEMIBOLD, padx=13, pady=7, relief="flat", cursor="hand2",
                activebackground=LIGHT_GREEN, activeforeground=ACCENT,
            )
            button.pack(side="left", padx=(0, 4))
            self.tabs[page] = button

        body = tk.Frame(main, bg=BACKGROUND)
        body.grid(row=3, column=0, sticky="nsew", padx=(28, 13))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=BACKGROUND, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        self.content = tk.Frame(self.canvas, bg=BACKGROUND)
        self.inner = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.content.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.resize_body)

        self.label(main, variable=self.status_text, foreground=MUTED, font=SMALL).grid(
            row=4, column=0, sticky="ew", padx=28, pady=(8, 12)
        )
        self.paint_tabs()

    def choose_folder(self):
        current = str(self.library) if self.library else str(Path.home())
        directory = filedialog.askdirectory(parent=self, initialdir=current, title=self.t("Choose a game library or installation"))
        if directory:
            self.scan(Path(directory), remember=True)

    def refresh(self):
        if self.library:
            current = self.games.get(self.selected_game, {}).get("saves", [])
            selected_index = self.saves.current()
            previous_path = current[selected_index]["path"] if 0 <= selected_index < len(current) else None
            self.scan(self.library, preserve_path=previous_path)

    def scan(self, folder: Path, *, remember=False, preserve_path=None):
        self.library = folder.resolve()
        previous_game = self.selected_game
        self.selected_game = None
        self.games = {}
        self.paint_games()
        self.folder_text.set(str(self.library))
        self.set_status("Finding games and save files…")
        self.report = None
        self.saves.configure(state="disabled", values=())
        self.saves.set("")
        self.refresh_button.configure(state="disabled")
        self.show_message("Finding local saves", "Reading the selected folder and the games’ Windows save locations.")

        args = Namespace(game=None, list_saves=True, slot=None)

        def complete(reports):
            self.games = {game["game_id"]: game for game in reports}
            self.refresh_button.configure(state="normal")
            self.selected_game = None
            if not self.games:
                self.set_status("No supported game or save folder found.")
                self.title_text.set(self.t("Your game progress"))
                self.paint_games()
                self.show_message("No supported games found", "Choose a library or installation for Final Fantasy VII Remake Intergrade or Baldur’s Gate 3.")
                return
            if remember and not self.remember(folder=self.library):
                self.set_status("This folder could not be remembered.")
            selected = previous_game if previous_game in self.games else next(iter(self.games))
            self.select_game(selected, preserve_path)

        self.run_background(inspect, (self.library, args), complete)

    def paint_games(self):
        for item in self.game_list.winfo_children():
            item.destroy()
        for key, game in self.games.items():
            selected = key == self.selected_game
            button = tk.Button(
                self.game_list, text=self.t(game["game"]), anchor="w", justify="left", wraplength=208,
                command=lambda game_id=key: self.select_game(game_id),
                bg=SIDEBAR_SELECTED if selected else SIDEBAR, fg=SIDEBAR_TEXT,
                activebackground=SIDEBAR_SELECTED, activeforeground=SIDEBAR_TEXT,
                bd=0, font=SEMIBOLD, padx=10, pady=12, relief="flat", cursor="hand2",
                disabledforeground=SIDEBAR_MUTED,
            )
            button.pack(fill="x", pady=(0, 2))

    def select_game(self, key, preserve_path=None):
        self.selected_game = key
        self.paint_games()
        self.report = None
        self.page = "progress"
        self.paint_tabs()
        game = self.games[key]
        self.title_text.set(self.t(game["game"]))
        available = game.get("saves", [])
        if not available:
            self.cancel_pending()
            self.saves.configure(state="disabled", values=())
            self.saves.set("")
            self.set_status("Game detected")
            self.show_message("No local saves found", "When the game has a local save, use Refresh to find it.")
            return
        self.saves.configure(state="readonly", values=[save_label(save, self.language) for save in available])
        index = next((index for index, save in enumerate(available) if save["path"] == preserve_path), 0)
        self.saves.current(index)
        self.change_save()

    def change_save(self, _event=None, *, preserve_view=False):
        index = self.saves.current()
        if index < 0 or self.selected_game is None:
            return
        game = self.games[self.selected_game]
        save = game["saves"][index]
        scroll_position = self.canvas.yview()[0] if preserve_view else 0
        if not preserve_view:
            self.expanded.clear()
        self.report = None
        self.set_status("Reading selected save…")
        self.show_message("Reading save", save_label(save, self.language))
        args = Namespace(game=self.selected_game, list_saves=False, slot=save["slot"], language=self.language)

        def complete(reports):
            if not reports or reports[0]["status"] != "read":
                errors = reports[0].get("errors", []) if reports else []
                self.set_status("Save unavailable")
                self.show_message("Could not read this save", "\n".join(errors) or "Refresh the list and choose a save.")
                return
            report = reports[0]
            if report["selected_save"]["path"] != save["path"]:
                self.set_status("The list of saves changed")
                self.show_message("Save list changed", "Refresh the list before choosing another save.")
                return
            self.report = report
            self.set_status("Local save · {count} available", count=len(game["saves"]))
            self.render()
            if scroll_position:
                self.after_idle(lambda: self.canvas.yview_moveto(scroll_position) if self.report is report else None)

        self.run_background(inspect, (self.library, args), complete)

    def cancel_pending(self):
        if self.pending:
            self.pending.cancel()
            self.pending = None

    def run_background(self, action, arguments, callback):
        self.cancel_pending()
        future = self.worker.submit(action, *arguments)
        self.pending = future

        def poll():
            if self.pending is not future:
                return
            if not future.done():
                self.after(45, poll)
                return
            self.pending = None
            try:
                result = future.result()
            except (OSError, ValueError, RuntimeError) as error:
                self.refresh_button.configure(state="normal")
                self.set_status("Could not read local game data")
                self.show_message("Read failed", str(error))
            else:
                callback(result)

        self.after(45, poll)

    def change_page(self, page):
        self.page = page
        self.paint_tabs()
        if self.report:
            self.render()

    def paint_tabs(self):
        for page, button in self.tabs.items():
            active = page == self.page
            button.configure(
                text=self.t("Progress" if page == "progress" else "Story so far"),
                bg=LIGHT_GREEN if active else BACKGROUND, fg=ACCENT if active else MUTED,
            )

    def clear_body(self):
        for item in self.content.winfo_children():
            item.destroy()
        self.wrap_labels.clear()
        self.canvas.yview_moveto(0)

    def card(self):
        card = tk.Frame(self.content, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 9))
        return card

    def show_message(self, heading, text):
        self.placeholder = (heading, text)
        self.clear_body()
        card = self.card()
        self.wrapping_label(card, heading, background=CARD, font=SECTION).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        self.wrapping_label(card, text, background=CARD, foreground=MUTED).pack(
            anchor="w", padx=16, pady=(0, 14)
        )

    def resize_body(self, event):
        self.canvas.itemconfigure(self.inner, width=event.width)
        for label, padding in self.wrap_labels:
            label.configure(wraplength=max(250, event.width - padding))

    def scroll(self, event):
        path = str(self.canvas)
        if str(event.widget).startswith(path):
            self.canvas.yview_scroll(-int(event.delta / 120) * 3, "units")

    def heading(self, text):
        self.label(self.content, text, font=SECTION).pack(anchor="w", pady=(5, 9))

    def paragraph(self, text):
        self.wrapping_label(self.content, text, foreground=MUTED, padding=14).pack(anchor="w", pady=(0, 11))

    def render(self):
        self.placeholder = None
        self.clear_body()
        if self.page == "progress":
            self.render_progress(self.report["progress"])
        else:
            self.render_story(self.report["progress"])

    def render_progress(self, progress):
        facts = progress["facts"]
        journal = progress["quests"]
        quests = [quest for quest in journal if quest.get("journal_visible") is True]
        available = [quest for quest in quests if quest["state"] == "unlocked"]
        inactive = [quest for quest in quests if quest["state"] == "disabled"]

        if "Chapter" in facts:
            card = self.card()
            self.label(card, facts["Story"], background=CARD, foreground=ACCENT, font=SEMIBOLD).pack(
                anchor="w", padx=16, pady=(13, 2)
            )
            chapter = self.t("Chapter {chapter}", chapter=facts["Chapter"])
            if facts.get("Chapter title"):
                chapter += f" · {facts['Chapter title']}"
            self.wrapping_label(card, chapter, background=CARD, font=HEADING).pack(anchor="w", padx=16)
            self.label(card, self.t("Play time  {time}", time=facts["Play time"]), background=CARD, foreground=MUTED).pack(
                anchor="w", padx=16, pady=(5, 14)
            )
        else:
            if journal and not any("journal_visible" in quest for quest in journal):
                self.heading("Journal details unavailable")
                self.paragraph("Choose the game installation or Steam library to resolve visible quests.")
                return
            summary = tk.Frame(self.card(), bg=CARD)
            summary.pack(fill="x", padx=15, pady=11)
            self.label(summary, str(len(available)), font=HEADING, background=CARD, foreground=ACCENT).pack(
                side="left", padx=(0, 10)
            )
            self.label(summary, "Available quests", font=SEMIBOLD, background=CARD).pack(side="left")
            self.label(summary, self.t("{count} inactive", count=len(inactive)), foreground=MUTED, background=CARD).pack(side="right")
            self.heading("Current objectives")
            if not available:
                self.paragraph("This save has no available quests in its journal.")
            for quest in available:
                card = self.card()
                self.wrapping_label(card, clean_text(quest.get("title") or quest["id"]),
                                    background=CARD, font=SEMIBOLD).pack(anchor="w", padx=14, pady=(10, 3))
                objectives = [clean_text(obj["title"]) for obj in quest.get("objectives", []) if obj.get("title")]
                objective_text = "\n".join(objectives) if objectives else "Objective text unavailable"
                self.wrapping_label(card, objective_text, background=CARD, foreground=MUTED).pack(
                    anchor="w", padx=14, pady=(0, 10)
                )
        if progress.get("note"):
            self.paragraph(progress["note"])

    def render_story(self, progress):
        if "Chapter" in progress["facts"]:
            self.render_chapter_recap(progress)
            return
        self.heading("Quest recaps")
        journal = [
            (
                quest,
                [update for update in quest.get("journal_updates", []) if update.get("text")],
            )
            for quest in progress["quests"]
        ]
        journal = [(quest, steps) for quest, steps in journal if quest.get("story_recap") or steps]
        if not journal:
            self.paragraph("There are no readable story updates in this save’s journal.")
            return
        journal.sort(key=lambda item: (not item[0].get("story_recap"), item[0]["state"] != "unlocked"))
        for index, (quest, steps) in enumerate(journal):
            self.add_quest_recap(quest, steps, expanded=index == 0 and bool(quest.get("story_recap")))

    def render_chapter_recap(self, progress):
        chapter = progress["facts"]["Chapter"]
        self.heading(self.t("Before chapter {chapter}", chapter=chapter))
        recap = progress.get("chapter_recap", [])
        if not recap:
            self.paragraph("No earlier chapters are available for this save’s story.")
        for entry in recap:
            card = self.card()
            header = tk.Frame(card, bg=CARD)
            header.pack(fill="x", padx=16, pady=(14, 5))
            chapter_label = self.t("Chapter {chapter}", chapter=entry["chapter"])
            title = f"{chapter_label}  ·  {entry['title']}"
            self.wrapping_label(header, title, background=CARD, font=SECTION, padding=200).pack(side="left")
            self.wrapping_label(card, entry["text"], background=CARD, padding=56).pack(
                anchor="w", padx=16, pady=(0, 12)
            )
            stake = tk.Frame(card, bg=LIGHT_GREEN)
            stake.pack(fill="x", padx=16, pady=(0, 14))
            self.label(stake, "Why it matters", background=LIGHT_GREEN, foreground=ACCENT, font=SEMIBOLD).pack(
                anchor="w", padx=10, pady=(7, 2)
            )
            self.wrapping_label(stake, entry["why_it_matters"], background=LIGHT_GREEN, padding=82).pack(
                anchor="w", padx=10, pady=(0, 8)
            )
            details = tk.Frame(card, bg=CARD)
            self.add_beats(details, entry["beats"])
            self.attach_toggle(header, details, "Key events", "Hide events",
                               key=("chapter", entry["chapter"]), padx=16)

    def add_beats(self, container, beats):
        for beat in beats:
            row = tk.Frame(container, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=(0, 7))
            self.wrapping_label(row, beat["title"], background=CARD, foreground=ACCENT,
                                font=SEMIBOLD, padding=86).pack(anchor="w", padx=10, pady=(7, 2))
            self.wrapping_label(row, beat["text"], background=CARD, padding=88).pack(
                anchor="w", padx=10, pady=(0, 8)
            )

    def attach_toggle(self, header, content, show_label, hide_label, *, key, padx=0, expanded=False):
        expanded = self.expanded.get(key, expanded)
        show_label, hide_label = self.t(show_label), self.t(hide_label)
        button = ttk.Button(header, text=hide_label if expanded else show_label)
        button.pack(side="right")

        def toggle():
            if content.winfo_manager():
                content.pack_forget()
                self.expanded[key] = False
                button.configure(text=show_label)
            else:
                content.pack(fill="x", padx=padx, pady=(0, 10))
                self.expanded[key] = True
                button.configure(text=hide_label)

        if expanded:
            content.pack(fill="x", padx=padx, pady=(0, 10))
        button.configure(command=toggle)

    def add_quest_recap(self, quest, steps, *, expanded):
        card = self.card()
        header = tk.Frame(card, bg=CARD)
        header.pack(fill="x", padx=14, pady=10)
        title = clean_text(quest.get("title") or quest["id"])
        self.wrapping_label(header, title, background=CARD, font=SEMIBOLD, padding=185).pack(side="left")
        details = tk.Frame(card, bg=CARD)
        beats = quest.get("story_recap", [])
        if beats:
            self.add_beats(details, beats)
            objectives = [clean_text(obj["title"]) for obj in quest.get("objectives", []) if obj.get("title")]
            if objectives:
                self.label(details, "Current objective", background=CARD, font=SEMIBOLD).pack(anchor="w")
                self.wrapping_label(details, "\n".join(objectives), background=CARD,
                                    foreground=MUTED, padding=80).pack(anchor="w", pady=(2, 10))
        if steps:
            log = tk.Frame(details, bg=CARD)
            english_fallback = self.language == "zh" and any(step.get("language", "en") == "en" for step in steps)
            if english_fallback:
                self.wrapping_label(
                    log, "Some journal entries use the installed English text.", background=CARD, foreground=MUTED,
                ).pack(anchor="w", pady=(0, 8))
            for index, step in enumerate(steps):
                row = tk.Frame(log, bg=CARD)
                row.pack(fill="x")
                marker = "●" if index == len(steps) - 1 else "○"
                self.label(row, marker, background=CARD, foreground=ACCENT).pack(side="left", anchor="n", padx=(0, 10))
                self.wrapping_label(row, clean_text(step["text"]), background=CARD, padding=96).pack(
                    side="left", anchor="w", pady=(0, 8)
                )
            if beats:
                log_header = tk.Frame(details, bg=CARD)
                log_header.pack(fill="x", pady=(2, 7))
                label = "Saved journal · English ({count})" if english_fallback else "Saved journal ({count})"
                self.attach_toggle(log_header, log, self.t(label, count=len(steps)), "Hide journal",
                                   key=("journal", quest["id"]))
            else:
                log.pack(fill="x")
        label = "Read recap" if beats else "Read journal"
        self.attach_toggle(header, details, label, "Close",
                           key=("quest", quest["id"]), padx=14, expanded=expanded)

    def close(self):
        self.cancel_pending()
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    parser = ArgumentParser(description="Explore the story progress recorded in local PC saves.")
    parser.add_argument("folder", type=Path, nargs="?", help="Optional Steam library, installation or save folder")
    window = TrackerWindow(parser.parse_args().folder)
    window.mainloop()


if __name__ == "__main__":
    main()
