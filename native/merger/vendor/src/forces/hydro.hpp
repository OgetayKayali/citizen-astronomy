#pragma once

#include "forces/forces.hpp"

namespace physics {

inline constexpr double kGasGamma = 5.0 / 3.0;

[[nodiscard]] bool any_gas(State const& state);

// Median initial density, and the star-formation threshold when the scene left it at zero.
void prepare_gas(State& state, ForceSet& forces);

// Pressure, viscosity, and the adiabatic energy rate. Cooling is a separate exact substep.
void add_hydro_forces(State& state, Accelerations& accelerations, ForceSet const& forces);

void kick_internal_energy(State& state, double half_dt);
void cool_gas(State& state, ForceSet const& forces, double dt);

// Converts eligible gas, fractionally or as whole particles. Each new stellar mass returns
// `gas_feedback_energy` per unit mass to the surrounding gas and suspends
// cooling there for `gas_feedback_time`. Returns the number of carriers changed.
int form_stars(State& state, ForceSet const& forces, double dt, std::size_t step);

}  // namespace physics
