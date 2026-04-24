#!/usr/bin/env python3
"""
Interview Transcriber — UI built with CustomTkinter.
recorder.py and transcriber.py are untouched.
"""

import datetime
import json
import multiprocessing
import plistlib
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import customtkinter as ctk
from pathlib import Path
from tkinter import messagebox, simpledialog

from recorder import AudioRecorder
from transcriber import COMPUTE_TYPE, LANGUAGE, MODEL_OPTIONS, Transcriber

ctk.set_appearance_mode("dark")

# ── Semantic palette ──────────────────────────────────────────────────────────
# Quiet desktop utility palette: matte dark root, restrained outlined surfaces,
# and sparse accent color that appears mainly for state.

# Surfaces
APP_BG    = "#0b0c0d"   # window matte background
CHROME    = "#121416"   # framed utility surfaces
CARD_BG   = "#14171a"   # transcript panel shell
EDITOR_BG = "#0f1113"   # calm writing surface
BORDER    = "#2a2e33"   # outline / divider
BORDER_HI = "#383d43"   # stronger outline for key affordances
RULE      = "#1b1e22"   # subtle inner separators

# Interactive surfaces (buttons inside CHROME)
BTN_FACE  = "#171a1d"   # secondary button bg
BTN_HOV   = "#1f2327"   # button hover
SEL_FACE  = "#262a30"   # segmented button selected cell
PRI_FACE  = "#cdc7bc"   # neutral primary action
PRI_HOV   = "#dbd4ca"
PRI_TEXT  = "#101113"

# Text hierarchy — four semantic roles, not a ramp
T_PRI     = "#ece8e0"   # primary  — transcript body, must read at a glance
T_SEC     = "#beb8ae"   # secondary — headings, button text
T_TER     = "#8a8f96"   # tertiary  — timestamps
T_META    = "#9c978f"   # metadata  — footer path, utility notes
T_GHOST   = "#4b4f55"   # placeholder text only

# Semantic / status
GN = "#7aae87"    # recording active
RD = "#c97067"    # stop / destructive action
AM = "#c4a16a"    # processing / transitional
CY = "#81a8a1"    # saved / complete

# ── Paths / config ────────────────────────────────────────────────────────────
APP_NAME = "Interview Transcriber"

# Device name substrings that identify virtual loopback / system audio devices.
# BlackHole is the primary target; Soundflower and Loopback are legacy fallbacks.
_SYSTEM_AUDIO_KEYWORDS = ("blackhole", "soundflower", "loopback")


def _bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def _user_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "InterviewTranscriber"


# Source runs stay project-local; packaged app bundles write to Application Support.
BUNDLE_DIR   = _bundle_dir()
SOURCE_MODE  = not getattr(sys, "frozen", False)
DATA_DIR     = Path(__file__).resolve().parent if SOURCE_MODE else _user_data_dir()
DISPLAY_ROOT = DATA_DIR
RECORDINGS_DIR = DATA_DIR / "recordings"
USER_GLOSSARY_FILE = DATA_DIR / "glossary.txt"
BUNDLED_GLOSSARY_FILE = BUNDLE_DIR / "glossary.txt"
DEBUG_LOG_FILE = DATA_DIR / "debug.log"
SETTINGS_FILE = DATA_DIR / "settings.json"
CHUNK_OPTIONS  = ["5s", "8s", "15s", "30s"]
INPUT_OPTIONS = ["Mic", "System", "Mic + System"]

# ── Fonts ─────────────────────────────────────────────────────────────────────
def F(size: int, weight: str = "normal", family: str = "Helvetica Neue") -> ctk.CTkFont:
    return ctk.CTkFont(family=family, size=size, weight=weight)

def FM(size: int) -> ctk.CTkFont:                 # Menlo monospace
    return ctk.CTkFont(family="Menlo", size=size)


# ── Glossary ──────────────────────────────────────────────────────────────────

def _load_glossary() -> tuple[str, list[str]]:
    glossary_path = USER_GLOSSARY_FILE if USER_GLOSSARY_FILE.exists() else BUNDLED_GLOSSARY_FILE
    if not glossary_path.exists():
        return "", []
    try:
        raw = glossary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "", []
    terms = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    return ", ".join(terms), terms


def _infer_title(lines: list[str], terms: list[str] | None = None) -> str:
    if not lines:
        return "interview"
    body = " ".join(
        re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", ln) for ln in lines
    ).lower()
    if terms:
        found: list[str] = []
        for t in sorted(terms, key=len, reverse=True):
            tl = t.lower().strip()
            if len(tl) >= 3 and tl in body and tl not in found:
                found.append(tl)
            if len(found) == 2:
                break
        if found:
            slug = "-".join(re.sub(r"[^a-z0-9]+", "-", t).strip("-") for t in found)
            slug = re.sub(r"-+", "-", slug).strip("-")[:42]
            if len(slug) >= 4:
                return slug
    for line in lines[:4]:
        text = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", line).strip()
        if len(text) > 8:
            words = re.findall(r"[a-zA-Z0-9]+", text)[:5]
            slug = "-".join(w.lower() for w in words if w)[:40].rstrip("-")
            if len(slug) >= 4:
                return slug
    return "interview"


def _recordings_dir() -> Path:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def _is_recordings_path(path: Path) -> bool:
    """Return True only for real paths inside the recordings directory."""
    try:
        root = _recordings_dir().resolve()
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _path_is_available(path: Path, current: Path | None = None) -> bool:
    if current and path == current:
        return True
    return not path.exists() and not path.with_suffix(".txt").exists()


def _unique_output_path(path: Path, current: Path | None = None) -> Path:
    if _path_is_available(path, current=current):
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if _path_is_available(candidate, current=current):
            return candidate
        counter += 1


def _session_file(
    started_at: datetime.datetime,
    title: str = "interview",
    current: Path | None = None,
) -> Path:
    stamp = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    path = _recordings_dir() / f"{stamp}_{title}.md"
    return _unique_output_path(path, current=current)


def _mode_from_label(label: str) -> str:
    if label == "System":
        return "system"
    if label == "Mic + System":
        return "mic_system"
    return "mic"


def _label_from_mode(mode: str) -> str:
    return {
        "mic": "Mic",
        "system": "System",
        "mic_system": "Mic + System",
    }.get(mode, "Mic")


