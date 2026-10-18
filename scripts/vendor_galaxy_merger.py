"""Copy the PhysicsEngine galaxy merger into native/merger/vendor.

The simulation library is copied unchanged. ``apps/viewer/main.cpp`` becomes
``viewer_core.hpp``: the same code with the ImGui panel, the GLFW window and
``main()`` removed, so CAst's bridge (native/merger/cast_merger.cpp) can drive
the viewer's own simulation, rendering and export functions. ``draw_scene`` is
renamed ``lab_draw_scene`` so the bridge can add CAst's optional sky after it.

Usage: python scripts/vendor_galaxy_merger.py [path\\to\\PhysicsEngine]
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "native" / "merger" / "vendor"
EXAMPLES = ROOT / "photometry_app" / "native" / "galaxy_merger"
LIBRARY = (
    "src/core/component.hpp",
    "src/core/spatial_tree.hpp",
    "src/core/state.cpp",
    "src/core/state.hpp",
    "src/core/units.hpp",
    "src/core/vec3.hpp",
    "src/forces/forces.cpp",
    "src/forces/forces.hpp",
    "src/forces/hydro.cpp",
    "src/forces/hydro.hpp",
    "src/integrators/integrators.cpp",
    "src/integrators/integrators.hpp",
    "src/io/gltf.cpp",
    "src/io/gltf.hpp",
    "src/io/sphere.cpp",
    "src/io/sphere.hpp",
    "src/io/trajectory.cpp",
    "src/io/trajectory.hpp",
    "src/simulation/galaxy.cpp",
    "src/simulation/galaxy.hpp",
    "src/simulation/scene.cpp",
    "src/simulation/scene.hpp",
    "src/simulation/validation.cpp",
    "src/simulation/validation.hpp",
    "src/visualization/optical.cpp",
    "src/visualization/optical.hpp",
    "src/visualization/tracers.cpp",
    "src/visualization/tracers.hpp",
    "apps/viewer/gl_loader.cpp",
    "apps/viewer/gl_loader.hpp",
    "apps/viewer/optical_shaders.hpp",
    "third_party/stb_image_write.h",
)
# frame_export carries CAst's ffmpeg override; reuse the Black Hole copy.
CAST_FRAME_EXPORT = ROOT / "native" / "blackhole" / "vendor" / "apps" / "viewer"
SCENES = (
    "galaxy_luminous_merger.scene",
    "galaxy_spiral_merger.scene",
    "galaxy_merger_retrograde.scene",
    "galaxy_merger.scene",
    "broadband.look",
    "nebular.look",
)
# Top-level definitions that belong to the ImGui/GLFW front end.
REMOVED = (
    "bool edit_double(",
    "bool edit_vec3(",
    "void place_panel(",
    "void draw_parameters(",
    "void draw_appearance(",
    "void draw_export(",
    "std::filesystem::path program_directory(",
    "void save_recording(",
    "void draw_playback(",
    "struct ResolutionChoice {",
    "ResolutionChoice const kResolutions[]",
    "void apply_window_resolution(",
    "void draw_settings_menu(",
    "void draw_studio(",
    "int main(",
)
REMOVED_LINES = (
    "#include <imgui.h>",
    "#include <imgui_impl_glfw.h>",
    "#include <imgui_impl_opengl3.h>",
    "ImGuiStyle base_style{};",
    "constexpr int kResolutionCount",
)
EXAMPLES_DIRECTORY = """std::filesystem::path examples_directory() {
    // CAst: the scenes ship in photometry_app/native/galaxy_merger.
    return cast_examples_directory;
}"""
# The viewer's draw_scene keeps its body under a new name; CAst's draw_scene
# (cast_merger.cpp) calls it and then draws the optional sky, so the viewport,
# Save frame and exports all include the sky.
DRAW_SCENE = "void draw_scene("
DRAW_SCENE_DECLARATION = """
// CAst: lab_draw_scene, then CAst's optional sky (native/merger/cast_sky.hpp).
void draw_scene(SceneGpu& gpu, ViewState const& view, std::vector<physics::Vec3> const& positions, double shown_time,
                int width, int height, GLuint framebuffer);"""


def _block_end(lines: list[str], start: int) -> int:
    depth = 0
    opened = False
    for index in range(start, len(lines)):
        text = re.sub(r'"(\\.|[^"\\])*"', '""', lines[index])
        text = re.sub(r"'(\\.|[^'\\])'", "''", text)
        text = text.split("//", 1)[0]
        depth += text.count("{") - text.count("}")
        opened = opened or "{" in text
        if opened and depth == 0:
            return index
    raise ValueError(f"unbalanced block at line {start + 1}")


def strip_viewer(source: str) -> str:
    lines = source.splitlines()
    out: list[str] = []
    index = 0
    removed: set[str] = set()
    while index < len(lines):
        line = lines[index]
        if any(line.strip().startswith(prefix) for prefix in REMOVED_LINES):
            removed.add(line.strip())
            index += 1
            continue
        match = next((prefix for prefix in REMOVED if line.startswith(prefix)), None)
        if match is not None:
            removed.add(match)
            index = _block_end(lines, index) + 1
            while out and not out[-1].strip():
                out.pop()
            out.append("")
            continue
        if line.startswith("std::filesystem::path examples_directory("):
            index = _block_end(lines, index) + 1
            out.extend(EXAMPLES_DIRECTORY.splitlines())
            removed.add("examples_directory")
            continue
        if line.startswith(DRAW_SCENE):
            end = _block_end(lines, index)
            out.append("void lab_draw_scene(" + line[len(DRAW_SCENE):])
            out.extend(lines[index + 1 : end + 1])
            out.extend(DRAW_SCENE_DECLARATION.splitlines())
            removed.add("draw_scene")
            index = end + 1
            continue
        out.append(line)
        index += 1
    missing = [prefix for prefix in REMOVED if prefix not in removed]
    if missing or "examples_directory" not in removed or "draw_scene" not in removed:
        raise ValueError(f"viewer layout changed; not found: {missing}")
    leftover = [n + 1 for n, text in enumerate(out) if "ImGui" in text or "glfwSet" in text or "GLFWwindow" in text]
    if leftover:
        raise ValueError(f"front-end code left on viewer_core lines {leftover}")
    header = (
        "// Generated by scripts/vendor_galaxy_merger.py from PhysicsEngine apps/viewer/main.cpp.\n"
        "// The viewer's code without its ImGui panel, GLFW window and main(); CAst's Qt\n"
        "// panel replaces them. Included once, by native/merger/cast_merger.cpp.\n"
        "#pragma once\n"
        "#include <filesystem>\n"
        "inline std::filesystem::path cast_examples_directory;\n"
    )
    return header + "\n".join(out) + "\n"


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "PhysicsEngine"
    if not (source / "apps" / "viewer" / "main.cpp").is_file():
        print(f"PhysicsEngine not found at {source}", file=sys.stderr)
        return 1
    for relative in LIBRARY:
        destination = TARGET / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    viewer = TARGET / "apps" / "viewer"
    for name in ("frame_export.cpp", "frame_export.hpp"):
        shutil.copy2(CAST_FRAME_EXPORT / name, viewer / name)
    core = strip_viewer((source / "apps" / "viewer" / "main.cpp").read_text(encoding="utf-8"))
    (viewer / "viewer_core.hpp").write_text(core, encoding="utf-8", newline="\n")
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    for name in SCENES:
        shutil.copy2(source / "examples" / name, EXAMPLES / name)
    print(f"Vendored PhysicsEngine galaxy merger into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
