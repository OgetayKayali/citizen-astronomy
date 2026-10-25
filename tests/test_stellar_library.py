from __future__ import annotations

import json
import math
import numpy as np
import pytest
from photometry_app.core.variable_stars import StellarLabModel,FAMILIES


@pytest.fixture
def model():
    return StellarLabModel()


@pytest.mark.parametrize('family',FAMILIES)
def test_every_library_model_has_finite_variable_flux_and_surface(model,family):
    model.select_family(family)
    assert not model.invalid
    curve=model.curve(81)
    assert np.isfinite(curve).all()
    assert np.all(curve[:,0]>0)
    assert np.ptp(curve[:,0])>1e-3
    mesh=model.mesh(.23)
    assert mesh.shape[1]==10 and len(mesh)>1000
    assert np.isfinite(mesh).all()
    assert np.all(mesh[:,6]>0)
    np.testing.assert_allclose(np.linalg.norm(mesh[:,3:6],axis=1),1,atol=2e-6)
    if model.periodic:
        np.testing.assert_allclose(model.observe(.123),model.observe(1.123),rtol=1e-9,atol=1e-12)


@pytest.mark.parametrize('family',[3,4,5,6,7,8,11,12])
def test_radial_model_bolometric_radius_temperature_scaling(model,family):
    model.select_family(family);model.set('band',0)
    model.set('radius_amp',0);model.set('temp_amp',0)
    np.testing.assert_allclose(model.curve(31)[:,0],1,rtol=1e-12)
    model.set('radius_amp',.2);model.set('temp_amp',.1)
    mesh=model.mesh(.17)
    radius=np.linalg.norm(mesh[0,:3]);temp=mesh[0,6]
    expected=(radius/model.get('radius_a'))**2*(temp/model.get('temperature_a'))**4
    assert model.observe(.17)[0]==pytest.approx(expected,rel=1e-6)


def test_spot_contrast_and_viewing_inclination(model):
    model.select_family(13);model.set('spot_temp',1)
    np.testing.assert_allclose(model.curve(41)[:,0],1,atol=8e-5)
    model.set('spot_temp',.6);model.set('inclination',0)
    assert np.ptp(model.curve(41)[:,0])<1e-5
    model.set('inclination',90)
    assert np.ptp(model.curve(41)[:,0])>.2


@pytest.mark.parametrize('family',[7,8,9,10,12])
def test_incommensurate_modes_are_not_folded(model,family):
    model.select_family(family)
    assert not model.periodic
    assert not math.isclose(model.observe(.13)[0],model.observe(1.13)[0],rel_tol=1e-5)


@pytest.mark.parametrize('family',[15,16])
def test_isolated_events_do_not_repeat(model,family):
    model.select_family(family)
    start=model.get('event_start');rise=model.get('rise');decay=model.get('decay')
    peak=(start+rise*math.log1p(decay/rise))/model.period
    assert model.observe((start-.001)/model.period)[0]==pytest.approx(1)
    assert model.observe(peak)[0]>5
    assert model.observe(peak+20)[0]==pytest.approx(1,rel=1e-4)


def test_roche_surfaces_are_equipotentials_with_a_shared_neck(model):
    for family in (1,2):
        model.select_family(family);model.set('inclination',0);model.set('node',0)
        mesh=model.mesh(0)
        q=model.get('mass_b')/model.get('mass_a')
        xyz=mesh[:,:3].astype(float)/model.get('separation');xyz[:,0]+=q/(1+q)
        x,y,z=xyz.T
        potential=1/np.linalg.norm(xyz,axis=1)+q/np.linalg.norm(xyz-[1,0,0],axis=1)+.5*(1+q)*(x*x+y*y)-q*x
        if family==1:
            for component in (0,1):assert np.ptp(potential[mesh[:,9]==component])<1e-5
        else:
            assert np.ptp(potential)<1e-5
            assert potential.mean()==pytest.approx(model.info()[2],rel=1e-6)
            neck=xyz[np.abs(x-model.info()[1])<.01]
            assert len(neck)>0
            assert np.min(np.linalg.norm(neck[:,1:],axis=1))>.02