def _display_input_mode(mode: str) -> str:
    return {
        "mic": "Mic",
        "system": "System Audio",
        "mic_system": "Mic + System",
    }.get(mode, "Mic")


def _yaml_string(value: object) -> str:
    return json.dumps("" if value is None else str(value))


# ── Status pill ───────────────────────────────────────────────────────────────

# Pill states: (frame_bg, border_color, dot_color, label_color, label_text)
_PILL: dict[str, tuple[str, str, str, str, str]] = {
    "loading":    (CHROME,      BORDER,    T_TER,  T_META, "Loading"),
    "ready":      (CHROME,      BORDER,    T_TER,  T_META, "Ready"),
    "recording":  ("#162119",   "#26412f", GN,     GN,     "Recording"),
    "processing": ("#211c15",   "#433724", AM,     AM,     "Processing"),
    "saved":      ("#15201f",   "#2a4441", CY,     CY,     "Saved"),
    "error":      ("#241716",   "#4a2a27", RD,     RD,     "Error"),
}


class StatusPill:
    def __init__(self, parent: ctk.CTkBaseClass) -> None:
        self._frame = ctk.CTkFrame(
            parent,
            corner_radius=15,
            fg_color=CHROME,
            border_width=1,
            border_color=BORDER,
            height=30,
            width=136,
        )
        self._frame.pack(side="right", padx=(0, 12))
        self._frame.pack_propagate(False)

        row = ctk.CTkFrame(self._frame, fg_color="transparent")
        row.place(relx=0.5, rely=0.5, anchor="center")

        self._dot = ctk.CTkLabel(row, text="●", font=F(7), text_color=T_TER, width=8)
        self._dot.pack(side="left")
        self._lbl = ctk.CTkLabel(
            row, text="Loading…", font=F(11), text_color=T_META
        )
        self._lbl.pack(side="left", padx=(5, 0))

    def set(self, state: str, detail: str = "") -> None:
        bg, border, dot_fg, lbl_fg, txt = _PILL.get(state, _PILL["ready"])
        display = f"{txt}  {detail}" if detail else txt
        self._frame.configure(fg_color=bg, border_color=border)
        self._dot.configure(text_color=dot_fg)
        self._lbl.configure(text_color=lbl_fg, text=display)


