"""Tkinter UI in a Classic-WoW palette: dark frame, gold headers, parchment text."""

from __future__ import annotations

import logging
import math
import os
import re
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from . import FROZEN, LEXICON_DIR, __version__, updater
from .app import ChatScribe, Result
from .audio import list_input_devices
from .calibration import (LEVEL_SENTENCE, TEST_PHRASES, PhraseResult, compute_levels,
                          learn_from, measure_levels, word_accuracy)
from .hotkey import describe
from .typer import set_clipboard

log = logging.getLogger(__name__)

# Palette borrowed from the classic dialog frames.
BG = "#120d08"
PANEL = "#1e160e"
EDGE = "#5a4325"
GOLD = "#ffd100"
GOLD_DIM = "#c9a54a"
TEXT = "#f0e6cc"
MUTED = "#9c8f74"
GREEN = "#40c040"
RED = "#e04a3a"
BLUE = "#6fb7ff"
HEAD_FONT = ("Georgia", 15, "bold")
SUB_FONT = ("Georgia", 10, "bold")
BODY = ("Segoe UI", 10)
BIG = ("Segoe UI", 12)
SMALL = ("Segoe UI", 9)

STATE_COLOR = {"loading": GOLD_DIM, "ready": GREEN, "listening": RED, "thinking": BLUE,
               "sent": GREEN, "error": RED}

PREFIX_CHOICES = [("Chat box (whatever channel is open)", ""), ("Say", "/s "), ("Party", "/p "),
                  ("Raid", "/raid "), ("Guild", "/g "), ("Officer", "/o "), ("Yell", "/y "),
                  ("Reply to last whisper", "/r "), ("Instance / BG", "/i "),
                  ("Raid warning", "/rw "), ("Trade (/2)", "/2 "), ("General (/1)", "/1 ")]
MODELS = [("large-v3-turbo", "Best - fast on NVIDIA GPU (recommended)"),
          ("large-v3", "Most accurate, slower"),
          ("distil-large-v3", "Fast and accurate (GPU)"),
          ("medium.en", "Medium - OK on CPU"),
          ("small.en", "Small - for computers without a GPU")]


def style_root(root: tk.Tk) -> None:
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL, font=BODY,
                bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, troughcolor=PANEL)
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=PANEL)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    s.configure("Head.TLabel", background=BG, foreground=GOLD, font=HEAD_FONT)
    s.configure("Sub.TLabel", background=BG, foreground=GOLD_DIM, font=SUB_FONT)
    s.configure("PanelSub.TLabel", background=PANEL, foreground=GOLD_DIM, font=SUB_FONT)
    s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=SMALL)
    s.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=SMALL)
    s.configure("TButton", background="#3a0d07", foreground=GOLD, bordercolor="#8a6a2f",
                focuscolor="#8a6a2f", font=("Georgia", 10, "bold"), padding=(8, 3))
    s.map("TButton", background=[("active", "#5c1a0e"), ("disabled", "#2a211a")],
          foreground=[("disabled", MUTED)])
    s.configure("TCheckbutton", background=BG, foreground=TEXT)
    s.map("TCheckbutton", background=[("active", BG)])
    s.configure("TRadiobutton", background=BG, foreground=TEXT)
    s.map("TRadiobutton", background=[("active", BG)])
    s.configure("TCombobox", fieldbackground=PANEL, background="#3a0d07", foreground=TEXT,
                arrowcolor=GOLD, selectbackground=PANEL, selectforeground=TEXT)
    # Read-only comboboxes otherwise get clam's light-grey field -> unreadable text.
    s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)],
          selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)],
          background=[("readonly", "#3a0d07")])
    s.configure("Horizontal.TProgressbar", background=GOLD_DIM, troughcolor=PANEL)
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", EDGE)


class Meter(tk.Canvas):
    """Mic level bar with the noise gate marked."""

    def __init__(self, master, **kw):
        super().__init__(master, height=14, bg=PANEL, highlightthickness=1,
                         highlightbackground=EDGE, **kw)
        self.level = -90.0
        self.gate = -50.0

    def _x(self, d: float) -> float:
        w = max(self.winfo_width(), 10)
        return w * min(max((d + 80) / 80, 0), 1)

    def draw(self, level_db: float, gate_db: float, live: bool) -> None:
        self.level = max(level_db, self.level - 2.5)  # smooth falloff
        self.gate = gate_db
        self.delete("all")
        h = self.winfo_height()
        x = self._x(self.level)
        color = RED if live else (GREEN if self.level > gate_db else GOLD_DIM)
        self.create_rectangle(0, 0, x, h, fill=color, width=0)
        gx = self._x(gate_db)
        self.create_line(gx, 0, gx, h, fill=TEXT, dash=(2, 2))


