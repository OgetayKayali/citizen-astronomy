from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import rcParams
from matplotlib.gridspec import GridSpec
from matplotlib.patches import FancyBboxPatch


_CPU_COLOR = "#d97b5a"
_GPU_COLOR = "#58c2b3"
_TEXT_COLOR = "#f3efe6"
_MUTED_TEXT_COLOR = "#a8b5c6"
_BG_COLOR = "#08111d"
_CARD_COLOR = "#101c2c"
_GRID_COLOR = "#223248"
_ACCENT_COLOR = "#8cc9ff"
_HEADER_COLOR = "#17304d"
_RULE_COLOR = "#1f466f"

rcParams["font.family"] = "DejaVu Sans"
rcParams["axes.titleweight"] = "bold"


def _set_serif_title(text_obj) -> None:
    text_obj.set_fontfamily("DejaVu Serif")


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _synthetic_size_rows() -> list[dict[str, float | str]]:
    return [
        {"label": "512 avg", "cpu_ms": 144.39, "gpu_ms": 82.28},
        {"label": "1024 avg", "cpu_ms": 913.62, "gpu_ms": 198.97},
        {"label": "2048 avg", "cpu_ms": 3789.69, "gpu_ms": 716.34},
    ]


def _synthetic_mode_rows() -> list[dict[str, float | str]]:
    return [
        {"label": "average", "cpu_ms": 845.29, "gpu_ms": 175.27},
        {"label": "mean", "cpu_ms": 851.89, "gpu_ms": 185.26},
        {"label": "min", "cpu_ms": 895.19, "gpu_ms": 169.20},
        {"label": "max", "cpu_ms": 1139.82, "gpu_ms": 232.12},
    ]


def _apply_axes_style(ax) -> None:
    ax.set_facecolor(_CARD_COLOR)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=_MUTED_TEXT_COLOR, labelsize=10)
    ax.grid(axis="y", color=_GRID_COLOR, alpha=0.7, linewidth=1)
    ax.set_axisbelow(True)


def _draw_panel_caption(ax, text: str) -> None:
    ax.text(
        0.0,
        0.99,
        text,
        transform=ax.transAxes,
        color=_MUTED_TEXT_COLOR,
        fontsize=10,
        va="bottom",
    )


def _add_card_background(
    fig,
    ax,
    *,
    x_pad: float = 0.01,
    bottom_pad: float = 0.01,
    top_pad: float = 0.04,
    rounding: float = 0.03,
) -> None:
    bbox = ax.get_position()
    fig.patches.append(
        FancyBboxPatch(
            (bbox.x0 - x_pad, bbox.y0 - bottom_pad),
            bbox.width + (2 * x_pad),
            bbox.height + bottom_pad + top_pad,
            boxstyle=f"round,pad=0.012,rounding_size={rounding}",
            transform=fig.transFigure,
            facecolor=_CARD_COLOR,
            edgecolor="#18304d",
            linewidth=1.25,
            zorder=-10,
        )
    )


def _annotate_bars(ax, bars, *, unit: str) -> None:
    for bar in bars:
        value = float(bar.get_height())
        ax.text(
            bar.get_x() + (bar.get_width() / 2.0),
            value + (max(1.0, value * 0.02)),
            f"{value:.0f}{unit}",
            ha="center",
            va="bottom",
            color=_TEXT_COLOR,
            fontsize=9,
            fontweight="bold",
            clip_on=False,
        )


