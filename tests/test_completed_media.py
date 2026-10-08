import io
import subprocess
from pathlib import Path

from PIL import Image

from render_beacon.completed_media import (
    AtomicMediaCache,
    CompletedMedia,
    HEIGHT,
    MAX_FRAME_BYTES,
    MAX_FRAMES,
    VIDEO_LOOPS,
    WIDTH,
    audio_derivative,
    still_derivative,
    video_derivative,
)


def assert_baseline_jpeg(payload: bytes):
    assert len(payload) <= MAX_FRAME_BYTES
    image = Image.open(io.BytesIO(payload))
    assert image.format == "JPEG"
    assert image.size == (WIDTH, HEIGHT)
    assert image.info.get("progressive") not in (1, True)


def test_still_derivative_is_exact_baseline_480x320():
    source = io.BytesIO()
    Image.new("RGBA", (1200, 600), (20, 40, 60, 128)).save(source, "PNG")
    frames = still_derivative(source.getvalue())
    assert len(frames) == 1
    assert_baseline_jpeg(frames[0])


def test_atomic_cache_rejects_oversize_without_partial_install():
    cache = AtomicMediaCache(max_bytes=100, max_items=2)
    good = CompletedMedia("a" * 16, "image", (b"x" * 40,), None, 10_000, 1)
    bad = CompletedMedia("b" * 16, "video", (b"y" * 60, b"z" * 60), 1000, 500, 2)
    assert cache.put(good) is True
    assert cache.put(bad) is False
    assert cache.get(good.media_id) == good
    assert cache.get(bad.media_id) is None
    assert cache.total_bytes == 40


def test_atomic_cache_rejects_more_than_max_frames_without_eviction():
    cache = AtomicMediaCache(max_bytes=1024, max_items=2)
    good = CompletedMedia("a" * 16, "image", (b"x",), None, 10_000, 1)
    too_many = CompletedMedia(
        "b" * 16, "video", tuple(b"x" for _ in range(MAX_FRAMES + 1)), 1000, 40, 2
    )
    assert cache.put(good) is True
    assert cache.put(too_many) is False
    assert cache.get(good.media_id) == good
    assert cache.get(too_many.media_id) is None


def test_video_sequence_and_audio_waveform_contract(tmp_path: Path):
    video = tmp_path / "sample.mp4"
    audio = tmp_path / "sample.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=8:d=1", "-pix_fmt", "yuv420p", str(video)],
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.25", str(audio)],
        check=True,
        timeout=30,
    )
    frames, duration_ms, interval_ms = video_derivative(video.read_bytes(), ".mp4")
    assert 1 <= len(frames) <= 24
    assert duration_ms == 1000
    assert interval_ms == 500
    assert VIDEO_LOOPS == 3
    for frame in frames:
        assert_baseline_jpeg(frame)
    waveform, audio_duration_ms = audio_derivative(audio.read_bytes(), ".wav")
    assert len(waveform) == 1
    assert 1240 <= audio_duration_ms <= 1260
    assert_baseline_jpeg(waveform[0])


def test_v2_rail_has_exact_fields_and_no_modality_text():
    from render_beacon.observer import ComfyObserver

    observer = ComfyObserver(command_runner=lambda command: "")
    observer._state.update(
        {
            "mode": "running",
            "queue": {"running_count": 1, "pending_count": 2},
            "active": {"family": "Flux", "media": "image", "pipeline": {"master_percent": 10}},
        }
    )
    state = observer.state_v2()
    assert state["schema_version"] == 2
    assert list(state["rail"]) == ["queue", "model_name", "modality_icon"]
    assert state["rail"] == {
        "queue": {"running": 1, "pending": 2},
        "model_name": "Flux",
        "modality_icon": "image",
    }
    assert "modality_label" not in str(state)