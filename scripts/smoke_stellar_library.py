"""Exercise every Library selection and its GPU surface inside CAst."""
from pathlib import Path
import sys
import time
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from photometry_app.ui.binary_light_view import BinaryLightPanel
from photometry_app.core.variable_stars import FAMILIES

output=Path(__file__).resolve().parents[1]/'build/stellar_library_smoke'
output.mkdir(parents=True,exist_ok=True)
app=QApplication.instance() or QApplication([])
app.setStyle('Fusion')
panel=BinaryLightPanel();panel.resize(1500,1000);panel.show();QTest.qWait(200)
panel.prediction.setChecked(True)
try:
    for family in FAMILIES:
        panel.library_buttons[family].click()
        panel.tabs.setCurrentIndex(1)
        QTest.qWait(160)
        deadline=time.monotonic()+20
        while panel._curve_future and time.monotonic()<deadline:QTest.qWait(50)
        assert panel.model.family==family
        assert not panel.model.invalid, panel.model.invalid
        assert len(panel.prediction_curve)==301, (family,panel.status.text())
        assert not panel.scene.error,panel.scene.error
        panel.model.cycles=.23
        panel._tick()
        frame=panel.scene.grabFramebuffer()
        rgb=frame.convertToFormat(frame.Format.Format_RGB888)
        pixels=np.frombuffer(rgb.constBits(),dtype=np.uint8).reshape(rgb.height(),rgb.bytesPerLine())[:,:rgb.width()*3].reshape(rgb.height(),rgb.width(),3)
        assert np.count_nonzero(np.max(pixels,axis=2)>100)>100, family
        frame.save(str(output/f'{family:02d}-surface.png'))
        panel.grab().save(str(output/f'{family:02d}-panel.png'))
        panel.playing=True
        QTest.qWait(120)
        panel.playing=False
        assert panel.model.samples
        print(f'PASS {family}: {panel.model.name}',flush=True)
    panel.presets.setCurrentIndex(0);QTest.qWait(200)
    assert not panel.model.family and not panel.scene.error
finally:
    panel.close();panel.deleteLater();app.processEvents()