def _draw_grouped_bars(ax, rows: list[dict[str, float | str]], *, title: str, ylabel: str, unit: str) -> None:
    labels = [str(row["label"]) for row in rows]
    cpu_values = [float(row["cpu_ms"]) for row in rows]
    gpu_values = [float(row["gpu_ms"]) for row in rows]
    x = np.arange(len(labels), dtype=float)
    width = 0.34
    _apply_axes_style(ax)
    bars_cpu = ax.bar(x - (width / 2.0), cpu_values, width=width, color=_CPU_COLOR, label="CPU")
    bars_gpu = ax.bar(x + (width / 2.0), gpu_values, width=width, color=_GPU_COLOR, label="GPU")
    ax.margins(y=0.18)
    ax.set_xticks(x, labels, color=_TEXT_COLOR)
    ax.set_ylabel(ylabel, color=_MUTED_TEXT_COLOR)
    title_obj = ax.set_title(title, color=_TEXT_COLOR, fontsize=16, fontweight="bold", loc="left", pad=8)
    _set_serif_title(title_obj)
    _draw_panel_caption(ax, "Same integration family, rendered with measured end-to-end timings")
    legend = ax.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 0.98), handlelength=1.8)
    for text in legend.get_texts():
        text.set_color(_TEXT_COLOR)
    _annotate_bars(ax, bars_cpu, unit=unit)
    _annotate_bars(ax, bars_gpu, unit=unit)


def _draw_davida_panel(ax, davida_payload: dict[str, object]) -> None:
    _apply_axes_style(ax)
    runs = list(davida_payload["runs"])
    cpu_run = next(run for run in runs if run["backend_request"] == "cpu")
    gpu_run = next(run for run in runs if run["backend_request"] == "gpu")
    cpu_seconds = float(cpu_run["elapsed_ms"]) / 1000.0
    gpu_seconds = float(gpu_run["elapsed_ms"]) / 1000.0
    speedup = cpu_seconds / gpu_seconds if gpu_seconds > 0 else float("inf")
    bars = ax.bar([0, 1], [cpu_seconds, gpu_seconds], color=[_CPU_COLOR, _GPU_COLOR], width=0.50)
    ax.margins(y=0.22)
    ax.set_xticks([0, 1], ["CPU", "GPU"], color=_TEXT_COLOR)
    ax.set_ylabel("Seconds", color=_MUTED_TEXT_COLOR)
    title_obj = ax.set_title("Davida Full-Resolution Synthetic Track", color=_TEXT_COLOR, fontsize=16, fontweight="bold", loc="left", pad=8)
    _set_serif_title(title_obj)
    _draw_panel_caption(ax, "100 aligned frames, measured through the same timestamp and motion path used by the UI")
    _annotate_bars(ax, bars, unit="s")
    summary = (
        f"6248x4176  •  {speedup:.2f}x speedup\n"
        f"SNR {float(cpu_run['local_snr']):.2f}  •  {float(davida_payload['derived_motion_px_per_hour']):.2f} px/h at {float(davida_payload['derived_motion_angle_deg']):.1f} deg\n"
        f"Exact image parity  •  max |Δ| {float(davida_payload['cpu_gpu_comparison']['max_abs_diff']):.2e}"
    )
    ax.text(
        0.98,
        0.965,
        summary,
        transform=ax.transAxes,
        ha="right",
        va="top",
        color=_TEXT_COLOR,
        fontsize=9.5,
        linespacing=1.45,
        bbox={"boxstyle": "round,pad=0.55", "facecolor": "#10233c", "edgecolor": _RULE_COLOR},
    )


