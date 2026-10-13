"""
MOV to MP4 Converter - Desktop GUI Application
A polished, modern desktop application designed for fast, lossless
remuxing and universal H.264/AAC video conversion.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

# Enable DPI Awareness on Windows so fonts and widgets render crisply
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

from converter import (
    FFMPEG_BIN,
    FFPROBE_BIN,
    ConversionOptions,
    ProgressState,
    VideoInfo,
    convert_video,
    find_binary,
    probe_video,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mov2mp4.gui")

# Config file location in user app workspace
CONFIG_FILE = Path(__file__).parent / "config.json"

# Modern Slate / Midnight Dark Theme
PALETTE = {
    "bg_main": "#0f172a",          # Deep slate
    "bg_surface": "#1e293b",       # Card surface
    "bg_input": "#334155",         # Inputs and selections
    "border": "#475569",           # Subtle border
    "accent": "#6366f1",           # Indigo accent
    "accent_hover": "#4f46e5",     # Darker indigo hover
    "accent_active": "#4338ca",
    "success": "#10b981",          # Emerald
    "warning": "#f59e0b",          # Amber
    "danger": "#ef4444",           # Rose/red
    "danger_hover": "#dc2626",
    "text_primary": "#f8fafc",     # High-contrast white
    "text_secondary": "#cbd5e1",   # Medium slate
    "text_muted": "#94a3b8",       # Muted slate
    "list_select": "#3730a3",      # Treeview row highlight
}


class Tooltip:
    """Lightweight tooltip widget that appears on mouse hover."""
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 400):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tip_window: Optional[tk.Toplevel] = None
        self.schedule_id: Optional[str] = None

        self.widget.bind("<Enter>", self._on_enter)
        self.widget.bind("<Leave>", self._on_leave)
        self.widget.bind("<ButtonPress>", self._on_leave)

    def _on_enter(self, event=None):
        self._cancel_timer()
        self.schedule_id = self.widget.after(self.delay_ms, self._show_tip)

    def _on_leave(self, event=None):
        self._cancel_timer()
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None

    def _cancel_timer(self):
        if self.schedule_id:
            self.widget.after_cancel(self.schedule_id)
            self.schedule_id = None

    def _show_tip(self):
        if self.tip_window or not self.text:
            return

        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        self.tip_window.attributes("-topmost", True)

        frame = tk.Frame(self.tip_window, bg=PALETTE["border"], padx=1, pady=1)
        frame.pack()
        lbl = tk.Label(
            frame,
            text=self.text,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 8),
            wraplength=280,
            justify="left",
            padx=8,
            pady=4
        )
        lbl.pack()


class ConfigManager:
    """Manages persistent user preferences in config.json."""
    DEFAULT_CONFIG = {
        "mode": "Universal Re-encode (H.264 + AAC)",
        "quality": "Balanced Quality (CRF 22)",
        "resolution": "Keep Original",
        "dest_mode": "same",
        "custom_dest_dir": "",
        "play_sound_on_complete": True
    }

    @classmethod
    def load(cls) -> dict:
        if CONFIG_FILE.is_file():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = dict(cls.DEFAULT_CONFIG)
                    cfg.update(data)
                    return cfg
            except Exception as e:
                logger.warning("Could not read config.json, using defaults: %s", e)
        return dict(cls.DEFAULT_CONFIG)

    @classmethod
    def save(cls, data: dict):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Could not persist config.json: %s", e)


class QueueItem:
    """Represents a single video queued for conversion."""
    def __init__(self, file_path: Path):
        self.path = file_path
        self.status = "Pending"
        self.info: Optional[VideoInfo] = None
        self.output_path: Optional[Path] = None
        self.error_detail: str = ""

    @property
    def filename(self) -> str:
        return self.path.name


class ConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MOV to MP4 Converter")
        self.root.geometry("960x740")
        self.root.minsize(820, 640)
        self.root.configure(bg=PALETTE["bg_main"])

        # Load saved user settings
        self.config = ConfigManager.load()

        # Operational state
        self.queue_items: List[QueueItem] = []
        self.is_converting = False
        self.cancel_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_events: queue.Queue = queue.Queue()
        self.last_destination_folder: Optional[Path] = None

        self._setup_typography()
        self._setup_ttk_styles()
        self._build_interface()
        self._bind_keyboard_shortcuts()
        self._verify_ffmpeg_installation()

        # Start periodic UI dispatcher
        self.root.after(80, self._process_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _setup_typography(self):
        self.font_title = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        self.font_subtitle = tkfont.Font(family="Segoe UI", size=9)
        self.font_bold = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.font_regular = tkfont.Font(family="Segoe UI", size=9)
        self.font_small = tkfont.Font(family="Segoe UI", size=8)
        self.font_badge = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.font_stat = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.font_mono = tkfont.Font(family="Consolas", size=8)

    def _setup_ttk_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background=PALETTE["bg_main"])
        style.configure("Card.TFrame", background=PALETTE["bg_surface"], relief="flat")

        # Treeview styling
        style.configure(
            "Custom.Treeview",
            background=PALETTE["bg_input"],
            foreground=PALETTE["text_primary"],
            fieldbackground=PALETTE["bg_input"],
            rowheight=30,
            font=self.font_regular,
            borderwidth=0,
            relief="flat"
        )
        style.map(
            "Custom.Treeview",
            background=[("selected", PALETTE["list_select"])],
            foreground=[("selected", PALETTE["text_primary"])]
        )
        style.configure(
            "Custom.Treeview.Heading",
            background=PALETTE["bg_surface"],
            foreground=PALETTE["text_secondary"],
            font=self.font_bold,
            relief="flat",
            padding=(8, 6)
        )
        style.map(
            "Custom.Treeview.Heading",
            background=[("active", PALETTE["border"])],
            foreground=[("active", PALETTE["text_primary"])]
        )

        # Progress bar
        style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor=PALETTE["bg_input"],
            background=PALETTE["accent"],
            darkcolor=PALETTE["accent"],
            lightcolor=PALETTE["accent"],
            bordercolor=PALETTE["border"],
            thickness=9
        )

        # Combobox styling
        style.configure(
            "Custom.TCombobox",
            fieldbackground=PALETTE["bg_input"],
            background=PALETTE["bg_surface"],
            foreground=PALETTE["text_primary"],
            arrowcolor=PALETTE["text_primary"],
            bordercolor=PALETTE["border"],
            darkcolor=PALETTE["bg_input"],
            lightcolor=PALETTE["bg_input"],
            padding=5
        )
        style.map(
            "Custom.TCombobox",
            fieldbackground=[("readonly", PALETTE["bg_input"])],
            foreground=[("readonly", PALETTE["text_primary"])],
            selectbackground=[("readonly", PALETTE["accent"])]
        )

    def _build_interface(self):
        container = tk.Frame(self.root, bg=PALETTE["bg_main"], padx=18, pady=14)
        container.pack(fill="both", expand=True)

        # ---------------- 1. Top Header ----------------
        header_bar = tk.Frame(container, bg=PALETTE["bg_main"])
        header_bar.pack(fill="x", pady=(0, 10))

        title_box = tk.Frame(header_bar, bg=PALETTE["bg_main"])
        title_box.pack(side="left")

        icon_lbl = tk.Label(
            title_box,
            text="🎥",
            font=("Segoe UI Emoji", 20),
            bg=PALETTE["bg_main"],
            fg=PALETTE["accent"]
        )
        icon_lbl.pack(side="left", padx=(0, 10))

        text_col = tk.Frame(title_box, bg=PALETTE["bg_main"])
        text_col.pack(side="left")

        lbl_app_name = tk.Label(
            text_col,
            text="MOV to MP4 Converter",
            font=self.font_title,
            bg=PALETTE["bg_main"],
            fg=PALETTE["text_primary"]
        )
        lbl_app_name.pack(anchor="w")

        lbl_desc = tk.Label(
            text_col,
            text="Fast lossless stream copy & universal H.264/AAC re-encoding for Apple QuickTime videos",
            font=self.font_subtitle,
            bg=PALETTE["bg_main"],
            fg=PALETTE["text_muted"]
        )
        lbl_desc.pack(anchor="w")

        # System FFmpeg health pill
        self.badge_ffmpeg = tk.Label(
            header_bar,
            text="Checking FFmpeg...",
            font=self.font_badge,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_muted"],
            padx=10,
            pady=4
        )
        self.badge_ffmpeg.pack(side="right", pady=4)

        # ---------------- 2. Queue Section ----------------
        queue_card = tk.Frame(
            container,
            bg=PALETTE["bg_surface"],
            padx=14,
            pady=12,
            highlightbackground=PALETTE["border"],
            highlightthickness=1
        )
        queue_card.pack(fill="both", expand=True, pady=(0, 10))

        # Toolbar
        toolbar = tk.Frame(queue_card, bg=PALETTE["bg_surface"])
        toolbar.pack(fill="x", pady=(0, 8))

        lbl_q_title = tk.Label(
            toolbar,
            text="Conversion Queue",
            font=self.font_bold,
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_primary"]
        )
        lbl_q_title.pack(side="left")

        self.lbl_q_stats = tk.Label(
            toolbar,
            text="(0 files)",
            font=self.font_regular,
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_muted"]
        )
        self.lbl_q_stats.pack(side="left", padx=(6, 0))

        # Action buttons
        btn_clear = tk.Button(
            toolbar,
            text="Clear",
            font=self.font_small,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_secondary"],
            activebackground=PALETTE["danger"],
            activeforeground=PALETTE["text_primary"],
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.clear_queue
        )
        btn_clear.pack(side="right", padx=(4, 0))
        Tooltip(btn_clear, "Remove all files from the queue (Ctrl+L)")

        btn_remove = tk.Button(
            toolbar,
            text="Remove",
            font=self.font_small,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_secondary"],
            activebackground=PALETTE["border"],
            activeforeground=PALETTE["text_primary"],
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.remove_selected
        )
        btn_remove.pack(side="right", padx=(4, 0))
        Tooltip(btn_remove, "Remove selected file(s) from the list (Delete)")

        btn_add_folder = tk.Button(
            toolbar,
            text="📁 Add Folder",
            font=self.font_small,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["border"],
            activeforeground=PALETTE["text_primary"],
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.browse_folder
        )
        btn_add_folder.pack(side="right", padx=(4, 0))
        Tooltip(btn_add_folder, "Scan and queue all .mov videos inside a folder")

        btn_add_files = tk.Button(
            toolbar,
            text="➕ Add .MOV Files",
            font=self.font_small,
            bg=PALETTE["accent"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["accent_hover"],
            activeforeground=PALETTE["text_primary"],
            relief="flat",
            padx=14,
            pady=3,
            cursor="hand2",
            command=self.browse_files
        )
        btn_add_files.pack(side="right", padx=(4, 0))
        Tooltip(btn_add_files, "Select one or more .mov files to convert (Ctrl+O)")

        # Treeview table
        tree_box = tk.Frame(queue_card, bg=PALETTE["bg_input"])
        tree_box.pack(fill="both", expand=True)

        columns = ("name", "resolution", "duration", "codec", "size", "status")
        self.tree = ttk.Treeview(
            tree_box,
            columns=columns,
            show="headings",
            style="Custom.Treeview",
            selectmode="extended"
        )

        self.tree.heading("name", text="Video File")
        self.tree.heading("resolution", text="Resolution")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("codec", text="Codec")
        self.tree.heading("size", text="File Size")
        self.tree.heading("status", text="Status")

        self.tree.column("name", width=280, anchor="w")
        self.tree.column("resolution", width=100, anchor="center")
        self.tree.column("duration", width=80, anchor="center")
        self.tree.column("codec", width=95, anchor="center")
        self.tree.column("size", width=85, anchor="center")
        self.tree.column("status", width=125, anchor="center")

        tree_vbar = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_vbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        tree_vbar.pack(side="right", fill="y")

        # Double click & right click bindings
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # Context Menu
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=PALETTE["bg_surface"], fg=PALETTE["text_primary"], activebackground=PALETTE["accent"])
        self.context_menu.add_command(label="▶ Open / Play Video", command=self._context_play_video)
        self.context_menu.add_command(label="📁 Show in File Explorer", command=self._context_show_in_folder)
        self.context_menu.add_command(label="ℹ View Video Details", command=self._context_view_details)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="❌ Remove from List", command=self.remove_selected)
        self.context_menu.add_command(label="🧹 Clear All", command=self.clear_queue)

        # Empty state prompt
        self.lbl_empty_banner = tk.Label(
            tree_box,
            text="No files added yet.\nClick '+ Add .MOV Files' or press Ctrl+O to begin.",
            font=self.font_regular,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_muted"],
            justify="center"
        )
        self.lbl_empty_banner.place(relx=0.5, rely=0.5, anchor="center")

        # ---------------- 3. Settings Card ----------------
        settings_card = tk.Frame(
            container,
            bg=PALETTE["bg_surface"],
            padx=14,
            pady=10,
            highlightbackground=PALETTE["border"],
            highlightthickness=1
        )
        settings_card.pack(fill="x", pady=(0, 10))

        lbl_s_title = tk.Label(
            settings_card,
            text="Settings & Quality",
            font=self.font_bold,
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_primary"]
        )
        lbl_s_title.pack(anchor="w", pady=(0, 6))

        row1 = tk.Frame(settings_card, bg=PALETTE["bg_surface"])
        row1.pack(fill="x", pady=(0, 6))

        # Mode
        col_m = tk.Frame(row1, bg=PALETTE["bg_surface"])
        col_m.pack(side="left", fill="x", expand=True, padx=(0, 10))
        lbl_m = tk.Label(col_m, text="Conversion Mode:", font=self.font_small, bg=PALETTE["bg_surface"], fg=PALETTE["text_secondary"])
        lbl_m.pack(anchor="w")
        self.var_mode = tk.StringVar(value=self.config.get("mode"))
        self.combo_mode = ttk.Combobox(
            col_m,
            textvariable=self.var_mode,
            values=[
                "Universal Re-encode (H.264 + AAC)",
                "Ultra-Fast Remux (Stream Copy)"
            ],
            state="readonly",
            style="Custom.TCombobox"
        )
        self.combo_mode.pack(fill="x", pady=(3, 0))
        self.combo_mode.bind("<<ComboboxSelected>>", self._on_mode_toggled)
        Tooltip(self.combo_mode, "Universal re-encodes for total compatibility; Ultra-Fast stream copies without quality loss in 1-2s.")

        # Quality
        self.col_q = tk.Frame(row1, bg=PALETTE["bg_surface"])
        self.col_q.pack(side="left", fill="x", expand=True, padx=(0, 10))
        lbl_q = tk.Label(self.col_q, text="Quality Preset:", font=self.font_small, bg=PALETTE["bg_surface"], fg=PALETTE["text_secondary"])
        lbl_q.pack(anchor="w")
        self.var_quality = tk.StringVar(value=self.config.get("quality"))
        self.combo_quality = ttk.Combobox(
            self.col_q,
            textvariable=self.var_quality,
            values=[
                "High Quality (CRF 18 - Near Lossless)",
                "Balanced Quality (CRF 22 - Recommended)",
                "Smaller File Size (CRF 28 - Web Friendly)"
            ],
            state="readonly",
            style="Custom.TCombobox"
        )
        self.combo_quality.pack(fill="x", pady=(3, 0))
        Tooltip(self.combo_quality, "Lower CRF values produce higher visual fidelity at the expense of slightly larger file size.")

        # Resolution
        self.col_r = tk.Frame(row1, bg=PALETTE["bg_surface"])
        self.col_r.pack(side="left", fill="x", expand=True)
        lbl_r = tk.Label(self.col_r, text="Resolution:", font=self.font_small, bg=PALETTE["bg_surface"], fg=PALETTE["text_secondary"])
        lbl_r.pack(anchor="w")
        self.var_res = tk.StringVar(value=self.config.get("resolution"))
        self.combo_res = ttk.Combobox(
            self.col_r,
            textvariable=self.var_res,
            values=[
                "Keep Original",
                "1080p (1920x1080)",
                "720p (1280x720)",
                "480p (854x480)"
            ],
            state="readonly",
            style="Custom.TCombobox"
        )
        self.combo_res.pack(fill="x", pady=(3, 0))

        # Output Directory row
        row2 = tk.Frame(settings_card, bg=PALETTE["bg_surface"])
        row2.pack(fill="x", pady=(6, 0))

        tk.Label(row2, text="Destination:", font=self.font_small, bg=PALETTE["bg_surface"], fg=PALETTE["text_secondary"]).pack(side="left")

        self.var_dest_mode = tk.StringVar(value=self.config.get("dest_mode", "same"))
        rb_same = tk.Radiobutton(
            row2,
            text="Same directory as source video",
            variable=self.var_dest_mode,
            value="same",
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_primary"],
            selectcolor=PALETTE["bg_input"],
            activebackground=PALETTE["bg_surface"],
            activeforeground=PALETTE["text_primary"],
            command=self._on_dest_mode_changed
        )
        rb_same.pack(side="left", padx=(10, 8))

        rb_custom = tk.Radiobutton(
            row2,
            text="Custom folder:",
            variable=self.var_dest_mode,
            value="custom",
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_primary"],
            selectcolor=PALETTE["bg_input"],
            activebackground=PALETTE["bg_surface"],
            activeforeground=PALETTE["text_primary"],
            command=self._on_dest_mode_changed
        )
        rb_custom.pack(side="left")

        self.var_custom_dest = tk.StringVar(value=self.config.get("custom_dest_dir", ""))
        self.entry_dest = tk.Entry(
            row2,
            textvariable=self.var_custom_dest,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_primary"],
            insertbackground=PALETTE["text_primary"],
            relief="flat",
            state="normal" if self.var_dest_mode.get() == "custom" else "disabled"
        )
        self.entry_dest.pack(side="left", padx=(6, 6), fill="x", expand=True)

        self.btn_browse_dest = tk.Button(
            row2,
            text="Browse...",
            font=self.font_small,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_primary"],
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2",
            state="normal" if self.var_dest_mode.get() == "custom" else "disabled",
            command=self.browse_custom_dest
        )
        self.btn_browse_dest.pack(side="left")

        # ---------------- 4. Progress & Control Bar ----------------
        control_card = tk.Frame(
            container,
            bg=PALETTE["bg_surface"],
            padx=14,
            pady=12,
            highlightbackground=PALETTE["border"],
            highlightthickness=1
        )
        control_card.pack(fill="x", pady=(0, 6))

        # Status text row
        stat_bar = tk.Frame(control_card, bg=PALETTE["bg_surface"])
        stat_bar.pack(fill="x", pady=(0, 6))

        self.lbl_status_lead = tk.Label(
            stat_bar,
            text="Ready",
            font=self.font_bold,
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_primary"]
        )
        self.lbl_status_lead.pack(side="left")

        self.lbl_pct = tk.Label(
            stat_bar,
            text="0%",
            font=self.font_stat,
            bg=PALETTE["bg_surface"],
            fg=PALETTE["accent"]
        )
        self.lbl_pct.pack(side="right")

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            control_card,
            orient="horizontal",
            mode="determinate",
            style="Custom.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill="x", pady=(0, 8))

        # Metrics telemetry strip
        strip = tk.Frame(control_card, bg=PALETTE["bg_input"], padx=10, pady=5)
        strip.pack(fill="x", pady=(0, 10))

        self.lbl_m_speed = tk.Label(strip, text="Speed: --", font=self.font_small, bg=PALETTE["bg_input"], fg=PALETTE["text_secondary"])
        self.lbl_m_speed.pack(side="left", expand=True)

        self.lbl_m_fps = tk.Label(strip, text="FPS: --", font=self.font_small, bg=PALETTE["bg_input"], fg=PALETTE["text_secondary"])
        self.lbl_m_fps.pack(side="left", expand=True)

        self.lbl_m_elapsed = tk.Label(strip, text="Elapsed: 00:00", font=self.font_small, bg=PALETTE["bg_input"], fg=PALETTE["text_secondary"])
        self.lbl_m_elapsed.pack(side="left", expand=True)

        self.lbl_m_eta = tk.Label(strip, text="ETA: --", font=self.font_small, bg=PALETTE["bg_input"], fg=PALETTE["text_secondary"])
        self.lbl_m_eta.pack(side="left", expand=True)

        # Buttons row
        btn_bar = tk.Frame(control_card, bg=PALETTE["bg_surface"])
        btn_bar.pack(fill="x")

        # Log drawer toggle
        self.var_show_logs = tk.BooleanVar(value=False)
        self.btn_logs = tk.Button(
            btn_bar,
            text="Show Console Logs ▼",
            font=self.font_small,
            bg=PALETTE["bg_surface"],
            fg=PALETTE["text_muted"],
            relief="flat",
            cursor="hand2",
            command=self.toggle_log_console
        )
        self.btn_logs.pack(side="left")

        # Right control actions
        self.btn_open_folder = tk.Button(
            btn_bar,
            text="📂 Open Output Folder",
            font=self.font_bold,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_primary"],
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            state="disabled",
            command=self.open_output_folder
        )
        self.btn_open_folder.pack(side="right", padx=(8, 0))

        self.btn_cancel = tk.Button(
            btn_bar,
            text="Cancel",
            font=self.font_bold,
            bg=PALETTE["danger"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["danger_hover"],
            activeforeground=PALETTE["text_primary"],
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            state="disabled",
            command=self.cancel_conversion
        )
        self.btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_convert = tk.Button(
            btn_bar,
            text="▶ Start Conversion",
            font=self.font_bold,
            bg=PALETTE["accent"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["accent_hover"],
            activeforeground=PALETTE["text_primary"],
            relief="flat",
            padx=20,
            pady=6,
            cursor="hand2",
            command=self.start_conversion
        )
        self.btn_convert.pack(side="right")

        # ---------------- 5. Log Console (Collapsible) ----------------
        self.log_frame = tk.Frame(container, bg=PALETTE["bg_surface"], highlightbackground=PALETTE["border"], highlightthickness=1)
        self.log_view = tk.Text(
            self.log_frame,
            bg=PALETTE["bg_main"],
            fg=PALETTE["text_muted"],
            insertbackground=PALETTE["text_primary"],
            font=self.font_mono,
            height=6,
            relief="flat",
            padx=8,
            pady=6
        )
        self.log_view.pack(fill="both", expand=True)

    def _bind_keyboard_shortcuts(self):
        self.root.bind("<Control-o>", lambda e: self.browse_files())
        self.root.bind("<Control-O>", lambda e: self.browse_files())
        self.root.bind("<Delete>", lambda e: self.remove_selected())
        self.root.bind("<BackSpace>", lambda e: self.remove_selected())
        self.root.bind("<Control-a>", self._select_all_items)
        self.root.bind("<Control-A>", self._select_all_items)
        self.root.bind("<Control-l>", lambda e: self.clear_queue())
        self.root.bind("<Control-L>", lambda e: self.clear_queue())
        self.root.bind("<Escape>", lambda e: self.cancel_conversion() if self.is_converting else None)
        self.root.bind("<Return>", lambda e: self.start_conversion() if not self.is_converting else None)

    def _verify_ffmpeg_installation(self):
        if FFMPEG_BIN and FFMPEG_BIN.is_file():
            self.badge_ffmpeg.configure(text="✓ FFmpeg Ready", fg=PALETTE["success"])
        else:
            self.badge_ffmpeg.configure(text="⚠ FFmpeg Missing", fg=PALETTE["danger"])

    def _on_mode_toggled(self, event=None):
        is_remux = "Remux" in self.var_mode.get()
        state = "disabled" if is_remux else "readonly"
        self.combo_quality.configure(state=state)
        self.combo_res.configure(state=state)

    def _on_dest_mode_changed(self):
        is_custom = self.var_dest_mode.get() == "custom"
        state = "normal" if is_custom else "disabled"
        self.entry_dest.configure(state=state)
        self.btn_browse_dest.configure(state=state)

    def browse_custom_dest(self):
        chosen = filedialog.askdirectory(title="Choose Output Directory")
        if chosen:
            self.var_custom_dest.set(chosen)

    def toggle_log_console(self):
        if self.var_show_logs.get():
            self.log_frame.pack_forget()
            self.btn_logs.configure(text="Show Console Logs ▼")
            self.var_show_logs.set(False)
        else:
            self.log_frame.pack(fill="x", pady=(4, 0))
            self.btn_logs.configure(text="Hide Console Logs ▲")
            self.var_show_logs.set(True)

    def append_log(self, text: str):
        self.log_view.insert("end", f"{text}\n")
        self.log_view.see("end")

    # ---------------- Queue Manipulation ----------------
    def browse_files(self):
        selected = filedialog.askopenfilenames(
            title="Select .MOV Videos",
            filetypes=[("QuickTime Movies (*.mov)", "*.mov"), ("All Files (*.*)", "*.*")]
        )
        if selected:
            self.add_paths([Path(p) for p in selected])

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder with .MOV Videos")
        if folder:
            dir_path = Path(folder)
            found = [p for p in dir_path.rglob("*.mov") if p.is_file()]
            if found:
                self.add_paths(found)
            else:
                messagebox.showinfo("No Files Found", f"No .mov videos found in:\n{folder}")

    def add_paths(self, paths: list[Path]):
        existing_paths = {item.path.resolve() for item in self.queue_items}
        new_items = []
        for p in paths:
            resolved = p.resolve()
            if resolved not in existing_paths and resolved.is_file():
                item = QueueItem(resolved)
                self.queue_items.append(item)
                new_items.append(item)

        if new_items:
            self._refresh_tree_display()
            # Asynchronously probe video metadata
            for it in new_items:
                threading.Thread(target=self._async_probe_metadata, args=(it,), daemon=True).start()

    def _async_probe_metadata(self, item: QueueItem):
        try:
            info = probe_video(item.path)
            item.info = info
            self.ui_events.put(("item_probed", item))
        except Exception as e:
            logger.debug("Failed probe on %s: %s", item.filename, e)

    def _refresh_tree_display(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        total_bytes = 0
        total_seconds = 0.0

        for idx, item in enumerate(self.queue_items):
            info = item.info
            res = info.resolution_str if info else "Probing..."
            dur = info.duration_str if info else "--"
            codec = info.video_codec if info else "--"
            size = info.human_size if info else "--"

            if info:
                total_bytes += info.file_size_bytes
                total_seconds += info.duration_secs

            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(item.filename, res, dur, codec, size, item.status)
            )

        count = len(self.queue_items)
        if count > 0:
            self.lbl_empty_banner.place_forget()
            human_total_size = f"{total_bytes / (1024*1024):.1f} MB" if total_bytes > 0 else ""
            total_time_str = f" | {int(total_seconds // 60)}m {int(total_seconds % 60)}s" if total_seconds > 0 else ""
            self.lbl_q_stats.configure(text=f"({count} {'file' if count == 1 else 'files'}{total_time_str} | {human_total_size})")
        else:
            self.lbl_empty_banner.place(relx=0.5, rely=0.5, anchor="center")
            self.lbl_q_stats.configure(text="(0 files)")

    def _select_all_items(self, event=None):
        all_iids = self.tree.get_children()
        self.tree.selection_set(all_iids)
        return "break"

    def remove_selected(self):
        selection = self.tree.selection()
        if not selection:
            return
        indices = sorted([int(iid) for iid in selection], reverse=True)
        for idx in indices:
            if idx < len(self.queue_items):
                del self.queue_items[idx]
        self._refresh_tree_display()

    def clear_queue(self):
        if self.is_converting:
            messagebox.showwarning("Busy", "Cannot clear queue while conversion is in progress.")
            return
        self.queue_items.clear()
        self._refresh_tree_display()

    # ---------------- Context Menu & Detail Inspection ----------------
    def _show_context_menu(self, event):
        row_id = self.tree.identify_row(event.y)
        if row_id:
            if row_id not in self.tree.selection():
                self.tree.selection_set(row_id)
            self.context_menu.post(event.x_root, event.y_root)

    def _on_tree_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        idx = int(row_id)
        if idx >= len(self.queue_items):
            return

        item = self.queue_items[idx]
        if item.status == "✓ Completed" and item.output_path and item.output_path.is_file():
            self._play_file(item.output_path)
        else:
            self._show_details_dialog(item)

    def _context_play_video(self):
        selection = self.tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        item = self.queue_items[idx]
        target = item.output_path if (item.status == "✓ Completed" and item.output_path) else item.path
        self._play_file(target)

    def _context_show_in_folder(self):
        selection = self.tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        item = self.queue_items[idx]
        target_dir = item.output_path.parent if (item.status == "✓ Completed" and item.output_path) else item.path.parent
        self._open_explorer(target_dir)

    def _context_view_details(self):
        selection = self.tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        self._show_details_dialog(self.queue_items[idx])

    def _show_details_dialog(self, item: QueueItem):
        info = item.info
        if not info:
            messagebox.showinfo("Video Details", f"File: {item.filename}\n\nTechnical stream details have not been analyzed yet.")
            return

        details = [
            f"File Name: {item.filename}",
            f"Location: {item.path}",
            f"File Size: {info.human_size} ({info.file_size_bytes:,} bytes)",
            f"Duration: {info.duration_str} ({info.duration_secs:.2f} seconds)",
            f"Resolution: {info.resolution_str}",
            f"Framerate: {info.fps:.2f} fps",
            f"Video Codec: {info.video_codec}",
            f"Audio Codec: {info.audio_codec or 'None (Silent video)'}",
            f"Total Bitrate: {info.bitrate_kbps:,} kbps",
            f"Rotation: {info.rotation}°",
            f"Current Status: {item.status}"
        ]
        if item.output_path:
            details.append(f"Output Target: {item.output_path}")

        messagebox.showinfo(f"Video Details - {item.filename}", "\n".join(details))

    def _play_file(self, path: Path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Playback Error", f"Could not launch media player: {e}")

    def _open_explorer(self, folder: Path):
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            else:
                subprocess.run(["xdg-open", str(folder)])
        except Exception as e:
            messagebox.showerror("Explorer Error", f"Could not open directory: {e}")

    # ---------------- Conversion Pipeline ----------------
    def start_conversion(self):
        if not self.queue_items:
            messagebox.showinfo("Queue Empty", "Please add at least one .mov video to convert.")
            return

        if not FFMPEG_BIN:
            messagebox.showerror("FFmpeg Missing", "FFmpeg could not be located. Please ensure FFmpeg is available.")
            return

        # Parse settings
        mode = "remux" if "Remux" in self.var_mode.get() else "reencode"
        crf = 22
        q_text = self.var_quality.get()
        if "18" in q_text:
            crf = 18
        elif "28" in q_text:
            crf = 28

        res = None
        r_text = self.var_res.get()
        if "1080p" in r_text:
            res = (1920, 1080)
        elif "720p" in r_text:
            res = (1280, 720)
        elif "480p" in r_text:
            res = (854, 480)

        out_dir: Optional[Path] = None
        if self.var_dest_mode.get() == "custom":
            custom_path_str = self.var_custom_dest.get().strip()
            if not custom_path_str:
                messagebox.showerror("Missing Path", "Please enter or browse for a custom destination directory.")
                return
            out_dir = Path(custom_path_str).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)

        options = ConversionOptions(
            mode=mode,
            crf=crf,
            preset="fast",
            resolution=res,
            audio_bitrate="192k",
            output_dir=out_dir
        )

        # Update controls
        self.is_converting = True
        self.cancel_event.clear()
        self.btn_convert.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.btn_open_folder.configure(state="disabled")
        self.progress_bar["value"] = 0
        self.lbl_pct.configure(text="0%")

        # Reset item statuses
        for item in self.queue_items:
            if item.status != "✓ Completed":
                item.status = "Pending"
        self._refresh_tree_display()

        # Save settings for next time
        self._save_current_settings()

        # Start worker thread
        self.worker_thread = threading.Thread(
            target=self._run_conversion_worker,
            args=(options,),
            daemon=True
        )
        self.worker_thread.start()

    def _run_conversion_worker(self, options: ConversionOptions):
        total_count = len(self.queue_items)
        success_count = 0

        for idx, item in enumerate(self.queue_items):
            if self.cancel_event.is_set():
                break

            if item.status == "✓ Completed":
                success_count += 1
                continue

            item.status = "Converting..."
            self.ui_events.put(("update_item_status", (idx, "Converting...")))
            self.ui_events.put(("status_headline", f"Converting {idx + 1} of {total_count}: {item.filename}"))

            # Determine output file path
            dest_folder = options.output_dir if options.output_dir else item.path.parent
            base_stem = item.path.stem
            dest_file = dest_folder / f"{base_stem}.mp4"

            # Auto deduplicate if destination file exists and is not our current input
            counter = 1
            while dest_file.exists() and dest_file == item.path:
                dest_file = dest_folder / f"{base_stem}_{counter}.mp4"
                counter += 1

            item.output_path = dest_file
            self.last_destination_folder = dest_folder

            def on_progress(p: ProgressState):
                self.ui_events.put(("progress", (idx, total_count, p)))

            def on_log(line: str):
                self.ui_events.put(("log", line))

            success, msg = convert_video(
                input_path=item.path,
                output_path=dest_file,
                options=options,
                info=item.info,
                on_progress=on_progress,
                on_log=on_log,
                cancel_flag=self.cancel_event
            )

            if success:
                item.status = "✓ Completed"
                success_count += 1
                self.ui_events.put(("update_item_status", (idx, "✓ Completed")))
            elif self.cancel_event.is_set():
                item.status = "Cancelled"
                self.ui_events.put(("update_item_status", (idx, "Cancelled")))
                break
            else:
                item.status = "✕ Failed"
                item.error_detail = msg
                self.ui_events.put(("update_item_status", (idx, "✕ Failed")))

        self.ui_events.put(("batch_finished", (success_count, total_count)))

    def cancel_conversion(self):
        if self.is_converting:
            self.cancel_event.set()
            self.lbl_status_lead.configure(text="Cancelling conversion...")
            self.btn_cancel.configure(state="disabled")

    def open_output_folder(self):
        target = self.last_destination_folder
        if not target and self.queue_items:
            target = self.queue_items[0].path.parent

        if target and target.is_dir():
            self._open_explorer(target)

    # ---------------- UI Event Dispatcher ----------------
    def _process_ui_queue(self):
        try:
            while True:
                evt_type, payload = self.ui_events.get_nowait()

                if evt_type == "item_probed":
                    item = payload
                    for iid in self.tree.get_children():
                        idx = int(iid)
                        if idx < len(self.queue_items) and self.queue_items[idx] == item:
                            self.tree.item(
                                iid,
                                values=(
                                    item.filename,
                                    item.info.resolution_str,
                                    item.info.duration_str,
                                    item.info.video_codec,
                                    item.info.human_size,
                                    item.status
                                )
                            )
                            break

                elif evt_type == "status_headline":
                    self.lbl_status_lead.configure(text=payload)

                elif evt_type == "update_item_status":
                    idx, status_text = payload
                    iid = str(idx)
                    if self.tree.exists(iid):
                        curr = list(self.tree.item(iid, "values"))
                        curr[5] = status_text
                        self.tree.item(iid, values=curr)

                elif evt_type == "progress":
                    idx, total, p = payload
                    slice_size = 100.0 / total
                    overall = (idx * slice_size) + ((p.percent / 100.0) * slice_size)
                    overall = min(100.0, overall)

                    self.progress_bar["value"] = overall
                    self.lbl_pct.configure(text=f"{overall:.1f}%")
                    self.lbl_m_speed.configure(text=f"Speed: {p.speed_factor}")
                    self.lbl_m_fps.configure(text=f"FPS: {p.fps}")
                    self.lbl_m_elapsed.configure(text=f"Elapsed: {p.elapsed_str}")
                    self.lbl_m_eta.configure(text=f"ETA: {p.eta_str}")

                elif evt_type == "log":
                    self.append_log(payload)

                elif evt_type == "batch_finished":
                    success_count, total = payload
                    self.is_converting = False
                    self.btn_convert.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
                    self.btn_open_folder.configure(state="normal")

                    if self.cancel_event.is_set():
                        self.lbl_status_lead.configure(text="Conversion cancelled by user")
                        self.lbl_pct.configure(text="Cancelled")
                    else:
                        self.progress_bar["value"] = 100
                        self.lbl_pct.configure(text="100%")
                        self.lbl_status_lead.configure(text=f"Finished! Successfully converted {success_count} of {total} video(s).")

                        # Sound chime
                        if self.config.get("play_sound_on_complete", True):
                            try:
                                self.root.bell()
                            except Exception:
                                pass

                        messagebox.showinfo(
                            "Conversion Complete",
                            f"Successfully converted {success_count} of {total} video(s) to MP4 format.\n\nClick 'Open Output Folder' to view your converted files."
                        )

        except queue.Empty:
            pass

        self.root.after(80, self._process_ui_queue)

    def _save_current_settings(self):
        settings_payload = {
            "mode": self.var_mode.get(),
            "quality": self.var_quality.get(),
            "resolution": self.var_res.get(),
            "dest_mode": self.var_dest_mode.get(),
            "custom_dest_dir": self.var_custom_dest.get(),
            "play_sound_on_complete": True
        }
        ConfigManager.save(settings_payload)

    def _on_window_close(self):
        if self.is_converting:
            if messagebox.askyesno("Exit Confirmation", "A video conversion is currently in progress.\nAre you sure you want to stop and exit?"):
                self.cancel_event.set()
                self._save_current_settings()
                self.root.destroy()
        else:
            self._save_current_settings()
            self.root.destroy()


def main():
    root = tk.Tk()
    app = ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
