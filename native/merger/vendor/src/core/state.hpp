#pragma once

#include "core/component.hpp"
#include "core/vec3.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace physics {

struct Particle {
    double mass = 0;
    Vec3 position{};
    Vec3 velocity{};
    Component component = Component::Body;
    // Specific internal energy. Collisionless particles keep this at zero.
    double internal_energy = 0;
};

// Co-moving stellar mass formed inside a hybrid gas/star particle.
struct StellarCohort {
    std::uint32_t body = 0;
    double time = 0;
    double mass = 0;
};

// Structure-of-arrays particle state. Display radius and color stay out of this record.
struct State {
    // Cumulative, extensive energy exchanges. Preserved by in-memory Continue.
    double radiated_energy = 0;
    double feedback_energy = 0;
    double formation_energy_removed = 0;
    double floor_energy_added = 0;
    std::vector<double> x;
    std::vector<double> y;
    std::vector<double> z;
    std::vector<double> vx;
    std::vector<double> vy;
    std::vector<double> vz;
    std::vector<double> mass;
    std::vector<std::uint8_t> component;
    std::vector<double> internal_energy;
    // Hydro scratch filled by the force evaluation. Empty when the state has no gas.
    std::vector<double> density;
    std::vector<double> divergence;
    std::vector<double> hydro_dudt;
    // Time remaining with radiative cooling suspended after stellar feedback.
    std::vector<double> cooling_delay;
    // Absolute simulation time; -1 until a gas particle becomes a star.
    std::vector<double> birth_time;
    std::vector<double> gas_fraction;
    std::vector<StellarCohort> stellar_cohorts;

    [[nodiscard]] double gas_mass(std::size_t i) const {
        return mass[i] * (gas_fraction.empty() ? 1.0 : gas_fraction[i]);
    }

    [[nodiscard]] std::size_t size() const { return mass.size(); }
    [[nodiscard]] bool empty() const { return mass.empty(); }

    std::size_t add(Particle const& particle);

    [[nodiscard]] Vec3 position(std::size_t index) const {
        return {x[index], y[index], z[index]};
    }
    [[nodiscard]] Vec3 velocity(std::size_t index) const {
        return {vx[index], vy[index], vz[index]};
    }

    void set_position(std::size_t index, Vec3 value);
    void set_velocity(std::size_t index, Vec3 value);
};

struct Accelerations {
    std::vector<double> ax;
    std::vector<double> ay;
    std::vector<double> az;

    void resize(std::size_t count);
    void clear();
};

[[nodiscard]] double kinetic_energy(State const& state);
[[nodiscard]] Vec3 linear_momentum(State const& state);
[[nodiscard]] Vec3 angular_momentum(State const& state);

}  // namespace physics
