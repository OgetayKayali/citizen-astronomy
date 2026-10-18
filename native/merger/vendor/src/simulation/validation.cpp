#include "simulation/validation.hpp"
#include "forces/hydro.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace physics {
std::vector<DiskProfile> disk_profiles(Sample const &sample, std::vector<BodyDesc> const &bodies,
                                       std::vector<double> const &edges, ForceSet const *forces) {
    if (edges.size() < 2 || sample.position.size() != bodies.size() || sample.velocity.size() != bodies.size())
        throw std::invalid_argument("invalid disk profile dimensions");
    for (std::size_t j = 0; j < edges.size(); ++j)
        if (!std::isfinite(edges[j]) || edges[j] < 0 || (j && edges[j] <= edges[j - 1]))
            throw std::invalid_argument("profile edges must be finite, nonnegative and increasing");
    Vec3 center{}, velocity{}, axis{};
    double mass = 0;
    for (std::size_t i = 0; i < bodies.size(); ++i)
        if (bodies[i].particle.component == Component::Disk) {
            const double m = bodies[i].particle.mass;
            mass += m;
            center += sample.position[i] * m;
            velocity += sample.velocity[i] * m;
        }
    std::vector<DiskProfile> result(edges.size() - 1);
    for (std::size_t j = 0; j < result.size(); ++j) {
        result[j].inner = edges[j];
        result[j].outer = edges[j + 1];
    }
    if (!(mass > 0))
        return result;
    center = center / mass;
    velocity = velocity / mass;
    for (std::size_t i = 0; i < bodies.size(); ++i)
        if (bodies[i].particle.component == Component::Disk)
            axis += cross(sample.position[i] - center, sample.velocity[i] - velocity) * bodies[i].particle.mass;
    axis = norm(axis) > 1e-12 ? axis / norm(axis) : Vec3{0, 0, 1};
    const Vec3 reference = std::abs(axis.x) < 0.8 ? Vec3{1, 0, 0} : Vec3{0, 1, 0};
    const Vec3 projected = reference - axis * dot(reference, axis);
    const Vec3 xaxis = projected / norm(projected), yaxis = cross(axis, xaxis);
    std::vector<double> weights(result.size()), radial(result.size()), vertical(result.size());
    std::vector<double> c2(result.size()), s2(result.size()), c4(result.size()), s4(result.size()), squared(result.size());
    std::vector<std::vector<std::pair<double, double>>> heights(result.size());
    for (std::size_t i = 0; i < bodies.size(); ++i) {
        if (bodies[i].particle.component != Component::Disk)
            continue;
        const auto p = sample.position[i] - center, v = sample.velocity[i] - velocity;
        const double z = dot(p, axis);
        const auto plane = p - axis * z;
        const double r = norm(plane);
        const auto upper = std::upper_bound(edges.begin(), edges.end(), r);
        if (upper == edges.begin() || upper == edges.end())
            continue;
        const auto j = static_cast<std::size_t>(upper - edges.begin() - 1);
        auto &bin = result[j];
        const double m = bodies[i].particle.mass;
        const auto direction = r > 1e-12 ? plane / r : Vec3{};
        const double vr = dot(v, direction), vz = dot(v, axis), vp = dot(v, cross(axis, direction));
        ++bin.count;
        weights[j] += m;
        const double phi = std::atan2(dot(plane, yaxis), dot(plane, xaxis));
        c2[j] += m * std::cos(2 * phi); s2[j] += m * std::sin(2 * phi);
        c4[j] += m * std::cos(4 * phi); s4[j] += m * std::sin(4 * phi);
        squared[j] += m * m;
        radial[j] += m * vr;
        vertical[j] += m * vz;
        bin.rotation += m * vp;
        bin.sigma_r += m * vr * vr;
        bin.sigma_z += m * vz * vz;
        heights[j].push_back({std::abs(z), m});
    }
    for (std::size_t j = 0; j < result.size(); ++j) {
        auto &b = result[j];
        const double m = weights[j];
        if (!(m > 0))
            continue;
        b.surface_density = m / (3.14159265358979323846 * (b.outer * b.outer - b.inner * b.inner));
        b.rotation /= m;
        b.a2 = std::hypot(c2[j], s2[j]) / m;
        b.a4 = std::hypot(c4[j], s4[j]) / m;
        b.phase2 = std::atan2(s2[j], c2[j]) / 2;
        b.phase4 = std::atan2(s4[j], c4[j]) / 4;
        b.noise = std::sqrt(squared[j]) / m; // RMS random-phase amplitude, not a confidence threshold.
        b.sigma_r = std::sqrt(std::max(0.0, b.sigma_r / m - std::pow(radial[j] / m, 2)));
        b.sigma_z = std::sqrt(std::max(0.0, b.sigma_z / m - std::pow(vertical[j] / m, 2)));
        if (forces && forces->gravity && forces->gravitational_constant > 0 && b.count >= 16) {
            // Measured softened gravity, azimuth-averaged in the disk plane. Use force,
            // not streaming speed (which includes asymmetric drift), for epicyclic frequency.
            auto vc2 = [&](double radius) {
                double sum = 0;
                constexpr int azimuths = 24;
                for (int a = 0; a < azimuths; ++a) {
                    const double phi = 2 * 3.14159265358979323846 * (a + 0.5) / azimuths;
                    const auto direction = xaxis * std::cos(phi) + yaxis * std::sin(phi);
                    const auto probe = center + direction * radius;
                    double radial_force = 0;
                    for (std::size_t i = 0; i < bodies.size(); ++i) {
                        const auto d = sample.position[i] - probe;
                        const double r2 = dot(d, d) + forces->softening_length * forces->softening_length;
                        if (r2 > 1e-20)
                            radial_force += bodies[i].particle.mass * dot(d, direction) / (r2 * std::sqrt(r2));
                    }
                    sum -= radius * forces->gravitational_constant * radial_force;
                }
                return sum / azimuths;
            };
            const double r = 0.5 * (b.inner + b.outer), speed2 = vc2(r);
            const double k2 = (vc2(r * 1.1) - vc2(r * 0.9)) / (0.2 * r * r) + 2 * speed2 / (r * r);
            if (speed2 > 0) b.circular_speed = std::sqrt(speed2);
            if (speed2 > 0 && k2 > 0 && b.surface_density > 0) {
                b.kappa = std::sqrt(k2);
                b.q = b.sigma_r * b.kappa / (3.36 * forces->gravitational_constant * b.surface_density);
            }
        }
        std::sort(heights[j].begin(), heights[j].end());
        double cumulative = 0;
        for (const auto &[h, w] : heights[j]) {
            cumulative += w;
            if (cumulative >= 0.5 * m) {
                b.height50 = h;
                break;
            }
        }
    }
    return result;
}
Scene isolated_control(Scene const &source, std::size_t galaxy) {
    if (galaxy >= source.galaxies.size())
        throw std::invalid_argument("galaxy index is out of range");
    Scene result;
    result.config = source.config;
    auto full = source.make_state();
    prepare_gas(full, result.config.forces);
    const auto &spec = source.galaxies[galaxy];
    // apply_galaxies appends one contiguous group for each galaxy; names carry
    // the owner and component. Match the complete owner, including any dots.
    for (std::size_t i = source.authored_bodies; i < source.bodies.size(); ++i) {
        const auto &body = source.bodies[i];
        const auto last = body.name.rfind('.');
        const auto kind = last == std::string::npos || last == 0 ? std::string::npos : body.name.rfind('.', last - 1);
        if (kind == std::string::npos || body.name.substr(0, kind) != spec.name)
            continue;
        auto local = body;
        local.particle.position = local.particle.position - spec.center;
        local.particle.velocity = local.particle.velocity - spec.velocity;
        result.bodies.push_back(local);
    }
    if (result.bodies.empty())
        throw std::invalid_argument("galaxy has no realized particles");
    result.authored_bodies = result.bodies.size();
    return result;
}
} // namespace physics
