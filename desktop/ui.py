"""
PixelSpryte Control Panel
AI edit pipeline for DaVinci Resolve — dark themed, product-grade UI.

Run: python ui.py            (or pythonw ui.py for no console window)
"""

import os
import sys
import json
import queue
import threading
import contextlib
import traceback

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from run_webinar import load_config

# Load .env so the Settings dialog can show the stored API key
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

# Windows: crisp text on high-DPI displays
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ─── Theme (DaVinci Resolve inspired) ───────────────────────────────

BG       = "#17171B"   # window background
PANEL    = "#1F1F25"   # cards / sections
FIELD    = "#2A2A33"   # inputs
BORDER   = "#34343E"
ACCENT   = "#E8642D"   # Resolve-style orange
ACCENT_H = "#F27E4A"   # hover
TEXT     = "#D9D9DE"
MUTED    = "#8B8B95"
GREEN    = "#5CB870"
RED      = "#E06060"
CONSOLE  = "#101014"

FONT      = ("Segoe UI", 10)
FONT_SM   = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_H1   = ("Segoe UI Semibold", 14)
FONT_MONO = ("Consolas", 9)

PROFILES = {
    "Industry Briefing": "config_webinar.yaml",
    "Family Vlog": "config_vlog.yaml",
    "YouTube": "config.yaml",
}
MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]
STYLES = ["vlog", "retention"]
CAPTIONS = ["none", "srt", "styled"]
WHISPER_SIZES = ["base", "small", "medium"]


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT, font=FONT,
                    borderwidth=0, focuscolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED,
                    font=FONT_SM)
    style.configure("Header.TLabel", background=BG, foreground=TEXT,
                    font=FONT_H1)
    style.configure("Section.TLabel", background=PANEL, foreground=MUTED,
                    font=("Segoe UI Semibold", 8))

    style.configure("TButton", background=FIELD, foreground=TEXT,
                    borderwidth=0, padding=(12, 6), font=FONT)
    style.map("TButton",
              background=[("active", BORDER), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                    font=FONT_BOLD, padding=(14, 7))
    style.map("Accent.TButton",
              background=[("active", ACCENT_H), ("disabled", "#54382C")])

    for w in ("TCombobox", "TSpinbox", "TEntry"):
        style.configure(w, fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=FIELD, darkcolor=FIELD, insertcolor=TEXT,
                        padding=4)
        style.map(w, fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("!focus", FIELD)],
                  selectforeground=[("!focus", TEXT)])

    style.configure("Vertical.TScrollbar", background=FIELD,
                    troughcolor=PANEL, bordercolor=PANEL,
                    arrowcolor=MUTED)

    # Combobox dropdown list colors
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")


class Toggle(tk.Canvas):
    """Resolve-style pill toggle switch bound to a BooleanVar."""

    W, H = 42, 22

    def __init__(self, parent, variable, bg=PANEL):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.var = variable
        self.bind("<Button-1>", self._flip)
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _flip(self, _e):
        self.var.set(not self.var.get())

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        track = ACCENT if on else "#3C3C46"
        r = 9
        y1, y2 = 2, 2 + 2 * r
        self.create_oval(2, y1, 2 + 2 * r, y2, fill=track, outline=track)
        self.create_oval(self.W - 2 - 2 * r, y1, self.W - 2, y2,
                         fill=track, outline=track)
        self.create_rectangle(2 + r, y1, self.W - 2 - r, y2,
                              fill=track, outline=track)
        kx = self.W - 2 - 2 * r + 2 if on else 4
        self.create_oval(kx, 4, kx + 14, 18,
                         fill="#FFFFFF" if on else "#9A9AA4", outline="")


# ─── .env helper ────────────────────────────────────────────────────

def update_env_key(key: str, value: str):
    path = os.path.join(PROJECT_ROOT, ".env")
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines()
                     if l.strip() and not l.startswith(key + "=")]
    lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ[key] = value


# ─── Comment-preserving YAML I/O ────────────────────────────────────