class Overlay:
    """Small always-on-top badge near the top of the screen (visible over windowed WoW).

    It must NEVER take focus: if it did, WoW would stop being the active window and the
    message would go to the clipboard instead of chat. So the window is created once,
    made click-through + no-activate at the Win32 level, and hidden via transparency
    instead of withdraw/deiconify (deiconify can activate a window)."""

    ALPHA = 0.88

    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.label = tk.Label(self.win, text="", bg="#1a120a", fg=GOLD,
                              font=("Georgia", 12, "bold"), padx=14, pady=5,
                              highlightthickness=1, highlightbackground="#8a6a2f")
        self.label.pack()
        self._after = None
        self._alpha(0.0)
        self.win.geometry("+-2000+-2000")
        self.win.update_idletasks()
        self._make_passive()

    def _alpha(self, a: float) -> None:
        try:
            self.win.attributes("-alpha", a)
        except tk.TclError:
            pass

    def _make_passive(self) -> None:
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
            WS_EX_LAYERED = 0x80000
            u32 = ctypes.windll.user32
            hwnd = u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
                               | WS_EX_NOACTIVATE | WS_EX_LAYERED)
        except Exception:
            log.exception("overlay style")

    def show(self, text: str, color: str = GOLD, hide_ms: int | None = None) -> None:
        self.label.configure(text=text, fg=color)
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        w = self.win.winfo_reqwidth()
        self.win.geometry(f"+{(sw - w) // 2}+{40}")
        self._alpha(self.ALPHA)
        if self._after:
            self.win.after_cancel(self._after)
            self._after = None
        if hide_ms:
            self._after = self.win.after(hide_ms, self.hide)

    def hide(self) -> None:
        self._alpha(0.0)
        self.win.geometry("+-2000+-2000")


