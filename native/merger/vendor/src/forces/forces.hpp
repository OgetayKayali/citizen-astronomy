#pragma once

#include "core/state.hpp"

#include <cstdint>
#include <vector>

namespace physics {

struct ForceSet {
    bool gravity = false;
    double gravitational_constant = 0;
    // Plummer softening length. Zero keeps the singular 1/r^2 force.
    double softening_length = 0;
    // Gas uses one cooling law for the whole scene. Zero sound speed means the
    // scene has no gas. `gas_star_density` of zero selects a threshold from the
    // initial gas density. The resolved threshold and the reference density are
    // filled when a run starts and are kept for Continue.
    double gas_sound_speed = 0;
    double gas_cooling_time = 0;
    double gas_star_time = 0;
    double gas_star_density = 0;
    bool gas_continuous_stars = false;
    double gas_star_temperature = 1.5;
    bool gas_star_converging = true;
    double gas_reference_density = 0;
    double gas_resolved_star_density = 0;
    // Specific energy released per unit stellar mass when a gas particle becomes a star.
    // Zero leaves the new star with no effect on the gas. The cooling delay is how long
    // the heated neighbors skip radiative losses, so the extra pressure can do work.
    double gas_feedback_energy = 0;
    double gas_feedback_time = 0;
    std::uint64_t gas_seed = 1;
    // Upper smoothing length for the hot atmosphere. Zero leaves every gas particle
    // on the ordinary neighbor search.
    double gas_hot_kernel_limit = 0;
};

struct Diagnostics {
    double kinetic = 0;
    double potential = 0;
    double energy = 0;
    Vec3 linear_momentum{};
    Vec3 angular_momentum{};
};

void compute_accelerations(State& state, Accelerations& accelerations, ForceSet const& forces, bool include_hydro = true);

[[nodiscard]] double potential_energy(State const& state, ForceSet const& forces);
[[nodiscard]] Diagnostics diagnose(State const& state, ForceSet const& forces);

}  // namespace physics
