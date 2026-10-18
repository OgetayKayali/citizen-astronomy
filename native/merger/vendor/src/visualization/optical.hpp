#pragma once
#include "simulation/scene.hpp"

namespace physics::visual {
// Limited cubic playback. Falls back to a segment for unresolved fast orbits.
Vec3 interpolate_position(Vec3 p0, Vec3 v0, Vec3 p1, Vec3 v1, double dt, double blend);
Component component_of(BodyRecord const &body, Sample const *sample, std::size_t index);
double young_fraction(double time, double birth, double lifetime);
double kernel_density(double radius_squared, double major, double minor);
struct StellarFrame {
    Vec3 center{}, normal{0, 0, 1};
    double radius = 1;
    bool valid = false;
};
StellarFrame stellar_frame(Trajectory const &trajectory, Sample const &sample, bool first_disk_only = false);
// First minimum of smoothed progenitor-core separation; returns start for a single galaxy.
double first_passage_time(Trajectory const &trajectory);

struct Splat {
    Vec3 position;
    Vec3 color;
    // Camera-plane ellipse: major direction (cosine, sine) and support radii.
    double cosine = 1, sine = 0, major = 0.1, minor = 0.1;
    double luminosity = 0;
    double optical_depth = 0;
    double depth = 0;
};
struct OpticalSettings {
    double smoothing = 1;
    double detail = 0.45; // Fraction of diffuse disk light in the narrower kernel.
    double arm_definition = 0.65; // Preserve local elongation when narrowing the detail kernel.
    double dust = 1;
    double dust_structure = 0.55; // Conserved column split into broad and narrow gas-aligned lanes.
    double gas_glow = 1;
    double lifetime = 12; // Blue continuum fading time, in code units.
    double ionizing_lifetime = 0.8;
    double oiii_lifetime = 0.5; // Harder radiation fades sooner by default.
    double young_boost = 2;
    double association_size = 0.4;
    double nebula_radius = 0.65;
    double ionizing_mass_scale = 0.00002; // Mass-weighted illumination at half saturation.
    double nebula_density_scale = 0.01; // Gas density at half response, code mass / length^3.
    double halpha_strength = 24;
    double oiii_strength = 6;
    double nebula_size = 0.6;
    Vec3 halpha_color{1, 0.08, 0.22};
    Vec3 oiii_color{0.08, 0.85, 0.75};
    double texture = 0.04; // Strength of compact population light; conserved within each packet.
    bool show_halo = false;
    bool show_hot_gas = false;
};
// Population-separated local ellipses; no global volume bounds or screen-sized kernels.
std::vector<Splat> make_splats(Trajectory const &trajectory, Sample const *sample, std::vector<Vec3> const &positions,
                               double time, Vec3 eye, Vec3 right, Vec3 up, Vec3 forward,
                               OpticalSettings const &settings);
} // namespace physics::visual
