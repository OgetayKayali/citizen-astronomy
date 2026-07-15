from __future__ import annotations

import math
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter

_ASTROSTACK_GIF_MAX_EXPORT_FRAMES = 60
_SKY_EXPLORER_COMPARISON_ANIMATION_FPS = 30.0
_SKY_EXPLORER_COMPARISON_ANIMATION_MAX_FRAMES = 900
_SKY_EXPLORER_COMPARISON_SMOOTH_MAX_PIXEL_STEP = 2.0
_SKY_EXPLORER_COMPARISON_SPLIT_MIN = 0.02
_SKY_EXPLORER_COMPARISON_SPLIT_MAX = 0.98
_SKY_EXPLORER_COMPARISON_SPLIT_SPAN = (
    _SKY_EXPLORER_COMPARISON_SPLIT_MAX - _SKY_EXPLORER_COMPARISON_SPLIT_MIN
)
_GIF_MIN_FRAME_DURATION_MS = 20


def mp4_export_install_hint() -> str:
    if getattr(sys, "frozen", False):
        return (
            "This installed build is missing the FFmpeg runtime required for MP4 export.\n\n"
            "Rebuild Citizen Astronomy with the latest packaging files, or run from source in a Python 3.11+ virtual environment."
        )
    python_command = Path(sys.executable).name
    return (
        "MP4 export requires FFmpeg from the imageio-ffmpeg package.\n\n"
        "Install it into the same Python environment that launches the app:\n"
        f"  {python_command} -m pip install imageio-ffmpeg\n\n"
        "Citizen Astronomy requires Python 3.11 or newer. If `pip install -e .` failed on Python 3.10, create and use a 3.11+ virtual environment first."
    )


def mp4_export_unavailable_message(reason: str) -> str:
    cleaned_reason = str(reason or "").strip()
    hint = mp4_export_install_hint()
    if cleaned_reason and cleaned_reason not in hint:
        return f"{cleaned_reason}\n\n{hint}"
    return hint


_MP4_EXPORT_INSTALL_HINT = mp4_export_install_hint()


