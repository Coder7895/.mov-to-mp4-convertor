"""
Core video conversion engine for MOV to MP4 Converter.

Handles FFmpeg / FFprobe discovery, stream metadata inspection,
command line construction, and real-time conversion progress parsing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger("mov2mp4.converter")

# Process creation flag on Windows to prevent console flashing
WINDOWS_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def find_binary(binary_name: str) -> Optional[Path]:
    """
    Locates an executable by scanning system PATH, known MSYS2/winget/scoop
    directories, and common Windows locations.
    """
    exe_name = f"{binary_name}.exe" if os.name == "nt" and not binary_name.endswith(".exe") else binary_name

    # 1. System PATH
    found = shutil.which(binary_name)
    if found:
        return Path(found).resolve()

    # 2. MSYS2 environments
    msys_roots = [
        Path(r"C:\msys64\ucrt64\bin"),
        Path(r"C:\msys64\mingw64\bin"),
        Path(r"C:\msys64\usr\bin"),
    ]
    for root in msys_roots:
        candidate = root / exe_name
        if candidate.is_file():
            return candidate.resolve()

    # 3. LocalAppData (WinGet, Scoop, etc.)
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        winget_link = Path(local_app) / "Microsoft" / "WinGet" / "Links" / exe_name
        if winget_link.is_file():
            return winget_link.resolve()

        scoop_shim = Path(local_app) / "scoop" / "shims" / exe_name
        if scoop_shim.is_file():
            return scoop_shim.resolve()

    # 4. Standard C:\ root tools
    common_roots = [
        Path(r"C:\ffmpeg\bin") / exe_name,
        Path(r"C:\Program Files\ffmpeg\bin") / exe_name,
    ]
    for candidate in common_roots:
        if candidate.is_file():
            return candidate.resolve()

    return None


FFMPEG_BIN = find_binary("ffmpeg")
FFPROBE_BIN = find_binary("ffprobe")


@dataclass
class VideoInfo:
    """Container for parsed video stream metadata."""
    path: Path
    duration_secs: float
    width: int
    height: int
    video_codec: str
    audio_codec: Optional[str]
    file_size_bytes: int
    fps: float
    bitrate_kbps: int
    has_audio: bool
    rotation: int = 0

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def resolution_str(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "Unknown"

    @property
    def duration_str(self) -> str:
        total = int(max(0, self.duration_secs))
        mins, secs = divmod(total, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    @property
    def human_size(self) -> str:
        size = float(self.file_size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"


@dataclass
class ConversionOptions:
    """User-specified parameters for the video conversion."""
    mode: str = "reencode"             # 'reencode' or 'remux'
    crf: int = 22                      # 18 = Near Lossless, 22 = Balanced, 28 = Small
    preset: str = "fast"               # ultrafast, fast, medium, slow
    resolution: Optional[Tuple[int, int]] = None  # (width, height) or None for original
    audio_bitrate: str = "192k"        # 128k, 192k, 256k, or 'copy'
    output_dir: Optional[Path] = None


@dataclass
class ProgressState:
    """Live telemetry emitted during active conversion."""
    percent: float
    speed_factor: str
    fps: str
    elapsed_str: str
    eta_str: str
    current_time_str: str


def format_seconds(seconds: float) -> str:
    """Converts raw float seconds to MM:SS or HH:MM:SS."""
    secs = int(max(0, seconds))
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{secs % 60:02d}"
    return f"{m:02d}:{s:02d}"


def probe_video(file_path: Path | str) -> Optional[VideoInfo]:
    """
    Runs ffprobe on the target media file and parses its JSON metadata.
    Returns None if the file is invalid, corrupt, or unreadable.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        logger.warning("Target file does not exist: %s", path)
        return None

    ffprobe = FFPROBE_BIN or find_binary("ffprobe")
    if not ffprobe:
        logger.error("ffprobe executable could not be found.")
        return None

    cmd = [
        str(ffprobe),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path)
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=WINDOWS_NO_WINDOW,
            text=True,
            timeout=15,
            check=True
        )
        data = json.loads(res.stdout)
    except Exception as exc:
        logger.warning("Failed to probe %s: %s", path.name, exc)
        return None

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    duration = 0.0
    try:
        duration = float(fmt.get("duration", 0.0))
    except (ValueError, TypeError):
        pass

    bitrate_kbps = 0
    try:
        bitrate_kbps = int(int(fmt.get("bit_rate", 0)) / 1000)
    except (ValueError, TypeError):
        pass

    width = 0
    height = 0
    video_codec = "Unknown"
    audio_codec: Optional[str] = None
    fps = 30.0
    has_audio = False
    rotation = 0

    for stream in streams:
        c_type = stream.get("codec_type")

        if c_type == "video" and video_codec == "Unknown":
            video_codec = stream.get("codec_name", "unknown")
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))

            # Parse framerate (e.g. "30000/1001" or "30/1")
            r_rate = stream.get("r_frame_rate", "30/1")
            try:
                if "/" in r_rate:
                    n, d = r_rate.split("/")
                    fps = float(n) / float(d) if float(d) != 0 else 30.0
                else:
                    fps = float(r_rate)
            except Exception:
                fps = 30.0

            # Fallback duration from stream if format lacked it
            if duration <= 0:
                try:
                    duration = float(stream.get("duration", 0.0))
                except (ValueError, TypeError):
                    pass

            # Detect rotation tag (common on iPhone MOV files)
            tags = stream.get("tags", {})
            if "rotate" in tags:
                try:
                    rotation = int(tags["rotate"])
                except ValueError:
                    pass
            for side_data in stream.get("side_data_list", []):
                if "rotation" in side_data:
                    try:
                        rotation = int(side_data["rotation"])
                    except ValueError:
                        pass

        elif c_type == "audio" and not has_audio:
            has_audio = True
            audio_codec = stream.get("codec_name", "unknown")

    file_size = path.stat().st_size if path.exists() else 0

    return VideoInfo(
        path=path,
        duration_secs=duration,
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
        file_size_bytes=file_size,
        fps=fps,
        bitrate_kbps=bitrate_kbps,
        has_audio=has_audio,
        rotation=rotation
    )


