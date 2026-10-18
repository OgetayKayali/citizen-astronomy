#include "visualization/tracers.hpp"
#include "core/spatial_tree.hpp"
#include "visualization/optical.hpp"
#include <memory>
#include <numeric>
#include <stdexcept>
#include <unordered_map>

namespace physics::visual {
namespace {
double random_unit(std::uint64_t &seed) {
    seed += 0x9e3779b97f4a7c15ull;
    auto x = seed;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
    return static_cast<double>((x ^ (x >> 31)) >> 11) / 9007199254740992.0;
}
} // namespace
Trajectory refine_starlight(Trajectory const &source, double G, TracerOptions const &o,
                            std::function<void(std::size_t, std::size_t)> progress) {
    if (source.samples.size() < 2 || source.bodies.empty() || o.per_star < 2 || o.per_star > 32 || o.per_star % 2 ||
        !(G > 0) || !std::isfinite(G) || !(o.dt > 0) || !std::isfinite(o.dt) || !(o.softening > 0) ||
        !std::isfinite(o.softening) || !(o.theta >= 0 && o.theta <= 0.8))
        throw std::invalid_argument("invalid tracer settings (even per-star count 2..32 required)");
    const auto n = source.bodies.size();
    std::vector<double> masses;
    std::vector<std::size_t> ids(n);
    std::iota(ids.begin(), ids.end(), 0);
    for (auto const &b : source.bodies) {
        if (!(b.mass > 0) || !std::isfinite(b.mass) || b.light_weight >= 0)
            throw std::invalid_argument("tracers require an unrefined recording with physical masses (V5 or later)");
        masses.push_back(b.mass);
    }
    double last_time = -std::numeric_limits<double>::infinity();
    for (auto const &s : source.samples) {
        if (s.position.size() != n || s.velocity.size() != n || s.component.size() != n ||
            !std::isfinite(s.time) || s.time <= last_time)
            throw std::invalid_argument("invalid particle sample or non-increasing times");
        last_time = s.time;
        for (std::size_t i = 0; i < n; ++i)
            if (!std::isfinite(norm(s.position[i])) || !std::isfinite(norm(s.velocity[i])))
                throw std::invalid_argument("non-finite recorded phase space");
    }
    const auto &first = source.samples.front();
    std::unordered_map<std::string, std::vector<std::size_t>> disks;
    std::vector<std::string> owner(n);
    for (std::size_t i = 0; i < n; ++i)
        if (component_of(source.bodies[i], &first, i) == Component::Disk) {
            const auto last = source.bodies[i].name.rfind('.');
            const auto kind =
                last == std::string::npos || last == 0 ? std::string::npos : source.bodies[i].name.rfind('.', last - 1);
            owner[i] = kind == std::string::npos ? "galaxy" : source.bodies[i].name.substr(0, kind);
            disks[owner[i]].push_back(i);
        }
    if (disks.empty())
        throw std::invalid_argument("recording has no stellar disk");
    std::size_t disk_count = 0;
    for (auto const& entry : disks) disk_count += entry.second.size();
    if (disk_count > 1000000 / o.per_star)
        throw std::invalid_argument("refinement exceeds one million display tracers");
    Trajectory result = source;
    result.tracer_softening = o.softening;
    result.tracer_dt = o.dt;
    result.tracer_theta = o.theta;
    std::vector<Vec3> p, v, a;
    // Stable source order, independent of unordered-map traversal.
    std::unordered_map<std::string, std::unique_ptr<SpatialTree>> neighbors;
    for (auto const &[name, indices] : disks)
        neighbors[name] = std::make_unique<SpatialTree>(first.position, indices);
    for (std::size_t i = 0; i < n; ++i)
        if (!owner[i].empty()) {
            const auto near = neighbors.at(owner[i])->nearest(first.position[i], 8, i);
            if (near.empty())
                throw std::invalid_argument("a disk needs at least two source particles");
            result.bodies[i].light_weight = 0.15 * masses[i];
            std::uint64_t seed = 0x471ef732ull + i * 0x9e3779b97f4a7c15ull;
            for (std::size_t k = 0; k < o.per_star; k += 2) {
                const auto j = near[static_cast<std::size_t>(random_unit(seed) * near.size())].second;
                const auto l = near[static_cast<std::size_t>(random_unit(seed) * near.size())].second;
                const double u = (random_unit(seed) - 0.5) * 0.9, w = (random_unit(seed) - 0.5) * 0.9;
                const auto dp =
                    (first.position[j] - first.position[i]) * u + (first.position[l] - first.position[i]) * w;
                const auto dv =
                    (first.velocity[j] - first.velocity[i]) * u + (first.velocity[l] - first.velocity[i]) * w;
                for (int sign : {-1, 1}) {
                    p.push_back(first.position[i] + dp * sign);
                    v.push_back(first.velocity[i] + dv * sign);
                    auto body = source.bodies[i];
                    body.name = owner[i] + ".disk.light" + std::to_string(p.size());
                    body.mass = 0;
                    body.light_weight = 0.85 * masses[i] / static_cast<double>(o.per_star);
                    result.bodies.push_back(body);
                }
            }
        }
    auto append = [&](std::size_t frame) {
        auto &s = result.samples[frame];
        s.position.insert(s.position.end(), p.begin(), p.end());
        s.velocity.insert(s.velocity.end(), v.begin(), v.end());
        s.component.resize(n + p.size(), static_cast<std::uint8_t>(Component::Disk));
        s.birth_time.resize(n, -1);
        s.birth_time.resize(n + p.size(), -1);
        s.gas_density.resize(n + p.size(), 0);
        s.internal_energy.resize(n + p.size(), 0);
        if (!s.gas_fraction.empty()) s.gas_fraction.resize(n + p.size(), 0);
    };
    a.resize(p.size());
    auto evaluate = [&](std::vector<Vec3> const &centers) {
        SpatialTree tree(centers, ids, &masses);
#ifdef _OPENMP
#pragma omp parallel for schedule(static) if (p.size() > 256)
#endif
        for (int i = 0; i < static_cast<int>(p.size()); ++i)
            a[i] = tree.acceleration(p[i], G, o.softening, o.theta);
    };
    evaluate(first.position);
    append(0);
    std::vector<Vec3> centers(n);
    for (std::size_t frame = 1; frame < source.samples.size(); ++frame) {
        const auto &lo = source.samples[frame - 1];
        const auto &hi = source.samples[frame];
        const double interval = hi.time - lo.time;
        if (interval / o.dt > 1000000)
            throw std::invalid_argument("tracer timestep requires too many substeps per snapshot");
        const auto steps = static_cast<std::size_t>(std::ceil(interval / o.dt));
        const double dt = interval / static_cast<double>(steps);
        for (std::size_t step = 1; step <= steps; ++step) {
            for (std::size_t i = 0; i < p.size(); ++i) {
                v[i] += a[i] * (0.5 * dt);
                p[i] += v[i] * dt;
            }
            const double blend = static_cast<double>(step) / static_cast<double>(steps);
            for (std::size_t i = 0; i < n; ++i)
                centers[i] = interpolate_position(lo.position[i], lo.velocity[i], hi.position[i], hi.velocity[i],
                                                  interval, blend);
            evaluate(centers);
            for (std::size_t i = 0; i < p.size(); ++i)
                v[i] += a[i] * (0.5 * dt);
        }
        append(frame);
        if (progress)
            progress(frame, source.samples.size() - 1);
    }
    return result;
}
} // namespace physics::visual
