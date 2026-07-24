#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

python_path="${PYTHON:-$project_root/.venv/bin/python}"
output_dir="${OUTPUT_DIR:-$project_root/packaging/dist/velopack-linux}"
bundle_dir="$project_root/_tmp_alpha_review_dist_linux/CitizenAstronomyAlphaReview"
icon_path="$project_root/_tmp_linux_package/citizen_astronomy.png"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This build must run on Linux." >&2
    exit 1
fi
if [[ ! -x "$python_path" ]]; then
    echo "Python was not found at $python_path. Create .venv or set PYTHON." >&2
    exit 1
fi
if ! command -v vpk >/dev/null 2>&1; then
    echo "Velopack CLI 1.2.0 is required: dotnet tool install --global vpk --version 1.2.0" >&2
    exit 1
fi
if ! command -v mksquashfs >/dev/null 2>&1; then
    echo "mksquashfs is required. Install the squashfs-tools package." >&2
    exit 1
fi

velopack_help="$(vpk -H 2>&1)"
if [[ "$velopack_help" != *"Velopack CLI 1.2.0"* ]]; then
    echo "Velopack CLI 1.2.0 is required." >&2
    exit 1
fi

app_version="$(
    "$python_path" -c \
        "from photometry_app.app_metadata import APP_VERSION; print(APP_VERSION)"
)"
update_channel="$(
    "$python_path" -c \
        "from photometry_app.app_metadata import application_update_channel; print(application_update_channel())"
)"

mkdir -p "$(dirname -- "$icon_path")" "$output_dir"
SOURCE_ICON="$project_root/assets/citizen_astronomy.ico" \
TARGET_ICON="$icon_path" \
"$python_path" -c \
    "import os; from PIL import Image; Image.open(os.environ['SOURCE_ICON']).save(os.environ['TARGET_ICON'])"

if [[ ! -f packaging/fixtures/smoke_tiny.fits ]] \
    || [[ ! -f packaging/fixtures/smoke_tiny.xisf ]] \
    || [[ ! -f packaging/fixtures/smoke_tiny.png ]] \
    || [[ ! -f packaging/fixtures/smoke_tiny.webp ]]; then
    "$python_path" packaging/generate_smoke_fixtures.py
fi
"$python_path" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$project_root/_tmp_alpha_review_dist_linux" \
    --workpath "$project_root/_tmp_alpha_review_build_linux" \
    "$project_root/CitizenAstronomyAlphaReview.spec"

QT_QPA_PLATFORM=offscreen "$bundle_dir/CitizenAstronomyAlphaReview" \
    --about-dialog-smoke
QT_QPA_PLATFORM=offscreen "$bundle_dir/CitizenAstronomyAlphaReview" \
    --packaged-format-smoke \
    --packaged-format-smoke-fixtures "$project_root/packaging/fixtures" \
    --packaged-format-smoke-output "$project_root/_tmp_packaged_format_smoke_result_linux.json"

vpk pack \
    --packId CitizenAstronomy.CAst \
    --packVersion "$app_version" \
    --packDir "$bundle_dir" \
    --packTitle "Citizen Astronomy (CAst)" \
    --packAuthors "Ogetay" \
    --mainExe CitizenAstronomyAlphaReview \
    --runtime linux-x64 \
    --channel "$update_channel" \
    --delta BestSize \
    --icon "$icon_path" \
    --categories "Education;Science;Astronomy" \
    --compression xz \
    --outputDir "$output_dir"

echo "Linux AppImage and update packages written to $output_dir"
