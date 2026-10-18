#include "forces/forces.hpp"
#include "forces/hydro.hpp"

#include <atomic>
#include <cmath>
#include <stdexcept>

namespace physics {
namespace {





void add_newtonian_gravity(State const& state, Accelerations& accelerations, double gravitational_constant,
                           double softening_length) {
    if (softening_length < 0.0) {
        throw std::invalid_argument("softening length must be non-negative");
    }
    const int count = static_cast<int>(state.size());
    const double softening_squared = softening_length * softening_length;
    std::atomic<int> singular{0};

#if defined(_OPENMP)
#pragma omp parallel for schedule(static) if (count >= 64)
#endif
    for (int i = 0; i < count; ++i) {
        double ax = 0.0;
        double ay = 0.0;
        double az = 0.0;
        const double xi = state.x[static_cast<std::size_t>(i)];
        const double yi = state.y[static_cast<std::size_t>(i)];
        const double zi = state.z[static_cast<std::size_t>(i)];
        for (int j = 0; j < count; ++j) {
            if (i == j) {
                continue;
            }
            const double dx = state.x[static_cast<std::size_t>(j)] - xi;
            const double dy = state.y[static_cast<std::size_t>(j)] - yi;
            const double dz = state.z[static_cast<std::size_t>(j)] - zi;
            const double radius_squared = dx * dx + dy * dy + dz * dz;
            if (softening_squared == 0.0 && radius_squared == 0.0) {
                singular.store(1, std::memory_order_relaxed);
                continue;
            }
            const double softened = radius_squared + softening_squared;
            const double inverse_radius = 1.0 / std::sqrt(softened);
            const double inverse_radius_cubed = inverse_radius * inverse_radius * inverse_radius;
            const double scale =
                gravitational_constant * state.mass[static_cast<std::size_t>(j)] * inverse_radius_cubed;
            ax += scale * dx;
            ay += scale * dy;
            az += scale * dz;
        }
        accelerations.ax[static_cast<std::size_t>(i)] += ax;
        accelerations.ay[static_cast<std::size_t>(i)] += ay;
        accelerations.az[static_cast<std::size_t>(i)] += az;
    }

    if (singular.load(std::memory_order_relaxed) != 0) {
        throw std::runtime_error("Newtonian gravity is singular for coincident particles");
    }
}



double gravitational_potential(State const& state, double gravitational_constant, double softening_length) {
    if (softening_length < 0.0) {
        throw std::invalid_argument("softening length must be non-negative");
    }
    double energy = 0.0;
    const double softening_squared = softening_length * softening_length;
    const std::size_t count = state.size();
    for (std::size_t i = 0; i < count; ++i) {
        for (std::size_t j = i + 1; j < count; ++j) {
            const double dx = state.x[j] - state.x[i];
            const double dy = state.y[j] - state.y[i];
            const double dz = state.z[j] - state.z[i];
            const double radius_squared = dx * dx + dy * dy + dz * dz;
            if (softening_squared == 0.0 && radius_squared == 0.0) {
                throw std::runtime_error("Newtonian gravity is singular for coincident particles");
            }
            energy -= gravitational_constant * state.mass[i] * state.mass[j] /
                      std::sqrt(radius_squared + softening_squared);
        }
    }
    return energy;
}



}  // namespace

void compute_accelerations(State& state, Accelerations& accelerations, ForceSet const& forces, bool include_hydro) {
    if (accelerations.ax.size() != state.size()) {
        accelerations.resize(state.size());
    } else {
        accelerations.clear();
    }

    if (forces.gravity) {
        add_newtonian_gravity(state, accelerations, forces.gravitational_constant, forces.softening_length);
    }
    if (include_hydro) add_hydro_forces(state, accelerations, forces);
}

double potential_energy(State const& state, ForceSet const& forces) {
    double energy = 0.0;
    if (forces.gravity) {
        energy += gravitational_potential(state, forces.gravitational_constant, forces.softening_length);
    }
    return energy;
}

Diagnostics diagnose(State const& state, ForceSet const& forces) {
    Diagnostics diagnostics;
    diagnostics.kinetic = kinetic_energy(state);
    diagnostics.potential = potential_energy(state, forces);
    double thermal = 0.0;
    if (state.internal_energy.size() == state.size()) {
        for (std::size_t index = 0; index < state.size(); ++index) {
            thermal += state.gas_mass(index) * state.internal_energy[index];
        }
    }
    diagnostics.energy = diagnostics.kinetic + thermal + diagnostics.potential;
    diagnostics.linear_momentum = linear_momentum(state);
    diagnostics.angular_momentum = angular_momentum(state);
    return diagnostics;
}

}  // namespace physics
