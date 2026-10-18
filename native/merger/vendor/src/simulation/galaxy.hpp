#pragma once

#include "simulation/scene.hpp"

#include <cstdint>
#include <vector>

namespace physics {

// Mass model for the analytic comparison formulas. Realized galaxies place the
// halo, an optional bulge, the stellar disk, and optional gas, then draw stellar
// disk speeds from the Shu distribution function in the softened N-body field.
struct EquilibriumComponents {
    double gravitational_constant = 1;
    double halo_mass = 1;
    double halo_scale = 1;
    // Particles are drawn only inside this radius, and the Hernquist density is
    // renormalized so the placed halo mass equals halo_mass.
    double halo_cutoff = 10;
    double disk_mass = 0;
    double disk_scale_radius = 1;
    // sech^2 scale height z0 in ρ ∝ sech^2(z/z0).
    double disk_height = 0.1;
    // σ_R / σ_z. Held constant with radius, so σ_R^2 declines as the surface density.
    double dispersion_ratio = 2;
    double target_q = 0; // Live-field Shu initializer; analytic comparison helpers retain dispersion_ratio.
};

[[nodiscard]] std::uint64_t derive_galaxy_seed(std::uint64_t scene_seed, std::size_t index);

// Razor-thin exponential disk, Freeman (1970). y = R / (2 Rd).
// v_c^2 = π G Σ0 (R^2 / Rd) [I0(y) K0(y) - I1(y) K1(y)]
[[nodiscard]] double exponential_disk_circular_speed_squared(double gravitational_constant, double disk_mass,
                                                             double disk_scale_radius, double radius);
[[nodiscard]] double exponential_disk_potential(double gravitational_constant, double disk_mass,
                                                double disk_scale_radius, double radius);

// Isotropic Hernquist (1990) halo with no disk and no truncation.
[[nodiscard]] double hernquist_isotropic_dispersion_squared(double gravitational_constant, double halo_mass,
                                                            double halo_scale, double radius);

// Circular speed of the halo plus the razor-thin disk. The halo contribution is
// G M_h(<R) / R for the truncated, renormalized Hernquist profile.
[[nodiscard]] double compound_circular_speed_squared(EquilibriumComponents const &model, double radius);
[[nodiscard]] double compound_halo_dispersion_squared(EquilibriumComponents const &model, double radius);
[[nodiscard]] double compound_streaming_speed(EquilibriumComponents const &model, double radius);
[[nodiscard]] double compound_toomre_q(EquilibriumComponents const &model, double radius);

// Invert the azimuthal CDF of 1 + amplitude*cos(m*(phi-phase)); conserves radius and mass.
double spiral_azimuth(double uniform_angle, double radius, GalaxySpec const &spec, bool gas = false);

// Solve d(rho*sigma_z^2)/dz = -rho*Kz for rho=sech^2(z/height), neglecting the tilt term.
// Positive restoring acceleration at nonnegative, strictly increasing heights.
std::vector<double> vertical_jeans_variance(double height, std::vector<double> const &z,
                                            std::vector<double> const &restoring);

// Positions follow the component profiles. Stellar-disk velocities are samples of
// the Shu distribution function in the softened field. The halo and the bulge
// use the isotropic Jeans solution in that same field. Gas is placed on the
// measured circular-speed curve with a sound speed. `particles` counts the
// collisionless bodies. Gas particles are separate so the gas mass can be
// resolved more finely than the halo.
[[nodiscard]] std::vector<BodyDesc> realize_galaxy(GalaxySpec const &spec, double gravitational_constant,
                                                   double softening_length);

} // namespace physics
