# 🎬 MOV to MP4 Converter

A clean, responsive desktop application for Windows to convert Apple QuickTime `.mov` videos into universal, web-ready `.mp4` format.

---

## ✨ Features & Polish

- **⚡ Two Purpose-Built Modes**:
  - **Universal Re-encode (H.264 + AAC)**: Produces MP4 files that play everywhere (Windows Media Player, QuickTime, iOS, Android, web browsers, Premiere, DaVinci Resolve). Includes `+faststart` web streaming optimization and even-dimension safeguards for odd resolutions.
  - **Ultra-Fast Remux (Stream Copy)**: Lossless container conversion in 1–2 seconds with zero re-encoding or quality degradation.
- **📁 Flexible Queue Management**:
  - Queue individual files or entire folders.
  - Live metadata inspector: resolution, duration, codec, bitrate, and file size.
  - Total queue summary in status bar (total files, total playback duration, combined file size).
- **📊 Real-Time Metrics & Telemetry**:
  - Live progress bar with combined batch percentage.
  - Speed multiplier (e.g. `14.8x`), current FPS, elapsed time, and ETA.
  - Expandable live console drawer to inspect FFmpeg output.
- **🎛️ Keyboard Shortcuts & Context Menu**:
  - `Ctrl + O`: Browse & add `.mov` files
  - `Delete` / `Backspace`: Remove selected items
  - `Ctrl + A`: Select all items
  - `Ctrl + L`: Clear all items
  - `Return` / `Enter`: Start conversion
  - `Escape`: Cancel ongoing conversion
  - **Right-Click**: Play video, show in File Explorer, inspect technical stream details, or remove.
  - **Double-Click**: Directly open/play completed `.mp4` videos in your default media player.
- **💾 Persistent Preferences**:
  - Remembers your chosen mode, quality preset, and destination folder across sessions (`config.json`).
- **🔔 Completion Chime**:
  - Plays a gentle notification chime upon batch completion.

---

## 🚀 How to Run

### Method 1: Double-Click Launcher (Recommended)
- **`run.bat`**: Standard double-click launcher.
- **`run.vbs`**: Silent launcher (opens the app without displaying a command prompt console window).

### Method 2: Command Line
```powershell
python app.py
```

---

## 🛠️ Architecture

```
mov-to-mp4-converter/
├── app.py              # Modern Tkinter/TTK desktop interface with threading & custom dark theme
├── converter.py        # FFmpeg/FFprobe process pipeline, stream probe, progress parsing
├── config.json         # Persistent user preferences (auto-generated)
├── run.bat             # Windows batch launcher
├── run.vbs             # Silent VBScript launcher
├── test_sample.mov     # Sample test video
└── README.md           # Documentation
```
