#pragma once

#include "forces/forces.hpp"

namespace physics {

enum class IntegratorKind {
    VelocityVerlet,
};

// `accelerations` must already match `state` on entry. On return it matches the updated state.
// The kick-drift-kick form is symplectic for H = T(p) + V(q).
// Gas selects a second-order predictor/corrector with split cooling and a final
// pressure refresh; velocity-dependent SPH is not a separable Hamiltonian.
void velocity_verlet_step(State& state, Accelerations& accelerations, double dt, ForceSet const& forces);

void advance_velocity_verlet(State& state, ForceSet const& forces, double dt, std::size_t steps);

}  // namespace physics
