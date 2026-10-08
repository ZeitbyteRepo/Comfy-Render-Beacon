import io
import json
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
    build_completed_media,
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

    completed = build_completed_media("a" * 16, "image", source.getvalue(), ".png")
    thumbnail = Image.open(io.BytesIO(completed.thumbnail))
    assert thumbnail.format == "JPEG"
    assert thumbnail.size == (112, 64)
    assert completed.descriptor()["thumbnail_url"] == "/v2/media/aaaaaaaaaaaaaaaa/thumb.jpg"


def test_atomic_cache_rejects_oversize_without_partial_install():
    cache = AtomicMediaCache(max_bytes=100, max_items=2)
    good = CompletedMedia("a" * 16, "image", (b"x" * 40,), None, 10_000, 1, b"t")
    bad = CompletedMedia(
        "b" * 16, "video", (b"y" * 60, b"z" * 60), 1000, 500, 2, b"t"
    )
    assert cache.put(good) is True
    assert cache.put(bad) is False
    assert cache.get(good.media_id) == good
    assert cache.get(bad.media_id) is None
    assert cache.total_bytes == 40
    assert cache.thumbnail_bytes == 1


def test_atomic_cache_rejects_more_than_max_frames_without_eviction():
    cache = AtomicMediaCache(max_bytes=1024, max_items=2)
    good = CompletedMedia("a" * 16, "image", (b"x",), None, 10_000, 1, b"t")
    too_many = CompletedMedia(
        "b" * 16,
        "video",
        tuple(b"x" for _ in range(MAX_FRAMES + 1)),
        1000,
        40,
        2,
        b"t",
    )
    assert cache.put(good) is True
    assert cache.put(too_many) is False
    assert cache.get(good.media_id) == good
    assert cache.get(too_many.media_id) is None


def test_atomic_cache_exposes_only_three_newest_thumbnail_descriptors():
    cache = AtomicMediaCache(max_bytes=1024, max_items=4)
    for index, character in enumerate("abcd"):
        assert cache.put(
            CompletedMedia(character * 16, "image", (b"frame",), None, 10_000, index, b"thumb")
        )
    recent = cache.recent_descriptors(99)
    assert [item["id"] for item in recent] == ["d" * 16, "c" * 16, "b" * 16]
    assert all(item["thumbnail_url"].endswith("/thumb.jpg") for item in recent)
    assert cache.thumbnail("b" * 16) == b"thumb"


def test_recent_three_thumbnails_survive_full_frame_byte_eviction():
    cache = AtomicMediaCache(max_bytes=80, max_items=4)
    ids = [character * 16 for character in "abcd"]
    for index, media_id in enumerate(ids):
        assert cache.put(
            CompletedMedia(
                media_id,
                "video",
                (bytes([index]) * 40,),
                1000,
                500,
                index,
                f"thumb-{index}".encode(),
            )
        )

    assert cache.frame(ids[0], 0) is None
    assert cache.frame(ids[1], 0) is None
    assert [item["id"] for item in cache.recent_descriptors()] == list(
        reversed(ids[1:])
    )
    assert cache.thumbnail(ids[0]) is None
    assert cache.thumbnail(ids[1]) == b"thumb-1"
    assert cache.thumbnail(ids[2]) == b"thumb-2"
    assert cache.thumbnail(ids[3]) == b"thumb-3"
    assert cache.total_bytes == 80
    assert cache.thumbnail_bytes == sum(len(f"thumb-{index}") for index in (1, 2, 3))


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
    assert len(json.dumps(state, separators=(",", ":")).encode()) <= 12 * 1024


def test_v2_bridge_epoch_is_stable_per_instance_and_changes_on_restart():
    from render_beacon.observer import ComfyObserver

    first = ComfyObserver(command_runner=lambda command: "")
    second = ComfyObserver(command_runner=lambda command: "")
    epoch = first.state_v2()["bridge_instance_epoch"]
    assert len(epoch) == 32
    assert first.state_v2()["bridge_instance_epoch"] == epoch
    assert second.state_v2()["bridge_instance_epoch"] != epoch


def test_v2_keeps_newest_ready_descriptor_visible_while_running():
    from render_beacon.observer import ComfyObserver

    observer = ComfyObserver(command_runner=lambda command: "")
    descriptor = {
        "id": "0123456789abcdef",
        "kind": "image",
        "frame_count": 1,
        "loop_count": 1,
        "frame_interval_ms": 10_000,
    }
    observer._completed_media = descriptor
    observer._completion_sequence = 7
    observer._completion_status = "ready"
    observer._state["mode"] = "running"
    state = observer.state_v2()
    assert state["mode"] == "running"
    assert state["completion_sequence"] == 7
    assert state["completed_media"] == descriptor