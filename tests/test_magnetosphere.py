import math

import numpy as np
import pytest

from photometry_app.core.magnetosphere import (
    Parameters, PRESETS, Timeline, dynamic_pressure, shue_parameters,
    shue_radius, dipole_field, dipole_lines, deform_lines, tail_angle,
    jelinek_nose, jelinek_rho, newell_driver, open_line_examples,
    EARTH_RADIUS_KM, TIME_SCALE, UPSTREAM_X, DRIVER_X, EVENT_START, PROFILE_X,
    alfven_mach_upper_bound,
)


def test_pressure_units_and_shue_published_formula():
    assert dynamic_pressure(5, 400) == pytest.approx(1.338097538952)
    # Independent direct transcription of Shue 1998 equations 10 and 11.
    for pressure, bz in ((2, 0), (8, -15), (20, 15), (.5, -25)):
        r0, alpha = shue_parameters(pressure, bz)
        expected = (10.22+1.29*math.tanh(.184*(bz+8.14)))*pressure**(-1/6.6)
        assert r0 == pytest.approx(expected, rel=1e-13)
        assert alpha == pytest.approx((.58-.007*bz)*(1+.024*math.log(pressure)), rel=1e-13)
        assert shue_radius(0, r0, alpha) == r0
        assert shue_radius(math.pi/2, r0, alpha) == pytest.approx(r0*2**alpha)


def test_quiet_and_pressure_trend():
    quiet = Timeline(PRESETS["Quiet Solar Wind"])
    assert 9 < quiet.sample(0).r0 < 12
    assert quiet.sample(100).r0 == quiet.sample(0).r0
    radii = [shue_parameters(p, 0)[0] for p in (1, 2, 5, 10, 30)]
    assert np.all(np.diff(radii) < 0)


def test_equal_pressure_north_south_comparison_and_recovery():
    north = Timeline(PRESETS["CME — Northward IMF"])
    south = Timeline(PRESETS["CME — Southward IMF"])
    for t in (0, 25, 35, 65, 95, 150):
        n, s = north.sample(t), south.sample(t)
        assert n.pressure == s.pressure
        assert n.speed == s.speed and n.density == s.density
    n, s = north.sample(45), south.sample(45)
    assert s.r0 < n.r0 and s.alpha > n.alpha and s.coupling > n.coupling
    assert s.bow_r0 < south.sample(0).bow_r0
    assert abs(south.sample(south.end).r0-south.sample(0).r0) < .01


def test_timeline_is_random_access_and_pressure_is_not_interpolated_independently():
    model = Timeline()
    reference = model.sample(36.137)
    for t in (150, 0, 17, 100, 36.137): model.sample(t)
    assert model.sample(36.137) == reference
    assert Timeline().sample(36.137) == reference
    assert reference.pressure == dynamic_pressure(reference.density, reference.speed)
    assert model.sample(-1) == model.sample(0)
    assert model.sample(1e6) == model.sample(model.end)
    assert [model.sample(t).stage[0] for t in (0, 12.5, model.arrival+3, 45, 110)] == list("ABCDE")


def test_continuity_at_stages_and_sample_boundaries():
    model = Timeline()
    for t in (12, 30, 30.25, 36, 42, 80, 100):
        a, b = model.sample(t-1e-5), model.sample(t+1e-5)
        assert abs(a.r0-b.r0) < 1e-4
        assert abs(a.alpha-b.alpha) < 1e-4
        aa = deform_lines(dipole_lines(24), a, 11)
        bb = deform_lines(dipole_lines(24), b, 11)
        assert np.max(np.abs(aa-bb)) < 1e-3


def test_vacuum_curves_are_dipole_integral_curves():
    lines = dipole_lines(24, 400)
    tangent = lines[:, 2:]-lines[:, :-2]
    b = dipole_field(lines[:, 1:-1], tilt=0)
    cosine = np.sum(tangent*b, axis=-1)/(np.linalg.norm(tangent, axis=-1)*np.linalg.norm(b, axis=-1))
    assert np.min(np.abs(cosine)) > .999
    assert np.linalg.norm(dipole_field([0, 0, 1], 0)) == pytest.approx(62400)
    assert np.linalg.norm(dipole_field([1, 0, 0], 0)) == pytest.approx(31200)