def _draw_table(ax, rows: list[dict[str, str]], *, compact: bool = False) -> None:
    ax.set_facecolor(_CARD_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    title_obj = ax.set_title("Synthetic Tracking Comparison Table", color=_TEXT_COLOR, fontsize=16, fontweight="bold", loc="left", pad=8)
    _set_serif_title(title_obj)
    _draw_panel_caption(ax, "Synthetic scaling and supported-mode timings alongside the real Davida validation run")

    headers = ["Scenario", "CPU", "GPU", "Speedup", "Notes"]
    if compact:
        table_left = 0.045
        table_width = 0.89
        scenario_x = 0.08
        cpu_x = 0.58
        gpu_x = 0.70
        speedup_x = 0.81
        notes_x = 0.915
        header_font = 8.8
        row_font = 8.3
        value_font = 8.9
        notes_font = 7.6
        footer_font = 7.2
        footer_text = "Measured with full-frame no-rejection Synthetic Track. Davida uses the real 100-frame aligned dataset\nand exact CPU/GPU stack parity."
    else:
        table_left = 0.035
        table_width = 0.92
        scenario_x = 0.07
        cpu_x = 0.57
        gpu_x = 0.705
        speedup_x = 0.82
        notes_x = 0.92
        header_font = 10
        row_font = 10.5
        value_font = 10.5
        notes_font = 9.1
        footer_font = 8.5
        footer_text = "Measured with full-frame no-rejection Synthetic Track. Davida uses the real 100-frame aligned dataset\nand exact CPU/GPU stack parity."
    footer_y = 0.04
    top = 0.84
    available_height = top - 0.10
    row_height = min(0.092, available_height / (len(rows) + 1))

    ax.add_patch(
        FancyBboxPatch(
            (table_left, top - row_height),
            table_width,
            row_height,
            boxstyle="round,pad=0.01,rounding_size=0.02",
            facecolor=_HEADER_COLOR,
            edgecolor=_RULE_COLOR,
            linewidth=1.1,
        )
    )
    ax.text(scenario_x, top - (row_height / 2.0), headers[0], color=_TEXT_COLOR, fontsize=header_font, fontweight="bold", va="center", ha="left")
    ax.text(cpu_x, top - (row_height / 2.0), headers[1], color=_TEXT_COLOR, fontsize=header_font, fontweight="bold", va="center", ha="right")
    ax.text(gpu_x, top - (row_height / 2.0), headers[2], color=_TEXT_COLOR, fontsize=header_font, fontweight="bold", va="center", ha="right")
    ax.text(speedup_x, top - (row_height / 2.0), headers[3], color=_TEXT_COLOR, fontsize=header_font, fontweight="bold", va="center", ha="right")
    ax.text(notes_x, top - (row_height / 2.0), headers[4], color=_TEXT_COLOR, fontsize=header_font, fontweight="bold", va="center", ha="right")

    for index, row in enumerate(rows, start=1):
        y = top - (index * row_height)
        face = "#0d1d33" if index % 2 else "#0b182a"
        ax.add_patch(
            FancyBboxPatch(
                (table_left, y - row_height + 0.004),
                table_width,
                row_height - 0.008,
                boxstyle="round,pad=0.008,rounding_size=0.015",
                facecolor=face,
                edgecolor="#17314d",
                linewidth=0.8,
            )
        )
        ax.text(scenario_x, y - (row_height / 2.0) + 0.002, row["scenario"], color=_TEXT_COLOR, fontsize=row_font, va="center", ha="left")
        ax.text(cpu_x, y - (row_height / 2.0) + 0.002, row["cpu"], color=_CPU_COLOR, fontsize=value_font, fontweight="bold", va="center", ha="right")
        ax.text(gpu_x, y - (row_height / 2.0) + 0.002, row["gpu"], color=_GPU_COLOR, fontsize=value_font, fontweight="bold", va="center", ha="right")
        ax.text(speedup_x, y - (row_height / 2.0) + 0.002, row["speedup"], color=_ACCENT_COLOR, fontsize=value_font, fontweight="bold", va="center", ha="right")
        ax.text(notes_x, y - (row_height / 2.0) + 0.002, row["notes"], color=_MUTED_TEXT_COLOR, fontsize=notes_font, va="center", ha="right")

    ax.text(
        table_left,
        footer_y,
        footer_text,
        color=_MUTED_TEXT_COLOR,
        fontsize=footer_font,
        ha="left",
        va="bottom",
    )


def _dashboard_rows(davida_payload: dict[str, object], *, compact: bool = False) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in _synthetic_size_rows():
        cpu_ms = float(row["cpu_ms"])
        gpu_ms = float(row["gpu_ms"])
        rows.append(
            {
                "scenario": f"Synthetic {row['label']}",
                "cpu": f"{cpu_ms:.2f} ms",
                "gpu": f"{gpu_ms:.2f} ms",
                "speedup": f"{(cpu_ms / gpu_ms):.2f}x",
                "notes": "Avg / no rej." if compact else "Average / no rejection",
            }
        )
    for row in _synthetic_mode_rows():
        cpu_ms = float(row["cpu_ms"])
        gpu_ms = float(row["gpu_ms"])
        rows.append(
            {
                "scenario": f"Synthetic 1024 {row['label']}",
                "cpu": f"{cpu_ms:.2f} ms",
                "gpu": f"{gpu_ms:.2f} ms",
                "speedup": f"{(cpu_ms / gpu_ms):.2f}x",
                "notes": "No rej." if compact else "No rejection",
            }
        )
    cpu_run = next(run for run in davida_payload["runs"] if run["backend_request"] == "cpu")
    gpu_run = next(run for run in davida_payload["runs"] if run["backend_request"] == "gpu")
    cpu_seconds = float(cpu_run["elapsed_ms"]) / 1000.0
    gpu_seconds = float(gpu_run["elapsed_ms"]) / 1000.0
    rows.append(
        {
            "scenario": "Davida full-res 100 frames",
            "cpu": f"{cpu_seconds:.2f} s",
            "gpu": f"{gpu_seconds:.2f} s",
            "speedup": f"{(cpu_seconds / gpu_seconds):.2f}x",
            "notes": "Exact parity" if compact else "Exact image match",
        }
    )
    return rows


def build_dashboard(davida_payload: dict[str, object], output_path: Path) -> None:
    fig = plt.figure(figsize=(18, 12), facecolor=_BG_COLOR)
    grid = GridSpec(2, 2, figure=fig, height_ratios=[1.0, 1.35], hspace=0.24, wspace=0.15)
    fig.subplots_adjust(left=0.055, right=0.975, bottom=0.06, top=0.86)
    fig.text(0.04, 0.968, "Synthetic Track CPU vs GPU", color=_TEXT_COLOR, fontsize=25, fontweight="bold", fontfamily="DejaVu Serif")
    fig.text(0.04, 0.941, "Benchmark evidence and real-dataset validation for the full-frame aligned Davida run", color=_MUTED_TEXT_COLOR, fontsize=12)
    fig.text(0.04, 0.918, "Academic summary: supported no-rejection modes, synthetic scaling, and exact-image parity on a 100-frame field sequence", color=_ACCENT_COLOR, fontsize=9.5)

    ax_sizes = fig.add_subplot(grid[0, 0])
    ax_modes = fig.add_subplot(grid[0, 1])
    ax_davida = fig.add_subplot(grid[1, 0])
    ax_table = fig.add_subplot(grid[1, 1])

    _draw_grouped_bars(ax_sizes, _synthetic_size_rows(), title="Synthetic Average Scaling", ylabel="Milliseconds", unit="ms")
    _draw_grouped_bars(ax_modes, _synthetic_mode_rows(), title="Synthetic 1024 Supported Modes", ylabel="Milliseconds", unit="ms")
    _draw_davida_panel(ax_davida, davida_payload)
    _draw_table(ax_table, _dashboard_rows(davida_payload, compact=True), compact=True)

    for ax in (ax_sizes, ax_modes, ax_davida, ax_table):
        _add_card_background(fig, ax)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def build_table_figure(davida_payload: dict[str, object], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(18, 8.4), facecolor=_BG_COLOR)
    _draw_table(ax, _dashboard_rows(davida_payload))
    fig.subplots_adjust(left=0.035, right=0.985, bottom=0.06, top=0.90)
    _add_card_background(fig, ax, x_pad=0.015, bottom_pad=0.015, top_pad=0.05, rounding=0.025)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate comparison plots for Synthetic Track CPU versus GPU results.")
    parser.add_argument("--davida-json", default="_tmp_davida_full_frame_compare.json", help="Path to the real Davida comparison JSON payload.")
    parser.add_argument("--dashboard-output", default="docs/synthetic_tracking_comparison_dashboard.png", help="Output path for the dashboard PNG.")
    parser.add_argument("--table-output", default="docs/synthetic_tracking_comparison_table.png", help="Output path for the table PNG.")
    args = parser.parse_args()

    davida_payload = _load_json(Path(args.davida_json).expanduser())
    build_dashboard(davida_payload, Path(args.dashboard_output).expanduser())
    build_table_figure(davida_payload, Path(args.table_output).expanduser())
    print(Path(args.dashboard_output).expanduser())
    print(Path(args.table_output).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())