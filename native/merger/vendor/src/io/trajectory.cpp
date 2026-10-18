#include "io/trajectory.hpp"

#include <algorithm>
#include <cstdint>
#include <cmath>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace physics {
namespace {

void write_bytes(std::ostream& output, void const* data, std::size_t size) {
    output.write(static_cast<char const*>(data), static_cast<std::streamsize>(size));
    if (!output) {
        throw std::runtime_error("failed while writing a trajectory");
    }
}

void read_bytes(std::istream& input, void* data, std::size_t size) {
    input.read(static_cast<char*>(data), static_cast<std::streamsize>(size));
    if (!input) {
        throw std::runtime_error("trajectory file ended early");
    }
}

void write_u32(std::ostream& output, std::uint32_t value) {
    unsigned char bytes[4] = {static_cast<unsigned char>(value & 0xffu),
                              static_cast<unsigned char>((value >> 8) & 0xffu),
                              static_cast<unsigned char>((value >> 16) & 0xffu),
                              static_cast<unsigned char>((value >> 24) & 0xffu)};
    write_bytes(output, bytes, 4);
}

std::uint32_t read_u32(std::istream& input) {
    unsigned char bytes[4] = {};
    read_bytes(input, bytes, 4);
    return static_cast<std::uint32_t>(bytes[0]) | (static_cast<std::uint32_t>(bytes[1]) << 8) |
           (static_cast<std::uint32_t>(bytes[2]) << 16) | (static_cast<std::uint32_t>(bytes[3]) << 24);
}

void write_f64(std::ostream& output, double value) { write_bytes(output, &value, sizeof(value)); }





double read_f64(std::istream& input) {
    double value = 0;
    read_bytes(input, &value, sizeof(value));
    return value;
}

void write_vec3(std::ostream& output, Vec3 value) {
    write_f64(output, value.x);
    write_f64(output, value.y);
    write_f64(output, value.z);
}

Vec3 read_vec3(std::istream& input) { return {read_f64(input), read_f64(input), read_f64(input)}; }

}  // namespace

void write_trajectory(Trajectory const& trajectory, std::string const& path) {
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("could not write trajectory '" + path + "'");
    }
    write_bytes(output, "PHYSTRJ7", 8);
    write_u32(output, static_cast<std::uint32_t>(trajectory.bodies.size()));
    write_u32(output, static_cast<std::uint32_t>(trajectory.samples.size()));
    write_f64(output, trajectory.tracer_softening);
    write_f64(output, trajectory.tracer_dt);
    write_f64(output, trajectory.tracer_theta);
    for (BodyRecord const& body : trajectory.bodies) {
        if (body.name.size() > 1024) {
            throw std::runtime_error("body name is too long to store");
        }
        write_u32(output, static_cast<std::uint32_t>(body.name.size()));
        write_bytes(output, body.name.data(), body.name.size());
        write_f64(output, body.display_radius);
        write_vec3(output, body.color);
        write_f64(output, body.roughness);
        write_f64(output, body.emission);
        write_vec3(output, body.formed_star_color);
        write_f64(output, body.formed_star_emission);
        write_f64(output, body.mass);
        write_f64(output, body.light_weight);
        write_f64(output, body.initial_young_fraction);
        write_f64(output, body.initial_young_age);
    }
    write_u32(output, static_cast<std::uint32_t>(trajectory.stellar_cohorts.size()));
    for (auto const& cohort : trajectory.stellar_cohorts) {
        if (cohort.body >= trajectory.bodies.size() || !std::isfinite(cohort.time) ||
            !std::isfinite(cohort.mass) || cohort.time < 0 || cohort.mass < 0)
            throw std::runtime_error("invalid stellar cohort");
        write_u32(output, cohort.body);
        write_f64(output, cohort.time);
        write_f64(output, cohort.mass);
    }
    for (Sample const& sample : trajectory.samples) {
        if (sample.position.size() != trajectory.bodies.size() ||
            sample.velocity.size() != trajectory.bodies.size() ||
            sample.component.size() != trajectory.bodies.size()) {
            throw std::runtime_error("trajectory sample does not match the body count");
        }
        write_f64(output, sample.time);
        write_f64(output, sample.diagnostics.kinetic);
        write_f64(output, sample.diagnostics.potential);
        write_f64(output, sample.diagnostics.energy);
        write_vec3(output, sample.diagnostics.linear_momentum);
        write_vec3(output, sample.diagnostics.angular_momentum);
        for (Vec3 position : sample.position) {
            write_vec3(output, position);
        }
        for (Vec3 velocity : sample.velocity) {
            write_vec3(output, velocity);
        }
        for (std::uint8_t component : sample.component) {
            write_bytes(output, &component, 1);
        }
        for (auto const* values : {&sample.birth_time, &sample.gas_density, &sample.internal_energy}) {
            if (!values->empty() && values->size() != trajectory.bodies.size())
                throw std::runtime_error("optical metadata does not match body count");
            for (std::size_t i=0; i<trajectory.bodies.size(); ++i)
                write_f64(output, values->empty() ? (values == &sample.birth_time ? -1.0 : 0.0) : (*values)[i]);
        }
        if (!sample.gas_fraction.empty() && sample.gas_fraction.size() != trajectory.bodies.size())
            throw std::runtime_error("gas fractions do not match body count");
        for (std::size_t i = 0; i < trajectory.bodies.size(); ++i) {
            const double fraction = sample.gas_fraction.empty() ?
                (sample.component[i] == static_cast<std::uint8_t>(Component::Gas) ||
                 sample.component[i] == static_cast<std::uint8_t>(Component::HotGas) ? 1.0 : 0.0) : sample.gas_fraction[i];
            if (!std::isfinite(fraction) || fraction < 0 || fraction > 1)
                throw std::runtime_error("invalid gas fraction");
            write_f64(output, fraction);
        }
        // Reserved V4 grid header: merger recordings always write an empty grid.
        write_u32(output, 0);
        write_f64(output, 0);
        write_f64(output, 0);
    }
}

