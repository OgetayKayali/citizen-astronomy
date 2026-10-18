#include "visualization/optical.hpp"
#include "core/spatial_tree.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <memory>
#include <unordered_map>

namespace physics::visual {
namespace {
constexpr double pi = 3.14159265358979323846;
}

Vec3 interpolate_position(Vec3 p0, Vec3 v0, Vec3 p1, Vec3 v1, double dt, double t) {
    t = std::clamp(t, 0.0, 1.0);
    const Vec3 linear = p0 * (1 - t) + p1 * t;
    if (!(dt > 0))
        return linear;
    const double t2 = t * t, t3 = t2 * t;
    const Vec3 cubic =
        p0 * (2 * t3 - 3 * t2 + 1) + v0 * (dt * (t3 - 2 * t2 + t)) + p1 * (-2 * t3 + 3 * t2) + v1 * (dt * (t3 - t2));
    const double chord = norm(p1 - p0);
    // Continuous reduction of curvature, avoiding loops when sampling missed an orbit.
    const double deviation = norm(cubic - linear);
    const double allowance = chord * 0.35;
    if (deviation > allowance && deviation > 0)
        return linear + (cubic - linear) * (allowance / deviation);
    return cubic;
}

Component component_of(BodyRecord const &body, Sample const *sample, std::size_t i) {
    if (sample && i < sample->component.size())
        return static_cast<Component>(sample->component[i]);
    for (auto const &entry : std::array<std::pair<char const *, Component>, 5>{{{".halo.", Component::Halo},
                                                                                {".bulge.", Component::Bulge},
                                                                                {".disk.", Component::Disk},
                                                                                {".gas.", Component::Gas},
                                                                                {".hot.", Component::HotGas}}}) {
        if (body.name.find(entry.first) != std::string::npos)
            return entry.second;
    }
    return Component::Body;
}

double young_fraction(double time, double birth, double lifetime) {
    if (birth < 0 || time < birth)
        return 0;
    const double age = (time - birth) / std::max(lifetime, 1e-6);
    return std::exp(-age);
}

double kernel_density(double r2, double major, double minor) {
    if (r2 >= 1 || r2 < 0 || major <= 0 || minor <= 0)
        return 0;
    return 3.0 * (1 - r2) * (1 - r2) / (pi * major * minor);
}

namespace {
std::string owner_name(std::string const &name) {
    const auto last = name.rfind('.');
    const auto kind = last == std::string::npos || last == 0 ? std::string::npos : name.rfind('.', last - 1);
    return kind == std::string::npos ? "legacy" : name.substr(0, kind);
}
} // namespace

StellarFrame stellar_frame(Trajectory const &trajectory, Sample const &sample, bool first_disk_only) {
    StellarFrame result;
    if (sample.position.size() != trajectory.bodies.size())
        return result;
    std::string owner;
    for (std::size_t i = 0; i < trajectory.bodies.size(); ++i)
        if (component_of(trajectory.bodies[i], &sample, i) == Component::Disk) {
            owner = owner_name(trajectory.bodies[i].name);
            break;
        }
    std::vector<std::size_t> selected;
    Vec3 center{}, velocity{}, angular{};
    double mass = 0;
    for (std::size_t i = 0; i < trajectory.bodies.size(); ++i) {
        const auto &body = trajectory.bodies[i];
        const auto c = component_of(body, &sample, i);
        if (c != Component::Disk && c != Component::Bulge && c != Component::YoungStar)
            continue;
        if (first_disk_only && owner_name(body.name) != owner)
            continue;
        selected.push_back(i);
        if (c == Component::Disk && owner_name(body.name) == owner &&
            sample.velocity.size() == sample.position.size()) {
            const double m = body.mass > 0 ? body.mass : body.light_weight >= 0 ? 0 : 1;
            mass += m;
            center += sample.position[i] * m;
            velocity += sample.velocity[i] * m;
        }
    }
    if (selected.empty())
        return result;
    if (mass > 0) {
        center = center / mass;
        velocity = velocity / mass;
        for (auto i : selected) {
            const auto &body = trajectory.bodies[i];
            if (component_of(body, &sample, i) != Component::Disk || owner_name(body.name) != owner)
                continue;
            const double m = body.mass > 0 ? body.mass : body.light_weight >= 0 ? 0 : 1;
            angular += cross(sample.position[i] - center, sample.velocity[i] - velocity) * m;
        }
        if (norm(angular) > 1e-12)
            result.normal = normalized(angular);
        if (result.normal.z < 0)
            result.normal = -result.normal;
    }
    auto percentile = [&](int axis, double q) {
        std::vector<double> v;
        for (auto i : selected) {
            const auto p = sample.position[i];
            v.push_back(axis == 0 ? p.x : axis == 1 ? p.y : p.z);
        }
        const auto index = static_cast<std::size_t>((v.size() - 1) * q);
        std::nth_element(v.begin(), v.begin() + index, v.end());
        return v[index];
    };
    const Vec3 low{percentile(0, .01), percentile(1, .01), percentile(2, .01)};
    const Vec3 high{percentile(0, .99), percentile(1, .99), percentile(2, .99)};
    result.center = (low + high) * .5;
    result.radius = std::max(0.1, norm(high - low) * .5);
    result.valid = true;
    return result;
}

double first_passage_time(Trajectory const &trajectory) {
    if (trajectory.samples.empty())
        return 0;
    const auto &first = trajectory.samples.front();
    std::vector<std::string> owners;
    std::vector<std::vector<std::size_t>> indices;
    for (auto kind : {Component::Bulge, Component::Disk}) {
        owners.clear();
        indices.clear();
        for (std::size_t i = 0; i < trajectory.bodies.size(); ++i) {
            const auto &b = trajectory.bodies[i];
            if (component_of(b, &first, i) != kind || (b.mass == 0 && b.light_weight >= 0))
                continue;
            const auto owner = owner_name(b.name);
            auto it = std::find(owners.begin(), owners.end(), owner);
            if (it == owners.end()) {
                owners.push_back(owner);
                indices.emplace_back();
                it = owners.end() - 1;
            }
            indices[static_cast<std::size_t>(it - owners.begin())].push_back(i);
        }
        if (owners.size() >= 2)
            break;
    }
    if (owners.size() < 2)
        return first.time;
    std::vector<double> separation;
    for (const auto &s : trajectory.samples) {
        if (s.position.size() != trajectory.bodies.size())
            return first.time;
        Vec3 centers[2];
        for (int g = 0; g < 2; ++g) {
            double mass = 0;
            for (auto i : indices[g]) {
                const double m = trajectory.bodies[i].mass > 0 ? trajectory.bodies[i].mass : 1;
                centers[g] += s.position[i] * m;
                mass += m;
            }
            centers[g] = centers[g] / mass;
        }
        separation.push_back(norm(centers[1] - centers[0]));
    }
    auto smooth = separation;
    for (std::size_t i = 2; i + 2 < separation.size(); ++i)
        smooth[i] = (separation[i - 2] + separation[i - 1] + separation[i] + separation[i + 1] + separation[i + 2]) / 5;
    for (std::size_t i = 3; i + 3 < smooth.size(); ++i)
        if (smooth[i] < .9 * smooth.front() && smooth[i] <= smooth[i - 1] && smooth[i] < smooth[i - 3] &&
            smooth[i] < smooth[i + 1] && smooth[i] < smooth[i + 3])
            return trajectory.samples[i].time;
    const auto minimum = std::min_element(smooth.begin(), smooth.end());
    return trajectory.samples[static_cast<std::size_t>(minimum - smooth.begin())].time;
}

std::vector<Splat> make_splats(Trajectory const &trajectory, Sample const *sample, std::vector<Vec3> const &positions,
                               double time, Vec3 eye, Vec3 right, Vec3 up, Vec3 forward,
                               OpticalSettings const &settings) {
    const std::size_t n = positions.size();
    if (n != trajectory.bodies.size())
        return {};
    std::vector<Component> components(n);
    std::array<std::vector<std::size_t>, 7> groups;
    std::vector<std::string> owners(n);
    std::unordered_map<std::string, std::array<std::size_t, 7>> population_counts;
    for (std::size_t i = 0; i < n; ++i) {
        components[i] = component_of(trajectory.bodies[i], sample, i);
        const auto group = static_cast<std::size_t>(components[i]);
        if (group < groups.size())
            groups[group].push_back(i);
        const auto &name = trajectory.bodies[i].name;
        const auto last = name.rfind('.');
        const auto kind = last == std::string::npos || last == 0 ? std::string::npos : name.rfind('.', last - 1);
        owners[i] = kind == std::string::npos ? "legacy" : name.substr(0, kind);
        const auto original = components[i] == Component::YoungStar ? Component::Gas : components[i];
        if (static_cast<std::size_t>(original) < 7)
            ++population_counts[owners[i]][static_cast<std::size_t>(original)];
    }
    // Evaluate stellar histories once. All channels use optical mass, including split tracer weights.
    std::vector<double> optical_mass(n), stellar_mass(n), blue(n), ionizing(n), hard(n);
    auto add_population = [&](std::size_t i, double mass, double age) {
        if (age < 0 || mass <= 0) return;
        blue[i] += mass * std::exp(-age / std::max(settings.lifetime, 1e-6));
        ionizing[i] += mass * std::exp(-age / std::max(settings.ionizing_lifetime, 1e-6));
        hard[i] += mass * std::exp(-age / std::max(settings.oiii_lifetime, 1e-6));
    };
    for (std::size_t i = 0; i < n; ++i) {
        auto const& b = trajectory.bodies[i];
        const auto c = components[i];
        const auto original = c == Component::YoungStar ? Component::Gas : c;
        const auto count = static_cast<std::size_t>(original) < 7 ?
            population_counts.at(owners[i])[static_cast<std::size_t>(original)] : 1;
        const double fallback = c == Component::Gas || c == Component::YoungStar ? .15 :
            c == Component::Bulge ? .3 : c == Component::Halo ? 2 : c == Component::HotGas ? .08 : 1;
        optical_mass[i] = b.light_weight >= 0 ? b.light_weight : b.mass > 0 ? b.mass : fallback / std::max<std::size_t>(1, count);
        if (c == Component::Disk) {
            stellar_mass[i] = optical_mass[i];
            add_population(i, optical_mass[i] * b.initial_young_fraction, time + b.initial_young_age);
        } else if (c == Component::YoungStar) {
            double birth = sample && i < sample->birth_time.size() ? sample->birth_time[i] : -1;
            if (birth < 0) for (auto const& frame : trajectory.samples)
                if (i < frame.component.size() && frame.component[i] == static_cast<std::uint8_t>(Component::YoungStar)) {
                    birth = frame.time;
                    break;
                }
            stellar_mass[i] = optical_mass[i];
            if (birth >= 0) add_population(i, optical_mass[i], time - birth);
        }
    }
    for (auto const& cohort : trajectory.stellar_cohorts) {
        if (cohort.body >= n || cohort.time > time) continue;
        const auto i = cohort.body;
        // Hybrid carriers retain their total gravitational mass throughout the recording.
        const double weight = trajectory.bodies[i].mass > 0 ? optical_mass[i] / trajectory.bodies[i].mass : 0;
        const double mass = cohort.mass * weight;
        stellar_mass[i] += mass;
        add_population(i, mass, time - cohort.time);
    }
    std::vector<std::size_t> sources;
    for (std::size_t i = 0; i < n; ++i) if (ionizing[i] > 1e-15 || hard[i] > 1e-15) sources.push_back(i);

    std::array<std::unique_ptr<SpatialTree>, 7> neighbor_trees;
    for (std::size_t g = 0; g < groups.size(); ++g)
        neighbor_trees[g] = std::make_unique<SpatialTree>(positions, groups[g]);
    std::vector<Splat> splats(n), stars(n), halpha(n), oiii(n);
    std::vector<Vec3> depth_axis(n);
    std::vector<char> resolved(n, 0);
    std::vector<char> visible(n, 0);
    // Independent per-body reconstruction; ordered output is restored after the loop.
#ifdef _OPENMP
#pragma omp parallel for schedule(static) if (n > 512)
#endif
    for (int slot = 0; slot < static_cast<int>(n); ++slot) {
        const auto i = static_cast<std::size_t>(slot);
        const Component c = components[i];
        const BodyRecord &body = trajectory.bodies[i];
        if ((c == Component::Halo && !settings.show_halo) || (c == Component::HotGas && !settings.show_hot_gas) ||
            (c == Component::Body && body.emission <= 0))
            continue;
        if (static_cast<std::size_t>(c) >= groups.size())
            continue;
        const bool gas = c == Component::Gas;
        const std::size_t neighbor_count =
            c == Component::Bulge                                                                    ? 32
            : trajectory.tracer_softening > 0 && (c == Component::Disk || c == Component::YoungStar) ? 96
            : c == Component::Disk || c == Component::YoungStar                                     ? 32
                                                                                                     : 16;
        const auto distances =
            neighbor_trees[static_cast<std::size_t>(c == Component::YoungStar ? Component::Disk : c)]->nearest(
                positions[i], neighbor_count, i);
        const std::size_t k = distances.size();
        // Compact weights vanish at the neighbor boundary, reducing topology flicker.
        double xx = 0, xy = 0, yy = 0, xz = 0, yz = 0, zz = 0, total = 0;
        if (k > 0) {
            const double h2 = std::max(distances[k - 1].first, 1e-8);
            for (std::size_t j = 0; j < k; ++j) {
                const Vec3 d = positions[distances[j].second] - positions[i];
                const double w = std::pow(std::max(0.0, 1 - distances[j].first / h2), 2);
                const double x = dot(d, right), y = dot(d, up), z = dot(d, forward);
                xx += w * x * x;
                xy += w * x * y;
                yy += w * y * y;
                xz += w * x * z;
                yz += w * y * z;
                zz += w * z * z;
                total += w;
            }
        }
        const double floor = gas ? 0.025 : 0.035;
        xx = xx / std::max(total, 1e-8) + floor * floor;
        xy /= std::max(total, 1e-8);
        yy = yy / std::max(total, 1e-8) + floor * floor;
        xz /= std::max(total, 1e-8);
        yz /= std::max(total, 1e-8);
        zz = zz / std::max(total, 1e-8) + floor * floor;
        if (c == Component::Bulge) {
            // Reduce sparse-particle streaks in the pressure-supported central light.
            // Keep half the measured anisotropy so a tidally distorted bulge can still elongate.
            const double mean = 0.5 * (xx + yy);
            xx = 0.5 * (xx + mean);
            yy = 0.5 * (yy + mean);
            xy *= 0.5;
        }
        // Conditional sections of a local 3D ellipsoid: tilted lanes retain
        // their orientation and stars can sit inside, ahead of, or behind dust.
        if (gas) {
            const double sigma = std::min(0.9, std::sqrt(zz)) * settings.smoothing;
            depth_axis[i] = (forward + right * (xz / zz) + up * (yz / zz)) * sigma;
            xx -= 0.8 * xz * xz / zz;
            xy -= 0.8 * xz * yz / zz;
            yy -= 0.8 * yz * yz / zz;
        }
        resolved[i] = k >= 4;
        const double disc = std::hypot(xx - yy, 2 * xy);
        const double angle = 0.5 * std::atan2(2 * xy, xx - yy);
        Splat s;
        s.position = positions[i];
        s.cosine = std::cos(angle);
        s.sine = std::sin(angle);
        const double scale = 2.8 * settings.smoothing;
        s.major = std::clamp(scale * std::sqrt(std::max(0.0, 0.5 * (xx + yy + disc))), 0.06, 2.5);
        s.minor = std::clamp(scale * std::sqrt(std::max(0.0, 0.5 * (xx + yy - disc))), 0.025, s.major);
        s.minor = std::max(s.minor, s.major * 0.12);
        if (c == Component::Body)
            s.major = s.minor = std::max(0.01, body.display_radius);
        s.depth = dot(s.position - eye, forward);
        s.color = body.color;
        const double mass = optical_mass[i];
        s.luminosity = mass * std::max(body.emission, 0.0);
        auto population_light = [&](Splat& light, double old_emission, Vec3 old_color) {
            const double young = std::clamp(blue[i], 0.0, stellar_mass[i]);
            const double old_light = (stellar_mass[i] - young) * std::max(old_emission, 0.0);
            const double young_light = young * std::max(body.formed_star_emission, 0.0) * settings.young_boost;
            light.luminosity = old_light + young_light;
            if (light.luminosity > 0)
                light.color = (old_color * old_light + body.formed_star_color * young_light) / light.luminosity;
        };
        if (c == Component::Disk || c == Component::YoungStar) {
            population_light(s, c == Component::Disk ? body.emission : 1.7,
                             c == Component::Disk ? body.color : Vec3{0.98, 0.91, 0.72});
            if (c == Component::YoungStar) {
                s.major = std::max(0.035, s.major * settings.association_size);
                s.minor = std::max(0.025, s.minor * settings.association_size);
            }
        }
        if (gas) {
            auto starlight = s;
            starlight.optical_depth = 0;
            population_light(starlight, 1.7, {0.98, 0.91, 0.72});
            starlight.major = std::max(0.035, starlight.major * settings.association_size);
            starlight.minor = std::max(0.025, starlight.minor * settings.association_size);
            stars[i] = starlight;
            double illumination = 0, hard_illumination = 0;
            const double radius2 = std::max(1e-8, settings.nebula_radius * settings.nebula_radius);
            for (auto j : sources) {
                const Vec3 d = positions[j] - positions[i];
                const double kernel = std::exp(-dot(d, d) / radius2);
                // Ionizing proxies scale with population mass and its configured young-star luminosity.
                const double yield = std::max(0.0, trajectory.bodies[j].formed_star_emission) / 6.5;
                illumination += ionizing[j] * kernel * yield;
                hard_illumination += hard[j] * kernel * yield;
            }
            const double gas_fraction = sample && i < sample->gas_fraction.size() ? sample->gas_fraction[i] : 1;
            const double gas_mass = mass * gas_fraction;
            const double energy = sample && i < sample->internal_energy.size() ? sample->internal_energy[i] : 0;
            const double cold = 1.0 / (1.0 + std::max(energy, 0.0) / 0.04);
            s.optical_depth = gas_mass * settings.dust * 12 * cold;
            s.luminosity = 0;
            const double rho = sample && i < sample->gas_density.size() ? sample->gas_density[i] : 0;
            const double dense = rho > 0 ? rho / (rho + std::max(1e-12, settings.nebula_density_scale)) : 0;
            const double response = illumination / (illumination + std::max(1e-12, settings.ionizing_mass_scale));
            // Hard radiation is a subset of the available ionizing illumination.
            const double hard_response = std::min(hard_illumination, illumination) /
                (illumination + std::max(1e-12, settings.ionizing_mass_scale));
            auto line = s;
            line.optical_depth = 0;
            line.major = std::max(0.035, line.major * settings.nebula_size);
            line.minor = std::max(0.025, line.minor * settings.nebula_size);
            const double luminosity = gas_mass * std::max(body.emission, 0.0) * settings.gas_glow * dense;
            line.color = settings.halpha_color;
            line.luminosity = luminosity * response * settings.halpha_strength;
            halpha[i] = line;
            line.color = settings.oiii_color;
            line.luminosity = luminosity * hard_response * settings.oiii_strength;
            oiii[i] = line;
        }
        if (s.luminosity <= 0 && s.optical_depth <= 0 && stars[i].luminosity <= 0 &&
            halpha[i].luminosity <= 0 && oiii[i].luminosity <= 0)
            continue;
        visible[i] = 1;
        splats[i] = s;
    }
    double reference_density = 0;
    if (!trajectory.samples.empty()) {
        std::vector<double> densities;
        const auto &initial = trajectory.samples.front();
        for (std::size_t i = 0; i < initial.gas_density.size() && i < trajectory.bodies.size(); ++i) {
            const double rho = initial.gas_density[i];
            if (component_of(trajectory.bodies[i], &initial, i) == Component::Gas && rho > 0 && std::isfinite(rho))
                densities.push_back(rho);
        }
        if (!densities.empty()) {
            const auto middle = densities.begin() + densities.size() / 2;
            std::nth_element(densities.begin(), middle, densities.end());
            reference_density = *middle;
        }
    }
    std::vector<Splat> result;
    result.reserve(n * 3);
    for (std::size_t i = 0; i < n; ++i)
        if (visible[i]) {
            auto diffuse = splats[i];
            if (components[i] == Component::Gas) {
                if (stars[i].luminosity > 0) result.push_back(stars[i]);
                if (halpha[i].luminosity > 0) result.push_back(halpha[i]);
                if (oiii[i].luminosity > 0) result.push_back(oiii[i]);
                constexpr std::array<double, 5> weights = {0.06, 0.24, 0.40, 0.24, 0.06};
                for (int slice = 0; slice < 5; ++slice) {
                    auto layer = diffuse;
                    layer.position += depth_axis[i] * static_cast<double>(slice - 2);
                    layer.depth = dot(layer.position - eye, forward);
                    layer.luminosity *= weights[slice];
                    layer.optical_depth *= weights[slice];
                    if (resolved[i] && settings.dust_structure > 0) {
                        const double rho = sample && i < sample->gas_density.size() ? sample->gas_density[i] : 0;
                        const double dense = reference_density > 0 && rho >= 0 ? rho / (rho + reference_density) : 1;
                        const double fraction = std::clamp(settings.dust_structure, 0.0, 1.0) * dense;
                        auto lane = layer;
                        lane.major *= 0.85;
                        lane.minor *= 0.60;
                        lane.optical_depth *= fraction;
                        lane.luminosity *= fraction;
                        layer.optical_depth *= 1 - fraction;
                        layer.luminosity *= 1 - fraction;
                        result.push_back(lane);
                    }
                    result.push_back(layer);
                }
                continue;
            }
            if (components[i] == Component::Disk || components[i] == Component::YoungStar) {
                // An unresolved stellar concentration shares its parent's actual orbit.
                // Split its existing luminosity: texture never creates additional light.
                auto compact = diffuse;
                // Stable ID variation is artistic population sampling, not new stars.
                std::uint64_t hash = 1469598103934665603ull;
                for (unsigned char ch : trajectory.bodies[i].name)
                    hash = (hash ^ ch) * 1099511628211ull;
                const double population = static_cast<double>((hash ^ (hash >> 32)) & 65535) / 65535.0;
                const double fraction =
                    std::clamp(settings.texture, 0.0, 0.3) * (0.15 + 2.55 * std::pow(population, 5));
                compact.luminosity *= fraction;
                diffuse.luminosity *= 1 - fraction;
                compact.major = compact.minor = 0.015 + 0.025 * population;
                if (fraction > 0)
                    result.push_back(compact);
                if (resolved[i]) {
                    // Normalized narrow and broad kernels preserve each packet's
                    // light while retaining faint envelopes and sharper streams.
                    auto detail = diffuse;
                    const double definition = std::clamp(settings.arm_definition, 0.0, 1.0);
                    const double elongation = 1 - diffuse.minor / diffuse.major;
                    detail.major *= 0.62 + 0.20 * definition * elongation;
                    detail.minor *= 0.62 - 0.24 * definition * elongation;
                    const double detail_fraction = std::clamp(settings.detail, 0.0, 1.0);
                    detail.luminosity *= detail_fraction;
                    diffuse.luminosity *= 1 - detail_fraction;
                    result.push_back(detail);
                }
            }
            result.push_back(diffuse);
        }
    std::stable_sort(result.begin(), result.end(), [](Splat const &a, Splat const &b) { return a.depth > b.depth; });
    return result;
}
} // namespace physics::visual
