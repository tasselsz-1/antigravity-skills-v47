#!/usr/bin/env python3
"""Extract evenly sampled frames and contact sheets from one video or a folder."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".m2ts", ".flv", ".mpg", ".mpeg"}
TS_STREAM_EXTENSIONS = {".ts", ".m2ts"}
WINDOWS_FFMPEG_FALLBACKS = (
    Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
)


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def find_ffmpeg(explicit: str | None) -> str:
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return str(candidate)
        raise FileNotFoundError(f"ffmpeg not found: {candidate}")
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    for candidate in WINDOWS_FFMPEG_FALLBACKS:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("ffmpeg was not found. Pass --ffmpeg with its full path.")


def videos_under(source: Path) -> list[Path]:
    if source.is_file() and source.suffix.casefold() in VIDEO_EXTENSIONS:
        return [source]
    if source.is_dir():
        return sorted(
            (path for path in source.rglob("*") if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS),
            key=lambda path: (str(path.parent).casefold(), natural_key(path)),
        )
    raise FileNotFoundError(f"No readable video source: {source}")


def make_sheet(frames: list[Path], output: Path, cell_width: int) -> None:
    columns = 3
    margin = 16
    label_height = 28
    prepared: list[tuple[Path, Image.Image]] = []
    for frame in frames:
        with Image.open(frame) as image:
            copy = image.convert("RGB")
        height = max(1, round(copy.height * cell_width / copy.width))
        prepared.append((frame, copy.resize((cell_width, height))))
    max_height = max(image.height for _, image in prepared)
    rows = (len(prepared) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * cell_width + (columns + 1) * margin, rows * (max_height + label_height) + (rows + 1) * margin),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (frame, image) in enumerate(prepared):
        row, column = divmod(index, columns)
        x = margin + column * (cell_width + margin)
        y = margin + row * (max_height + label_height + margin)
        sheet.paste(image, (x, y))
        draw.text((x, y + max_height + 6), frame.stem, fill="black", font=font)
    sheet.save(output, quality=90)


def extract(
    ffmpeg: str,
    video: Path,
    output_root: Path,
    fps: float,
    max_frames: int,
    width: int,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> Path:
    target = output_root / video.stem
    target.mkdir(parents=True, exist_ok=True)
    pattern = target / "frame_%03d.jpg"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    is_ts = video.suffix.casefold() in TS_STREAM_EXTENSIONS
    if start_sec is not None and not is_ts:
        command.extend(["-ss", f"{start_sec:.3f}"])
    command.extend(["-i", str(video)])
    if start_sec is not None and is_ts:
        # .ts/.m2ts 传输流采用输出端精确 -ss 逐帧精准 seek，避免输入端关键帧跳变或 PTS 偏移
        command.extend(["-ss", f"{start_sec:.3f}"])
    if end_sec is not None:
        duration_sec = max(0.05, end_sec - (start_sec or 0.0))
        command.extend(["-t", f"{duration_sec:.3f}"])
    command.extend(["-vf", f"fps={fps},scale={width}:-2", "-frames:v", str(max_frames), str(pattern)])
    subprocess.run(command, check=True)
    frames = sorted(target.glob("frame_*.jpg"), key=natural_key)
    if not frames:
        raise RuntimeError(f"No frames extracted from {video}")
    sheet = target / "contact_sheet.jpg"
    make_sheet(frames, sheet, width)
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Video file or directory")
    parser.add_argument("output", type=Path, help="Directory for frames and contact sheets")
    parser.add_argument("--ffmpeg", help="Full path to ffmpeg")
    parser.add_argument("--fps", type=float, default=1.0, help="Sampling rate; default: 1 frame/second")
    parser.add_argument("--max-frames", type=int, default=12, help="Maximum frames per video")
    parser.add_argument("--sheet-width", type=int, default=384, help="Width of each contact-sheet cell")
    parser.add_argument("--start-sec", type=float, default=None, help="Optional start time in seconds (skip intro)")
    parser.add_argument("--end-sec", type=float, default=None, help="Optional end time in seconds (skip outro)")
    args = parser.parse_args()
    if args.fps <= 0 or args.max_frames <= 0 or args.sheet_width <= 0:
        parser.error("--fps, --max-frames, and --sheet-width must be positive")
    if args.start_sec is not None and args.start_sec < 0:
        parser.error("--start-sec must be >= 0")
    if args.end_sec is not None and args.end_sec <= (args.start_sec or 0.0):
        parser.error("--end-sec must be greater than --start-sec (or 0)")

    clips = videos_under(args.source.resolve())
    if not clips:
        parser.error("No supported videos found")
    args.output.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg(args.ffmpeg)
    for number, clip in enumerate(clips, start=1):
        sheet = extract(
            ffmpeg,
            clip,
            args.output,
            args.fps,
            args.max_frames,
            args.sheet_width,
            start_sec=args.start_sec,
            end_sec=args.end_sec,
        )
        print(f"[{number}/{len(clips)}] {clip.name} -> {sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