class MainWindow:
    def __init__(self, app: ChatScribe):
        self.app = app
        self.root = tk.Tk()
        self.root.title(f"ChatScribe {__version__}")
        self.root.geometry("540x640")
        self.root.minsize(400, 480)
        style_root(self.root)
        self.root.attributes("-topmost", app.cfg.always_on_top)
        self.overlay = Overlay(self.root)
        self.history: list[Result] = []
        self.calib: CalibrationWizard | None = None
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(50, self._tick)
        if app.cfg.check_updates:
            self.root.after(4000, lambda: self.check_updates(manual=False))

    # --------------------------------------------------------------- layout
    def _build(self) -> None:
        r = self.root
        top = ttk.Frame(r, padding=(14, 10, 14, 4))
        top.pack(fill="x")
        ttk.Label(top, text="ChatScribe", style="Head.TLabel").pack(side="left")
        ttk.Label(top, text="voice to WoW Forever chat", style="Muted.TLabel").pack(
            side="left", padx=8, pady=(6, 0))

        st = ttk.Frame(r, padding=(14, 4))
        st.pack(fill="x")
        self.dot = tk.Canvas(st, width=14, height=14, bg=BG, highlightthickness=0)
        self.dot.pack(side="left")
        self._dot = self.dot.create_oval(2, 2, 12, 12, fill=GOLD_DIM, width=0)
        self.status = ttk.Label(st, text="Starting...", font=BIG)
        self.status.pack(side="left", padx=6)

        m = ttk.Frame(r, padding=(14, 2))
        m.pack(fill="x")
        self.meter = Meter(m)
        self.meter.pack(fill="x")
        ttk.Label(m, text="Mic level - the dashed line is the silence gate",
                  style="Muted.TLabel").pack(anchor="w")

        last = ttk.Frame(r, style="Panel.TFrame", padding=10)
        last.pack(fill="x", padx=14, pady=8)
        ttk.Label(last, text="Last message", style="PanelSub.TLabel").pack(anchor="w")
        self.last_text = tk.Label(last, text="-", bg=PANEL, fg=TEXT, font=BIG, wraplength=400,
                                  justify="left", anchor="w")
        self.last_text.pack(fill="x", pady=(2, 2))
        self.last_raw = ttk.Label(last, text="", style="PanelMuted.TLabel", wraplength=400,
                                  justify="left")
        self.last_raw.pack(fill="x")
        self.last_fix = ttk.Label(last, text="", style="PanelMuted.TLabel", wraplength=400,
                                  justify="left")
        self.last_fix.pack(fill="x")
        self.root.bind("<Configure>", self._rewrap)

        btns = ttk.Frame(r, padding=(14, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Calibrate", command=self.open_calibration).pack(side="left")
        ttk.Button(btns, text="Settings", command=self.open_settings).pack(side="left", padx=6)
        ttk.Button(btns, text="Fix a word", command=lambda: self.teach_dialog()).pack(side="left")
        self.pause_btn = ttk.Button(btns, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side="right")

        hist = ttk.Frame(r, padding=(14, 8, 14, 4))
        hist.pack(fill="both", expand=True)
        ttk.Label(hist, text="History  (double-click: copy - right-click: fix a word)",
                  style="Sub.TLabel").pack(anchor="w")
        self.hist = tk.Listbox(hist, bg=PANEL, fg=TEXT, selectbackground=EDGE,
                               selectforeground=GOLD, font=BODY, borderwidth=0,
                               highlightthickness=1, highlightbackground=EDGE, activestyle="none")
        self.hist.pack(fill="both", expand=True, pady=(4, 0))
        self.hist.bind("<Double-Button-1>", self._copy_selected)
        self.hist.bind("<Button-3>", self._hist_menu)

        foot = ttk.Frame(r, padding=(14, 2, 14, 8))
        foot.pack(fill="x")
        self.foot = ttk.Label(foot, text="", style="Muted.TLabel")
        self.foot.pack(side="left")
        ttk.Button(foot, text="Word lists", command=lambda: os.startfile(LEXICON_DIR)).pack(
            side="right")
        self.update_btn = ttk.Button(foot, text="Updates", command=self.check_updates)
        self.update_btn.pack(side="right", padx=6)
        self.update_info = None

    def _rewrap(self, _e=None) -> None:
        w = max(self.root.winfo_width() - 60, 200)
        for lab in (self.last_text, self.last_raw, self.last_fix):
            lab.configure(wraplength=w)

    # ----------------------------------------------------------- event loop
    def _tick(self) -> None:
        try:
            while True:
                ev = self.app.events.get_nowait()
                self._handle(ev)
        except queue.Empty:
            pass
        gain_db = 20 * math.log10(max(self.app.cfg.gain, 1e-6))
        self.meter.draw(self.app.recorder.level_db + gain_db, self.app.cfg.gate_db,
                        self.app.recorder.recording)
        self.root.after(50, self._tick)

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "status":
            state, text = ev[1], ev[2]
            self.status.configure(text=text)
            self.dot.itemconfigure(self._dot, fill=STATE_COLOR.get(state, GOLD_DIM))
            self.foot.configure(text=f"Model: {self.app.engine.model_name} on "
                                     f"{self.app.engine.actual_device}")
            if self.app.cfg.overlay:
                if state == "listening":
                    self.overlay.show("●  Listening", RED)
                elif state == "thinking":
                    self.overlay.show("…  Writing", BLUE)
                elif state == "sent":
                    self.overlay.show("✓  " + text.split(" (")[0], GREEN, 1400)
                elif state == "ready":
                    if text.startswith(("Didn't", "Couldn't", "Cancelled")):
                        self.overlay.show(text.split(" - ")[0], GOLD_DIM, 1600)
                    else:
                        self.overlay.hide()
        elif kind == "result":
            self._show_result(ev[1])
        elif kind == "error":
            self.status.configure(text=ev[1])
            self.dot.itemconfigure(self._dot, fill=RED)
            self.overlay.hide()
        elif kind == "ui_call":
            ev[1](*ev[2])
        elif kind == "update":
            self._on_update(ev[1], ev[2], ev[3])
        elif kind == "capture":
            owner, fn, spec = ev[1], ev[2], ev[3]
            try:
                if owner.win.winfo_exists():
                    fn(spec)
            except tk.TclError:
                pass                            # settings window was closed meanwhile
        elif kind == "calib":
            if self.calib:
                self.calib.on_event(*ev[1:])

    def _show_result(self, res: Result) -> None:
        if res.line.cancelled:
            self.last_text.configure(text="(cancelled)", fg=MUTED)
        else:
            self.last_text.configure(text="\n".join(res.lines) or "-", fg=TEXT)
        self.last_raw.configure(text=f"Heard: {res.raw}")
        fixes = [f"{r.original} → {r.new}" for r in res.line.replacements
                 if r.original.lower() != r.new.lower()]
        extra = f"   [{res.line.route_label}]" if res.line.route_label else ""
        self.last_fix.configure(text=("Fixed: " + ", ".join(fixes) if fixes else "") + extra)
        if not res.line.cancelled and res.lines:
            self.history.insert(0, res)
            self.history = self.history[: self.app.cfg.history_size]
            self.hist.delete(0, "end")
            for h in self.history:
                self.hist.insert("end", "  " + " / ".join(h.lines))

    # ------------------------------------------------------------- actions
    def _copy_selected(self, _e=None) -> None:
        sel = self.hist.curselection()
        if sel:
            set_clipboard("\n".join(self.history[sel[0]].lines))
            self.status.configure(text="Copied to clipboard")

    def _hist_menu(self, e) -> None:
        idx = self.hist.nearest(e.y)
        if idx < 0 or idx >= len(self.history):
            return
        self.hist.selection_clear(0, "end")
        self.hist.selection_set(idx)
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=EDGE,
                       activeforeground=GOLD)
        menu.add_command(label="Fix a word in this message...",
                         command=lambda: self.teach_dialog(self.history[idx].raw))
        menu.add_command(label="Copy", command=self._copy_selected)
        menu.tk_popup(e.x_root, e.y_root)

    def toggle_pause(self) -> None:
        self.app.paused = not self.app.paused
        self.pause_btn.configure(text="Resume" if self.app.paused else "Pause")
        self.status.configure(text="Paused" if self.app.paused else self.app.ready_text())

    def teach_dialog(self, heard: str = "") -> None:
        TeachDialog(self.root, self.app, heard)

    def open_settings(self) -> None:
        SettingsWindow(self)

    def open_calibration(self) -> None:
        if self.calib and self.calib.win.winfo_exists():
            self.calib.win.lift()
            return
        self.calib = CalibrationWizard(self)

    # ------------------------------------------------------------ updates
    def check_updates(self, manual: bool = True) -> None:
        if self.update_info is not None:
            UpdateDialog(self, self.update_info)
            return
        if manual:
            self.update_btn.configure(text="Checking...")

        def work():
            try:
                info, err = updater.check(), None
            except Exception as e:
                info, err = None, e
            self.app.emit("update", info, err, manual)
        threading.Thread(target=work, daemon=True).start()

    def _on_update(self, info, err, manual: bool) -> None:
        self.update_btn.configure(text="Updates")
        if err is not None:
            log.info("Update check failed: %s", err)
            if manual:
                messagebox.showinfo("ChatScribe", "Couldn't reach GitHub to check for updates."
                                    f"\n\n{err}", parent=self.root)
            return
        if info is None:
            if manual:
                messagebox.showinfo("ChatScribe", f"You're up to date (version {__version__}).",
                                    parent=self.root)
            return
        self.update_info = info
        self.update_btn.configure(text=f"⬆ Update to {info.version}")
        if manual or info.version != self.app.cfg.skipped_version:
            UpdateDialog(self, info)

    def quit(self) -> None:
        try:
            self.app.shutdown()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ===================================================================== dialogs
class TeachDialog:
    """User-taught corrections: 'it wrote X, I meant Y'. Saved to data/learned_aliases.json."""

    def __init__(self, parent, app: ChatScribe, heard: str = ""):
        self.app = app
        w = self.win = tk.Toplevel(parent)
        w.title("Fix a word")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both")
        ttk.Label(f, text="Teach ChatScribe a word", style="Sub.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        if heard:
            ttk.Label(f, text=f"It heard: {heard}", style="Muted.TLabel", wraplength=360).grid(
                row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        ttk.Label(f, text="It wrote:").grid(row=2, column=0, sticky="w")
        self.wrong = tk.Entry(f, width=34, bg=PANEL, fg=TEXT, insertbackground=GOLD)
        self.wrong.grid(row=2, column=1, pady=3)
        ttk.Label(f, text="I meant:").grid(row=3, column=0, sticky="w")
        self.right = tk.Entry(f, width=34, bg=PANEL, fg=TEXT, insertbackground=GOLD)
        self.right.grid(row=3, column=1, pady=3)
        ttk.Label(f, text="Example: it wrote \"bad blood\" - I meant \"Badbluud\" (your guildie)",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(b, text="Save", command=self.save).pack(side="right")
        ttk.Button(b, text="Cancel", command=w.destroy).pack(side="right", padx=6)
        self.wrong.focus_set()
        w.bind("<Return>", lambda e: self.save())

    def save(self) -> None:
        wrong, right = self.wrong.get().strip(), self.right.get().strip()
        if not wrong or not right:
            return
        if self.app.teach(wrong, right):
            self.win.destroy()
        else:
            messagebox.showinfo("ChatScribe", "That one can't be learned (too common a phrase, "
                                "or it's already the same).", parent=self.win)


class SettingsWindow:
    def __init__(self, main: MainWindow):
        self.main = main
        self.app = main.app
        cfg = self.app.cfg
        self._orig_gate = cfg.gate_db
        w = self.win = tk.Toplevel(main.root)
        w.title("ChatScribe settings")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry("540x790")
        outer = ttk.Frame(w, padding=14)
        outer.pack(fill="both", expand=True)

        def section(text):
            ttk.Label(outer, text=text, style="Sub.TLabel").pack(anchor="w", pady=(10, 2))

        # --- microphone
        section("Microphone")
        devices = ["(Windows default)"] + list_input_devices()
        self.mic = ttk.Combobox(outer, values=devices, state="readonly", width=60)
        self.mic.set(cfg.input_device or "(Windows default)")
        self.mic.pack(anchor="w")
        # Silence gate: where quiet becomes "silence" (the dashed line on the mic meter).
        gate_row = ttk.Frame(outer)
        gate_row.pack(fill="x", pady=(6, 0))
        ttk.Label(gate_row, text="Silence gate:").pack(side="left")
        self.gate = tk.DoubleVar(value=max(-75.0, min(-20.0, cfg.gate_db)))
        self.gate_lbl = ttk.Label(gate_row, width=8)
        tk.Scale(gate_row, from_=-75, to=-20, resolution=1, orient="horizontal",
                 variable=self.gate, showvalue=False, length=260, bg=BG, fg=TEXT,
                 troughcolor=PANEL, highlightthickness=0, activebackground=GOLD_DIM,
                 command=lambda v: self._gate_changed()).pack(side="left", padx=6)
        self.gate_lbl.pack(side="left")
        ttk.Label(outer, text="Left = picks up quieter speech. Move it right only if background "
                  "noise gets typed as words. Calibrate sets this for you.",
                  style="Muted.TLabel", wraplength=480).pack(anchor="w")
        self._gate_changed()

        # --- keys
        section("Push-to-talk keys")
        self.bind_frame = ttk.Frame(outer)
        self.bind_frame.pack(fill="x")
        self.bindings = [dict(b) for b in cfg.bindings]
        self._draw_bindings()
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(4, 0))
        self.add_btn = ttk.Button(row, text="+ Add key", command=self._add_binding)
        self.add_btn.pack(side="left")
        self.mode = tk.StringVar(value=cfg.mode)
        ttk.Radiobutton(row, text="Hold to talk", variable=self.mode, value="hold").pack(
            side="left", padx=(16, 4))
        ttk.Radiobutton(row, text="Press to start/stop", variable=self.mode, value="toggle").pack(
            side="left")
        self.suppress = tk.BooleanVar(value=cfg.suppress_hotkey)
        ttk.Checkbutton(outer, text="Block the push-to-talk key from reaching WoW",
                        variable=self.suppress).pack(anchor="w")

        # --- text
        section("Writing style")
        self.style = tk.StringVar(value=cfg.style)
        for val, label in (("proper", "Proper - \"Heading to Orgrimmar.\""),
                           ("natural", "Natural - \"Heading to Orgrimmar\" (no final full stop)"),
                           ("casual", "Casual - \"heading to Orgrimmar\"")):
            ttk.Radiobutton(outer, text=label, variable=self.style, value=val).pack(anchor="w")
        self.flags = {}
        for key, label in (
                ("spoken_punctuation", "Spoken punctuation (\"question mark\", \"comma\", "
                                       "\"smiley face\")"),
                ("remove_fillers", "Remove um / uh / repeated words"),
                ("shorthand", "Chat shorthand (\"looking for group\" → LFG, "
                              "\"be right back\" → brb)"),
                ("voice_channels", "Voice channel commands (\"party chat ...\", "
                                   "\"whisper Name ...\", \"slash dance\")")):
            v = tk.BooleanVar(value=getattr(cfg, key))
            self.flags[key] = v
            ttk.Checkbutton(outer, text=label, variable=v).pack(anchor="w")

        # --- output
        section("Sending")
        for key, label in (("auto_send", "Press Enter automatically (send right away)"),
                           ("open_chat_first", "Open the chat box first (press Enter before pasting)"),
                           ("only_when_wow_focused", "Only type when WoW is the active window "
                                                     "(otherwise copy to clipboard)"),
                           ("overlay", "Show the Listening badge at the top of the screen"),
                           ("always_on_top", "Keep this window on top"),
                           ("check_updates", "Check GitHub for updates at startup")):
            v = tk.BooleanVar(value=getattr(cfg, key))
            self.flags[key] = v
            ttk.Checkbutton(outer, text=label, variable=v).pack(anchor="w")

        # --- model
        section("Speech model")
        self.model = ttk.Combobox(outer, values=[f"{m} - {d}" for m, d in MODELS],
                                  state="readonly", width=60)
        cur = next((f"{m} - {d}" for m, d in MODELS if m == cfg.model), cfg.model)
        self.model.set(cur)
        self.model.pack(anchor="w")

        b = ttk.Frame(outer)
        b.pack(fill="x", side="bottom", pady=(12, 0))
        ttk.Button(b, text="Save", command=self.save).pack(side="right")
        ttk.Button(b, text="Cancel", command=self.cancel).pack(side="right", padx=6)
        w.protocol("WM_DELETE_WINDOW", self.cancel)
        w.bind("<Destroy>", lambda e: e.widget is w and self.app.hotkeys.cancel_capture())

    def _gate_changed(self) -> None:
        v = self.gate.get()
        self.gate_lbl.configure(text=f"{v:.0f} dB")
        self.app.cfg.gate_db = v          # live: the main window's dashed line follows

    def _draw_bindings(self) -> None:
        for c in self.bind_frame.winfo_children():
            c.destroy()
        labels = [p[0] for p in PREFIX_CHOICES]
        for i, bnd in enumerate(self.bindings):
            row = ttk.Frame(self.bind_frame)
            row.pack(fill="x", pady=2)
            btn = ttk.Button(row, text=describe(bnd["key"]), width=18)
            btn.configure(command=lambda i=i, btn=btn: self._capture(i, btn))
            btn.pack(side="left")
            ttk.Label(row, text="→").pack(side="left", padx=4)
            cb = ttk.Combobox(row, values=labels, state="readonly", width=32)
            cur = next((p[0] for p in PREFIX_CHOICES if p[1] == bnd.get("prefix", "")), labels[0])
            cb.set(cur)
            cb.bind("<<ComboboxSelected>>", lambda e, i=i, cb=cb: self._set_prefix(i, cb.get()))
            cb.pack(side="left")
            if len(self.bindings) > 1:
                ttk.Button(row, text="✕", width=3,
                           command=lambda i=i: self._remove_binding(i)).pack(side="left", padx=4)

    def _capture(self, i: int, btn) -> None:
        btn.configure(text="Press a key / mouse button...")

        def apply(spec):                    # runs on the Tk thread (via the event queue)
            if spec and all(b["key"] != spec for j, b in enumerate(self.bindings) if j != i):
                self.bindings[i]["key"] = spec
            self._draw_bindings()
        self.app.hotkeys.capture_next(lambda spec: self.app.emit("capture", self, apply, spec))

    def _set_prefix(self, i: int, label: str) -> None:
        for lab, pre in PREFIX_CHOICES:
            if lab == label:
                self.bindings[i]["prefix"] = pre
                self.bindings[i]["label"] = lab.split(" (")[0]

    def _add_binding(self) -> None:
        """Ask for the key first - never invent one (a made-up Mouse 5 binding sent
        Discord push-to-talk speech to party chat)."""
        self.add_btn.configure(text="Press a key or mouse button (Esc cancels)...")

        def apply(spec):                    # runs on the Tk thread (via the event queue)
            self.add_btn.configure(text="+ Add key")
            if spec and all(b["key"] != spec for b in self.bindings):
                self.bindings.append({"key": spec, "prefix": "", "label": "Chat"})
                self._draw_bindings()
        self.app.hotkeys.capture_next(lambda spec: self.app.emit("capture", self, apply, spec))

    def _remove_binding(self, i: int) -> None:
        del self.bindings[i]
        self._draw_bindings()

    def cancel(self) -> None:
        self.app.cfg.gate_db = self._orig_gate
        self.win.destroy()

    def save(self) -> None:
        cfg = self.app.cfg
        mic = self.mic.get()
        mic = "" if mic.startswith("(Windows default") else mic
        mic_changed = mic != cfg.input_device
        model = self.model.get().split(" - ")[0]
        model_changed = model != cfg.model
        cfg.input_device = mic
        cfg.gate_db = float(self.gate.get())
        cfg.bindings = self.bindings
        cfg.mode = self.mode.get()
        cfg.suppress_hotkey = self.suppress.get()
        cfg.style = self.style.get()
        for k, v in self.flags.items():
            setattr(cfg, k, v.get())
        cfg.model = model
        cfg.save()
        self.app.lexicon.shorthand = cfg.shorthand
        self.app.apply_bindings()
        self.main.root.attributes("-topmost", cfg.always_on_top)
        if mic_changed:
            self.app.reopen_mic()
        if model_changed:
            self.app.reload_model()
        else:
            self.app.emit("status", "ready", self.app.ready_text())
        self.win.destroy()


class CalibrationWizard:
    """1) silence  2) speaking level  3) read WoW phrases - learns your misheard words."""

    def __init__(self, main: MainWindow):
        self.main = main
        self.app = main.app
        w = self.win = tk.Toplevel(main.root)
        w.title("ChatScribe calibration")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry("560x470")
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.body = ttk.Frame(w, padding=16)
        self.body.pack(fill="both", expand=True)
        self.noise: list[float] = []
        self.speech: list[float] = []
        self.results: list[PhraseResult] = []
        self.phrase_i = 0
        self.levels = None
        self.step_intro()

    def clear(self) -> None:
        for c in self.body.winfo_children():
            c.destroy()

    def title(self, text: str, sub: str = "") -> None:
        ttk.Label(self.body, text=text, style="Head.TLabel").pack(anchor="w")
        if sub:
            ttk.Label(self.body, text=sub, wraplength=520, justify="left").pack(
                anchor="w", pady=(6, 10))

    def close(self) -> None:
        self.app.capture_hook = None
        self.win.destroy()

    # ------------------------------------------------------------- steps
    def step_intro(self) -> None:
        self.clear()
        self.title("Calibration", "About two minutes. ChatScribe measures your room noise and "
                   "voice level, then you read a few WoW lines. Any WoW words it gets wrong for "
                   "YOUR voice are learned, so they come out right next time.\n\n"
                   "Use the same mic and distance you use while playing.")
        ttk.Label(self.body, text="Microphone:", style="Sub.TLabel").pack(anchor="w")
        devices = ["(Windows default)"] + list_input_devices()
        self.mic = ttk.Combobox(self.body, values=devices, state="readonly", width=64)
        self.mic.set(self.app.cfg.input_device or "(Windows default)")
        self.mic.pack(anchor="w", pady=(0, 12))
        ttk.Button(self.body, text="Start", command=self._start).pack(anchor="e", side="bottom")

    def _start(self) -> None:
        mic = self.mic.get()
        mic = "" if mic.startswith("(Windows default") else mic
        if mic != self.app.cfg.input_device:
            self.app.cfg.input_device = mic
            self.app.reopen_mic()
        self.step_noise()

    def step_noise(self) -> None:
        self.clear()
        self.title("Step 1 of 3 - Silence", "Stay quiet for 3 seconds (game sound can stay on, "
                   "the way you normally play).")
        self.countdown = ttk.Label(self.body, text="", font=("Georgia", 22, "bold"),
                                   foreground=GOLD)
        self.countdown.pack(pady=20)
        self.go = ttk.Button(self.body, text="Measure silence", command=self._run_noise)
        self.go.pack()

    def _run_noise(self) -> None:
        self.go.configure(state="disabled")
        self._measure(3.0, "noise", self.step_speech)

    def step_speech(self) -> None:
        self.clear()
        self.title("Step 2 of 3 - Your voice", "Click Start, then read this out loud at your "
                   "normal in-game volume:")
        tk.Label(self.body, text=f"“{LEVEL_SENTENCE}”", bg=PANEL, fg=TEXT, font=BIG,
                 wraplength=500, justify="left", padx=10, pady=10).pack(fill="x")
        self.countdown = ttk.Label(self.body, text="", font=("Georgia", 22, "bold"),
                                   foreground=GOLD)
        self.countdown.pack(pady=14)
        self.go = ttk.Button(self.body, text="Start (6 seconds)", command=self._run_speech)
        self.go.pack()

    def _run_speech(self) -> None:
        self.go.configure(state="disabled")
        self._measure(6.0, "speech", self.step_levels_done)

    def _measure(self, seconds: float, which: str, then) -> None:
        def work():
            def tick(left):
                self.app.emit("calib", "tick", left)
            lv = measure_levels(self.app.recorder, seconds, tick)
            self.app.emit("calib", which, lv, then)
        threading.Thread(target=work, daemon=True).start()

    def step_levels_done(self) -> None:
        cfg = self.app.cfg
        r = compute_levels(self.noise, self.speech, cfg.gain)
        self.levels = r
        cfg.gain, cfg.gate_db, cfg.noise_db, cfg.speech_db = r.gain, r.gate_db, r.noise_db, r.speech_db
        cfg.save()
        self.clear()
        self.title("Levels set", r.verdict)
        info = (f"Background noise: {r.noise_db:.0f} dB\nYour voice: {r.speech_db:.0f} dB\n"
                f"Silence gate: {r.gate_db:.0f} dB\nInput gain: x{r.gain:.2f}")
        tk.Label(self.body, text=info, bg=PANEL, fg=TEXT, font=BODY, justify="left", padx=10,
                 pady=8).pack(fill="x")
        ttk.Label(self.body, text="\nNext: read WoW phrases so ChatScribe can learn how you say "
                  "names like Ahn'Qiraj and Vaelastrasz.", wraplength=520).pack(anchor="w")
        row = ttk.Frame(self.body)
        row.pack(side="bottom", fill="x")
        ttk.Button(row, text="Continue", command=self.step_phrases).pack(side="right")
        ttk.Button(row, text="Finish here", command=self.close).pack(side="right", padx=6)

    def step_phrases(self) -> None:
        if not self.app.ready:
            messagebox.showinfo("ChatScribe", "The speech model is still loading - try again "
                                "in a moment.", parent=self.win)
            return
        self.phrase_i = 0
        self.results = []
        self.app.capture_hook = self._captured
        self._show_phrase()

    def _show_phrase(self) -> None:
        self.clear()
        keys = " / ".join(describe(b["key"]) for b in self.app.cfg.bindings)
        n = len(TEST_PHRASES)
        self.title(f"Step 3 of 3 - Phrase {self.phrase_i + 1} of {n}",
                   f"Hold {keys} and read this, then let go:")
        tk.Label(self.body, text=TEST_PHRASES[self.phrase_i], bg=PANEL, fg=GOLD,
                 font=("Georgia", 14, "bold"), wraplength=500, justify="left", padx=12,
                 pady=12).pack(fill="x")
        self.heard = ttk.Label(self.body, text="", wraplength=520, justify="left")
        self.heard.pack(anchor="w", pady=(12, 0))
        self.learned_lbl = ttk.Label(self.body, text="", style="Muted.TLabel", wraplength=520)
        self.learned_lbl.pack(anchor="w")
        row = ttk.Frame(self.body)
        row.pack(side="bottom", fill="x")
        self.next_btn = ttk.Button(row, text="Skip", command=self._next_phrase)
        self.next_btn.pack(side="right")
        ttk.Button(row, text="Stop", command=self.step_summary).pack(side="right", padx=6)
        ttk.Progressbar(self.body, maximum=n, value=self.phrase_i).pack(side="bottom", fill="x",
                                                                         pady=8)

    def _captured(self, raw: str, line) -> None:    # worker thread
        self.app.emit("calib", "phrase", raw, line)

    def _next_phrase(self) -> None:
        self.phrase_i += 1
        if self.phrase_i >= len(TEST_PHRASES):
            self.step_summary()
        else:
            self._show_phrase()

    def step_summary(self) -> None:
        self.app.capture_hook = None
        self.clear()
        if not self.results:
            self.title("Calibration saved", "Levels are saved. You can run the phrase test any "
                       "time from Calibrate.")
        else:
            raw = sum(r.raw_acc for r in self.results) / len(self.results)
            fin = sum(r.final_acc for r in self.results) / len(self.results)
            learned = [x for r in self.results for x in r.learned]
            self.title("Calibration complete",
                       f"Raw recognition: {raw:.0%} of words right\n"
                       f"After WoW corrections: {fin:.0%}\n"
                       f"Learned {len(learned)} new spelling{'s' if len(learned) != 1 else ''} "
                       f"for your voice.")
            if learned:
                txt = "\n".join(f"  “{h}” → {c}" for h, c in learned[:12])
                tk.Label(self.body, text=txt, bg=PANEL, fg=TEXT, font=BODY, justify="left",
                         padx=10, pady=8).pack(fill="x")
            if fin < 0.85:
                ttk.Label(self.body, text="\nTip: accuracy is low. Speak a bit slower, move "
                          "the mic closer, or run calibration again - each run learns more.",
                          wraplength=520).pack(anchor="w")
        ttk.Button(self.body, text="Done", command=self.close).pack(side="bottom", anchor="e")

    # -------------------------------------------------------- UI events
    def on_event(self, kind, *args) -> None:
        if not self.win.winfo_exists():
            return
        if kind == "tick":
            try:
                self.countdown.configure(text=f"{args[0]:.1f}")
            except tk.TclError:
                pass
        elif kind in ("noise", "speech"):
            levels, then = args
            if kind == "noise":
                self.noise = levels
            else:
                self.speech = levels
            then()
        elif kind == "phrase":
            raw, line = args
            expected = TEST_PHRASES[self.phrase_i]
            if not raw:
                self.heard.configure(text="Didn't catch that - hold the key the whole time "
                                          "and try again.")
                return
            final = " ".join(line.lines()) if line else ""
            if line and line.prefix:
                final = line.prefix + line.text
            learned = learn_from(expected, raw, final, self.app.lexicon)
            res = PhraseResult(expected, raw, final, word_accuracy(expected, raw),
                               word_accuracy(expected, final), learned)
            self.results.append(res)
            self.heard.configure(text=f"Heard: {raw}\nWrote: {final}   "
                                      f"({res.final_acc:.0%} right)")
            if learned:
                self.learned_lbl.configure(text="Learned: " + ", ".join(
                    f"“{h}” → {c}" for h, c in learned))
            self.next_btn.configure(text="Next")


# ===================================================================== updates
class UpdateDialog:
    """'ChatScribe X is available' popup with download progress and one-click install."""

    def __init__(self, main: "MainWindow", info):
        self.main, self.app, self.info = main, main.app, info
        self._cancel = False
        w = self.win = tk.Toplevel(main.root)
        w.title("ChatScribe update")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry("500x420")
        w.protocol("WM_DELETE_WINDOW", self.later)
        f = self.body = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"ChatScribe {info.version} is available", style="Head.TLabel").pack(
            anchor="w")
        size_mb = info.size / 1e6
        if not FROZEN:
            kind_text = "Updates this source folder with git pull."
        elif info.full:
            kind_text = f"Download: {size_mb:,.0f} MB (includes the GPU runtime)."
        else:
            kind_text = f"Download: {size_mb:,.0f} MB."
        ttk.Label(f, text=f"You have {__version__}.  {kind_text}",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        notes = tk.Text(f, height=9, bg=PANEL, fg=TEXT, font=SMALL, wrap="word",
                        relief="flat", highlightthickness=1, highlightbackground=EDGE)
        notes.insert("1.0", _clean_notes(info.notes) or "See the release page for details.")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        self.status = ttk.Label(f, text="Your settings, learned words and custom words are kept.",
                                style="Muted.TLabel", wraplength=460)
        self.status.pack(anchor="w", pady=(8, 2))
        self.bar = ttk.Progressbar(f, maximum=1000)
        row = self.buttons = ttk.Frame(f)
        row.pack(fill="x", side="bottom", pady=(8, 0))
        if updater.can_self_update():
            ttk.Button(row, text="Update now", command=self.start).pack(side="right")
        else:   # e.g. source folder without git: nothing we can safely overwrite
            ttk.Button(row, text="Open release page",
                       command=lambda: webbrowser.open(info.page_url)).pack(side="right")
            self.status.configure(text="This copy can't update itself - download the new "
                                       "version from the release page.")
        ttk.Button(row, text="Later", command=self.later).pack(side="right", padx=6)
        ttk.Button(row, text="Skip this version", command=self.skip).pack(side="left")

    def later(self) -> None:
        self._cancel = True
        self.win.destroy()

    def skip(self) -> None:
        self.app.cfg.skipped_version = self.info.version
        self.app.cfg.save()
        self.later()

    def start(self) -> None:
        for c in self.buttons.winfo_children():
            c.destroy()
        ttk.Button(self.buttons, text="Cancel", command=self.later).pack(side="right")
        self.bar.pack(fill="x", pady=(4, 0))
        threading.Thread(target=self._work, daemon=True).start()

    def _ui(self, fn, *a) -> None:
        self.app.emit("ui_call", fn, a)            # Tk is only touched on the main thread

    def _work(self) -> None:
        try:
            def prog(done, total):
                self._ui(self._progress, done, total)
            if not FROZEN:
                updater.update_source_and_restart(lambda m: self._ui(self._set_status, m))
                self._ui(self.main.quit)
                return
            zip_path = updater.download(self.info, prog, lambda: self._cancel)
            self._ui(self._set_status, "Unpacking...")
            src = updater.stage(zip_path)
            self._ui(self._set_status, "Installing - ChatScribe will restart in a moment...")
            updater.install_and_restart(src)
            self._ui(self.main.quit)
        except Exception as e:
            if not self._cancel:
                log.exception("update failed")
                self._ui(self._failed, str(e))

    def _progress(self, done, total) -> None:
        if not self.win.winfo_exists():
            return
        if total:
            self.bar.configure(value=1000 * done / total)
        self.status.configure(text=f"Downloading... {done / 1e6:,.0f} / {total / 1e6:,.0f} MB")

    def _set_status(self, text) -> None:
        if self.win.winfo_exists():
            self.status.configure(text=text)

    def _failed(self, err) -> None:
        if not self.win.winfo_exists():
            return
        self.status.configure(text=f"Update failed: {err}")
        for c in self.buttons.winfo_children():
            c.destroy()
        ttk.Button(self.buttons, text="Open release page",
                   command=lambda: webbrowser.open(self.info.page_url)).pack(side="right")
        ttk.Button(self.buttons, text="Close", command=self.later).pack(side="right", padx=6)


def _clean_notes(md: str) -> str:
    """GitHub release notes are Markdown; show them as plain text."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    md = re.sub(r"\*\*(.*?)\*\*", r"\1", md)
    md = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", md)
    md = re.sub(r"^#+\s*", "", md, flags=re.M)
    md = re.sub(r"^\s*[-*]\s+", "• ", md, flags=re.M)
    return md.strip()[:3000]