@pytest.mark.parametrize("speed", [250, 1500])
@pytest.mark.parametrize("density", [1, 40])
@pytest.mark.parametrize("bz", [-25, 20])
@pytest.mark.parametrize("tilt", [-30, 0, 30])
def test_supported_extremes_have_finite_contained_geometry(speed, density, bz, tilt):
    model = Timeline(Parameters(speed, density, bz, tilt=tilt))
    for t in (0, 35, 70, 100, model.end):
        s = model.sample(t)
        assert s.r0 > 1 and s.alpha > 0
        assert s.r0 < s.bow_r0 < DRIVER_X
        # The complete displayed dayside MP lies inside the independent BS fit.
        theta = np.linspace(0, np.pi/2, 200)
        mp_r = shue_radius(theta, s.r0, s.alpha)
        assert np.all(mp_r*np.sin(theta) < jelinek_rho(mp_r*np.cos(theta), s.bow_r0))
        curves = deform_lines(dipole_lines(), s, tilt)
        assert np.isfinite(curves).all()
        radius = np.linalg.norm(curves, axis=-1)
        assert np.min(radius) > .99999
        np.testing.assert_allclose(radius[:, [0, -1]], 1, atol=5e-6)
        theta = np.arccos(np.clip(curves[..., 0]/radius, -.9999, 1))
        assert np.all(radius < shue_radius(theta, s.r0, s.alpha))
        angle = tail_angle(s)
        assert shue_radius(angle, s.r0, s.alpha)*np.cos(angle) == pytest.approx(-60, abs=1e-3)


@pytest.mark.parametrize("kwargs", [dict(speed=0), dict(density=float("nan")), dict(bz=99), dict(duration=0), dict(tilt=31)])
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(ValueError): Parameters(**kwargs)


def test_nonfinite_model_inputs_rejected():
    with pytest.raises(ValueError): shue_parameters(0, 1)
    with pytest.raises(ValueError): shue_parameters(1, np.inf)
    with pytest.raises(ValueError): shue_radius(np.pi, 10, .6)
    with pytest.raises(ValueError): dipole_field([0, 0, 0])
    with pytest.raises(ValueError): Timeline().sample(np.nan)


def test_jelinek_generalized_parabola_and_reference_noses():
    for pressure in (.6, 1, 2, 11, 20):
        nose = jelinek_nose(pressure)
        assert nose == pytest.approx(15.02*pressure**(-1/6.55))
        x = np.linspace(0, nose, 100)
        rho = jelinek_rho(x, nose)
        # Independently substitute into the published generalized coordinates.
        np.testing.assert_allclose(np.sqrt(x*x+(1.17*rho)**2)+x, 2*nose)
        assert jelinek_rho(nose, nose) == 0
    assert jelinek_nose(1) == 15.02


def test_pressure_and_newell_respond_to_different_parameters():
    a = Timeline(Parameters(400, 20, -5, quiet=True)).sample(0)
    b = Timeline(Parameters(800, 5, -5, quiet=True)).sample(0)
    assert a.pressure == b.pressure
    assert a.r0 == b.r0 and a.bow_r0 == b.bow_r0
    assert a.newell == 1
    assert b.newell == pytest.approx(2**(4/3))
    assert b.coupling > a.coupling
    assert newell_driver(800, 5) == 0
    assert newell_driver(400, -10) == pytest.approx(2**(2/3))


def test_tilt_rotates_footpoints_without_inventing_fit_dependence():
    a = Timeline(Parameters(tilt=-30)).sample(45)
    b = Timeline(Parameters(tilt=30)).sample(45)
    assert a == b  # These empirical fits deliberately omit tilt.
    left = deform_lines(dipole_lines(24), a, -30)
    right = deform_lines(dipole_lines(24), b, 30)
    assert not np.allclose(left[:, 0], right[:, 0])
    np.testing.assert_allclose(np.linalg.norm(left[:, 0], axis=-1), 1, atol=1e-6)


def test_monitor_and_near_earth_wind_are_separate_and_causal():
    model = Timeline()
    before = model.sample(12.5)
    assert before.upstream.pressure > before.pressure
    assert before.speed == 400 and before.density == 5 and before.bz == 3
    assert before.r0 == model.sample(0).r0
    assert before.front_x > DRIVER_X
    assert model.sample(model.arrival).front_x == pytest.approx(DRIVER_X, abs=1e-10)
    for t in (0, 12.5, 15, 22, 45, 70, 100):
        s = model.sample(t)
        assert s.source_time < s.time
        assert s.pressure == dynamic_pressure(s.density, s.speed)
        np.testing.assert_allclose(model.wind_at(t, UPSTREAM_X), model._source(t), atol=1e-12)
        np.testing.assert_allclose(model.wind_at(t), model._source(s.source_time))
        assert s.front_x == pytest.approx(UPSTREAM_X-(s.travel-model.sample(EVENT_START).travel))
    assert Timeline(Parameters(speed=1200)).arrival < Timeline(Parameters(speed=500)).arrival


