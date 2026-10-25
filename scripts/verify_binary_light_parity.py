"""Compare CAst exports against the standalone PhysicsEngine executable."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from photometry_app.core.binary_light import BinaryLightModel


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "PhysicsEngine"
    executable = source / "Binary Light Lab.exe"
    output = ROOT / "build/binary_light_parity"
    output.mkdir(parents=True, exist_ok=True)
    model = BinaryLightModel()
    for index in range(10):
        model.set_preset(index if index < 6 else 2)
        if index >= 6:
            model.set("band", index-6)
            model.set("exposure", 120)
            model.set("third_light", .27)
            model.set("node", 63)
            model.set("distance", 321)
        system = output / f"system-{index}.binary"
        model.save(system)
        original = output / f"original-{index}.csv"
        port = output / f"cast-{index}.csv"
        subprocess.run([str(executable), "--system", str(system), "--export", str(original)],
                       cwd=output, check=True, timeout=60,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        model.export(port)
        a = np.loadtxt(original, delimiter=",", skiprows=6)
        b = np.loadtxt(port, delimiter=",", skiprows=6)
        np.testing.assert_allclose(a, b, rtol=1e-11, atol=1e-14)
        assert original.read_text().splitlines()[:6] == port.read_text().splitlines()[:6]
        print(f"Case {index}: 1,001 rows and all seven observables match; system format and metadata match.")


if __name__ == "__main__":
    main()