def _subprocess_no_window_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def _ffmpeg_executable_works(ffmpeg_path: Path) -> bool:
    if not ffmpeg_path.is_file():
        return False
    try:
        subprocess.run(
            [str(ffmpeg_path), "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            **_subprocess_no_window_kwargs(),
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _ffmpeg_candidates_for_bundle(bundle_root: Path) -> list[Path]:
    candidates: list[Path] = []
    binaries_dir = bundle_root / "imageio_ffmpeg" / "binaries"
    if binaries_dir.is_dir():
        candidates.extend(sorted(binaries_dir.glob("ffmpeg*")))
    candidates.extend(sorted(bundle_root.rglob("ffmpeg-win*.exe")))
    candidates.extend(sorted(bundle_root.rglob("ffmpeg-linux*")))
    candidates.extend(sorted(bundle_root.rglob("ffmpeg-osx*")))
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if candidate.is_file() and resolved not in seen:
            seen.add(resolved)
            unique_candidates.append(candidate)
    return unique_candidates


def resolve_ffmpeg_executable() -> str:
    configured = os.environ.get("IMAGEIO_FFMPEG_EXE", "").strip()
    if configured:
        configured_path = Path(configured)
        if _ffmpeg_executable_works(configured_path):
            return str(configured_path)

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", ""))
        if bundle_root.is_dir():
            for candidate in _ffmpeg_candidates_for_bundle(bundle_root):
                if _ffmpeg_executable_works(candidate):
                    os.environ["IMAGEIO_FFMPEG_EXE"] = str(candidate)
                    return str(candidate)

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        if getattr(sys, "frozen", False):
            raise ValueError(
                "This installed build does not include the FFmpeg runtime needed for MP4 export."
            ) from exc
        raise ValueError(
            "The Python environment running Citizen Astronomy does not include imageio-ffmpeg."
        ) from exc

    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc

    ffmpeg_path = Path(ffmpeg_exe)
    if not _ffmpeg_executable_works(ffmpeg_path):
        raise ValueError(f"The FFmpeg binary is missing or not executable: {ffmpeg_exe}")
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg_path)
    return str(ffmpeg_path)


def mp4_export_dependencies_available() -> tuple[bool, str]:
    try:
        resolve_ffmpeg_executable()
    except ValueError as exc:
        return False, str(exc)
    return True, ""


def _configure_imageio_ffmpeg() -> str:
    return resolve_ffmpeg_executable()


class _SubprocessFfmpegMp4Encoder:
    def __init__(self, output_path: Path, *, ffmpeg_exe: str, fps: float) -> None:
        self._output_path = Path(output_path)
        self._ffmpeg_exe = ffmpeg_exe
        self._fps = max(1.0, float(fps))
        self._process: subprocess.Popen[bytes] | None = None
        self._frame_size: tuple[int, int] | None = None

    @staticmethod
    def _write_stdin_chunked(process: subprocess.Popen[bytes], payload: bytes) -> None:
        if process.stdin is None:
            raise RuntimeError("FFmpeg stdin is not available.")
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = process.stdin.write(view[offset:])
            if written is None or written <= 0:
                break
            offset += int(written)

    def _raise_ffmpeg_failure(self, exc: Exception | None = None) -> None:
        process = self._process
        stderr_text = ""
        if process is not None and process.stderr is not None:
            stderr_text = process.stderr.read().decode(errors="ignore").strip()
        if process is not None and process.poll() is None:
            process.kill()
            self._process = None
        prefix = "FFmpeg failed to encode the MP4 video."
        if exc is not None:
            prefix = f"{prefix} ({exc})"
        if stderr_text:
            raise ValueError(f"{prefix}\n\n{stderr_text}")
        raise ValueError(prefix)

    def append_rgb_frame(self, rgb_array: np.ndarray) -> None:
        frame = np.ascontiguousarray(rgb_array, dtype=np.uint8)
        height, width, channels = frame.shape
        if channels != 3:
            raise ValueError("MP4 export requires RGB frames.")
        frame_size = (width, height)
        if self._process is None:
            command = [
                self._ffmpeg_exe,
                "-y",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-s",
                f"{width}x{height}",
                "-pix_fmt",
                "rgb24",
                "-r",
                f"{self._fps:.6f}",
                "-i",
                "-",
                "-an",
                "-vcodec",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "23",
                "-r",
                f"{self._fps:.6f}",
                "-loglevel",
                "error",
                str(self._output_path),
            ]
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **_subprocess_no_window_kwargs(),
            )
            self._frame_size = frame_size
        elif frame_size != self._frame_size:
            raise ValueError("All frames in an MP4 export must have the same dimensions.")
        try:
            self._write_stdin_chunked(self._process, frame.tobytes())
        except OSError as exc:
            self._raise_ffmpeg_failure(exc)
        if self._process.poll() is not None:
            self._raise_ffmpeg_failure()

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        stderr_bytes = self._process.stderr.read() if self._process.stderr is not None else b""
        return_code = self._process.wait()
        self._process = None
        if return_code != 0:
            stderr_text = stderr_bytes.decode(errors="ignore").strip()
            detail = f"\n\n{stderr_text}" if stderr_text else ""
            raise ValueError(f"FFmpeg failed to encode the MP4 video.{detail}")


def astrostack_gif_export_frame_indices(
    total_frames: int,
    *,
    max_export_frames: int = _ASTROSTACK_GIF_MAX_EXPORT_FRAMES,
) -> tuple[int, ...]:
    normalized_total = max(0, int(total_frames))
    normalized_max = max(2, int(max_export_frames))
    if normalized_total <= 0:
        return ()
    if normalized_total <= normalized_max:
        return tuple(range(1, normalized_total + 1))
    indices: list[int] = []
    for step_index in range(normalized_max):
        frame_index = 1 + int(round(step_index * (normalized_total - 1) / (normalized_max - 1)))
        indices.append(frame_index)
    return tuple(sorted(set(indices)))


def resolve_sky_explorer_comparison_animation_frame_count(
    duration_seconds: float,
    *,
    fps: float = _SKY_EXPLORER_COMPARISON_ANIMATION_FPS,
    ping_pong: bool = False,
    divider_travel_pixels: float | None = None,
    smooth_motion: bool = False,
    max_frames: int = _SKY_EXPLORER_COMPARISON_ANIMATION_MAX_FRAMES,
) -> int:
    normalized_duration = max(0.5, float(duration_seconds))
    normalized_fps = max(1.0, min(120.0, float(fps)))
    frame_count = max(2, int(round(normalized_duration * normalized_fps)))

    if smooth_motion and divider_travel_pixels is not None:
        travel_pixels = float(divider_travel_pixels)
        if ping_pong:
            travel_pixels *= 2.0
        if travel_pixels > 0.0:
            min_frames = max(
                2,
                int(
                    math.ceil(
                        travel_pixels / max(0.5, float(_SKY_EXPLORER_COMPARISON_SMOOTH_MAX_PIXEL_STEP))
                    )
                )
                + 1,
            )
            frame_count = max(frame_count, min_frames)

    return min(max(2, frame_count), max(2, int(max_frames)))