def build_ffmpeg_args(
    input_path: Path,
    output_path: Path,
    options: ConversionOptions,
    info: Optional[VideoInfo] = None
) -> list[str]:
    """
    Constructs the exact FFmpeg command line arguments based on user options
    and source video properties.
    """
    ffmpeg = FFMPEG_BIN or find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg executable was not found on this system.")

    args = [
        str(ffmpeg),
        "-y",               # Overwrite output if already confirmed
        "-nostdin",         # Prevent ffmpeg from hijacking console input
        "-i", str(input_path)
    ]

    if options.mode == "remux":
        # Stream copy: ultra fast container change without re-encoding
        args.extend(["-c", "copy"])
        # If moving to MP4, preserve metadata and place moov atom at beginning
        args.extend(["-movflags", "+faststart"])
    else:
        # Standard high-compatibility encoding
        # Video: H.264
        args.extend(["-c:v", "libx264"])
        # Pixel format: yuv420p is mandatory for universal playback (QuickTime, Windows, Web)
        args.extend(["-pix_fmt", "yuv420p"])
        args.extend(["-crf", str(options.crf)])
        args.extend(["-preset", options.preset])

        # Video filters (Resolution scaling & Ensuring dimensions are divisible by 2)
        # libx264 will fail if width or height is an odd number!
        filter_parts = []
        if options.resolution:
            w, h = options.resolution
            filter_parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
            filter_parts.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
        else:
            # Ensure width and height are even numbers (trunc(iw/2)*2)
            filter_parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

        if filter_parts:
            args.extend(["-vf", ",".join(filter_parts)])

        # Audio stream handling
        # Only map audio if the file actually contains an audio stream
        has_audio = info.has_audio if info else True
        if has_audio:
            if options.audio_bitrate == "copy":
                args.extend(["-c:a", "copy"])
            else:
                args.extend(["-c:a", "aac", "-b:a", options.audio_bitrate])
        else:
            args.extend(["-an"])  # Explicitly disable audio to avoid encoder confusion

        # Move moov atom to beginning of file for instant web/preview playback
        args.extend(["-movflags", "+faststart"])

    # Real-time progress output format
    args.extend([
        "-progress", "pipe:1",
        "-nostats",
        str(output_path)
    ])

    return args