def test_constant_wind_delay_has_physical_units_and_profile_shares_history():
    for speed in (250, 400, 1500):
        model = Timeline(Parameters(speed=speed, quiet=True))
        assert model.sample(45).lag_seconds == pytest.approx((UPSTREAM_X-DRIVER_X)*EARTH_RADIUS_KM/speed)
    model = Timeline()
    profile = model.wind_profile(23)
    wind = model.wind_at(23, PROFILE_X)
    np.testing.assert_allclose(profile[:, 0]*5, wind[:, 1], rtol=1e-6)
    np.testing.assert_allclose(profile[:, 1], wind[:, 2], rtol=1e-6)
    np.testing.assert_allclose(profile[:, 2], wind[:, 0], rtol=1e-6)
    assert profile.dtype == np.float32 and profile.flags.c_contiguous
    assert np.ptp(profile[:, 1]) > 10  # Different IMF at different locations.


def test_sheath_precedes_magnetic_cloud_and_tail_release_persists():
    model = Timeline()
    # Evaluate source epochs at the near-Earth plane via the same transport map.
    arrival = lambda source: float(np.interp(model._distance(source)+UPSTREAM_X-DRIVER_X, model.displacement, model.times))
    sheath = model.sample(arrival(15))
    cloud = model.sample(arrival(30))
    assert sheath.density == pytest.approx(22.5)
    assert sheath.bz == 3 and sheath.newell == 0
    assert cloud.density == 18 and cloud.bz == -15 and cloud.newell > 0
    recovery = model.sample(100)
    assert recovery.newell == 0 and recovery.tail_loading == 0
    assert recovery.coupling > 0 and recovery.tail_release > 0
    assert model.sample(120).coupling == pytest.approx(recovery.coupling*math.exp(-20/20), rel=1e-8)


def test_storage_budget_and_time_step_convergence():
    model = Timeline()
    fine = Timeline(step=.025)
    for t in (14.5, 18, 23, 35, 65, 75, 100):
        a, b = model.sample(t), fine.sample(t)
        assert abs(a.r0-b.r0) < .08
        assert abs(a.bow_r0-b.bow_r0) < .08
        assert abs(a.coupling-b.coupling) < .012
        assert 0 <= a.coupling <= 1
    # Integrate the reported loading/release rates independently.
    net = model.drivers*(1-model.values[:, 6])/10-model.values[:, 6]/20
    integral = np.sum((net[:-1]+net[1:])*.5*np.diff(model.times))
    assert integral == pytest.approx(model.values[-1, 6]-model.values[0, 6], abs=.025)
    steady = Timeline(Parameters(bz=-5, speed=400, quiet=True))
    assert np.ptp(steady.values[:, 6]) < 1e-12
    assert steady.sample(10).tail_loading == pytest.approx(steady.sample(10).tail_release)


def test_open_examples_and_precipitation_contract_are_not_quantitative_flux():
    model = Timeline()
    state = model.sample(45)
    curves = open_line_examples(state, 11)
    assert curves.shape == (12, 120, 3) and np.isfinite(curves).all()
    np.testing.assert_allclose(np.linalg.norm(curves[:, 0], axis=-1), 1, atol=1e-6)
    np.testing.assert_allclose(curves[:, -1, 0], -60)
    assert np.all(np.linalg.norm(curves[:, 1:], axis=-1) > 1)
    driver = model.auroral_driver(45)
    assert driver.physical_seconds == 45*TIME_SCALE
    assert driver.normalized_coupling == state.newell
    assert driver.near_earth_wind.bz == state.bz
    assert driver.energy_flux_mw_m2 is None and driver.characteristic_energy_kev is None


def test_bow_fit_applicability_is_distinct_from_finite_extrapolation():
    quiet = Timeline(PRESETS["Quiet Solar Wind"]).sample(0)
    strong = Timeline().sample(45)
    assert quiet.bow_applicable and not quiet.bow_extrapolated
    assert strong.bow_applicable and strong.bow_extrapolated
    slow = Timeline(Parameters(250, 1, -25, quiet=True))
    s = slow.sample(0)
    assert s.alfven_mach_bound == pytest.approx(.4585, abs=.001)
    assert not s.bow_applicable and not slow.bow_applicable.any()
    assert math.isfinite(s.bow_r0)  # Candidate fit retained internally, not displayed.
    assert alfven_mach_upper_bound(400, 5, 0) == np.inf
