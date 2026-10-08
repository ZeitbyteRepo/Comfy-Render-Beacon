from __future__ import annotations

import io
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from PIL import Image, ImageDraw, ImageOps

MediaKind = Literal["image", "video", "audio"]

WIDTH = 480
HEIGHT = 320
VIDEO_LOOPS = 3
STILL_HOLD_MS = 10_000
MAX_FRAMES = 24
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_FRAME_BYTES = 196_608


@dataclass(frozen=True)
class CompletedMedia:
    media_id: str
    kind: MediaKind
    frames: tuple[bytes, ...]
    duration_ms: int | None
    frame_interval_ms: int
    created_at_ms: int

    def descriptor(self) -> dict:
        result = {
            "id": self.media_id,
            "kind": self.kind,
            "frame_count": len(self.frames),
            "frame_interval_ms": self.frame_interval_ms,
            "loop_count": VIDEO_LOOPS if self.kind == "video" else 1,
            "still_hold_ms": STILL_HOLD_MS if self.kind == "image" else None,
            "duration_ms": self.duration_ms,
            "frame_url_template": f"/v2/media/{self.media_id}/frame/{{frame}}.jpg",
        }
        return result


class AtomicMediaCache:
    """Thread-safe all-or-nothing media cache with byte and item bounds."""

    def __init__(self, max_bytes: int = 4 * 1024 * 1024, max_items: int = 4) -> None:
        if max_bytes <= 0 or max_items <= 0:
            raise ValueError("cache bounds must be positive")
        self.max_bytes = max_bytes
        self.max_items = max_items
        self._items: OrderedDict[str, CompletedMedia] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    def put(self, media: CompletedMedia) -> bool:
        size = sum(len(frame) for frame in media.frames)
        if not media.frames or size > self.max_bytes or any(len(frame) > MAX_FRAME_BYTES for frame in media.frames):
            return False
        with self._lock:
            old = self._items.pop(media.media_id, None)
            if old is not None:
                self._bytes -= sum(len(frame) for frame in old.frames)
            while self._items and (len(self._items) >= self.max_items or self._bytes + size > self.max_bytes):
                _, evicted = self._items.popitem(last=False)
                self._bytes -= sum(len(frame) for frame in evicted.frames)
            if self._bytes + size > self.max_bytes:
                if old is not None:
                    self._items[old.media_id] = old
                    self._bytes += sum(len(frame) for frame in old.frames)
                return False
            self._items[media.media_id] = media
            self._bytes += size
            return True

    def get(self, media_id: str) -> CompletedMedia | None:
        with self._lock:
            return self._items.get(media_id)

    def frame(self, media_id: str, index: int) -> bytes | None:
        media = self.get(media_id)
        if media is None or index < 0 or index >= len(media.frames):
            return None
        return media.frames[index]

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._bytes


def _jpeg(image: Image.Image, quality: int = 76) -> bytes:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (12, 15, 20))
    fitted = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGB"), (WIDTH, HEIGHT))
    canvas.paste(fitted, ((WIDTH - fitted.width) // 2, (HEIGHT - fitted.height) // 2))
    output = io.BytesIO()
    canvas.save(output, "JPEG", quality=quality, optimize=True, progressive=False, subsampling=2)
    payload = output.getvalue()
    if len(payload) > MAX_FRAME_BYTES:
        output = io.BytesIO()
        canvas.save(output, "JPEG", quality=58, optimize=True, progressive=False, subsampling=2)
        payload = output.getvalue()
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError("derived JPEG exceeds frame bound")
    return payload


def still_derivative(source: bytes) -> tuple[bytes, ...]:
    if not source or len(source) > MAX_SOURCE_BYTES:
        raise ValueError("invalid source size")
    with Image.open(io.BytesIO(source)) as image:
        image.load()
        return (_jpeg(image),)


def _probe_duration(path: Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    result = runner(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    seconds = float(result.stdout.strip())
    if not 0 < seconds <= 24 * 60 * 60:
        raise ValueError("invalid media duration")
    return round(seconds * 1000)


def video_derivative(source: bytes, suffix: str = ".mp4") -> tuple[tuple[bytes, ...], int, int]:
    if not source or len(source) > MAX_SOURCE_BYTES:
        raise ValueError("invalid source size")
    with tempfile.TemporaryDirectory(prefix="render-beacon-") as directory:
        source_path = Path(directory) / f"source{suffix}"
        source_path.write_bytes(source)
        duration_ms = _probe_duration(source_path)
        target_frames = max(1, min(MAX_FRAMES, round(duration_ms / 500)))
        fps = target_frames * 1000 / duration_ms
        pattern = str(Path(directory) / "frame-%03d.jpg")
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(source_path), "-vf",
                f"fps={fps:.6f},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x0c0f14",
                "-frames:v", str(target_frames), "-q:v", "5", pattern,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        frames = tuple(still_derivative(path.read_bytes())[0] for path in sorted(Path(directory).glob("frame-*.jpg")))
    if not frames:
        raise ValueError("video produced no frames")
    return frames, duration_ms, max(40, round(duration_ms / len(frames)))


def audio_derivative(source: bytes, suffix: str = ".wav") -> tuple[tuple[bytes, ...], int]:
    if not source or len(source) > MAX_SOURCE_BYTES:
        raise ValueError("invalid source size")
    with tempfile.TemporaryDirectory(prefix="render-beacon-") as directory:
        source_path = Path(directory) / f"source{suffix}"
        source_path.write_bytes(source)
        duration_ms = _probe_duration(source_path)
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(source_path), "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    samples = memoryview(pcm).cast("h") if len(pcm) >= 2 else []
    image = Image.new("RGB", (WIDTH, HEIGHT), (12, 15, 20))
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 18, WIDTH - 19, HEIGHT - 19), outline=(48, 53, 45), width=2)
    center = HEIGHT // 2
    draw.line((28, center, WIDTH - 29, center), fill=(105, 115, 94), width=1)
    columns = WIDTH - 56
    if samples:
        stride = max(1, len(samples) // columns)
        for x in range(columns):
            block = samples[x * stride : min(len(samples), (x + 1) * stride)]
            peak = max((abs(value) for value in block), default=0)
            height = max(1, round((peak / 32768) * 112))
            draw.line((28 + x, center - height, 28 + x, center + height), fill=(201, 214, 91))
    seconds, millis = divmod(duration_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    draw.text((28, 278), f"{minutes:02d}:{seconds:02d}.{millis // 100:01d}", fill=(239, 238, 231))
    return (_jpeg(image),), duration_ms


def build_completed_media(media_id: str, kind: MediaKind, source: bytes, suffix: str) -> CompletedMedia:
    created = int(time.time() * 1000)
    if kind == "image":
        return CompletedMedia(media_id, kind, still_derivative(source), None, STILL_HOLD_MS, created)
    if kind == "video":
        frames, duration_ms, interval = video_derivative(source, suffix)
        return CompletedMedia(media_id, kind, frames, duration_ms, interval, created)
    frames, duration_ms = audio_derivative(source, suffix)
    return CompletedMedia(media_id, kind, frames, duration_ms, STILL_HOLD_MS, created)