def _yaml_rt():
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    return y


def read_raw_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return _yaml_rt().load(f)


def write_raw_config(path, data):
    with open(path, "w", encoding="utf-8") as f:
        _yaml_rt().dump(data, f)


# ─── Pipeline steps (non-interactive) ───────────────────────────────

def step_merge(cfg):
    r = cfg["_resolved"]
    videos = [v for v in r["videos"] if os.path.exists(v)]
    if not videos:
        raise RuntimeError(
            "No video clips found. Use 'Open input folder' and drop your "
            "footage there (named in playback order).")
    audio_cfg = cfg.get("audio") or {}
    lufs = audio_cfg.get("target_lufs") if audio_cfg.get("normalize") else None

    if r["audio_master"]:
        if not os.path.exists(r["audio_master"]):
            raise RuntimeError(f"Master audio not found: {r['audio_master']}")
        from core.audio_sync import sync_and_merge
        sync_and_merge(video_files=videos, master_audio=r["audio_master"],
                       output_path=r["synced"],
                       sync_method=audio_cfg.get("sync_method", "waveform"),
                       normalize_lufs=lufs)
    else:
        from core.audio_sync import merge_clips_only
        merge_clips_only(videos, r["synced"], normalize_lufs=lufs)


def step_transcribe(cfg):
    r = cfg["_resolved"]
    if not os.path.exists(r["synced"]):
        raise RuntimeError("Merged video not found. Run 'Merge clips' first.")
    from core.asr_whisperx import transcribe_files, segments_to_metadata

    model_size = (cfg.get("asr") or {}).get("model_size", "base")
    raw = transcribe_files([r["synced"]], model_size=model_size)
    segments = segments_to_metadata(raw)
    with open(r["transcript"], "w") as f:
        json.dump(segments, f, indent=2)
    with open(r["transcript"].replace(".json", "_raw.json"), "w") as f:
        json.dump(raw, f, indent=2)
    print(f"  Transcription complete: {len(segments)} segments")


def step_edl(cfg):
    r = cfg["_resolved"]
    if not os.path.exists(r["transcript"]):
        raise RuntimeError("Transcript not found. Run 'Transcribe' first.")
    with open(r["transcript"], "r") as f:
        segments = json.load(f)

    script_text = None
    if r["script"] and os.path.exists(r["script"]):
        with open(r["script"], "r", encoding="utf-8") as f:
            script_text = f.read()
        print(f"  Script reference loaded ({len(script_text)} chars)")

    from core.planner_llm import build_edl_with_llm
    from core.edl_schema import EDLPlan

    edl_json = build_edl_with_llm(
        segments=segments,
        target_minutes=cfg["edit_defaults"]["target_minutes"],
        structure=cfg["edit_defaults"]["keep_structure"],
        allow_zoom=cfg["edit_defaults"]["allow_zoom_ins"],
        allow_jump=cfg["edit_defaults"]["allow_jumpcuts"],
        captions=cfg["edit_defaults"]["captions"],
        cfg=cfg,
        script_reference=script_text,
    )
    plan = EDLPlan.model_validate(edl_json)
    print(f"  Validation PASSED\n  {plan.summary()}")
    with open(r["edl"], "w") as f:
        json.dump(edl_json, f, indent=2)
    print(f"  Saved: {r['edl']}")


def step_timeline(cfg):
    r = cfg["_resolved"]
    if not os.path.exists(r["edl"]):
        raise RuntimeError("EDL not found. Run 'Generate EDL' first.")
    if not os.path.exists(r["synced"]):
        raise RuntimeError("Merged video not found. Run 'Merge clips' first.")

    from core.resolve_driver import check_resolve_connection, build_timeline_from_edl
    if not check_resolve_connection():
        raise RuntimeError(
            "DaVinci Resolve is not reachable. Open Resolve and enable\n"
            "Preferences > System > General > External scripting = Local.")
    with open(r["edl"], "r") as f:
        edl = json.load(f)
    brand = cfg.get("brand", {}).get("name", "PixelSpryte")
    edl["title"] = brand.replace(" ", "_").replace("—", "-")
    build_timeline_from_edl(edl_plan=edl, source_video=r["synced"],
                            cfg=cfg, out_dir=r["output_dir"])