Trajectory read_trajectory(std::string const& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("could not open trajectory '" + path + "'");
    }
    char magic[8] = {};
    read_bytes(input, magic, 8);
    const std::string magic_text(magic, 8);
    const bool version7 = magic_text == "PHYSTRJ7";
    const bool version6 = magic_text == "PHYSTRJ6" || version7;
    const bool version5 = magic_text == "PHYSTRJ5" || version6;
    const bool version4 = magic_text == "PHYSTRJ4" || version5;
    const bool version3 = magic_text == "PHYSTRJ3" || version4;
    const bool version2 = magic_text == "PHYSTRJ2" || version3;
    if (magic_text != "PHYSTRJ1" && !version2) {
        throw std::runtime_error("'" + path + "' is not a PhysicsEngine trajectory");
    }
    const std::uint32_t body_count = read_u32(input);
    const std::uint32_t sample_count = read_u32(input);
    Trajectory trajectory;
    if (version6) {
        trajectory.tracer_softening = read_f64(input);
        trajectory.tracer_dt = read_f64(input);
        trajectory.tracer_theta = read_f64(input);
    }
    trajectory.bodies.resize(body_count);
    for (BodyRecord& body : trajectory.bodies) {
        const std::uint32_t name_length = read_u32(input);
        if (name_length > 1024) throw std::runtime_error("trajectory body name is too long");
        body.name.assign(name_length, '\0');
        if (name_length > 0) {
            read_bytes(input, body.name.data(), name_length);
        }
        body.display_radius = read_f64(input);
        body.color = read_vec3(input);
        if (version2) {
            body.roughness = read_f64(input);
            body.emission = read_f64(input);
        } else {
            body.roughness = 0.45;
            body.emission = 0;
        }
        if (version3) {
            body.formed_star_color = read_vec3(input);
            body.formed_star_emission = read_f64(input);
        }
        if (version5) body.mass = read_f64(input);
        if (version6) body.light_weight = read_f64(input);
        if (version7) {
            body.initial_young_fraction = read_f64(input);
            body.initial_young_age = read_f64(input);
            if (!std::isfinite(body.initial_young_fraction) || body.initial_young_fraction < 0 ||
                body.initial_young_fraction > 1 || !std::isfinite(body.initial_young_age) || body.initial_young_age < 0)
                throw std::runtime_error("invalid initial stellar population");
        }
    }
    if (version7) {
        const auto count = read_u32(input);
        // Check the file extent before reserving space for a potentially corrupt count.
        const auto start = input.tellg();
        input.seekg(0, std::ios::end);
        const auto remaining = input.tellg() - start;
        input.seekg(start);
        if (remaining < static_cast<std::streamoff>(count) * 20)
            throw std::runtime_error("truncated stellar cohorts");
        trajectory.stellar_cohorts.reserve(count);
        for (std::uint32_t i = 0; i < count; ++i) {
            StellarCohort cohort{read_u32(input), read_f64(input), read_f64(input)};
            if (cohort.body >= body_count || !std::isfinite(cohort.time) || !std::isfinite(cohort.mass) ||
                cohort.time < 0 || cohort.mass < 0)
                throw std::runtime_error("invalid stellar cohort");
            trajectory.stellar_cohorts.push_back(cohort);
        }
    }
    trajectory.samples.resize(sample_count);
    for (Sample& sample : trajectory.samples) {
        sample.time = read_f64(input);
        sample.diagnostics.kinetic = read_f64(input);
        sample.diagnostics.potential = read_f64(input);
        sample.diagnostics.energy = read_f64(input);
        sample.diagnostics.linear_momentum = read_vec3(input);
        sample.diagnostics.angular_momentum = read_vec3(input);
        sample.position.resize(body_count);
        sample.velocity.resize(body_count);
        for (Vec3& position : sample.position) {
            position = read_vec3(input);
        }
        for (Vec3& velocity : sample.velocity) {
            velocity = read_vec3(input);
        }
        if (version3) {
            sample.component.resize(body_count);
            for (std::uint8_t& component : sample.component) {
                read_bytes(input, &component, 1);
            }
        }
        if (version5) {
            for (auto* values : {&sample.birth_time, &sample.gas_density, &sample.internal_energy}) {
                values->resize(body_count);
                for (double& value : *values) value = read_f64(input);
            }
        }
        if (version7) {
            sample.gas_fraction.resize(body_count);
            for (auto& fraction : sample.gas_fraction) {
                fraction = read_f64(input);
                if (!std::isfinite(fraction) || fraction < 0 || fraction > 1)
                    throw std::runtime_error("invalid gas fraction");
            }
        }
        if (version4) {
            const auto legacy_grid_size = read_u32(input);
            (void)read_f64(input);
            (void)read_f64(input);
            if (legacy_grid_size != 0)
                throw std::runtime_error("Grid recordings are not supported by Galaxy Merger");
        }
    }
    return trajectory;
}

}  // namespace physics
