#include "forces/hydro.hpp"

#include <algorithm>
#include <cmath>
#include <vector>

namespace physics {

bool holds_gas(std::uint8_t component) {
    return component == static_cast<std::uint8_t>(Component::Gas) ||
           component == static_cast<std::uint8_t>(Component::HotGas);
}

// Warm gas keeps the density clock. Well above the cold floor the time grows,
// so a hot halo and a shock do not drain back to the floor on the warm-gas clock.
double cooling_slowdown(double energy, double floor_energy) {
    if (!(floor_energy > 0.0) || !(energy > floor_energy)) {
        return 1.0;
    }
    constexpr double kWarm = 64.0;
    const double excess = energy / floor_energy - 1.0;
    const double ratio = excess / kWarm;
    return 1.0 + ratio * ratio;
}

// A cold disk particle and a hot halo particle do not share a kernel. A factor of
// a few, as in a feedback dump, still couples.
bool same_temperature(double left_energy, double right_energy, double floor_energy) {
    const double floor = std::max(floor_energy, 1e-12);
    const double left = std::max(left_energy, floor);
    const double right = std::max(right_energy, floor);
    const double ratio = left > right ? left / right : right / left;
    return ratio <= 24.0;
}

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kNeighborCount = 48.0;
constexpr double kViscosityAlpha = 1.0;
constexpr double kViscosityBeta = 2.0;

double cold_energy(double sound_speed) {
    const double speed_squared = sound_speed * sound_speed;
    return speed_squared / (kGasGamma * (kGasGamma - 1.0));
}

double spline_value(double radius, double smoothing) {
    if (!(smoothing > 0.0) || !(radius >= 0.0)) {
        return 0.0;
    }
    const double q = radius / smoothing;
    if (q >= 2.0) {
        return 0.0;
    }
    const double scale = 1.0 / (kPi * smoothing * smoothing * smoothing);
    if (q < 1.0) {
        return scale * (1.0 - 1.5 * q * q + 0.75 * q * q * q);
    }
    const double tail = 2.0 - q;
    return scale * 0.25 * tail * tail * tail;
}

// dW/dr. Negative inside the kernel, so -grad W points away from a neighbor.
double spline_slope(double radius, double smoothing) {
    if (!(smoothing > 0.0) || !(radius > 0.0)) {
        return 0.0;
    }
    const double q = radius / smoothing;
    if (q >= 2.0) {
        return 0.0;
    }
    const double scale = 1.0 / (kPi * smoothing * smoothing * smoothing * smoothing);
    if (q < 1.0) {
        return scale * (-3.0 * q + 2.25 * q * q);
    }
    const double tail = 2.0 - q;
    return scale * (-0.75 * tail * tail);
}

double hashed_unit(std::uint64_t seed, std::uint64_t step, std::uint64_t index) {
    std::uint64_t mixed = seed + 0x9E3779B97F4A7C15ull * (index + 1ull) + 0xD1B54A32D192ED03ull * (step + 1ull);
    mixed = (mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9ull;
    mixed = (mixed ^ (mixed >> 27)) * 0x94D049BB133111EBull;
    mixed ^= mixed >> 31;
    return static_cast<double>(mixed >> 11) * (1.0 / 9007199254740992.0);
}

struct GasTables {
    std::vector<std::size_t> index;
    std::vector<double> smoothing;
    std::vector<double> density;
    std::vector<double> pressure;
    std::vector<double> sound_squared;
};

bool collect_gas(State const& state, GasTables& gas) {
    gas.index.clear();
    for (std::size_t index = 0; index < state.size(); ++index) {
        if (holds_gas(state.component[index])) {
            gas.index.push_back(index);
        }
    }
    return gas.index.size() >= 2;
}

void resize_scratch(State& state) {
    if (state.density.size() != state.size()) {
        state.density.assign(state.size(), 0.0);
    }
    if (state.divergence.size() != state.size()) {
        state.divergence.assign(state.size(), 0.0);
    }
    if (state.hydro_dudt.size() != state.size()) {
        state.hydro_dudt.assign(state.size(), 0.0);
    }
}

void compute_density(State const& state, ForceSet const& forces, GasTables& gas, double floor_energy) {
    const double floor_h = std::max(forces.softening_length, 1e-4);
    const double volume_factor = (kNeighborCount * 3.0) / (32.0 * kPi);
    gas.smoothing.assign(gas.index.size(), std::max(floor_h, 0.25));
    gas.density.assign(gas.index.size(), 0.0);

    for (int iteration = 0; iteration < 4; ++iteration) {
        for (std::size_t a = 0; a < gas.index.size(); ++a) {
            const std::size_t i = gas.index[a];
            const double smoothing = gas.smoothing[a];
            double density = state.gas_mass(i) * spline_value(0.0, smoothing);
            const double xi = state.x[i];
            const double yi = state.y[i];
            const double zi = state.z[i];
            for (std::size_t b = 0; b < gas.index.size(); ++b) {
                if (a == b) {
                    continue;
                }
                const std::size_t j = gas.index[b];
                if (!same_temperature(state.internal_energy[i], state.internal_energy[j], floor_energy)) {
                    continue;
                }
                const double dx = state.x[j] - xi;
                const double dy = state.y[j] - yi;
                const double dz = state.z[j] - zi;
                const double radius = std::sqrt(dx * dx + dy * dy + dz * dz);
                density += state.gas_mass(j) * spline_value(radius, smoothing);
            }
            gas.density[a] = std::max(density, 1e-12);
            const double updated = std::cbrt(volume_factor * state.gas_mass(i) / gas.density[a]);
            double cap = 64.0;
            if (forces.gas_hot_kernel_limit > 0.0 &&
                state.component[i] == static_cast<std::uint8_t>(Component::HotGas)) {
                cap = std::max(forces.gas_hot_kernel_limit, floor_h);
            }
            gas.smoothing[a] = std::clamp(std::max(updated, floor_h), floor_h, cap);
        }
    }
}

void write_density(State& state, GasTables const& gas) {
    std::fill(state.density.begin(), state.density.end(), 0.0);
    std::fill(state.divergence.begin(), state.divergence.end(), 0.0);
    std::fill(state.hydro_dudt.begin(), state.hydro_dudt.end(), 0.0);
    for (std::size_t a = 0; a < gas.index.size(); ++a) {
        state.density[gas.index[a]] = gas.density[a];
    }
}

double feedback_smoothing(double mass, double density) {
    const double volume_factor = (kNeighborCount * 3.0) / (32.0 * kPi);
    return std::clamp(std::cbrt(volume_factor * mass / std::max(density, 1e-12)), 1e-4, 64.0);
}

// Thermal dump into the gas that remains inside the new star's smoothing kernel.
// The weights sum to one, so the injected energy is exactly feedback_energy * mass.
void deposit_feedback(State& state, std::size_t star, std::vector<char> const& forming, double stored_density,
                      ForceSet const& forces, double stellar_mass) {
    if (!(forces.gas_feedback_energy > 0.0)) {
        return;
    }
    const double smoothing = feedback_smoothing(state.mass[star], stored_density);
    std::vector<std::size_t> neighbors;
    std::vector<double> weight;
    double weight_sum = 0.0;
    double nearest_distance = 0.0;
    std::size_t nearest = state.size();
    for (std::size_t index = 0; index < state.size(); ++index) {
        if (index == star || forming[index] != 0 || state.component[index] != static_cast<std::uint8_t>(Component::Gas)) {
            continue;
        }
        const double dx = state.x[index] - state.x[star];
        const double dy = state.y[index] - state.y[star];
        const double dz = state.z[index] - state.z[star];
        const double radius = std::sqrt(dx * dx + dy * dy + dz * dz);
        if (nearest == state.size() || radius < nearest_distance) {
            nearest = index;
            nearest_distance = radius;
        }
        const double kernel = state.gas_mass(index) * spline_value(radius, smoothing);
        if (!(kernel > 0.0)) {
            continue;
        }
        neighbors.push_back(index);
        weight.push_back(kernel);
        weight_sum += kernel;
    }
    if (!(weight_sum > 0.0)) {
        if (nearest == state.size()) {
            return;
        }
        neighbors = {nearest};
        weight = {1.0};
        weight_sum = 1.0;
    }
    const double energy = forces.gas_feedback_energy * stellar_mass;
    state.feedback_energy += energy;
    for (std::size_t slot = 0; slot < neighbors.size(); ++slot) {
        const std::size_t index = neighbors[slot];
        state.internal_energy[index] += (weight[slot] / weight_sum) * energy / state.gas_mass(index);
        if (forces.gas_feedback_time > 0.0) {
            state.cooling_delay[index] = std::max(state.cooling_delay[index], forces.gas_feedback_time);
        }
    }
}

}  // namespace

bool any_gas(State const& state) {
    if (state.component.size() != state.size()) {
        return false;
    }
    return std::any_of(state.component.begin(), state.component.end(), [](std::uint8_t value) {
        return holds_gas(value);
    });
}

void prepare_gas(State& state, ForceSet& forces) {
    if (!any_gas(state)) {
        return;
    }
    resize_scratch(state);
    GasTables gas;
    if (!collect_gas(state, gas)) {
        return;
    }
    const double floor_energy = cold_energy(forces.gas_sound_speed);
    compute_density(state, forces, gas, floor_energy);
    write_density(state, gas);
    if (!(forces.gas_reference_density > 0.0)) {
        std::vector<double> densities;
        densities.reserve(gas.index.size());
        for (std::size_t slot = 0; slot < gas.index.size(); ++slot) {
            if (state.component[gas.index[slot]] == static_cast<std::uint8_t>(Component::Gas)) {
                densities.push_back(gas.density[slot]);
            }
        }
        if (densities.empty()) {
            densities = gas.density;
        }
        std::sort(densities.begin(), densities.end());
        forces.gas_reference_density = densities[densities.size() / 2];
    }
    if (!(forces.gas_resolved_star_density > 0.0)) {
        forces.gas_resolved_star_density =
            forces.gas_star_density > 0.0 ? forces.gas_star_density : 4.0 * forces.gas_reference_density;
    }
}

void add_hydro_forces(State& state, Accelerations& accelerations, ForceSet const& forces) {
    resize_scratch(state);
    std::fill(state.density.begin(), state.density.end(), 0.0);
    std::fill(state.divergence.begin(), state.divergence.end(), 0.0);
    std::fill(state.hydro_dudt.begin(), state.hydro_dudt.end(), 0.0);
    if (!any_gas(state)) {
        return;
    }
    GasTables gas;
    if (!collect_gas(state, gas)) {
        return;
    }
    const double floor_energy = cold_energy(forces.gas_sound_speed);
    compute_density(state, forces, gas, floor_energy);
    write_density(state, gas);
    gas.pressure.resize(gas.index.size());
    gas.sound_squared.resize(gas.index.size());
    for (std::size_t a = 0; a < gas.index.size(); ++a) {
        const std::size_t i = gas.index[a];
        const double thermal = (kGasGamma - 1.0) * gas.density[a] * std::max(state.internal_energy[i], floor_energy);
        double jeans = 0.0;
        if (forces.gravity && forces.gravitational_constant > 0.0) {
            const double smoothing = gas.smoothing[a];
            jeans = forces.gravitational_constant * gas.density[a] * gas.density[a] * smoothing * smoothing / kGasGamma;
        }
        gas.pressure[a] = std::max(thermal, jeans);
        gas.sound_squared[a] = kGasGamma * gas.pressure[a] / gas.density[a];
    }

    for (std::size_t a = 0; a < gas.index.size(); ++a) {
        const std::size_t i = gas.index[a];
        double ax = 0.0;
        double ay = 0.0;
        double az = 0.0;
        double dudt = 0.0;
        double divergence = 0.0;
        for (std::size_t b = 0; b < gas.index.size(); ++b) {
            if (a == b) {
                continue;
            }
            const std::size_t j = gas.index[b];
            if (!same_temperature(state.internal_energy[i], state.internal_energy[j], floor_energy)) {
                continue;
            }
            const double dx = state.x[i] - state.x[j];
            const double dy = state.y[i] - state.y[j];
            const double dz = state.z[i] - state.z[j];
            const double radius_squared = dx * dx + dy * dy + dz * dz;
            const double radius = std::sqrt(radius_squared);
            if (!(radius > 1e-12)) {
                continue;
            }
            const double smoothing = 0.5 * (gas.smoothing[a] + gas.smoothing[b]);
            const double slope = spline_slope(radius, smoothing);
            if (slope == 0.0) {
                continue;
            }
            const double gradient = slope / radius;
            const double dvx = state.vx[i] - state.vx[j];
            const double dvy = state.vy[i] - state.vy[j];
            const double dvz = state.vz[i] - state.vz[j];
            const double approach = dvx * dx + dvy * dy + dvz * dz;
            double viscosity = 0.0;
            if (approach < 0.0) {
                const double mu = smoothing * approach / (radius_squared + 0.01 * smoothing * smoothing);
                const double signal = std::sqrt(std::max(0.0, 0.5 * (gas.sound_squared[a] + gas.sound_squared[b])));
                const double mean_density = 0.5 * (gas.density[a] + gas.density[b]);
                viscosity = (-kViscosityAlpha * signal * mu + kViscosityBeta * mu * mu) / mean_density;
            }
            const double pair = gas.pressure[a] / (gas.density[a] * gas.density[a]) +
                                gas.pressure[b] / (gas.density[b] * gas.density[b]) + viscosity;
            const double weight = state.gas_mass(j) * pair;
            ax -= weight * gradient * dx;
            ay -= weight * gradient * dy;
            az -= weight * gradient * dz;
            dudt += 0.5 * weight * gradient * approach;
            divergence += (state.gas_mass(j) / gas.density[a]) * gradient * (-approach);
        }
        // Gas pressure acts on the gas fraction; gravity still sees the total mass.
        const double fraction = state.gas_mass(i) / state.mass[i];
        accelerations.ax[i] += fraction * ax;
        accelerations.ay[i] += fraction * ay;
        accelerations.az[i] += fraction * az;
        state.hydro_dudt[i] = dudt;
        state.divergence[i] = divergence;
    }
}

void kick_internal_energy(State& state, double half_dt) {
    if (state.hydro_dudt.size() != state.size() || state.component.size() != state.size()) {
        return;
    }
    for (std::size_t index = 0; index < state.size(); ++index) {
        if (!holds_gas(state.component[index])) {
            continue;
        }
        const double updated = state.internal_energy[index] + state.hydro_dudt[index] * half_dt;
        state.floor_energy_added += state.gas_mass(index) * std::max(0.0, -updated);
        state.internal_energy[index] = std::max(0.0, updated);
    }
}

void cool_gas(State& state, ForceSet const& forces, double dt) {
    if (!(dt > 0.0) || state.component.size() != state.size()) {
        return;
    }
    if (state.cooling_delay.size() != state.size()) {
        state.cooling_delay.assign(state.size(), 0.0);
    }
    const bool can_cool = forces.gas_cooling_time > 0.0 && forces.gas_reference_density > 0.0 &&
                          state.density.size() == state.size();
    const double floor_energy = cold_energy(forces.gas_sound_speed);
    for (std::size_t index = 0; index < state.size(); ++index) {
        if (!holds_gas(state.component[index])) {
            state.cooling_delay[index] = 0.0;
            continue;
        }
        const double suspended = std::min(std::max(0.0, state.cooling_delay[index]), dt);
        state.cooling_delay[index] -= suspended;
        const double cooling_dt = dt - suspended;
        if (!(cooling_dt > 0.0)) {
            continue;
        }
        if (!can_cool || !(state.density[index] > 0.0)) {
            continue;
        }
        const double tau = forces.gas_cooling_time * cooling_slowdown(state.internal_energy[index], floor_energy) *
                           forces.gas_reference_density / state.density[index];
        const double decay = tau > 0.0 ? std::exp(-cooling_dt / tau) : 0.0;
        const double before = state.internal_energy[index];
        state.internal_energy[index] = floor_energy + (state.internal_energy[index] - floor_energy) * decay;
        // The floor is the cold-phase equilibrium. Expansion is not allowed to
        // leave the stored energy below the pressure that the force used.
        if (state.internal_energy[index] < floor_energy) {
            state.internal_energy[index] = floor_energy;
        }
        const double change = state.gas_mass(index) * (state.internal_energy[index] - before);
        state.radiated_energy += std::max(0.0, -change);
        state.floor_energy_added += std::max(0.0, change);
    }
}

int form_stars(State& state, ForceSet const& forces, double dt, std::size_t step) {
    if (!(forces.gas_resolved_star_density > 0.0) || !(forces.gas_star_time > 0.0) || !(dt > 0.0)) {
        return 0;
    }
    if (state.density.size() != state.size() || state.divergence.size() != state.size()) {
        return 0;
    }
    if (state.cooling_delay.size() != state.size()) {
        state.cooling_delay.assign(state.size(), 0.0);
    }
    const double floor_energy = cold_energy(forces.gas_sound_speed);
    std::vector<char> forming(state.size(), 0);
    std::vector<double> birth_density(state.size(), 0.0);
    std::vector<double> converted(state.size(), 0.0);
    int formed = 0;
    for (std::size_t index = 0; index < state.size(); ++index) {
        if (state.component[index] != static_cast<std::uint8_t>(Component::Gas)) {
            continue;
        }
        if (!(state.density[index] > forces.gas_resolved_star_density) || (forces.gas_star_converging && !(state.divergence[index] < 0.0))) {
            continue;
        }
        if (state.internal_energy[index] > forces.gas_star_temperature * std::max(floor_energy, 0.0)) {
            continue;
        }
        const double dynamical =
            forces.gas_star_time * std::sqrt(forces.gas_resolved_star_density / state.density[index]);
        const double probability = dynamical > 1e-12 ? 1.0 - std::exp(-dt / dynamical) : 1.0;
        if (!forces.gas_continuous_stars && hashed_unit(forces.gas_seed, static_cast<std::uint64_t>(step), static_cast<std::uint64_t>(index)) >=
            probability) {
            continue;
        }
        if (forces.gas_continuous_stars && state.gas_fraction[index] <= 1e-6) continue;
        converted[index] = state.gas_mass(index) * (forces.gas_continuous_stars ? probability : 1.0);
        forming[index] = 1;
        birth_density[index] = state.density[index];
        ++formed;
    }
    if (forces.gas_continuous_stars) {
        // Update every reservoir before distributing feedback: no dependence on loop order.
        for (std::size_t i = 0; i < state.size(); ++i) if (forming[i]) {
            // Retain a tiny residual for a well-conditioned SPH carrier.
            converted[i] = std::min(converted[i], state.mass[i] * (state.gas_fraction[i] - 1e-6));
            state.gas_fraction[i] -= converted[i] / state.mass[i];
            state.formation_energy_removed += converted[i] * state.internal_energy[i];
            state.stellar_cohorts.push_back({static_cast<std::uint32_t>(i), static_cast<double>(step) * dt, converted[i]});
        }
        const std::vector<char> none(state.size(), 0);
        for (std::size_t i = 0; i < state.size(); ++i) if (forming[i])
            deposit_feedback(state, i, none, birth_density[i], forces, converted[i]);
        return formed;
    }
    for (std::size_t index = 0; index < state.size(); ++index) {
        if (forming[index] == 0) {
            continue;
        }
        deposit_feedback(state, index, forming, birth_density[index], forces, converted[index]);
        state.gas_fraction[index] = 0;
        state.component[index] = static_cast<std::uint8_t>(Component::YoungStar);
        if (state.birth_time.size() != state.size()) {
            state.birth_time.assign(state.size(), -1.0);
        }
        // Formation precedes the drift of this step.
        state.birth_time[index] = static_cast<double>(step > 0 ? step - 1 : 0) * dt;
        state.formation_energy_removed += state.mass[index] * state.internal_energy[index];
        state.internal_energy[index] = 0.0;
        state.density[index] = 0.0;
        state.divergence[index] = 0.0;
        state.hydro_dudt[index] = 0.0;
        state.cooling_delay[index] = 0.0;
    }
    return formed;
}

}  // namespace physics