def convert_video(
    input_path: Path | str,
    output_path: Path | str,
    options: ConversionOptions,
    info: Optional[VideoInfo] = None,
    on_progress: Optional[Callable[[ProgressState], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel_flag: Optional[threading.Event] = None
) -> Tuple[bool, str]:
    """
    Executes FFmpeg to convert a single video with real-time progress callbacks.
    Returns (success: bool, status_message: str).
    """
    src = Path(input_path).resolve()
    dst = Path(output_path).resolve()

    if not src.is_file():
        return False, f"Source video not found: {src.name}"

    if info is None or info.duration_secs <= 0:
        info = probe_video(src)

    total_duration = info.duration_secs if info and info.duration_secs > 0 else 0.0

    # Ensure output parent directory exists
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        cmd = build_ffmpeg_args(src, dst, options, info)
    except Exception as exc:
        return False, str(exc)

    if on_log:
        on_log(f"[Command] {' '.join(cmd)}")

    start_wall_time = time.time()
    proc: Optional[subprocess.Popen] = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=WINDOWS_NO_WINDOW
        )

        current_fps = "0.0"
        current_speed = "1.0x"
        current_out_time_us = 0
        stderr_tail: list[str] = []

        # Read stderr asynchronously to prevent buffer deadlock
        def drain_stderr():
            try:
                for line in proc.stderr:
                    trimmed = line.strip()
                    if trimmed:
                        stderr_tail.append(trimmed)
                        if len(stderr_tail) > 30:
                            stderr_tail.pop(0)
                        if on_log:
                            on_log(trimmed)
            except Exception:
                pass

        reader_thread = threading.Thread(target=drain_stderr, daemon=True)
        reader_thread.start()

        # Parse key=value metrics from stdout
        for line in proc.stdout:
            # Check user cancellation
            if cancel_flag and cancel_flag.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()

                # Clean up incomplete file
                if dst.is_file():
                    try:
                        dst.unlink(missing_ok=True)
                    except OSError:
                        pass
                return False, "Conversion cancelled by user."

            line = line.strip()
            if not line or "=" not in line:
                continue

            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()

            if k == "fps":
                current_fps = v
            elif k == "speed":
                current_speed = v
            elif k == "out_time_us":
                try:
                    current_out_time_us = int(v)
                except ValueError:
                    pass
            elif k == "progress" and v == "end":
                if on_progress:
                    elapsed = time.time() - start_wall_time
                    on_progress(ProgressState(
                        percent=100.0,
                        speed_factor=current_speed,
                        fps=current_fps,
                        elapsed_str=format_seconds(elapsed),
                        eta_str="00:00",
                        current_time_str=format_seconds(total_duration)
                    ))

            # Emit continuous progress update
            if total_duration > 0 and current_out_time_us > 0:
                elapsed_secs = current_out_time_us / 1_000_000.0
                pct = min(99.9, (elapsed_secs / total_duration) * 100.0)
                wall_elapsed = time.time() - start_wall_time

                eta_secs = 0.0
                if pct > 1.0:
                    speed_rate = pct / max(wall_elapsed, 0.001)
                    eta_secs = max(0.0, (100.0 - pct) / speed_rate)

                if on_progress:
                    on_progress(ProgressState(
                        percent=pct,
                        speed_factor=current_speed,
                        fps=current_fps,
                        elapsed_str=format_seconds(wall_elapsed),
                        eta_str=format_seconds(eta_secs),
                        current_time_str=format_seconds(elapsed_secs)
                    ))

        proc.wait()
        reader_thread.join(timeout=1.0)

        if proc.returncode != 0:
            error_details = "\n".join(stderr_tail[-5:]) if stderr_tail else "Unknown FFmpeg error"
            return False, f"FFmpeg failed (exit code {proc.returncode}):\n{error_details}"

        if on_progress:
            total_elapsed = time.time() - start_wall_time
            on_progress(ProgressState(
                percent=100.0,
                speed_factor=current_speed,
                fps=current_fps,
                elapsed_str=format_seconds(total_elapsed),
                eta_str="00:00",
                current_time_str=format_seconds(total_duration)
            ))

        return True, "Success"

    except Exception as exc:
        if proc and proc.poll() is None:
            proc.kill()
        return False, str(exc)
