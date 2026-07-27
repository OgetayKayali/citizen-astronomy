"""Illustrative co-orbital meteoroid streams for Trajectory View.

Level-1 pedagogical model: sample meteoroids along the parent's osculating
Keplerian orbit (spread in mean anomaly, with a thin tube of radial /
out-of-plane scatter). This communicates that Earth crosses debris left
along the comet's path — not that the nucleus itself must be nearby.

Not a full dynamical dust-trail forecast (no radiation pressure, planetary
perturbations, or age-dated filaments).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Sequence

import numpy as np

# Same GM☉ used by solar_system._estimate_orbital_period_days (AU³/day²).
_SUN_MU_AU3_PER_DAY2 = 0.00029591220828559115
_TWO_PI = 2.0 * math.pi
_IDENTITY_ALIASES_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class MeteorShowerParent:
    """Known meteor shower linked to a parent small body."""

    shower_name: str
    iau_code: str
    parent_aliases: tuple[str, ...]
    peak_month_day: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class KeplerianElements:
    """Heliocentric osculating elements in radians / AU."""

    semi_major_axis_au: float
    eccentricity: float
    inclination_rad: float
    longitude_of_ascending_node_rad: float
    argument_of_periapsis_rad: float
    mean_anomaly_rad: float
    period_days: float


@dataclass(frozen=True, slots=True)
class MeteorStreamOverlay:
    """Renderable stream samples for one shower parent."""

    shower: MeteorShowerParent
    particle_positions_au: np.ndarray  # (N, 3)
    near_earth_mask: np.ndarray  # (N,) bool
    orbit_polyline_au: np.ndarray  # (M, 3) closed
    closest_approach_au: float
    crossing_position_au: tuple[float, float, float] | None
    semi_major_axis_au: float
    eccentricity: float
    orbital_period_days: float
    elements: KeplerianElements | None = None
    particle_true_anomalies: np.ndarray | None = None  # (N,) legacy / optional
    particle_path_fractions: np.ndarray | None = None  # (N,) even spacing along orbit 0..1
    particle_radial_offsets_au: np.ndarray | None = None  # (N,)
    particle_normal_offsets_au: np.ndarray | None = None  # (N,)
    particle_binormal_offsets_au: np.ndarray | None = None  # (N,)


def _closed_orbit_body(orbit_polyline_au: np.ndarray) -> np.ndarray:
    orbit = np.asarray(orbit_polyline_au, dtype=float)
    if len(orbit) >= 2 and np.allclose(orbit[0], orbit[-1]):
        return orbit[:-1]
    return orbit


def orbit_arc_length_table(orbit_polyline_au: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (orbit_body, cumulative_lengths, total_length) for a closed polyline."""

    orbit = _closed_orbit_body(orbit_polyline_au)
    if len(orbit) < 2:
        return orbit, np.zeros(0, dtype=float), 0.0
    segment_lengths = np.linalg.norm(np.roll(orbit, -1, axis=0) - orbit, axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
    return orbit, cumulative, float(cumulative[-1])


def polyline_indices_from_path_fractions(
    orbit_polyline_au: np.ndarray,
    path_fractions: np.ndarray,
) -> np.ndarray:
    """Map even arc-length fractions in [0, 1) onto continuous polyline indices.

    The guide polyline is sampled in true anomaly, so index is *not* proportional
    to path length on eccentric orbits. This conversion keeps debris evenly spaced.
    """

    orbit, cumulative, total_length = orbit_arc_length_table(orbit_polyline_au)
    fractions = np.asarray(path_fractions, dtype=float)
    if len(orbit) < 2 or total_length <= 1.0e-12:
        return np.mod(fractions, 1.0) * max(1.0, float(len(orbit)))
    targets = np.mod(fractions, 1.0) * total_length
    segment_index = np.searchsorted(cumulative, targets, side="right") - 1
    segment_index = np.clip(segment_index, 0, len(orbit) - 1)
    seg_len = np.maximum(cumulative[segment_index + 1] - cumulative[segment_index], 1.0e-12)
    local = (targets - cumulative[segment_index]) / seg_len
    return segment_index.astype(float) + local


def resample_closed_polyline_even_arc_length(
    orbit_polyline_au: np.ndarray,
    *,
    sample_count: int,
) -> np.ndarray:
    """Return a closed (N+1, 3) polyline with equal path-length spacing."""

    count = max(16, int(sample_count))
    orbit, cumulative, total_length = orbit_arc_length_table(orbit_polyline_au)
    if len(orbit) < 3 or total_length <= 1.0e-12:
        return np.asarray(orbit_polyline_au, dtype=float)

    targets = np.linspace(0.0, total_length, count, endpoint=False)
    segment_index = np.searchsorted(cumulative, targets, side="right") - 1
    segment_index = np.clip(segment_index, 0, len(orbit) - 1)
    seg_len = np.maximum(cumulative[segment_index + 1] - cumulative[segment_index], 1.0e-12)
    local = ((targets - cumulative[segment_index]) / seg_len).reshape(-1, 1)
    next_index = (segment_index + 1) % len(orbit)
    points = orbit[segment_index] * (1.0 - local) + orbit[next_index] * local
    return np.vstack([points, points[:1]])


def _rodrigues_rotate(vector: np.ndarray, axis: np.ndarray, angle_rad: float) -> np.ndarray:
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return (
        vector * cos_a
        + np.cross(axis, vector) * sin_a
        + axis * float(np.dot(axis, vector)) * (1.0 - cos_a)
    )


def _parallel_transport_frames(centers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (normals, binormals) for a closed centerline."""

    count = len(centers)
    tangents = np.zeros((count, 3), dtype=float)
    for index in range(count):
        delta = centers[(index + 1) % count] - centers[(index - 1) % count]
        norm = float(np.linalg.norm(delta))
        tangents[index] = delta / norm if norm > 1.0e-12 else np.array([1.0, 0.0, 0.0], dtype=float)

    normals = np.zeros((count, 3), dtype=float)
    binormals = np.zeros((count, 3), dtype=float)
    reference = np.array([0.0, 0.0, 1.0], dtype=float)
    if abs(float(np.dot(tangents[0], reference))) > 0.9:
        reference = np.array([0.0, 1.0, 0.0], dtype=float)
    normals[0] = np.cross(tangents[0], reference)
    normal_norm = float(np.linalg.norm(normals[0]))
    if normal_norm <= 1.0e-12:
        normals[0] = np.array([0.0, 1.0, 0.0], dtype=float)
    else:
        normals[0] /= normal_norm
    binormals[0] = np.cross(tangents[0], normals[0])

    for index in range(1, count):
        axis = np.cross(tangents[index - 1], tangents[index])
        axis_norm = float(np.linalg.norm(axis))
        if axis_norm <= 1.0e-12:
            normals[index] = normals[index - 1]
        else:
            axis /= axis_norm
            cos_theta = float(np.clip(np.dot(tangents[index - 1], tangents[index]), -1.0, 1.0))
            angle = math.atan2(axis_norm, cos_theta)
            normals[index] = _rodrigues_rotate(normals[index - 1], axis, angle)
        normals[index] = normals[index] - tangents[index] * float(np.dot(normals[index], tangents[index]))
        normal_norm = float(np.linalg.norm(normals[index]))
        if normal_norm <= 1.0e-12:
            reference = np.array([0.0, 0.0, 1.0], dtype=float)
            if abs(float(np.dot(tangents[index], reference))) > 0.9:
                reference = np.array([0.0, 1.0, 0.0], dtype=float)
            normals[index] = np.cross(tangents[index], reference)
            normal_norm = float(np.linalg.norm(normals[index]))
        normals[index] /= max(normal_norm, 1.0e-12)
        binormals[index] = np.cross(tangents[index], normals[index])
    return normals, binormals


def build_orbit_soft_glow_mesh(
    orbit_polyline_au: np.ndarray,
    *,
    radius_au: float,
    rgb: tuple[float, float, float],
    path_samples: int = 420,
    peak_alpha: float = 0.32,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a soft line-glow mesh from crossed ribbons along the orbit.

    Two camera-independent ribbons (normal × binormal) are bright on the
    centerline and fade smoothly to zero alpha at ``radius_au``. This avoids
    hard cylindrical shells while still reading as a glow around a line.

    Returns (vertexes, faces, vertex_colors) for ``MeshData`` / ``GLMeshItem``.
    """

    radius = max(1.0e-4, float(radius_au))
    closed = resample_closed_polyline_even_arc_length(orbit_polyline_au, sample_count=path_samples)
    centers = closed[:-1]
    slice_count = len(centers)
    if slice_count < 3:
        return (
            np.zeros((0, 3), dtype=float),
            np.zeros((0, 3), dtype=np.uint32),
            np.zeros((0, 4), dtype=float),
        )

    normals, binormals = _parallel_transport_frames(centers)
    red, green, blue = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
    peak = max(0.01, min(1.0, float(peak_alpha)))
    # Across-ribbon samples: Gaussian falloff from the centerline.
    across = np.array([-1.0, -0.6, -0.3, 0.0, 0.3, 0.6, 1.0], dtype=float)
    across_alpha = peak * np.exp(-((across / 0.42) ** 2))
    across_alpha[-1] = 0.0
    across_alpha[0] = 0.0
    samples_across = len(across)
    ribbon_count = 2
    verts_per_slice = ribbon_count * samples_across
    vertexes = np.zeros((slice_count * verts_per_slice, 3), dtype=float)
    colors = np.zeros((slice_count * verts_per_slice, 4), dtype=float)

    for slice_index in range(slice_count):
        base = slice_index * verts_per_slice
        center = centers[slice_index]
        axes = (normals[slice_index] * radius, binormals[slice_index] * radius)
        for ribbon_index, axis in enumerate(axes):
            ribbon_base = base + ribbon_index * samples_across
            for sample_index, fraction in enumerate(across):
                vertexes[ribbon_base + sample_index] = center + axis * float(fraction)
                colors[ribbon_base + sample_index] = (red, green, blue, float(across_alpha[sample_index]))

    faces: list[list[int]] = []
    for slice_index in range(slice_count):
        next_slice = (slice_index + 1) % slice_count
        base = slice_index * verts_per_slice
        next_base = next_slice * verts_per_slice
        for ribbon_index in range(ribbon_count):
            ribbon_base = base + ribbon_index * samples_across
            next_ribbon_base = next_base + ribbon_index * samples_across
            for sample_index in range(samples_across - 1):
                a = ribbon_base + sample_index
                b = ribbon_base + sample_index + 1
                c = next_ribbon_base + sample_index + 1
                d = next_ribbon_base + sample_index
                faces.append([a, b, c])
                faces.append([a, c, d])

    return vertexes, np.asarray(faces, dtype=np.uint32), colors


def orbit_extent_au(orbit_polyline_au: np.ndarray) -> float:
    """Largest heliocentric radius on the polyline (AU)."""

    orbit = _closed_orbit_body(orbit_polyline_au)
    if len(orbit) == 0:
        return 1.0
    extent = float(np.max(np.linalg.norm(orbit, axis=1)))
    return extent if math.isfinite(extent) and extent > 0.0 else 1.0


def effective_meteor_stream_tube_radius_au(
    orbit_polyline_au: np.ndarray,
    *,
    thickness_scale: float,
    base_radius_au: float = 0.03,
) -> float:
    """Choose a constant AU tube radius that stays visible on large eccentric orbits."""

    thickness = max(0.5, float(thickness_scale))
    extent = orbit_extent_au(orbit_polyline_au)
    # Constant along the path (no aphelion widening). Scales with orbit size once.
    return max(float(base_radius_au), 0.0048 * extent) * thickness


def sample_positions_even_along_orbit(
    orbit_polyline_au: np.ndarray,
    *,
    count: int,
    tube_radius_au: float,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Place particles evenly by path length for a visually uniform debris ribbon.

    Returns (positions, path_fractions, cartesian_offsets).
    """

    orbit = _closed_orbit_body(orbit_polyline_au)
    point_count = len(orbit)
    if point_count < 3 or count <= 0:
        return (
            np.zeros((0, 3), dtype=float),
            np.zeros(0, dtype=float),
            np.zeros((0, 3), dtype=float),
        )

    segment_vectors = np.roll(orbit, -1, axis=0) - orbit
    segment_lengths = np.linalg.norm(segment_vectors, axis=1)
    total_length = float(np.sum(segment_lengths))
    if not math.isfinite(total_length) or total_length <= 1.0e-12:
        fractions = np.linspace(0.0, 1.0, count, endpoint=False)
        positions = orbit[np.mod(np.round(fractions * point_count).astype(int), point_count)]
        offsets = rng.normal(0.0, max(1.0e-4, tube_radius_au), size=(count, 3))
        return positions, fractions, offsets

    cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
    # Even arc-length targets with modest jitter so the ribbon does not look like a lattice.
    step = total_length / count
    targets = (np.arange(count, dtype=float) * step + rng.uniform(0.25 * step, 0.75 * step, count)) % total_length
    segment_index = np.searchsorted(cumulative, targets, side="right") - 1
    segment_index = np.clip(segment_index, 0, point_count - 1)
    segment_start = cumulative[segment_index]
    seg_len = np.maximum(segment_lengths[segment_index], 1.0e-12)
    local_frac = ((targets - segment_start) / seg_len).reshape(-1, 1)
    next_index = (segment_index + 1) % point_count
    centers = orbit[segment_index] * (1.0 - local_frac) + orbit[next_index] * local_frac

    tangents = segment_vectors[segment_index]
    tangent_norm = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangent_norm = np.maximum(tangent_norm, 1.0e-12)
    tangent_hat = tangents / tangent_norm
    # Build a stable perpendicular basis so thickness follows the ribbon instead of
    # isotropic blobs that pile up at foreshortened tips.
    reference = np.tile(np.array([[0.0, 0.0, 1.0]], dtype=float), (count, 1))
    parallel = np.abs(np.sum(tangent_hat * reference, axis=1)) > 0.9
    reference[parallel] = np.array([0.0, 1.0, 0.0], dtype=float)
    normal = np.cross(tangent_hat, reference)
    normal_norm = np.linalg.norm(normal, axis=1, keepdims=True)
    normal_norm = np.maximum(normal_norm, 1.0e-12)
    normal_hat = normal / normal_norm
    binormal_hat = np.cross(tangent_hat, normal_hat)
    along = rng.normal(0.0, tube_radius_au * 0.15, count).reshape(-1, 1)
    side = rng.normal(0.0, tube_radius_au, count).reshape(-1, 1)
    out = rng.normal(0.0, tube_radius_au * 0.85, count).reshape(-1, 1)
    offsets = tangent_hat * along + normal_hat * side + binormal_hat * out
    positions = centers + offsets
    fractions = targets / total_length
    return positions, fractions.astype(float), offsets.astype(float)


def particle_positions_from_anomalies(
    elements: KeplerianElements,
    true_anomalies: np.ndarray,
    radial_offsets_au: np.ndarray,
    normal_offsets_au: np.ndarray,
    binormal_offsets_au: np.ndarray,
) -> np.ndarray:
    """Build heliocentric particle positions from true anomalies + tube offsets."""

    count = len(true_anomalies)
    positions = np.zeros((count, 3), dtype=float)
    for index, true_anomaly in enumerate(true_anomalies):
        center = position_from_true_anomaly(elements, float(true_anomaly))
        radius = float(np.linalg.norm(center))
        if radius <= 1.0e-9:
            positions[index] = center
            continue
        radial_hat = center / radius
        reference = np.array([0.0, 0.0, 1.0], dtype=float)
        if abs(float(np.dot(radial_hat, reference))) > 0.9:
            reference = np.array([0.0, 1.0, 0.0], dtype=float)
        normal_hat = np.cross(radial_hat, reference)
        normal_norm = float(np.linalg.norm(normal_hat))
        if normal_norm <= 1.0e-12:
            positions[index] = center
            continue
        normal_hat /= normal_norm
        binormal_hat = np.cross(radial_hat, normal_hat)
        positions[index] = (
            center
            + radial_hat * float(radial_offsets_au[index])
            + normal_hat * float(normal_offsets_au[index])
            + binormal_hat * float(binormal_offsets_au[index])
        )
    return positions


def near_earth_mask_for_positions(
    positions: np.ndarray,
    earth_positions_au: np.ndarray,
    *,
    threshold_au: float,
) -> np.ndarray:
    """Boolean mask of particles within threshold_au of any Earth-orbit sample."""

    count = len(positions)
    if count == 0 or earth_positions_au.size == 0:
        return np.zeros(count, dtype=bool)
    min_distances = np.full(count, np.inf, dtype=float)
    chunk = 256
    for start in range(0, len(earth_positions_au), chunk):
        earth_chunk = earth_positions_au[start : start + chunk]
        deltas = positions[:, None, :] - earth_chunk[None, :, :]
        distances = np.linalg.norm(deltas, axis=2)
        min_distances = np.minimum(min_distances, np.min(distances, axis=1))
    return min_distances <= float(threshold_au)


# Major annual showers with well-established parent bodies.
KNOWN_METEOR_SHOWER_PARENTS: tuple[MeteorShowerParent, ...] = (
    MeteorShowerParent(
        shower_name="Perseids",
        iau_code="PER",
        parent_aliases=("109P", "109P/Swift-Tuttle", "Swift-Tuttle", "P/Swift-Tuttle", "Swift Tuttle"),
        peak_month_day="Aug 12",
        notes="Earth crosses the descending node of 109P debris each mid-August.",
    ),
    MeteorShowerParent(
        shower_name="Orionids",
        iau_code="ORI",
        parent_aliases=("1P", "1P/Halley", "Halley", "P/Halley"),
        peak_month_day="Oct 21",
        notes="Halley stream; shares parent with η Aquariids.",
    ),
    MeteorShowerParent(
        shower_name="η Aquariids",
        iau_code="ETA",
        parent_aliases=("1P", "1P/Halley", "Halley", "P/Halley"),
        peak_month_day="May 6",
        notes="Halley stream; shares parent with Orionids.",
    ),
    MeteorShowerParent(
        shower_name="Leonids",
        iau_code="LEO",
        parent_aliases=("55P", "55P/Tempel-Tuttle", "Tempel-Tuttle", "Tempel Tuttle", "P/Tempel-Tuttle"),
        peak_month_day="Nov 17",
        notes="Famous for storm years when Earth hits dense trail filaments.",
    ),
    MeteorShowerParent(
        shower_name="Lyrids",
        iau_code="LYR",
        parent_aliases=("C/1861 G1", "Thatcher", "C/1861 G1 Thatcher"),
        peak_month_day="Apr 22",
        notes="Long-period comet Thatcher debris.",
    ),
    MeteorShowerParent(
        shower_name="Geminids",
        iau_code="GEM",
        parent_aliases=("3200", "3200 Phaethon", "Phaethon"),
        peak_month_day="Dec 14",
        notes="Asteroid/rock comet Phaethon; dense December shower.",
    ),
    MeteorShowerParent(
        shower_name="Quadrantids",
        iau_code="QUA",
        parent_aliases=("196256", "2003 EH1", "(196256) 2003 EH1", "2003EH1"),
        peak_month_day="Jan 3",
        notes="Linked primarily to 2003 EH1.",
    ),
    MeteorShowerParent(
        shower_name="Draconids",
        iau_code="DRA",
        parent_aliases=("21P", "21P/Giacobini-Zinner", "Giacobini-Zinner", "Giacobini Zinner"),
        peak_month_day="Oct 8",
        notes="Often weak; can outburst when Earth hits fresh trails.",
    ),
    MeteorShowerParent(
        shower_name="Ursids",
        iau_code="URS",
        parent_aliases=("8P", "8P/Tuttle", "P/Tuttle"),
        peak_month_day="Dec 22",
        notes="December shower from 8P/Tuttle.",
    ),
    MeteorShowerParent(
        shower_name="Southern Taurids",
        iau_code="STA",
        parent_aliases=("2P", "2P/Encke", "Encke", "P/Encke"),
        peak_month_day="Nov 5",
        notes="Encke complex; related Northern Taurids also from this stream family.",
    ),
)


def normalize_body_identity(text: str | None) -> str:
    """Lowercase alphanumeric identity key for alias matching."""

    return _IDENTITY_ALIASES_RE.sub("", str(text or "").strip().lower())


def _alias_matches_identity(alias: str, token: str) -> bool:
    """Strict alias match: exact, designation prefix, or long name contained in token."""

    if not alias or not token:
        return False
    if token == alias:
        return True
    # Designation-style: 109P, 55P, 3200, C1861G1 — token may be "109pswifttuttle…".
    if alias[0].isdigit() or (alias.startswith("c") and any(character.isdigit() for character in alias)):
        if token.startswith(alias):
            remainder = token[len(alias) :]
            # Require a boundary so "1p" does not match a hypothetical "1px…",
            # while still allowing "1phalley" / "109pswifttuttle".
            return (not remainder) or (not remainder[0].isdigit())
        return False
    # Proper-name aliases: require the full normalized name inside the identity.
    return len(alias) >= 5 and alias in token


def match_meteor_shower_parents(*identity_texts: str | None) -> tuple[MeteorShowerParent, ...]:
    """Return known showers whose parent aliases match any provided identity text."""

    tokens = {normalize_body_identity(text) for text in identity_texts if str(text or "").strip()}
    tokens.discard("")
    if not tokens:
        return ()

    matched: list[MeteorShowerParent] = []
    seen_codes: set[str] = set()
    for shower in KNOWN_METEOR_SHOWER_PARENTS:
        aliases = {normalize_body_identity(alias) for alias in shower.parent_aliases}
        aliases.discard("")
        if not aliases:
            continue
        hit = any(_alias_matches_identity(alias, token) for token in tokens for alias in aliases)
        if hit and shower.iau_code not in seen_codes:
            # Halley parents map to two showers; keep both.
            matched.append(shower)
            seen_codes.add(shower.iau_code)
    return tuple(matched)


def format_meteor_stream_label(showers: Sequence[MeteorShowerParent]) -> str:
    """Human label for one or more showers sharing a parent body."""

    if not showers:
        return "Meteor stream"
    if len(showers) == 1:
        return f"{showers[0].shower_name} stream"
    names = " / ".join(shower.shower_name for shower in showers)
    return f"{names} stream"


def primary_meteor_shower_for_identities(*identity_texts: str | None) -> MeteorShowerParent | None:
    """Pick a single shower label when multiple IAU codes share a parent (e.g. Halley)."""

    matched = match_meteor_shower_parents(*identity_texts)
    if not matched:
        return None
    # Prefer Perseids/Orionids-style first entry order for Halley: ORI then ETA —
    # use the first match unless identities explicitly name a shower.
    joined = " ".join(str(text or "") for text in identity_texts).lower()
    for shower in matched:
        if shower.shower_name.lower() in joined or shower.iau_code.lower() in joined:
            return shower
    return matched[0]


def keplerian_elements_from_state(
    *,
    x_au: float,
    y_au: float,
    z_au: float,
    vx_au_per_day: float,
    vy_au_per_day: float,
    vz_au_per_day: float,
    mu: float = _SUN_MU_AU3_PER_DAY2,
) -> KeplerianElements | None:
    """Convert a heliocentric state vector to classical osculating elements."""

    position = np.array([float(x_au), float(y_au), float(z_au)], dtype=float)
    velocity = np.array([float(vx_au_per_day), float(vy_au_per_day), float(vz_au_per_day)], dtype=float)
    radius = float(np.linalg.norm(position))
    speed = float(np.linalg.norm(velocity))
    if not math.isfinite(radius) or radius <= 0.0 or not math.isfinite(speed):
        return None

    specific_energy = 0.5 * speed * speed - mu / radius
    if not math.isfinite(specific_energy) or specific_energy >= 0.0:
        return None

    semi_major_axis = -mu / (2.0 * specific_energy)
    if not math.isfinite(semi_major_axis) or semi_major_axis <= 0.0:
        return None

    angular_momentum = np.cross(position, velocity)
    h_norm = float(np.linalg.norm(angular_momentum))
    if not math.isfinite(h_norm) or h_norm <= 0.0:
        return None

    eccentricity_vector = (np.cross(velocity, angular_momentum) / mu) - (position / radius)
    eccentricity = float(np.linalg.norm(eccentricity_vector))
    if not math.isfinite(eccentricity) or eccentricity >= 1.0:
        return None

    inclination = math.acos(max(-1.0, min(1.0, float(angular_momentum[2] / h_norm))))
    node_vector = np.array([-angular_momentum[1], angular_momentum[0], 0.0], dtype=float)
    node_norm = float(np.linalg.norm(node_vector))
    if node_norm < 1.0e-12:
        longitude_of_ascending_node = 0.0
        if eccentricity < 1.0e-12:
            argument_of_periapsis = 0.0
        else:
            argument_of_periapsis = math.atan2(eccentricity_vector[1], eccentricity_vector[0])
            if angular_momentum[2] < 0.0:
                argument_of_periapsis = _TWO_PI - argument_of_periapsis
    else:
        longitude_of_ascending_node = math.atan2(node_vector[1], node_vector[0])
        if longitude_of_ascending_node < 0.0:
            longitude_of_ascending_node += _TWO_PI
        cos_arg = float(np.dot(node_vector, eccentricity_vector) / (node_norm * max(eccentricity, 1.0e-15)))
        argument_of_periapsis = math.acos(max(-1.0, min(1.0, cos_arg)))
        if eccentricity_vector[2] < 0.0:
            argument_of_periapsis = _TWO_PI - argument_of_periapsis

    if eccentricity < 1.0e-12:
        if node_norm < 1.0e-12:
            true_anomaly = math.atan2(position[1], position[0])
        else:
            cos_nu = float(np.dot(node_vector, position) / (node_norm * radius))
            true_anomaly = math.acos(max(-1.0, min(1.0, cos_nu)))
            if position[2] < 0.0:
                true_anomaly = _TWO_PI - true_anomaly
        eccentric_anomaly = true_anomaly
    else:
        cos_nu = float(np.dot(eccentricity_vector, position) / (eccentricity * radius))
        true_anomaly = math.acos(max(-1.0, min(1.0, cos_nu)))
        if float(np.dot(position, velocity)) < 0.0:
            true_anomaly = _TWO_PI - true_anomaly
        cos_e = (eccentricity + math.cos(true_anomaly)) / (1.0 + eccentricity * math.cos(true_anomaly))
        eccentric_anomaly = math.acos(max(-1.0, min(1.0, cos_e)))
        if true_anomaly > math.pi:
            eccentric_anomaly = _TWO_PI - eccentric_anomaly

    mean_anomaly = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly)
    mean_anomaly %= _TWO_PI
    period_days = _TWO_PI * math.sqrt((semi_major_axis**3) / mu)
    if not math.isfinite(period_days) or period_days <= 0.0:
        return None

    return KeplerianElements(
        semi_major_axis_au=float(semi_major_axis),
        eccentricity=float(eccentricity),
        inclination_rad=float(inclination),
        longitude_of_ascending_node_rad=float(longitude_of_ascending_node % _TWO_PI),
        argument_of_periapsis_rad=float(argument_of_periapsis % _TWO_PI),
        mean_anomaly_rad=float(mean_anomaly),
        period_days=float(period_days),
    )


def _solve_kepler_eccentric_anomaly(mean_anomaly_rad: float, eccentricity: float) -> float:
    mean_anomaly = float(mean_anomaly_rad) % _TWO_PI
    eccentric_anomaly = mean_anomaly if eccentricity < 0.8 else math.pi
    for _ in range(30):
        f_value = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly
        f_prime = 1.0 - eccentricity * math.cos(eccentric_anomaly)
        if abs(f_prime) < 1.0e-15:
            break
        delta = f_value / f_prime
        eccentric_anomaly -= delta
        if abs(delta) < 1.0e-12:
            break
    return float(eccentric_anomaly % _TWO_PI)


def eccentric_anomaly_from_true_anomaly(true_anomaly_rad: float, eccentricity: float) -> float:
    """Convert true anomaly to eccentric anomaly for an elliptical orbit."""

    true_anomaly = float(true_anomaly_rad) % _TWO_PI
    beta = eccentricity / (1.0 + math.sqrt(max(0.0, 1.0 - eccentricity * eccentricity)))
    eccentric_anomaly = true_anomaly + 2.0 * math.atan(
        (beta * math.sin(true_anomaly)) / (1.0 - beta * math.cos(true_anomaly))
    )
    return float(eccentric_anomaly % _TWO_PI)


def mean_anomaly_from_true_anomaly(true_anomaly_rad: float, eccentricity: float) -> float:
    eccentric_anomaly = eccentric_anomaly_from_true_anomaly(true_anomaly_rad, eccentricity)
    return float((eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly)) % _TWO_PI)


def position_from_keplerian(elements: KeplerianElements, mean_anomaly_rad: float) -> np.ndarray:
    """Heliocentric position (AU) for the given mean anomaly on the osculating ellipse."""

    eccentric_anomaly = _solve_kepler_eccentric_anomaly(mean_anomaly_rad, elements.eccentricity)
    return _position_from_eccentric_anomaly(elements, eccentric_anomaly)


def position_from_true_anomaly(elements: KeplerianElements, true_anomaly_rad: float) -> np.ndarray:
    """Heliocentric position (AU) for the given true anomaly on the osculating ellipse."""

    eccentric_anomaly = eccentric_anomaly_from_true_anomaly(true_anomaly_rad, elements.eccentricity)
    return _position_from_eccentric_anomaly(elements, eccentric_anomaly)


def _position_from_eccentric_anomaly(elements: KeplerianElements, eccentric_anomaly: float) -> np.ndarray:
    cos_e = math.cos(eccentric_anomaly)
    sin_e = math.sin(eccentric_anomaly)
    # Perifocal coordinates.
    x_peri = elements.semi_major_axis_au * (cos_e - elements.eccentricity)
    y_peri = elements.semi_major_axis_au * math.sqrt(max(0.0, 1.0 - elements.eccentricity**2)) * sin_e
    cos_o = math.cos(elements.longitude_of_ascending_node_rad)
    sin_o = math.sin(elements.longitude_of_ascending_node_rad)
    cos_w = math.cos(elements.argument_of_periapsis_rad)
    sin_w = math.sin(elements.argument_of_periapsis_rad)
    cos_i = math.cos(elements.inclination_rad)
    sin_i = math.sin(elements.inclination_rad)
    x_ecl = (cos_o * cos_w - sin_o * sin_w * cos_i) * x_peri + (-cos_o * sin_w - sin_o * cos_w * cos_i) * y_peri
    y_ecl = (sin_o * cos_w + cos_o * sin_w * cos_i) * x_peri + (-sin_o * sin_w + cos_o * cos_w * cos_i) * y_peri
    z_ecl = (sin_w * sin_i) * x_peri + (cos_w * sin_i) * y_peri
    return np.array([x_ecl, y_ecl, z_ecl], dtype=float)


def sample_closed_orbit_polyline(
    elements: KeplerianElements,
    *,
    sample_count: int = 720,
) -> np.ndarray:
    """Closed (N+1, 3) polyline sampling true anomaly uniformly around the orbit.

    True-anomaly sampling keeps perihelion / Earth-crossing geometry resolved on
    high-eccentricity comet orbits. Mean-anomaly sampling would pile almost all
    points near aphelion and miss the node.
    """

    count = max(64, int(sample_count))
    true_anomalies = np.linspace(0.0, _TWO_PI, count, endpoint=False)
    points = np.vstack(
        [position_from_true_anomaly(elements, float(true_anomaly)) for true_anomaly in true_anomalies]
    )
    return np.vstack([points, points[:1]])


def _minimum_orbit_intersection(
    orbit_a: np.ndarray,
    orbit_b: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Return (distance, point_on_a, point_on_b) for the closest sample pair."""

    if orbit_a.size == 0 or orbit_b.size == 0:
        return float("nan"), np.zeros(3, dtype=float), np.zeros(3, dtype=float)

    min_distance = float("inf")
    best_a = orbit_a[0]
    best_b = orbit_b[0]
    chunk = 256
    for start in range(0, len(orbit_a), chunk):
        chunk_a = orbit_a[start : start + chunk]
        deltas = chunk_a[:, None, :] - orbit_b[None, :, :]
        distances = np.linalg.norm(deltas, axis=2)
        flat_index = int(np.argmin(distances))
        local_a, local_b = divmod(flat_index, distances.shape[1])
        distance = float(distances[local_a, local_b])
        if distance < min_distance:
            min_distance = distance
            best_a = chunk_a[local_a]
            best_b = orbit_b[local_b]
    return min_distance, np.asarray(best_a, dtype=float), np.asarray(best_b, dtype=float)


def _earth_orbit_positions_for_crossing(
    earth_positions_au: np.ndarray,
    *,
    sample_count: int = 360,
) -> np.ndarray:
    """Prefer a full Earth ellipse when the provided Earth path is only a short arc."""

    earth = np.asarray(earth_positions_au, dtype=float)
    if earth.ndim != 2 or earth.shape[1] != 3 or len(earth) == 0:
        return np.zeros((0, 3), dtype=float)
    if len(earth) >= 180:
        return earth

    # Estimate angular coverage in the ecliptic plane; short arcs get completed.
    xy = earth[:, :2]
    angles = np.unwrap(np.arctan2(xy[:, 1], xy[:, 0]))
    coverage = float(angles.max() - angles.min()) if len(angles) > 1 else 0.0
    if coverage >= math.radians(300.0):
        return earth

    # Build a unit-circle-like Earth ellipse from the mean radius / plane of samples.
    radii = np.linalg.norm(earth, axis=1)
    mean_radius = float(np.median(radii)) if len(radii) else 1.0
    if not math.isfinite(mean_radius) or mean_radius <= 0.0:
        mean_radius = 1.0
    # Fit a simple circular ecliptic Earth orbit at the observed mean radius.
    theta = np.linspace(0.0, _TWO_PI, max(180, int(sample_count)), endpoint=False)
    completed = np.column_stack(
        [
            mean_radius * np.cos(theta),
            mean_radius * np.sin(theta),
            np.zeros(len(theta), dtype=float),
        ]
    )
    return completed


def build_meteor_stream_overlay(
    *,
    shower: MeteorShowerParent,
    state_x_au: float,
    state_y_au: float,
    state_z_au: float,
    state_vx_au_per_day: float,
    state_vy_au_per_day: float,
    state_vz_au_per_day: float,
    earth_positions_au: np.ndarray | Sequence[Sequence[float]],
    particle_count: int = 1200,
    tube_radius_au: float = 0.03,
    near_earth_threshold_au: float = 0.12,
    orbit_sample_count: int = 720,
    rng_seed: int = 109_000_109,
) -> MeteorStreamOverlay | None:
    """Build an illustrative debris tube along the parent's osculating orbit."""

    elements = keplerian_elements_from_state(
        x_au=state_x_au,
        y_au=state_y_au,
        z_au=state_z_au,
        vx_au_per_day=state_vx_au_per_day,
        vy_au_per_day=state_vy_au_per_day,
        vz_au_per_day=state_vz_au_per_day,
    )
    if elements is None:
        return None

    earth_raw = np.asarray(earth_positions_au, dtype=float)
    if earth_raw.ndim != 2 or earth_raw.shape[1] != 3 or earth_raw.size == 0:
        earth_raw = np.zeros((0, 3), dtype=float)
    earth = _earth_orbit_positions_for_crossing(earth_raw)

    orbit_polyline = sample_closed_orbit_polyline(elements, sample_count=orbit_sample_count)

    if earth.size:
        closest_approach, crossing_stream, _crossing_earth = _minimum_orbit_intersection(orbit_polyline, earth)
        crossing = tuple(float(value) for value in crossing_stream)
    else:
        closest_approach = float("nan")
        crossing = None
        crossing_stream = None

    count = max(64, int(particle_count))
    rng = np.random.default_rng(int(rng_seed))
    thickness_scale = max(0.5, float(tube_radius_au) / 0.03) if float(tube_radius_au) > 0.0 else 1.0
    effective_tube_radius = effective_meteor_stream_tube_radius_au(
        orbit_polyline,
        thickness_scale=thickness_scale,
        base_radius_au=0.03,
    )
    # Even path-length spacing keeps the ribbon visually uniform on high-e orbits.
    # Equal true-anomaly samples crowd near perihelion and look blotchy under tip foreshortening.
    positions, path_fractions, cartesian_offsets = sample_positions_even_along_orbit(
        orbit_polyline,
        count=count,
        tube_radius_au=effective_tube_radius,
        rng=rng,
    )
    # Keep legacy offset slots filled for callers; animation prefers path fractions.
    radial_offsets = cartesian_offsets[:, 0]
    normal_offsets = cartesian_offsets[:, 1]
    binormal_offsets = cartesian_offsets[:, 2]
    true_anomalies = path_fractions * _TWO_PI

    if earth.size and crossing_stream is not None:
        threshold = max(
            float(near_earth_threshold_au),
            float(closest_approach) + 0.04,
            float(effective_tube_radius) * 1.35,
        )
        near_mask = near_earth_mask_for_positions(positions, earth, threshold_au=threshold)
    else:
        near_mask = np.zeros(count, dtype=bool)

    return MeteorStreamOverlay(
        shower=shower,
        particle_positions_au=positions,
        near_earth_mask=near_mask,
        orbit_polyline_au=orbit_polyline,
        closest_approach_au=closest_approach,
        crossing_position_au=crossing,
        semi_major_axis_au=elements.semi_major_axis_au,
        eccentricity=elements.eccentricity,
        orbital_period_days=elements.period_days,
        elements=elements,
        particle_true_anomalies=np.asarray(true_anomalies, dtype=float),
        particle_path_fractions=np.asarray(path_fractions, dtype=float),
        particle_radial_offsets_au=np.asarray(radial_offsets, dtype=float),
        particle_normal_offsets_au=np.asarray(normal_offsets, dtype=float),
        particle_binormal_offsets_au=np.asarray(binormal_offsets, dtype=float),
    )


def build_meteor_stream_overlay_from_samples(
    *,
    shower: MeteorShowerParent,
    object_samples: Sequence[object],
    earth_samples: Sequence[object],
    particle_count: int = 1200,
    tube_radius_au: float = 0.03,
    near_earth_threshold_au: float = 0.12,
) -> MeteorStreamOverlay | None:
    """Convenience wrapper using SolarSystemStateVectorSample-like objects."""

    if not object_samples:
        return None
    # Prefer the sample nearest perihelion when available (more representative for
    # highly eccentric comets); otherwise use the middle sample.
    best_sample = None
    best_radius = float("inf")
    for sample in object_samples:
        radius = math.sqrt(float(sample.x_au) ** 2 + float(sample.y_au) ** 2 + float(sample.z_au) ** 2)
        speed = math.sqrt(
            float(sample.vx_au_per_day) ** 2 + float(sample.vy_au_per_day) ** 2 + float(sample.vz_au_per_day) ** 2
        )
        if speed <= 0.0 or not math.isfinite(radius):
            continue
        if radius < best_radius:
            best_radius = radius
            best_sample = sample
    if best_sample is None:
        best_sample = object_samples[len(object_samples) // 2]

    earth_positions = np.array(
        [[float(sample.x_au), float(sample.y_au), float(sample.z_au)] for sample in earth_samples],
        dtype=float,
    ) if earth_samples else np.zeros((0, 3), dtype=float)

    # Complete a short Earth path into a full osculating ellipse when velocities exist.
    if earth_samples:
        earth_state = earth_samples[len(earth_samples) // 2]
        earth_elements = keplerian_elements_from_state(
            x_au=float(earth_state.x_au),
            y_au=float(earth_state.y_au),
            z_au=float(earth_state.z_au),
            vx_au_per_day=float(earth_state.vx_au_per_day),
            vy_au_per_day=float(earth_state.vy_au_per_day),
            vz_au_per_day=float(earth_state.vz_au_per_day),
        )
        if earth_elements is not None:
            earth_positions = sample_closed_orbit_polyline(earth_elements, sample_count=360)

    return build_meteor_stream_overlay(
        shower=shower,
        state_x_au=float(best_sample.x_au),
        state_y_au=float(best_sample.y_au),
        state_z_au=float(best_sample.z_au),
        state_vx_au_per_day=float(best_sample.vx_au_per_day),
        state_vy_au_per_day=float(best_sample.vy_au_per_day),
        state_vz_au_per_day=float(best_sample.vz_au_per_day),
        earth_positions_au=earth_positions,
        particle_count=particle_count,
        tube_radius_au=tube_radius_au,
        near_earth_threshold_au=near_earth_threshold_au,
    )
