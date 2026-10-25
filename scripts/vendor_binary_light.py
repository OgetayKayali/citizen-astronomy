"""Copy the unmodified Binary Light Lab solver and shader from PhysicsEngine."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "PhysicsEngine"
    target = ROOT / "native/binary_light/vendor"
    hashes = {}
    for name in ("src/photometry/binary.hpp", "src/photometry/binary.cpp", "apps/photometry/shaders.hpp", "tests/test_photometry.cpp"):
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)
        hashes[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    (target.parent / "source_manifest.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