def resolve_sky_explorer_comparison_animation_timing(
    duration_seconds: float,
    *,
    fps: float = _SKY_EXPLORER_COMPARISON_ANIMATION_FPS,
    ping_pong: bool = False,
    divider_travel_pixels: float | None = None,
    smooth_motion: bool = False,
    max_frames: int = _SKY_EXPLORER_COMPARISON_ANIMATION_MAX_FRAMES,
    gif_mode: bool = False,
) -> tuple[int, int]:
    """Return frame count and per-frame duration (ms) for the requested total duration."""
    frame_count = resolve_sky_explorer_comparison_animation_frame_count(
        duration_seconds,
        fps=fps,
        ping_pong=ping_pong,
        divider_travel_pixels=divider_travel_pixels,
        smooth_motion=smooth_motion,
        max_frames=max_frames,
    )
    duration_ms = max(500.0, float(duration_seconds) * 1000.0)
    if gif_mode:
        max_gif_frames = max(2, int(duration_ms // _GIF_MIN_FRAME_DURATION_MS))
        frame_count = min(frame_count, max_gif_frames)
        frame_duration_ms = max(
            _GIF_MIN_FRAME_DURATION_MS,
            int(round(duration_ms / max(1, frame_count))),
        )
    else:
        frame_duration_ms = max(1, int(round(duration_ms / max(1, frame_count))))
    return frame_count, frame_duration_ms


def sky_explorer_comparison_split_fractions(
    *,
    frame_count: int,
    ping_pong: bool,
) -> tuple[float, ...]:
    count = max(2, int(frame_count))
    span = _SKY_EXPLORER_COMPARISON_SPLIT_SPAN
    if not ping_pong:
        if count == 1:
            return (_SKY_EXPLORER_COMPARISON_SPLIT_MIN,)
        return tuple(
            _SKY_EXPLORER_COMPARISON_SPLIT_MIN + (span * index / (count - 1))
            for index in range(count)
        )

    fractions: list[float] = []
    for index in range(count):
        phase = index / (count - 1)
        if phase <= 0.5:
            travel = phase / 0.5
        else:
            travel = 1.0 - ((phase - 0.5) / 0.5)
        fractions.append(_SKY_EXPLORER_COMPARISON_SPLIT_MIN + (span * travel))
    return tuple(fractions)


def resolve_astrostack_stack_export_frame_indices(
    total_frames: int,
    *,
    fast_mode: bool = True,
    max_export_frames: int = _ASTROSTACK_GIF_MAX_EXPORT_FRAMES,
) -> tuple[int, ...]:
    normalized_total = max(0, int(total_frames))
    if normalized_total <= 0:
        return ()
    if not fast_mode:
        return tuple(range(1, normalized_total + 1))
    return astrostack_gif_export_frame_indices(normalized_total, max_export_frames=max_export_frames)


def _qimage_to_pil_rgb(image: QImage) -> Image.Image:
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError("Could not open the in-memory buffer for GIF export.")
    if not image.save(buffer, "PNG"):
        raise ValueError("Could not encode a GIF frame for export.")

    encoded_png = bytes(buffer.data())
    with BytesIO(encoded_png) as payload:
        frame = Image.open(payload)
        frame.load()
    return frame.convert("RGB")


def _build_gif_palette_source_rgb(frames: Sequence[Image.Image]) -> Image.Image:
    rgb_frames = [frame.convert("RGB") for frame in frames]
    if not rgb_frames:
        raise ValueError("GIF export requires at least one frame.")
    if len(rgb_frames) == 1:
        return rgb_frames[0]

    sample_indices = sorted({0, len(rgb_frames) // 2, len(rgb_frames) - 1})
    samples = [rgb_frames[index] for index in sample_indices]
    width, height = samples[0].size
    montage = Image.new("RGB", (width * len(samples), height))
    for index, sample in enumerate(samples):
        montage.paste(sample, (index * width, 0))
    return montage


def _quantize_pil_frames_for_gif(frames: Sequence[Image.Image]) -> list[Image.Image]:
    if not frames:
        return []
    rgb_frames = [frame.convert("RGB") for frame in frames]
    palette_source = _build_gif_palette_source_rgb(rgb_frames)
    try:
        palette_frame = palette_source.quantize(
            colors=256,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
    except Exception:
        palette_frame = palette_source.quantize(colors=256)

    quantized_frames: list[Image.Image] = []
    for frame in rgb_frames:
        try:
            quantized_frames.append(
                frame.quantize(
                    palette=palette_frame,
                    dither=Image.Dither.FLOYDSTEINBERG,
                ).copy()
            )
        except Exception:
            quantized_frames.append(
                frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=255).copy()
            )
    return quantized_frames


def _qimage_to_gif_frame(image: QImage) -> Image.Image:
    rgb_frame = _qimage_to_pil_rgb(image)
    try:
        return rgb_frame.quantize(
            colors=256,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
    except Exception:
        return rgb_frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)


def _scaled_qimage(image: QImage, scale_percent: int) -> QImage:
    normalized_scale = min(100, max(10, int(scale_percent)))
    if normalized_scale == 100:
        return image
    width = max(1, int(round(image.width() * normalized_scale / 100.0)))
    height = max(1, int(round(image.height() * normalized_scale / 100.0)))
    return image.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _qimage_to_rgb_array_via_png(image: QImage) -> np.ndarray:
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError("Could not open the in-memory buffer for animation export.")
    if not image.save(buffer, "PNG"):
        raise ValueError("Could not encode an animation frame for export.")
    encoded_png = bytes(buffer.data())
    with BytesIO(encoded_png) as payload:
        frame = Image.open(payload)
        frame.load()
    return np.ascontiguousarray(frame.convert("RGB"))


def _qimage_to_rgb_array(image: QImage) -> np.ndarray:
    if image.isNull():
        raise ValueError("Cannot export a null image frame.")
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    if converted.isNull():
        raise ValueError("Could not convert an export frame to RGB888.")
    width = max(1, int(converted.width()))
    height = max(1, int(converted.height()))
    bytes_per_line = max(width * 3, int(converted.bytesPerLine()))
    byte_count = height * bytes_per_line
    payload = converted.constBits().tobytes()
    if len(payload) < byte_count:
        payload = converted.bits().tobytes()
    if len(payload) < byte_count:
        return _qimage_to_rgb_array_via_png(image)
    frame_buffer = np.frombuffer(payload, dtype=np.uint8, count=byte_count)
    frame_rows = frame_buffer.reshape((height, bytes_per_line))[:, : width * 3]
    rgb_array = np.ascontiguousarray(frame_rows.reshape((height, width, 3)))
    if int(rgb_array.max()) == 0:
        return _qimage_to_rgb_array_via_png(image)
    return rgb_array


_MP4_H264_MACRO_BLOCK_SIZE = 16


def _mp4_aligned_dimension(value: int) -> int:
    normalized = max(1, int(value))
    remainder = normalized % _MP4_H264_MACRO_BLOCK_SIZE
    if remainder == 0:
        return normalized
    return normalized + (_MP4_H264_MACRO_BLOCK_SIZE - remainder)


def _mp4_compatible_qimage(image: QImage) -> QImage:
    if image.isNull():
        raise ValueError("Cannot pad a null image frame.")
    source = image.convertToFormat(QImage.Format.Format_RGB888)
    if source.isNull():
        source = image.copy()
    width = source.width()
    height = source.height()
    padded_width = _mp4_aligned_dimension(width)
    padded_height = _mp4_aligned_dimension(height)
    if padded_width == width and padded_height == height:
        return source

    padded = QImage(padded_width, padded_height, QImage.Format.Format_RGB888)
    padded.fill(Qt.GlobalColor.black)
    painter = QPainter(padded)
    try:
        painter.drawImage(0, 0, source)
        if padded_width != width:
            painter.drawImage(width, 0, source, width - 1, 0, 1, height)
        if padded_height != height:
            painter.drawImage(0, height, source, 0, height - 1, width, 1)
        if padded_width != width and padded_height != height:
            painter.drawImage(width, height, source, width - 1, height - 1, 1, 1)
    finally:
        painter.end()
    return padded


class StreamingGifWriter:
    def __init__(
        self,
        output_path: Path,
        *,
        frame_duration_ms: int,
        loop_count: int | None = 0,
        scale_percent: int = 100,
    ) -> None:
        self._output_path = Path(output_path)
        self._frame_duration_ms = max(1, int(frame_duration_ms))
        self._loop_count = loop_count
        self._scale_percent = scale_percent
        self._rgb_frames: list[Image.Image] = []
        self._frame_count = 0

    def __enter__(self) -> StreamingGifWriter:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    def append_qimage(self, image: QImage) -> None:
        self._rgb_frames.append(_qimage_to_pil_rgb(_scaled_qimage(image, self._scale_percent)))
        self._frame_count += 1

    def __exit__(self, *_args: object) -> None:
        if not self._rgb_frames:
            return
        pil_frames = _quantize_pil_frames_for_gif(self._rgb_frames)
        frame_duration_ms = self._frame_duration_ms
        save_kwargs: dict[str, object] = {
            "save_all": True,
            "append_images": pil_frames[1:],
            "duration": [frame_duration_ms] * len(pil_frames),
            "disposal": 2,
        }
        if self._loop_count is not None:
            save_kwargs["loop"] = max(0, int(self._loop_count))
        pil_frames[0].save(self._output_path, **save_kwargs)
        self._rgb_frames.clear()

    @property
    def frame_count(self) -> int:
        return self._frame_count


class StreamingMp4Writer:
    def __init__(
        self,
        output_path: Path,
        *,
        frame_duration_ms: int,
        scale_percent: int = 100,
    ) -> None:
        self._output_path = Path(output_path)
        self._frame_duration_ms = max(1, int(frame_duration_ms))
        self._scale_percent = scale_percent
        self._ffmpeg_exe: str | None = None
        self._subprocess_encoder: _SubprocessFfmpegMp4Encoder | None = None
        self._frame_count = 0

    def __enter__(self) -> StreamingMp4Writer:
        try:
            self._ffmpeg_exe = _configure_imageio_ffmpeg()
        except ValueError as exc:
            raise ValueError(mp4_export_unavailable_message(str(exc))) from exc

        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    def append_qimage(self, image: QImage) -> None:
        if self._ffmpeg_exe is None:
            raise RuntimeError("StreamingMp4Writer is not open.")
        rgb_array = _qimage_to_rgb_array(
            _mp4_compatible_qimage(_scaled_qimage(image, self._scale_percent))
        )
        if self._subprocess_encoder is None:
            fps = max(1.0, 1000.0 / self._frame_duration_ms)
            self._subprocess_encoder = _SubprocessFfmpegMp4Encoder(
                self._output_path,
                ffmpeg_exe=self._ffmpeg_exe,
                fps=fps,
            )
        self._subprocess_encoder.append_rgb_frame(rgb_array)
        self._frame_count += 1

    def __exit__(self, *_args: object) -> None:
        if self._subprocess_encoder is not None:
            self._subprocess_encoder.close()
            self._subprocess_encoder = None

    @property
    def frame_count(self) -> int:
        return self._frame_count


def export_qimages_to_gif_for_total_duration(
    frames: Sequence[QImage],
    output_path: Path,
    *,
    total_duration_seconds: float,
    loop_count: int | None = 0,
    scale_percent: int = 100,
) -> None:
    if not frames:
        raise ValueError("GIF export requires at least one frame.")
    duration_ms = max(
        500.0,
        float(total_duration_seconds) * 1000.0,
    )
    frame_duration_ms = max(
        _GIF_MIN_FRAME_DURATION_MS,
        int(round(duration_ms / max(1, len(frames)))),
    )
    export_qimages_to_gif(
        frames,
        output_path,
        frame_duration_ms=frame_duration_ms,
        loop_count=loop_count,
        scale_percent=scale_percent,
    )


def export_qimages_to_mp4_for_total_duration(
    frames: Sequence[QImage],
    output_path: Path,
    *,
    total_duration_seconds: float,
    scale_percent: int = 100,
) -> None:
    if not frames:
        raise ValueError("MP4 export requires at least one frame.")
    duration_ms = max(
        1,
        int(round(float(total_duration_seconds) * 1000.0 / max(1, len(frames)))),
    )
    export_qimages_to_mp4(
        frames,
        output_path,
        frame_duration_ms=duration_ms,
        scale_percent=scale_percent,
    )


def export_qimages_to_gif(
    frames: Sequence[QImage],
    output_path: Path,
    *,
    frame_duration_ms: int,
    loop_count: int | None = 0,
    scale_percent: int = 100,
) -> None:
    if not frames:
        raise ValueError("Blink export requires at least one frame.")

    with StreamingGifWriter(
        output_path,
        frame_duration_ms=frame_duration_ms,
        loop_count=loop_count,
        scale_percent=scale_percent,
    ) as writer:
        for frame in frames:
            writer.append_qimage(frame)


def export_qimages_to_mp4(
    frames: Sequence[QImage],
    output_path: Path,
    *,
    frame_duration_ms: int,
    scale_percent: int = 100,
) -> None:
    if not frames:
        raise ValueError("Blink export requires at least one frame.")

    with StreamingMp4Writer(
        output_path,
        frame_duration_ms=frame_duration_ms,
        scale_percent=scale_percent,
    ) as writer:
        for frame in frames:
            writer.append_qimage(frame)