@pytest.mark.parametrize('family',[1,2,14])
def test_surface_flux_pixel_convergence(model,family):
    model.select_family(family)
    phases=(.03,.18,.27,.42)
    model.quality=192
    normal=np.array([model.observe(p)[0] for p in phases])
    model.quality=384
    high=np.array([model.observe(p)[0] for p in phases])
    np.testing.assert_allclose(normal,high,rtol=.012,atol=.001)


def test_dilution_distance_exposure_and_component_accounting(model):
    model.select_family(3)
    original=model.observe(.17)
    model.set('third_light',.3)
    assert model.observe(.17)[0]==pytest.approx(.3+.7*original[0])
    assert model.observe(.17)[3]+.3==pytest.approx(model.observe(.17)[0])
    model.set('third_light',0);model.set('distance',200)
    assert model.observe(.17)[2]==pytest.approx(original[2]/4,abs=0)
    model.set('exposure',model.period*86400*.04)
    value=model.observe(.17)[0]
    model.set('exposure',0)
    expected=np.mean([model.observe(.17+((k+.5)/25-.5)*.04)[0] for k in range(25)])
    assert value==pytest.approx(expected,rel=1e-12)


@pytest.mark.parametrize('family',FAMILIES)
def test_library_files_roundtrip_and_reject_bad_file_transactionally(model,family,tmp_path):
    model.select_family(family);model.noise_ppm=120;model.comparison=.8
    path=tmp_path/'星.starlab';model.save(path)
    expected=model.observe(.19)
    restored=StellarLabModel();restored.load(path)
    assert restored.family==family and restored.noise_ppm==120 and restored.comparison==.8
    np.testing.assert_array_equal(restored.values,model.values)
    np.testing.assert_allclose(restored.observe(.19),expected,rtol=1e-12)
    data=json.loads(path.read_text(encoding='utf-8'));data['variability']['rise']=0
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):restored.load(path)
    np.testing.assert_allclose(restored.observe(.19),expected,rtol=1e-12)


def test_recording_export_and_legacy_load(model,tmp_path):
    model.select_family(16);model.noise_ppm=500;model.restart()
    for _ in range(20):model.advance(.1,.5)
    recorded=np.array(model.samples)
    model.restart()
    for _ in range(40):model.advance(.05,.5)
    np.testing.assert_array_equal(model.samples,recorded)
    path=tmp_path/'flare.csv';observed=model.export(path,True)
    assert 'Flare star' in path.read_text(encoding='utf-8')
    values=np.genfromtxt(observed,delimiter=',',skip_header=1,names=True)
    np.testing.assert_allclose(values['measured_flux'],recorded[:,1])
    legacy=tmp_path/'source.binary';model.set_preset(2);model.save(legacy)
    model.select_family(16);model.load(legacy)
    assert model.family==0 and model.get('eccentricity')==.45


def test_library_controls_and_family_switches(model):
    from PySide6.QtWidgets import QApplication
    from photometry_app.ui.binary_light_view import BinaryLightPanel
    app=QApplication.instance() or QApplication([])
    panel=BinaryLightPanel();panel.model=model;panel._build()
    try:
        assert panel.presets.count()==23
        for family,entry in FAMILIES.items():
            panel.library_buttons[family].click()
            assert model.family==family
            assert not panel.controls['event_start'].isHidden() if family>=14 else panel.controls['event_start'].isHidden()
            assert panel.controls['radius_b'].isHidden()==(not model.binary)
            assert panel.play.isEnabled()
        panel._edit('phase',.04)
        assert model.cycles==pytest.approx(.04/model.period)
        panel._edit('event_amp',150)
        assert model.get('event_amp')==150 and panel.presets.currentIndex()==22
        panel.presets.setCurrentIndex(0)
        assert model.family==0 and not panel.controls['eccentricity'].isHidden()
    finally:
        panel.close();panel.deleteLater();app.processEvents()
