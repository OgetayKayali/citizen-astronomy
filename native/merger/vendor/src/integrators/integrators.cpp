#include "forces/hydro.hpp"
#include "integrators/integrators.hpp"
#include <algorithm>

namespace physics {
// Second-order predictor/corrector for velocity- and energy-dependent SPH.
// Cooling is split around it. No global energy rescaling is performed.
static void gas_step(State &state, Accelerations &accelerations, double dt, ForceSet const &forces) {
    const std::size_t n = state.size();
    Accelerations hydro, gravity;
    hydro.resize(n);
    add_hydro_forces(state, hydro, forces);
    gravity = accelerations;
    for (std::size_t i = 0; i < n; ++i) {
        gravity.ax[i] -= hydro.ax[i];
        gravity.ay[i] -= hydro.ay[i];
        gravity.az[i] -= hydro.az[i];
    }
    cool_gas(state, forces, 0.5 * dt);
    accelerations = gravity;
    add_hydro_forces(state, accelerations, forces);
    const auto start_acc = accelerations;
    const auto start_u = state.internal_energy, start_du = state.hydro_dudt;
    const auto start_vx = state.vx, start_vy = state.vy, start_vz = state.vz;
    for (std::size_t i = 0; i < n; ++i) {
        state.x[i] += dt * state.vx[i] + 0.5 * dt * dt * start_acc.ax[i];
        state.y[i] += dt * state.vy[i] + 0.5 * dt * dt * start_acc.ay[i];
        state.z[i] += dt * state.vz[i] + 0.5 * dt * dt * start_acc.az[i];
        state.vx[i] += dt * start_acc.ax[i];
        state.vy[i] += dt * start_acc.ay[i];
        state.vz[i] += dt * start_acc.az[i];
        // Predictor clipping is not an accepted energy change.
        state.internal_energy[i] = std::max(0.0, start_u[i] + dt * start_du[i]);
    }
    compute_accelerations(state, gravity, forces, false);
    accelerations = gravity;
    add_hydro_forces(state, accelerations, forces);
    for (std::size_t i = 0; i < n; ++i) {
        state.vx[i] = start_vx[i] + 0.5 * dt * (start_acc.ax[i] + accelerations.ax[i]);
        state.vy[i] = start_vy[i] + 0.5 * dt * (start_acc.ay[i] + accelerations.ay[i]);
        state.vz[i] = start_vz[i] + 0.5 * dt * (start_acc.az[i] + accelerations.az[i]);
        const double u = start_u[i] + 0.5 * dt * (start_du[i] + state.hydro_dudt[i]);
        state.floor_energy_added += state.gas_mass(i) * std::max(0.0, -u);
        state.internal_energy[i] = std::max(0.0, u);
    }
    cool_gas(state, forces, 0.5 * dt);
    // Positions are unchanged: reuse gravity, refresh thermodynamic forces.
    accelerations = gravity;
    add_hydro_forces(state, accelerations, forces);
}

void velocity_verlet_step(State &state, Accelerations &accelerations, double dt, ForceSet const &forces) {
    if (any_gas(state)) {
        gas_step(state, accelerations, dt, forces);
        return;
    }
    const double half_dt = 0.5 * dt;
    const std::size_t count = state.size();
    kick_internal_energy(state, half_dt);
    for (std::size_t i = 0; i < count; ++i) {
        state.vx[i] += accelerations.ax[i] * half_dt;
        state.vy[i] += accelerations.ay[i] * half_dt;
        state.vz[i] += accelerations.az[i] * half_dt;
        state.x[i] += state.vx[i] * dt;
        state.y[i] += state.vy[i] * dt;
        state.z[i] += state.vz[i] * dt;
    }
    compute_accelerations(state, accelerations, forces);
    kick_internal_energy(state, half_dt);
    cool_gas(state, forces, dt);
    for (std::size_t i = 0; i < count; ++i) {
        state.vx[i] += accelerations.ax[i] * half_dt;
        state.vy[i] += accelerations.ay[i] * half_dt;
        state.vz[i] += accelerations.az[i] * half_dt;
    }
}

void advance_velocity_verlet(State& state, ForceSet const& forces, double dt, std::size_t steps) {
    Accelerations accelerations;
    compute_accelerations(state, accelerations, forces);
    for (std::size_t step = 0; step < steps; ++step) {
        velocity_verlet_step(state, accelerations, dt, forces);
    }
}

}  // namespace physics
