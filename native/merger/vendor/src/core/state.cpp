#include "core/state.hpp"

#include <algorithm>
#include <stdexcept>

namespace physics {

std::size_t State::add(Particle const& particle) {
    if (!(particle.mass > 0.0)) {
        throw std::invalid_argument("particle mass must be positive");
    }
    x.push_back(particle.position.x);
    y.push_back(particle.position.y);
    z.push_back(particle.position.z);
    vx.push_back(particle.velocity.x);
    vy.push_back(particle.velocity.y);
    vz.push_back(particle.velocity.z);
    mass.push_back(particle.mass);
    component.push_back(static_cast<std::uint8_t>(particle.component));
    internal_energy.push_back(particle.internal_energy);
    density.push_back(0.0);
    divergence.push_back(0.0);
    hydro_dudt.push_back(0.0);
    cooling_delay.push_back(0.0);
    birth_time.push_back(-1.0);
    gas_fraction.push_back(particle.component == Component::Gas || particle.component == Component::HotGas ? 1.0 : 0.0);
    return mass.size() - 1;
}

void State::set_position(std::size_t index, Vec3 value) {
    x[index] = value.x;
    y[index] = value.y;
    z[index] = value.z;
}

void State::set_velocity(std::size_t index, Vec3 value) {
    vx[index] = value.x;
    vy[index] = value.y;
    vz[index] = value.z;
}

void Accelerations::resize(std::size_t count) {
    ax.assign(count, 0.0);
    ay.assign(count, 0.0);
    az.assign(count, 0.0);
}

void Accelerations::clear() {
    std::fill(ax.begin(), ax.end(), 0.0);
    std::fill(ay.begin(), ay.end(), 0.0);
    std::fill(az.begin(), az.end(), 0.0);
}

double kinetic_energy(State const& state) {
    double energy = 0.0;
    for (std::size_t i = 0; i < state.size(); ++i) {
        const double speed_squared =
            state.vx[i] * state.vx[i] + state.vy[i] * state.vy[i] + state.vz[i] * state.vz[i];
        energy += 0.5 * state.mass[i] * speed_squared;
    }
    return energy;
}

Vec3 linear_momentum(State const& state) {
    Vec3 momentum{};
    for (std::size_t i = 0; i < state.size(); ++i) {
        momentum.x += state.mass[i] * state.vx[i];
        momentum.y += state.mass[i] * state.vy[i];
        momentum.z += state.mass[i] * state.vz[i];
    }
    return momentum;
}

Vec3 angular_momentum(State const& state) {
    Vec3 angular{};
    for (std::size_t i = 0; i < state.size(); ++i) {
        const double px = state.mass[i] * state.vx[i];
        const double py = state.mass[i] * state.vy[i];
        const double pz = state.mass[i] * state.vz[i];
        angular.x += state.y[i] * pz - state.z[i] * py;
        angular.y += state.z[i] * px - state.x[i] * pz;
        angular.z += state.x[i] * py - state.y[i] * px;
    }
    return angular;
}

}  // namespace physics