def step_render(cfg):
    r = cfg["_resolved"]
    from core.resolve_driver import check_resolve_connection, get_resolve, render_and_wait
    if not check_resolve_connection():
        raise RuntimeError("DaVinci Resolve is not reachable. Open Resolve first.")
    project = get_resolve().GetProjectManager().GetCurrentProject()
    if not project or not project.GetCurrentTimeline():
        raise RuntimeError("No timeline open. Run 'Build timeline' first.")
    brand = cfg.get("brand", {}).get("name", "PixelSpryte")
    name = brand.replace(" ", "_").replace("—", "-") + "_final"
    render_and_wait(project, r["output_dir"], name)


STEPS = [
    ("Merge clips", step_merge, "synced"),
    ("Transcribe", step_transcribe, "transcript"),
    ("Generate EDL", step_edl, "edl"),
    ("Build timeline", step_timeline, None),
    ("Render MP4", step_render, "render"),
]


class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(("log", text))

    def flush(self):
        pass


# ─── Settings dialog ────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("PixelSpryte — Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, style="Panel.TFrame", padding=18)
        frame.pack(fill="both", expand=True, padx=14, pady=14)

        ttk.Label(frame, text="LLM API", style="Section.TLabel").pack(anchor="w")

        row1 = ttk.Frame(frame, style="Panel.TFrame")
        row1.pack(fill="x", pady=(6, 2))
        ttk.Label(row1, text="DeepSeek API key", style="Panel.TLabel",
                  width=18).pack(side="left")
        self.key_var = tk.StringVar(value=os.environ.get("DEEPSEEK_API_KEY", ""))
        self.key_entry = ttk.Entry(row1, textvariable=self.key_var,
                                   show="•", width=38)
        self.key_entry.pack(side="left", padx=(0, 6))
        self.show_var = tk.BooleanVar(value=False)
        show_btn = ttk.Button(row1, text="Show", width=6, command=self._toggle_show)
        show_btn.pack(side="left")

        row2 = ttk.Frame(frame, style="Panel.TFrame")
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="API endpoint", style="Panel.TLabel",
                  width=18).pack(side="left")
        self.url_var = tk.StringVar(value="https://api.deepseek.com/v1")
        ttk.Entry(row2, textvariable=self.url_var, width=46).pack(side="left")

        ttk.Label(frame,
                  text="The key is stored in the local .env file only — "
                       "never inside config files.",
                  style="Muted.TLabel", wraplength=420).pack(anchor="w", pady=(6, 10))

        row3 = ttk.Frame(frame, style="Panel.TFrame")
        row3.pack(fill="x", pady=(2, 0))
        ttk.Button(row3, text="Test connection",
                   command=self._test).pack(side="left")
        self.test_label = ttk.Label(row3, text="", style="Panel.TLabel")
        self.test_label.pack(side="left", padx=10)

        bar = ttk.Frame(frame, style="Panel.TFrame")
        bar.pack(fill="x", pady=(16, 0))
        ttk.Button(bar, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(bar, text="Cancel",
                   command=self.destroy).pack(side="right", padx=8)

    def _toggle_show(self):
        current = self.key_entry.cget("show")
        self.key_entry.config(show="" if current else "•")

    def _test(self):
        self.test_label.config(text="Testing...", foreground=MUTED)

        def work():
            try:
                import core.planner_llm  # truststore + dotenv side effects
                import requests
                r = requests.get(
                    self.url_var.get().rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {self.key_var.get().strip()}"},
                    timeout=20)
                if r.status_code == 200:
                    models = [m.get("id") for m in r.json().get("data", [])]
                    msg = ("OK — models: " + ", ".join(models), GREEN)
                elif r.status_code == 401:
                    msg = ("Invalid API key (401)", RED)
                else:
                    msg = (f"HTTP {r.status_code}", RED)
            except Exception as e:
                msg = (f"Failed: {str(e)[:60]}", RED)
            self.after(0, lambda: self.test_label.config(
                text=msg[0], foreground=msg[1]))

        threading.Thread(target=work, daemon=True).start()

    def _save(self):
        key = self.key_var.get().strip()
        if key:
            update_env_key("DEEPSEEK_API_KEY", key)
        url = self.url_var.get().strip()
        if url:
            for cfg_file in PROFILES.values():
                try:
                    data = read_raw_config(cfg_file)
                    if "llm" in data:
                        data["llm"]["api_url"] = url
                        write_raw_config(cfg_file, data)
                except Exception:
                    pass
        self.destroy()


# ─── Main window ────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PixelSpryte")
        self.geometry("1100x800")
        self.minsize(1000, 740)
        self.configure(bg=BG)
        try:
            self.iconbitmap(os.path.join(PROJECT_ROOT, "assets", "pixelspryte.ico"))
        except Exception:
            pass

        apply_theme(self)

        self.log_queue = queue.Queue()
        self.worker = None
        self.cfg = None
        self.profile_buttons = {}
        self._broll_prev = "library"

        self._build_layout()
        self._select_profile(list(PROFILES)[0])
        self.after(100, self._poll)

    # -- layout ------------------------------------------------------

    def _build_layout(self):
        # Header
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(header, text="◆", fg=ACCENT, bg=BG,
                 font=("Segoe UI", 16)).pack(side="left")
        tk.Label(header, text=" PixelSpryte", fg=TEXT, bg=BG,
                 font=FONT_H1).pack(side="left")
        tk.Label(header, text="  AI edit pipeline for DaVinci Resolve",
                 fg=MUTED, bg=BG, font=FONT).pack(side="left", pady=(3, 0))
        ttk.Button(header, text="⚙  Settings",
                   command=lambda: SettingsDialog(self)).pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(4, 14))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # LEFT column
        left = tk.Frame(body, bg=BG, width=344)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        left.pack_propagate(False)

        # Profile card
        pcard = tk.Frame(left, bg=PANEL, padx=12, pady=10)
        pcard.pack(fill="x")
        ttk.Label(pcard, text="PROFILE", style="Section.TLabel").pack(anchor="w")
        for name in PROFILES:
            btn = tk.Label(pcard, text=f"  {name}", anchor="w", bg=PANEL,
                           fg=TEXT, font=FONT, padx=8, pady=6, cursor="hand2")
            btn.pack(fill="x", pady=1)
            btn.bind("<Button-1>", lambda e, n=name: self._select_profile(n))
            self.profile_buttons[name] = btn

        # Settings card
        scard = tk.Frame(left, bg=PANEL, padx=12, pady=10)
        scard.pack(fill="x", pady=(10, 0))
        ttk.Label(scard, text="EDIT SETTINGS", style="Section.TLabel").pack(anchor="w", pady=(0, 4))

        def combo_row(label, var, values, width=18):
            f = tk.Frame(scard, bg=PANEL)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, bg=PANEL, fg=TEXT, font=FONT,
                     width=14, anchor="w").pack(side="left")
            c = ttk.Combobox(f, textvariable=var, values=values,
                             state="readonly", width=width)
            c.pack(side="left")
            return c

        self.model_var = tk.StringVar()
        combo_row("LLM model", self.model_var, MODELS)
        self.target_var = tk.IntVar(value=10)
        f = tk.Frame(scard, bg=PANEL)
        f.pack(fill="x", pady=2)
        tk.Label(f, text="Target minutes", bg=PANEL, fg=TEXT, font=FONT,
                 width=14, anchor="w").pack(side="left")
        ttk.Spinbox(f, from_=1, to=120, textvariable=self.target_var,
                    width=6).pack(side="left")
        self.style_var = tk.StringVar()
        combo_row("Edit style", self.style_var, STYLES)
        self.captions_var = tk.StringVar()
        combo_row("Captions", self.captions_var, CAPTIONS)
        self.whisper_var = tk.StringVar(value="base")
        combo_row("Whisper model", self.whisper_var, WHISPER_SIZES)

        def toggle_row(label, var, hint=""):
            f = tk.Frame(scard, bg=PANEL)
            f.pack(fill="x", pady=3)
            Toggle(f, var).pack(side="left")
            tk.Label(f, text=" " + label, bg=PANEL, fg=TEXT,
                     font=FONT).pack(side="left")
            if hint:
                tk.Label(f, text="  " + hint, bg=PANEL, fg=MUTED,
                         font=FONT_SM).pack(side="left")

        ttk.Label(scard, text="TOGGLES", style="Section.TLabel").pack(anchor="w", pady=(8, 2))
        self.jumpcuts_var = tk.BooleanVar()
        toggle_row("Jump cuts", self.jumpcuts_var, "cut dead air & flubs")
        self.zoom_var = tk.BooleanVar()
        toggle_row("Punch-ins", self.zoom_var, "AI zooms on key moments")
        self.broll_var = tk.BooleanVar()
        toggle_row("B-roll inserts", self.broll_var, "from tagged library")
        self.normalize_var = tk.BooleanVar()
        toggle_row("Normalize audio", self.normalize_var, "-14 LUFS")
        self.ai_captions_var = tk.BooleanVar()
        toggle_row("Resolve AI captions", self.ai_captions_var, "burned-in")

        # LOOK section (LUT)
        ttk.Label(scard, text="LOOK", style="Section.TLabel").pack(anchor="w", pady=(8, 2))
        lutf = tk.Frame(scard, bg=PANEL)
        lutf.pack(fill="x", pady=2)
        tk.Label(lutf, text="LUT (.cube)", bg=PANEL, fg=TEXT, font=FONT,
                 width=10, anchor="w").pack(side="left")
        self.lut_var = tk.StringVar()
        ttk.Entry(lutf, textvariable=self.lut_var, width=14).pack(
            side="left", padx=(0, 4))
        ttk.Button(lutf, text="Browse", width=7,
                   command=self._browse_lut).pack(side="left")
        ttk.Button(lutf, text="✕", width=2,
                   command=lambda: self.lut_var.set("")).pack(side="left", padx=(2, 0))

        ttk.Button(scard, text="Save settings", style="Accent.TButton",
                   command=self._save_settings).pack(anchor="e", pady=(10, 2))

        # RIGHT column
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        pipe = tk.Frame(right, bg=PANEL, padx=14, pady=12)
        pipe.grid(row=0, column=0, sticky="ew")
        top = tk.Frame(pipe, bg=PANEL)
        top.pack(fill="x")
        ttk.Label(top, text="PIPELINE", style="Section.TLabel").pack(side="left")
        self.busy_label = tk.Label(top, text="", bg=PANEL, fg=ACCENT, font=FONT_SM)
        self.busy_label.pack(side="right")
        ttk.Button(top, text="Refresh", width=8,
                   command=self._refresh_status).pack(side="right", padx=8)

        self.step_buttons = []
        self.step_status = []
        for i, (name, fn, _art) in enumerate(STEPS):
            rowf = tk.Frame(pipe, bg=PANEL)
            rowf.pack(fill="x", pady=3)
            badge = tk.Canvas(rowf, width=24, height=24, bg=PANEL,
                              highlightthickness=0)
            badge.create_oval(2, 2, 22, 22, fill=FIELD, outline=BORDER)
            badge.create_text(12, 12, text=str(i + 1), fill=MUTED, font=FONT_SM)
            badge.pack(side="left")
            btn = ttk.Button(rowf, text=name, width=18,
                             command=lambda f=fn, n=name: self._run_step(n, f))
            btn.pack(side="left", padx=(8, 10))
            status = tk.Label(rowf, text="", bg=PANEL, fg=MUTED, font=FONT_SM,
                              anchor="w")
            status.pack(side="left", fill="x", expand=True)
            self.step_buttons.append(btn)
            self.step_status.append(status)

        # Folders bar
        fcard = tk.Frame(right, bg=PANEL, padx=14, pady=8)
        fcard.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(fcard, text="FOLDERS", style="Section.TLabel").pack(side="left")
        ttk.Button(fcard, text="Input", width=8,
                   command=self._open_inputs).pack(side="left", padx=(12, 4))
        ttk.Button(fcard, text="Output", width=8,
                   command=self._open_outputs).pack(side="left", padx=4)
        ttk.Button(fcard, text="B-roll library", width=12,
                   command=self._open_broll).pack(side="left", padx=4)
        self.inputs_label = tk.Label(fcard, text="", bg=PANEL, fg=MUTED,
                                     font=FONT_SM, anchor="e")
        self.inputs_label.pack(side="right")

        logf = tk.Frame(right, bg=PANEL, padx=8, pady=8)
        logf.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(1, weight=1)
        ttk.Label(logf, text="LOG", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        self.log = tk.Text(logf, wrap="word", state="disabled", font=FONT_MONO,
                           bg=CONSOLE, fg="#C9CED6", insertbackground=TEXT,
                           relief="flat", padx=8, pady=6)
        self.log.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(logf, command=self.log.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.tag_configure("err", foreground=RED)
        self.log.tag_configure("ok", foreground=GREEN)

    # -- profile / settings -------------------------------------------

    def _config_path(self):
        return PROFILES[self.active_profile]

    def _select_profile(self, name):
        if self.worker and self.worker.is_alive():
            return
        self.active_profile = name
        for n, btn in self.profile_buttons.items():
            if n == name:
                btn.config(bg=FIELD, fg="#FFFFFF",
                           font=FONT_BOLD)
            else:
                btn.config(bg=PANEL, fg=MUTED, font=FONT)
        try:
            self.cfg = load_config(self._config_path())
        except SystemExit:
            messagebox.showerror("Config missing", f"{self._config_path()} not found")
            return
        ed = self.cfg.get("edit_defaults", {})
        self.model_var.set(self.cfg.get("llm", {}).get("model_name", MODELS[0]))
        self.target_var.set(ed.get("target_minutes", 10))
        self.style_var.set(ed.get("style", "retention"))
        self.captions_var.set(ed.get("captions", "none"))
        self.whisper_var.set((self.cfg.get("asr") or {}).get("model_size", "base"))
        self.jumpcuts_var.set(bool(ed.get("allow_jumpcuts", True)))
        self.zoom_var.set(bool(ed.get("allow_zoom_ins", False)))
        broll = ed.get("broll", "none")
        self.broll_var.set(broll != "none")
        if broll != "none":
            self._broll_prev = broll
        self.normalize_var.set(bool((self.cfg.get("audio") or {}).get("normalize", True)))
        self.ai_captions_var.set(bool((self.cfg.get("subtitles") or {}).get("resolve_ai", False)))
        self.lut_var.set((self.cfg.get("color") or {}).get("lut_path", "") or "")
        self._refresh_status()
        self._log_write(f"\n=== Profile: {name} ({self._config_path()}) ===\n")

    def _browse_lut(self):
        path = filedialog.askopenfilename(
            title="Choose a LUT", filetypes=[("LUT files", "*.cube"), ("All", "*.*")])
        if path:
            self.lut_var.set(path)

    def _save_settings(self):
        path = self._config_path()
        try:
            data = read_raw_config(path)
            data["llm"]["model_name"] = self.model_var.get()
            ed = data["edit_defaults"]
            ed["target_minutes"] = int(self.target_var.get())
            ed["style"] = self.style_var.get()
            ed["captions"] = self.captions_var.get()
            ed["allow_jumpcuts"] = bool(self.jumpcuts_var.get())
            ed["allow_zoom_ins"] = bool(self.zoom_var.get())
            ed["broll"] = (self._broll_prev if self.broll_var.get() else "none")
            if "audio" in data:
                data["audio"]["normalize"] = bool(self.normalize_var.get())
            for section, key, value in [
                ("asr", "model_size", self.whisper_var.get()),
                ("subtitles", "resolve_ai", bool(self.ai_captions_var.get())),
                ("color", "lut_path", self.lut_var.get().strip()),
            ]:
                if section not in data:
                    data[section] = {}
                data[section][key] = value
            write_raw_config(path, data)
            self.cfg = load_config(path)
            self._log_write(f"  Settings saved to {path}\n", "ok")
            self._refresh_status()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    # -- status --------------------------------------------------------

    def _render_path(self):
        r = self.cfg["_resolved"]
        brand = self.cfg.get("brand", {}).get("name", "PixelSpryte")
        return os.path.join(r["output_dir"],
                            brand.replace(" ", "_").replace("—", "-") + "_final.mp4")

    def _refresh_status(self):
        if not self.cfg:
            return
        r = self.cfg["_resolved"]
        artifacts = [r["synced"], r["transcript"], r["edl"], None,
                     self._render_path()]
        for label, art in zip(self.step_status, artifacts):
            if art is None:
                label.config(text="needs Resolve open", fg=MUTED)
            elif os.path.exists(art):
                mb = os.path.getsize(art) / (1024 * 1024)
                label.config(text=f"✓ {os.path.basename(art)} ({mb:.1f} MB)",
                             fg=GREEN)
            else:
                label.config(text="not yet", fg=MUTED)

        n = len([v for v in r["videos"] if os.path.exists(v)])
        master = os.path.basename(r["audio_master"]) if r["audio_master"] else "camera audio"
        self.inputs_label.config(
            text=f"Clips found: {n}   Audio: {master}")

    def _open_inputs(self):
        d = os.path.join(PROJECT_ROOT,
                         (self.cfg.get("paths", {}) or {}).get("inputs_dir", "inputs"))
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def _open_outputs(self):
        d = self.cfg["_resolved"]["output_dir"]
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def _open_broll(self):
        d = os.path.join(PROJECT_ROOT,
                         (self.cfg.get("paths", {}) or {}).get("broll_dir", "assets/broll"))
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    # -- pipeline execution --------------------------------------------

    def _run_step(self, name, fn):
        if self.worker and self.worker.is_alive():
            return
        self.cfg = load_config(self._config_path())

        artifact_map = {"Merge clips": "synced", "Transcribe": "transcript",
                        "Generate EDL": "edl"}
        art = None
        if name in artifact_map:
            art = self.cfg["_resolved"][artifact_map[name]]
        elif name == "Render MP4":
            art = self._render_path()
        if art and os.path.exists(art):
            if not messagebox.askyesno(
                    "Overwrite?",
                    f"{os.path.basename(art)} already exists.\nRegenerate it?"):
                return
            os.remove(art)

        self._set_busy(True, name)
        self._log_write(f"\n--- {name} ---\n")

        def work():
            writer = QueueWriter(self.log_queue)
            try:
                with contextlib.redirect_stdout(writer):
                    fn(self.cfg)
                self.log_queue.put(("ok", f"\n  {name}: DONE\n"))
            except Exception as e:
                self.log_queue.put(("err", f"\n  ERROR: {e}\n"))
                self.log_queue.put(("err", traceback.format_exc(limit=3)))
            finally:
                self.log_queue.put(("done", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _set_busy(self, busy, name=""):
        state = "disabled" if busy else "normal"
        for b in self.step_buttons:
            b.config(state=state)
        self.busy_label.config(text=f"●  Running: {name}" if busy else "")

    def _poll(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._log_write(payload)
                elif kind == "ok":
                    self._log_write(payload, "ok")
                elif kind == "err":
                    self._log_write(payload, "err")
                elif kind == "done":
                    self._set_busy(False)
                    self._refresh_status()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _log_write(self, text, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()