# ── App ───────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=APP_BG)
        self.title("Interview Transcriber")
        self.geometry("900x720")
        self.minsize(720, 560)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._debug_log_path = DEBUG_LOG_FILE
        self._settings = _load_settings()
        self.chunk_queue: queue.Queue = queue.Queue()
        self.recorder = AudioRecorder(self.chunk_queue, debug_log=self._log_debug)
        self.transcriber = Transcriber()
        preferred_model = self._settings.get("model")
        if preferred_model in MODEL_OPTIONS:
            self.transcriber.set_model(preferred_model)

        self.is_recording = False
        self._model_loading = False
        self._input_mode: str = "mic"   # "mic" | "system" | "mic_system"
        self.transcript_lines: list[str] = []
        self.session_start: datetime.datetime | None = None
        self._session_path: Path | None = None
        self._audio_paths: dict[str, Path] = {}
        self._session_meta_path: Path | None = None
        self._source_devices: dict[str, dict] = {}
        self._chunk_count = 0
        self._selected_history_path: Path | None = None
        self._history_buttons: list[ctk.CTkButton] = []
        self._history_collapsed = False
        self._glossary_prompt, self._glossary_terms = _load_glossary()
        self._log_debug(
            f"[app] launch source_mode={SOURCE_MODE} executable={sys.executable}"
        )
        self._log_debug(f"[app] selected_model={self.transcriber.model_name}")
        self._log_debug(
            f"[app] bundle_dir={BUNDLE_DIR} data_dir={DATA_DIR} recordings_dir={RECORDINGS_DIR}"
        )
        runtime_info = self._bundle_runtime_info()
        if runtime_info:
            self._log_debug(
                "[app] bundle_info "
                f"identifier={runtime_info.get('identifier')} "
                f"name={runtime_info.get('name')} "
                f"plist={runtime_info.get('plist_path')}"
            )

        self._build()
        self._load_model_async()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self._build_header()
        self._build_footer()      # BOTTOM first
        self._build_ctrl_bar()    # BOTTOM second (sits above footer)
        self._build_workspace()   # fills remaining space

    def _has_saved_file(self) -> bool:
        return bool(self._session_path and self._session_path.exists())

    def _bundle_runtime_info(self) -> dict | None:
        if not getattr(sys, "frozen", False):
            return None
        plist_path = Path(sys.executable).resolve().parents[1] / "Info.plist"
        if not plist_path.exists():
            return {
                "identifier": None,
                "name": None,
                "plist_path": str(plist_path),
            }
        try:
            data = plistlib.loads(plist_path.read_bytes())
        except Exception as exc:
            self._log_debug(f"[app] failed to read Info.plist: {exc}")
            return {
                "identifier": None,
                "name": None,
                "plist_path": str(plist_path),
            }
        return {
            "identifier": data.get("CFBundleIdentifier"),
            "name": data.get("CFBundleName"),
            "plist_path": str(plist_path),
        }

    def _log_debug(self, message: str) -> None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._debug_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._debug_log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {message}\n")
        except OSError:
            pass

    def _save_settings(self) -> None:
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps(self._settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            self._log_debug(f"[app] failed to save settings: {exc}")

    def _reset_recorder(self) -> None:
        self.chunk_queue = queue.Queue()
        self.recorder = AudioRecorder(self.chunk_queue, debug_log=self._log_debug)

    def _find_system_audio_device(self) -> tuple[int, str] | None:
        """Return (device_index, device_name) for the first virtual loopback device found."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
        except Exception as exc:
            self._log_debug(f"[app] device enumeration failed: {exc}")
            return None
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                if any(kw in d["name"].lower() for kw in _SYSTEM_AUDIO_KEYWORDS):
                    return i, d["name"]
        return None

    def _sidecar_paths_for(self, transcript_path: Path, mode: str) -> tuple[dict[str, Path], Path]:
        stem = transcript_path.with_suffix("")
        audio_paths: dict[str, Path] = {"mixed": stem.with_name(f"{stem.name}_mixed.wav")}
        if mode in ("mic", "mic_system"):
            audio_paths["mic"] = stem.with_name(f"{stem.name}_mic.wav")
        if mode in ("system", "mic_system"):
            audio_paths["system"] = stem.with_name(f"{stem.name}_system.wav")
        return audio_paths, stem.with_name(f"{stem.name}_session.json")

    def _preflight_recording(self) -> tuple[bool, str, int | None]:
        if self._model_loading or self.transcriber.model is None:
            return False, "The Whisper model is still loading. Try again when the status says Ready.", None
        if self._input_mode not in ("mic", "system", "mic_system"):
            return False, "The selected input mode is not supported.", None
        try:
            _recordings_dir()
        except OSError as exc:
            return False, f"Unable to create the recordings folder.\n\nDetails: {exc}", None

        system_device: int | None = None
        if self._input_mode in ("system", "mic_system"):
            result = self._find_system_audio_device()
            if result is None:
                return (
                    False,
                    "System audio requires BlackHole or an equivalent virtual audio device.\n\n"
                    "Install BlackHole, then route the interview/computer audio output to it. "
                    "Use Mic mode if you only need the microphone.",
                    None,
                )
            system_device, device_name = result
            self._log_debug(f"[app] system audio device: index={system_device} name={device_name}")
        return True, "", system_device

    def _history_files(self) -> list[Path]:
        if not RECORDINGS_DIR.exists():
            return []
        paths: list[Path] = []
        for path in RECORDINGS_DIR.glob("*.md"):
            if not path.is_file() or not _is_recordings_path(path):
                self._log_debug(f"[app] ignored unsafe history path: {path}")
                continue
            paths.append(path)
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)

    def _selected_file_for_actions(self) -> Path | None:
        if (
            self._selected_history_path
            and self._selected_history_path.exists()
            and _is_recordings_path(self._selected_history_path)
        ):
            return self._selected_history_path
        if self._has_saved_file() and _is_recordings_path(self._session_path):
            return self._session_path
        return None

    def _apply_history_visibility(self) -> None:
        if self._history_collapsed:
            self._history_shell.configure(width=64)
            self._history_title_lbl.pack_forget()
            self._history_subtitle.pack_forget()
            self._history_body.pack_forget()
            self._history_toggle_btn.configure(text="‹")
            self._history_toggle_btn.pack_forget()
            self._history_toggle_btn.pack(expand=True)
        else:
            self._history_shell.configure(width=276)
            self._history_toggle_btn.configure(text="›")
            self._history_toggle_btn.pack_forget()
            self._history_toggle_btn.pack(side="right")
            self._history_title_lbl.pack(side="left")
            self._history_subtitle.pack(anchor="w", pady=(2, 0))
            self._history_body.pack(fill="both", expand=True, padx=0, pady=0)

    def toggle_history_panel(self) -> None:
        self._history_collapsed = not self._history_collapsed
        self._apply_history_visibility()

    def _can_rename_selected(self) -> bool:
        target = self._selected_history_path
        if not (target and target.exists()):
            return False
        return not (self.is_recording and target == self._session_path)

    def _history_state(self) -> str:
        return "disabled" if self.is_recording else "normal"

    def _set_editor_content(self, content: str, tag: str | None = None) -> None:
        self._box.configure(state="normal")
        tb = self._box._textbox
        tb.delete("1.0", "end")
        if tag:
            tb.insert("end", content, tag)
        else:
            tb.insert("end", content)
        tb.see("1.0")
        self._box.configure(state="disabled")

    def _history_summary(self, path: Path) -> str:
        try:
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "Unavailable"
        segments = len(re.findall(r"^\[\d{2}:\d{2}:\d{2}\]", text, flags=re.MULTILINE))
        stamp = mtime.strftime("%b %d, %Y  %I:%M %p")
        return f"{stamp}  ·  {segments} seg{'s' if segments != 1 else ''}"

    def _history_label(self, path: Path) -> str:
        return f"{path.name}\n{self._history_summary(path)}"

    def _refresh_history(self, select: Path | None = None) -> None:
        paths = self._history_files()
        if select and select.exists():
            self._selected_history_path = select
        elif self._selected_history_path and self._selected_history_path.exists():
            pass
        else:
            self._selected_history_path = None

        for child in self._history_list.winfo_children():
            child.destroy()
        self._history_buttons = []

        if not paths:
            ctk.CTkLabel(
                self._history_list,
                text="No transcripts yet",
                font=F(11),
                text_color=T_TER,
                anchor="w",
            ).pack(fill="x", padx=2, pady=(4, 0))
        else:
            for path in paths:
                selected = path == self._selected_history_path
                button = ctk.CTkButton(
                    self._history_list,
                    text=self._history_label(path),
                    font=F(11),
                    fg_color=SEL_FACE if selected else BTN_FACE,
                    hover_color=SEL_FACE if selected else BTN_HOV,
                    text_color=T_PRI if selected else T_SEC,
                    border_width=1,
                    border_color=BORDER_HI if selected else BORDER,
                    anchor="w",
                    corner_radius=10,
                    height=56,
                    state=self._history_state(),
                    command=lambda p=path: self._select_history_file(p),
                )
                button.pack(fill="x", pady=(0, 8))
                self._history_buttons.append(button)

        self._update_file_action_states()

    def _select_history_file(self, path: Path) -> None:
        if self.is_recording or not path.exists():
            return
        if not _is_recordings_path(path):
            self._show_error_state(
                "Unsafe path",
                "The selected transcript is not inside the recordings folder.",
            )
            return
        self._selected_history_path = path
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self._show_error_state(
                "Open failed",
                "Unable to read the selected transcript.\n\n"
                f"Details: {exc}",
            )
            return
        self._set_editor_content(content)
        self._set_path()
        self._refresh_history(select=path)

    def _update_file_action_states(self) -> None:
        target = self._selected_file_for_actions()
        history_state = self._history_state()
        reveal_state = "normal" if target and history_state == "normal" else "disabled"
        rename_state = "normal" if self._can_rename_selected() and history_state == "normal" else "disabled"

        self._save_btn.configure(state=reveal_state)
        if not self._history_collapsed:
            self._open_btn.configure(state=history_state if self._selected_history_path else "disabled")
            self._reveal_btn.configure(state=history_state if self._selected_history_path else "disabled")
            self._rename_btn.configure(state=rename_state)
        for button in self._history_buttons:
            button.configure(state=history_state)

    def _sanitize_transcript_name(self, raw: str) -> str:
        stem = raw.strip()
        if stem.lower().endswith(".md"):
            stem = stem[:-3]
        stem = re.sub(r"[\\/:\*\?\"<>\|]+", "-", stem)
        stem = re.sub(r"\s+", "-", stem)
        stem = re.sub(r"-+", "-", stem).strip(" .-_")
        return stem[:80]

    def _open_path(self, path: Path, reveal: bool = False) -> None:
        if not _is_recordings_path(path):
            messagebox.showerror(
                "Interview Transcriber",
                "Refusing to open a file outside the recordings folder.",
            )
            return
        cmd = ["open", "-R", str(path)] if reveal else ["open", str(path)]
        try:
            subprocess.run(cmd, check=True)
        except Exception as exc:
            messagebox.showerror(
                "Interview Transcriber",
                "Unable to open the selected file.\n\n"
                f"Details: {exc}",
            )

    def reveal_selected_file(self) -> None:
        target = self._selected_file_for_actions()
        if not target:
            messagebox.showinfo("No transcript", "No saved transcript is currently selected.")
            return
        self._open_path(target, reveal=True)

    def open_selected_file(self) -> None:
        if not (self._selected_history_path and self._selected_history_path.exists()):
            messagebox.showinfo("No transcript", "Select a completed transcript first.")
            return
        self._open_path(self._selected_history_path)

    def rename_selected_file(self) -> None:
        target = self._selected_history_path
        if not (target and target.exists()):
            messagebox.showinfo("No transcript", "Select a completed transcript first.")
            return
        if not _is_recordings_path(target):
            messagebox.showerror(
                "Interview Transcriber",
                "Refusing to rename a file outside the recordings folder.",
            )
            return
        if self.is_recording and target == self._session_path:
            messagebox.showinfo(
                "Recording in progress",
                "Active session files cannot be renamed while recording is in progress.",
            )
            return

        proposed = simpledialog.askstring(
            "Rename Transcript",
            "New transcript name:",
            initialvalue=target.stem,
            parent=self,
        )
        if proposed is None:
            return

        sanitized = self._sanitize_transcript_name(proposed)
        if not sanitized:
            messagebox.showerror(
                "Interview Transcriber",
                "Enter a valid transcript name.",
            )
            return

        new_md = target.with_name(f"{sanitized}{target.suffix}")
        new_txt = new_md.with_suffix(".txt")
        old_txt = target.with_suffix(".txt")

        if new_md == target:
            return
        if new_md.exists() or (old_txt.exists() and new_txt.exists()):
            messagebox.showerror(
                "Interview Transcriber",
                "A transcript with that name already exists.",
            )
            return

        try:
            target.rename(new_md)
            if old_txt.exists():
                old_txt.rename(new_txt)
            self._rename_matching_sidecars(target, new_md)
        except OSError as exc:
            messagebox.showerror(
                "Interview Transcriber",
                "Unable to rename the selected transcript.\n\n"
                f"Details: {exc}",
            )
            return

        if self._session_path == target:
            self._session_path = new_md
        self._selected_history_path = new_md
        self._refresh_history(select=new_md)
        self._select_history_file(new_md)

    def _rename_matching_sidecars(self, old_md: Path, new_md: Path) -> None:
        old_stem = old_md.with_suffix("")
        new_stem = new_md.with_suffix("")
        for suffix in ("mic.wav", "system.wav", "mixed.wav", "session.json"):
            old_path = old_stem.with_name(f"{old_stem.name}_{suffix}")
            new_path = new_stem.with_name(f"{new_stem.name}_{suffix}")
            if old_path.exists() and old_path != new_path:
                old_path.rename(new_path)

    def _set_recording_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._clear_btn.configure(state=state)
        self._chunk_btn.configure(state=state)
        self._input_btn.configure(state=state)
        self._model_btn.configure(state="disabled" if (not enabled or self._model_loading) else "normal")
        self._update_file_action_states()

    def _set_idle_state(self) -> None:
        self.is_recording = False
        self._rec_btn.configure(
            state="normal",
            text="▶   Start Recording",
            fg_color=PRI_FACE,
            hover_color=PRI_HOV,
            text_color=PRI_TEXT,
        )
        self._set_recording_controls_enabled(True)
        self._update_file_action_states()

    def _show_error_state(self, detail: str, dialog: str = "") -> None:
        self._log_debug(f"[app] error state detail={detail}")
        self._reset_recorder()
        self.pill.set("error", detail[:26])
        self._set_idle_state()
        self._set_path()
        if dialog:
            messagebox.showerror("Interview Transcriber", dialog)

    def _build_header(self) -> None:
        shell = ctk.CTkFrame(self, corner_radius=0, fg_color=APP_BG, height=54)
        shell.pack(fill="x", side="top")
        shell.pack_propagate(False)

        hdr = ctk.CTkFrame(
            shell,
            corner_radius=14,
            fg_color=CHROME,
            border_width=1,
            border_color=BORDER,
            height=38,
        )
        hdr.pack(fill="x", padx=18, pady=(12, 4))
        hdr.pack_propagate(False)

        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.pack(side="left", padx=14)

        ctk.CTkLabel(
            left,
            text="Interview Transcriber",
            font=F(13),
            text_color=T_SEC,
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            left,
            text="local",
            font=F(10),
            text_color=T_TER,
            anchor="w",
        ).pack(side="left", padx=(8, 0))

        self.pill = StatusPill(hdr)

    def _build_footer(self) -> None:
        shell = ctk.CTkFrame(self, corner_radius=0, fg_color=APP_BG, height=42)
        shell.pack(fill="x", side="bottom")
        shell.pack_propagate(False)

        foot = ctk.CTkFrame(
            shell,
            corner_radius=12,
            fg_color=CHROME,
            border_width=1,
            border_color=BORDER,
            height=30,
        )
        foot.pack(fill="x", padx=18, pady=(0, 10))
        foot.pack_propagate(False)

        self._path_var = tk.StringVar(value="  recordings/")
        ctk.CTkLabel(
            foot,
            textvariable=self._path_var,
            font=FM(10),
            text_color=T_META,
            anchor="w",
        ).pack(side="left", padx=12)

    def _build_ctrl_bar(self) -> None:
        shell = ctk.CTkFrame(self, corner_radius=0, fg_color=APP_BG, height=88)
        shell.pack(fill="x", side="bottom")
        shell.pack_propagate(False)

        bar = ctk.CTkFrame(
            shell,
            corner_radius=18,
            fg_color=CHROME,
            border_width=1,
            border_color=BORDER,
            height=64,
        )
        bar.pack(fill="x", padx=18, pady=(0, 10))
        bar.pack_propagate(False)

        # Left group
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.place(relx=0, rely=0.5, anchor="w", x=14)

        self._rec_btn = ctk.CTkButton(
            left,
            text="▶   Start Recording",
            font=F(12, "bold"),
            fg_color=PRI_FACE,
            hover_color=PRI_HOV,
            text_color=PRI_TEXT,
            corner_radius=18,
            width=184,
            height=38,
            cursor="hand2",
            command=self.toggle_recording,
            state="disabled",
        )
        self._rec_btn.pack(side="left")

        self._save_btn = ctk.CTkButton(
            left,
            text="Reveal",
            font=F(12),
            fg_color=BTN_FACE,
            hover_color=BTN_HOV,
            text_color=T_SEC,
            border_width=1,
            border_color=BORDER_HI,
            corner_radius=10,
            width=78,
            height=34,
            cursor="hand2",
            command=self.reveal_selected_file,
            state="disabled",
        )
        self._save_btn.pack(side="left", padx=(10, 0))

        ctk.CTkLabel(
            left, text="Model", font=F(11), text_color=T_META
        ).pack(side="left", padx=(20, 8))

        self._model_btn = ctk.CTkSegmentedButton(
            left,
            values=list(MODEL_OPTIONS),
            font=F(11),
            fg_color=BTN_FACE,
            selected_color=SEL_FACE,
            selected_hover_color=SEL_FACE,
            unselected_color=BTN_FACE,
            unselected_hover_color=BTN_HOV,
            text_color=T_PRI,
            corner_radius=10,
            height=34,
            width=224,
            command=self._on_model_selected,
        )
        self._model_btn.set(self.transcriber.model_name)
        self._model_btn.pack(side="left")

        ctk.CTkLabel(
            left, text="Chunk", font=F(11), text_color=T_META
        ).pack(side="left", padx=(20, 8))

        self._chunk_btn = ctk.CTkSegmentedButton(
            left,
            values=CHUNK_OPTIONS,
            font=F(11),
            fg_color=BTN_FACE,
            selected_color=SEL_FACE,
            selected_hover_color=SEL_FACE,
            unselected_color=BTN_FACE,
            unselected_hover_color=BTN_HOV,
            text_color=T_PRI,
            corner_radius=10,
            height=34,
            command=lambda _: self._refresh_meta(),
        )
        self._chunk_btn.set("8s")
        self._chunk_btn.pack(side="left")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.place(relx=1.0, rely=0.5, anchor="e", x=-14)

        ctk.CTkLabel(
            right, text="Input", font=F(11), text_color=T_META
        ).pack(side="left", padx=(0, 8))

        self._input_btn = ctk.CTkSegmentedButton(
            right,
            values=INPUT_OPTIONS,
            font=F(11),
            fg_color=BTN_FACE,
            selected_color=SEL_FACE,
            selected_hover_color=SEL_FACE,
            unselected_color=BTN_FACE,
            unselected_hover_color=BTN_HOV,
            text_color=T_PRI,
            corner_radius=10,
            height=34,
            width=218,
            command=self._on_input_selected,
        )
        self._input_btn.set("Mic")
        self._input_btn.pack(side="left", padx=(0, 16))

        self._clear_btn = ctk.CTkButton(
            right,
            text="Clear",
            font=F(12),
            fg_color=BTN_FACE,
            hover_color=BTN_HOV,
            text_color=T_META,
            border_width=1,
            border_color=BORDER,
            corner_radius=10,
            width=72,
            height=34,
            cursor="hand2",
            command=self.clear_transcript,
        )
        self._clear_btn.pack(side="left")

    def _build_workspace(self) -> None:
        outer = ctk.CTkFrame(self, fg_color=APP_BG, corner_radius=0)
        outer.pack(fill="both", expand=True)

        card = ctk.CTkFrame(
            outer,
            corner_radius=20,
            fg_color=CARD_BG,
            border_width=1,
            border_color=BORDER,
        )
        card.pack(fill="both", expand=True, padx=18, pady=(6, 12))

        header = ctk.CTkFrame(card, fg_color="transparent", height=60)
        header.pack(fill="x", padx=20, pady=(16, 8))
        header.pack_propagate(False)

        header_left = ctk.CTkFrame(header, fg_color="transparent")
        header_left.pack(side="left")

        ctk.CTkLabel(
            header_left,
            text="Transcript",
            font=F(14),
            text_color=T_SEC,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            header_left,
            text="Project-local autosave with quiet live capture.",
            font=F(11),
            text_color=T_TER,
            anchor="w",
        ).pack(anchor="w", pady=(3, 0))

        self._meta_var = tk.StringVar()
        ctk.CTkLabel(
            header,
            textvariable=self._meta_var,
            font=F(11),
            text_color=T_META,
            anchor="e",
        ).pack(side="right")

        ctk.CTkFrame(
            card,
            fg_color=RULE,
            corner_radius=0,
            height=1,
        ).pack(fill="x", padx=18, pady=(0, 12))

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True)

        editor_shell = ctk.CTkFrame(
            content,
            fg_color=EDITOR_BG,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )
        editor_shell.pack(side="left", fill="both", expand=True, padx=(16, 8), pady=(0, 16))

        self._box = ctk.CTkTextbox(
            editor_shell,
            font=FM(15),
            fg_color=EDITOR_BG,
            text_color=T_PRI,
            corner_radius=18,
            border_width=0,
            scrollbar_button_color=BTN_FACE,
            scrollbar_button_hover_color=BTN_HOV,
            activate_scrollbars=True,
            wrap="word",
            state="disabled",
        )
        self._box.pack(fill="both", expand=True, padx=2, pady=2)

        # Tag-based styling on the internal tk.Text
        tb = self._box._textbox
        tb.configure(
            padx=36,
            pady=26,
            spacing3=0,
            insertbackground=T_PRI,
            selectbackground="#2b3037",
            selectforeground=T_PRI,
        )
        tb.tag_configure("ts", font=("Menlo", 11), foreground=T_TER)
        tb.tag_configure("body", font=("Menlo", 15), foreground=T_PRI, spacing3=22)
        tb.tag_configure(
            "ph",
            font=("Helvetica Neue", 13),
            foreground=T_GHOST,
            justify="center",
        )

        self._refresh_meta()
        self._show_placeholder()

        self._history_shell = ctk.CTkFrame(
            content,
            fg_color=CHROME,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
            width=276,
        )
        self._history_shell.pack(side="right", fill="y", padx=(8, 16), pady=(0, 16))
        self._history_shell.pack_propagate(False)

        history_header = ctk.CTkFrame(self._history_shell, fg_color="transparent", height=48)
        history_header.pack(fill="x", padx=14, pady=(12, 6))
        history_header.pack_propagate(False)

        title_row = ctk.CTkFrame(history_header, fg_color="transparent")
        title_row.pack(fill="x")

        self._history_title_lbl = ctk.CTkLabel(
            title_row,
            text="Recordings",
            font=F(13),
            text_color=T_SEC,
            anchor="w",
        )
        self._history_title_lbl.pack(side="left")

        self._history_toggle_btn = ctk.CTkButton(
            title_row,
            text="›",
            font=F(10),
            fg_color=BTN_FACE,
            hover_color=BTN_HOV,
            text_color=T_META,
            border_width=1,
            border_color=BORDER,
            corner_radius=8,
            width=28,
            height=24,
            command=self.toggle_history_panel,
        )
        self._history_toggle_btn.pack(side="right")

        self._history_subtitle = ctk.CTkLabel(
            history_header,
            text="Newest first",
            font=F(10),
            text_color=T_TER,
            anchor="w",
        )
        self._history_subtitle.pack(anchor="w", pady=(2, 0))

        self._history_body = ctk.CTkFrame(self._history_shell, fg_color="transparent")
        self._history_body.pack(fill="both", expand=True, padx=0, pady=0)

        action_row = ctk.CTkFrame(self._history_body, fg_color="transparent", height=36)
        action_row.pack(fill="x", padx=14, pady=(0, 8))
        action_row.pack_propagate(False)

        self._open_btn = ctk.CTkButton(
            action_row,
            text="Open",
            font=F(11),
            fg_color=BTN_FACE,
            hover_color=BTN_HOV,
            text_color=T_SEC,
            border_width=1,
            border_color=BORDER,
            corner_radius=9,
            width=72,
            height=32,
            command=self.open_selected_file,
            state="disabled",
        )
        self._open_btn.pack(side="left")

        self._reveal_btn = ctk.CTkButton(
            action_row,
            text="Reveal",
            font=F(11),
            fg_color=BTN_FACE,
            hover_color=BTN_HOV,
            text_color=T_SEC,
            border_width=1,
            border_color=BORDER,
            corner_radius=9,
            width=76,
            height=32,
            command=self.reveal_selected_file,
            state="disabled",
        )
        self._reveal_btn.pack(side="left", padx=(8, 0))

        self._rename_btn = ctk.CTkButton(
            action_row,
            text="Rename",
            font=F(11),
            fg_color=BTN_FACE,
            hover_color=BTN_HOV,
            text_color=T_SEC,
            border_width=1,
            border_color=BORDER,
            corner_radius=9,
            width=84,
            height=32,
            command=self.rename_selected_file,
            state="disabled",
        )
        self._rename_btn.pack(side="left", padx=(8, 0))

        self._history_list = ctk.CTkScrollableFrame(
            self._history_body,
            fg_color="transparent",
            corner_radius=0,
        )
        self._history_list.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self._refresh_history()
        self._apply_history_visibility()

    def _show_placeholder(self) -> None:
        self._set_editor_content(
            "\n\n\n\n\nTranscript will appear here as you record.\n\n"
            "Autosaves stay inside this project in recordings/.",
            "ph",
        )

    # ── Model loading ─────────────────────────────────────────────────────────

    def _model_warning(self, model_name: str) -> str:
        if model_name == "medium":
            return "larger download · slower local inference"
        if model_name == "large-v3":
            return "largest download · slowest local inference"
        return "practical default"

    def _on_input_selected(self, label: str) -> None:
        if self.is_recording:
            self._input_btn.set(_label_from_mode(self._input_mode))
            return
        self._input_mode = _mode_from_label(label)
        self._refresh_meta()

    def _on_model_selected(self, model_name: str) -> None:
        if self.is_recording or self._model_loading:
            self._model_btn.set(self.transcriber.model_name)
            return
        if model_name == self.transcriber.model_name:
            return
        self.transcriber.set_model(model_name)
        self._settings["model"] = model_name
        self._save_settings()
        self._log_debug(f"[app] model changed to {model_name}")
        self._meta_var.set(f"Preparing model {model_name} — {self._model_warning(model_name)}.")
        self._load_model_async()

    def _load_model_async(self) -> None:
        model_name = self.transcriber.model_name
        self._model_loading = True
        self._rec_btn.configure(state="disabled")
        self._model_btn.configure(state="disabled")
        self.pill.set("loading", f"model {model_name}")
        if model_name == "small":
            message = (
                "Preparing model small — practical default. First use may download model files; "
                "subsequent launches use the local cache."
            )
        else:
            message = (
                f"Preparing model {model_name} — first use may download larger model files, "
                "and local transcription may be slower."
            )
        self._meta_var.set(message)

        def _run():
            try:
                self.transcriber.load(
                    progress_callback=lambda msg: self.after(
                        0, self.pill.set, "loading", f"model {model_name}"
                    )
                )
                self.after(0, self._on_model_ready)
            except Exception as exc:
                self.after(0, self._on_model_load_failed, exc)

        threading.Thread(target=_run, daemon=True).start()

    def _on_model_ready(self) -> None:
        self._model_loading = False
        self.pill.set("ready")
        self._rec_btn.configure(state="normal")
        self._model_btn.set(self.transcriber.model_name)
        self._set_recording_controls_enabled(True)
        self._refresh_meta()
        self._set_path()
        self._refresh_history()

    def _on_model_load_failed(self, exc: Exception) -> None:
        self._model_loading = False
        self.pill.set("error", str(exc)[:26])
        self._rec_btn.configure(state="disabled")
        self._model_btn.set(self.transcriber.model_name)
        self._set_recording_controls_enabled(True)

    # ── Recording flow ────────────────────────────────────────────────────────

    def toggle_recording(self) -> None:
        if not self.is_recording:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        self._log_debug("[app] start requested")
        ok, message, system_device = self._preflight_recording()
        if not ok:
            self._show_error_state("Preflight failed", message)
            self._refresh_meta()
            return

        self.session_start = datetime.datetime.now()
        self.transcript_lines = []
        self._chunk_count = 0
        self._session_path = _session_file(self.session_start, "interview")
        self._audio_paths, self._session_meta_path = self._sidecar_paths_for(
            self._session_path, self._input_mode
        )
        self._source_devices = {}

        # Clear placeholder
        self._box.configure(state="normal")
        self._box._textbox.delete("1.0", "end")
        self._box.configure(state="disabled")

        self._selected_history_path = None
        chunk_seconds = int(self._chunk_btn.get().rstrip("s"))

        device_arg: int | None = system_device if self._input_mode == "system" else None
        source_name = "system" if self._input_mode == "system" else "mic"
        mix_system_device = system_device if self._input_mode == "mic_system" else None

        try:
            startup = self.recorder.start(
                chunk_seconds=chunk_seconds,
                device=device_arg,
                source_name=source_name,
                system_device=mix_system_device,
                audio_paths=self._audio_paths,
            )
        except Exception as exc:
            self._log_debug(f"[app] start failed: {exc}")
            self.is_recording = False
            self._session_path = None
            self._audio_paths = {}
            self._session_meta_path = None
            self._source_devices = {}
            self.transcript_lines = []
            self._chunk_count = 0
            try:
                self.recorder.stop()
            except Exception:
                pass
            self._show_placeholder()
            self._show_error_state(
                "Audio unavailable",
                "Unable to start recording.\n\n"
                "Check microphone permissions and audio input availability.\n\n"
                f"Details: {exc}\n\n"
                f"Debug log: {self._debug_log_path}",
            )
            self._refresh_meta()
            return
        self._log_debug(f"[app] start succeeded: {startup}")
        self._source_devices = startup.get("selected_input_devices", {}) if isinstance(startup, dict) else {}
        self.is_recording = True
        self._rec_btn.configure(
            text="■   Stop",
            fg_color=RD,
            hover_color="#b75f57",
            text_color="#120b0a",
        )
        self._save_btn.configure(state="disabled")
        self._set_recording_controls_enabled(False)
        self.pill.set("recording")
        self._set_path()
        self._refresh_meta()
        threading.Thread(target=self._consume_chunks, daemon=True).start()
        self._refresh_history()

    def _stop(self) -> None:
        self._log_debug(f"[app] stop requested recorder={self.recorder.diagnostics_snapshot()}")
        self.is_recording = False
        self.recorder.stop()
        self._rec_btn.configure(
            state="disabled",
            text="Finishing…",
            fg_color=BTN_FACE,
            text_color=T_META,
        )
        self.pill.set("processing")

    def _consume_chunks(self) -> None:
        while True:
            chunk = self.chunk_queue.get()
            if chunk is None:
                self.after(0, self._on_done)
                break
            try:
                text = self.transcriber.transcribe_chunk(
                    chunk, initial_prompt=self._glossary_prompt
                )
            except Exception as exc:
                self._log_debug(f"[app] transcription failed: {exc}")
                if self.is_recording:
                    self.is_recording = False
                    try:
                        self.recorder.stop()
                    except Exception:
                        pass
                self.after(
                    0,
                    self._show_error_state,
                    "Transcription failed",
                    "A transcription error interrupted the session.\n\n"
                    "The app has been returned to an idle state so you can retry.\n\n"
                    f"Details: {exc}",
                )
                break
            if text:
                self._log_debug(
                    f"[app] chunk transcribed len={len(text)} recorder={self.recorder.diagnostics_snapshot()}"
                )
                self._chunk_count += 1
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                line = f"[{ts}] {text}"
                self.transcript_lines.append(line)
                self.after(0, self._append_line, line)
                write_ok = self._write_session()
                n = self._chunk_count
                if write_ok:
                    self.after(
                        0, self.pill.set, "recording",
                        f"{n} chunk{'s' if n != 1 else ''}"
                    )
                else:
                    if self.is_recording:
                        self.is_recording = False
                        try:
                            self.recorder.stop()
                        except Exception:
                            pass
                    self.after(
                        0,
                        self._show_error_state,
                        "Save failed",
                        "Unable to write the transcript to disk.\n\n"
                        "The app has been returned to an idle state so you can retry.",
                    )
                    break

    def _append_line(self, line: str) -> None:
        m = re.match(r"^(\[\d{2}:\d{2}:\d{2}\])\s*(.*)", line, re.DOTALL)
        tb = self._box._textbox
        self._box.configure(state="normal")
        if m:
            tb.insert("end", m.group(1) + "  ", "ts")
            tb.insert("end", m.group(2) + "\n\n", "body")
        else:
            tb.insert("end", line + "\n\n", "body")
        tb.see("end")
        self._box.configure(state="disabled")

    def _on_done(self) -> None:
        self._log_debug(
            f"[app] session done lines={len(self.transcript_lines)} recorder={self.recorder.diagnostics_snapshot()}"
        )
        if self.transcript_lines:
            finalized = self._finalize_path()
            persisted = self._write_session()
            if persisted and finalized:
                n = len(self.transcript_lines)
                self.pill.set("saved", f"{n} seg{'s' if n != 1 else ''}")
            elif persisted:
                self.pill.set("error", "Name unchanged")
                messagebox.showwarning(
                    "Interview Transcriber",
                    "Transcript content was saved, but the final filename could not be applied.",
                )
            else:
                self.pill.set("error", "Save failed")
                messagebox.showerror(
                    "Interview Transcriber",
                    "The transcript could not be fully written to disk.",
                )
        else:
            self._session_path = None
            self.pill.set("ready")
            self._show_placeholder()

        self._set_idle_state()
        self._refresh_meta()
        self._set_path()
        self._refresh_history(select=self._session_path if self._has_saved_file() else None)

    # ── File handling ─────────────────────────────────────────────────────────

    def _finalize_path(self) -> bool:
        if not (self._session_path and self.transcript_lines):
            return False
        if not _is_recordings_path(self._session_path):
            self._log_debug(f"[app] refused to finalize unsafe session path: {self._session_path}")
            return False
        title = _infer_title(self.transcript_lines, self._glossary_terms)
        new_md = _session_file(self.session_start, title, current=self._session_path)
        if new_md == self._session_path:
            return True

        old_md = self._session_path
        old_txt = old_md.with_suffix(".txt")
        new_txt = new_md.with_suffix(".txt")

        if not old_md.exists():
            return False

        try:
            old_md.rename(new_md)
            if old_txt.exists():
                old_txt.rename(new_txt)
            self._rename_sidecars(old_md, new_md)
            self._session_path = new_md
            return True
        except OSError:
            return False

    def _rename_sidecars(self, old_md: Path, new_md: Path) -> None:
        old_stem = old_md.with_suffix("")
        new_stem = new_md.with_suffix("")
        renamed_paths: dict[str, Path] = {}
        for source, old_path in self._audio_paths.items():
            expected = old_stem.with_name(f"{old_stem.name}_{source}.wav")
            target = new_stem.with_name(f"{new_stem.name}_{source}.wav")
            candidate = old_path if old_path.exists() else expected
            if candidate.exists() and candidate != target:
                candidate.rename(target)
            renamed_paths[source] = target
        self._audio_paths = renamed_paths

        old_meta = self._session_meta_path or old_stem.with_name(f"{old_stem.name}_session.json")
        new_meta = new_stem.with_name(f"{new_stem.name}_session.json")
        if old_meta.exists() and old_meta != new_meta:
            old_meta.rename(new_meta)
        self._session_meta_path = new_meta

    def _write_session(self) -> bool:
        if not (self._session_path and self.transcript_lines):
            return False
        if not _is_recordings_path(self._session_path):
            self._log_debug(f"[app] refused to write unsafe session path: {self._session_path}")
            return False
        try:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            content = self._build_content()
            self._session_path.write_text(content, encoding="utf-8")
            self._session_path.with_suffix(".txt").write_text(content, encoding="utf-8")
            self._write_session_metadata()
            return True
        except OSError:
            return False

    def _build_content(self) -> str:
        date = self.session_start or datetime.datetime.now()
        created_at = date.isoformat(timespec="seconds")
        chunk_seconds = int(self._chunk_btn.get().rstrip("s")) if hasattr(self, "_chunk_btn") else 8
        lines = [
            "---",
            f"app: {_yaml_string(APP_NAME)}",
            f"created_at: {_yaml_string(created_at)}",
            f"input_mode: {_yaml_string(self._input_mode)}",
            f"model: {_yaml_string(self.transcriber.model_name)}",
            f"compute_type: {_yaml_string(COMPUTE_TYPE)}",
            f"language: {_yaml_string(LANGUAGE)}",
            f"chunk_seconds: {chunk_seconds}",
            f"segments: {len(self.transcript_lines)}",
            "audio_sources:",
        ]
        if self._audio_paths:
            for source in ("mic", "system", "mixed"):
                path = self._audio_paths.get(source)
                if path:
                    lines.append(f"  {source}: {_yaml_string(path.name)}")
        else:
            lines.append("  {}")
        lines.append("devices:")
        if self._source_devices:
            for source in ("mic", "system"):
                device = self._source_devices.get(source)
                if device:
                    lines.append(f"  {source}: {_yaml_string(device.get('name'))}")
        else:
            lines.append("  {}")
        lines.extend(["---", "", "# Transcript", ""])
        return "\n".join(lines) + "\n" + "\n\n".join(self.transcript_lines) + "\n"

    def _write_session_metadata(self) -> None:
        if not self._session_meta_path:
            return
        created_at = (self.session_start or datetime.datetime.now()).isoformat(timespec="seconds")
        chunk_seconds = int(self._chunk_btn.get().rstrip("s")) if hasattr(self, "_chunk_btn") else 8
        data = {
            "app": APP_NAME,
            "created_at": created_at,
            "input_mode": self._input_mode,
            "model": self.transcriber.model_name,
            "compute_type": COMPUTE_TYPE,
            "language": LANGUAGE,
            "chunk_seconds": chunk_seconds,
            "segments": len(self.transcript_lines),
            "transcript": self._session_path.name if self._session_path else None,
            "audio_sources": {
                source: path.name for source, path in self._audio_paths.items()
            },
            "devices": {
                source: device.get("name") for source, device in self._source_devices.items()
            },
        }
        self._session_meta_path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ── Utility ──────────────────────────────────────────────────────────────

    def _refresh_meta(self) -> None:
        chunk = self._chunk_btn.get() if hasattr(self, "_chunk_btn") else CHUNK_OPTIONS[1]
        model_name = self.transcriber.model_name
        model = f"Model {model_name}"
        if model_name != "small":
            model += " · slower"
        glossary = (
            f"Glossary {len(self._glossary_terms)} terms"
            if self._glossary_terms
            else "Glossary none"
        )
        segments = f"{self._chunk_count} segment{'s' if self._chunk_count != 1 else ''}"
        input_label = _display_input_mode(self._input_mode)
        self._meta_var.set(f"{model}  ·  {input_label}  ·  Chunk {chunk}  ·  {glossary}  ·  {segments}")

    def _set_path(self) -> None:
        target = self._selected_history_path if self._selected_history_path else self._session_path
        if target:
            try:
                rel = target.relative_to(DISPLAY_ROOT)
                self._path_var.set(f"  {rel}")
            except ValueError:
                self._path_var.set(f"  {target}")
        else:
            self._path_var.set("  recordings/")

    def clear_transcript(self) -> None:
        self._show_placeholder()
        self.transcript_lines = []
        self._chunk_count = 0
        self._session_path = None
        self._audio_paths = {}
        self._session_meta_path = None
        self._source_devices = {}
        self._selected_history_path = None
        self.pill.set("ready")
        self._refresh_meta()
        self._set_path()
        self._refresh_history()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
