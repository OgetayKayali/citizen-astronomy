#include "simulation/galaxy.hpp"
#include "forces/forces.hpp"

#include <algorithm>
#include <cmath>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace physics {
namespace {

constexpr double kPi = 3.14159265358979323846;

struct Field {
    std::vector<double> radius;
    std::vector<double> circular_speed_squared;
    std::vector<double> kappa_squared;
    std::vector<double> potential;
    std::vector<double> halo_dispersion_squared;
};

double hernquist_cdf(double radius, double scale) {
    const double fraction = radius / (radius + scale);
    return fraction * fraction;
}

double halo_enclosed_mass(EquilibriumComponents const &model, double radius) {
    if (!(radius > 0.0)) {
        return 0.0;
    }
    const double cutoff_fraction = hernquist_cdf(model.halo_cutoff, model.halo_scale);
    const double limited = std::min(radius, model.halo_cutoff);
    return model.halo_mass * hernquist_cdf(limited, model.halo_scale) / cutoff_fraction;
}

double truncated_hernquist_density(double mass, double scale, double cutoff, double radius) {
    if (!(mass > 0.0) || !(radius > 0.0) || radius >= cutoff) {
        return 0.0;
    }
    const double cutoff_fraction = hernquist_cdf(cutoff, scale);
    const double untruncated = mass * scale / (2.0 * kPi * radius * std::pow(radius + scale, 3));
    return untruncated / cutoff_fraction;
}

double halo_density(EquilibriumComponents const &model, double radius) {
    return truncated_hernquist_density(model.halo_mass, model.halo_scale, model.halo_cutoff, radius);
}

double disk_surface_density(EquilibriumComponents const &model, double radius) {
    const double central = model.disk_mass / (2.0 * kPi * model.disk_scale_radius * model.disk_scale_radius);
    return central * std::exp(-radius / model.disk_scale_radius);
}

double radial_acceleration(EquilibriumComponents const &model, double radius) {
    if (!(radius > 0.0)) {
        return 0.0;
    }
    const double halo_speed_squared = model.gravitational_constant * halo_enclosed_mass(model, radius) / radius;
    const double disk_speed_squared = exponential_disk_circular_speed_squared(
        model.gravitational_constant, model.disk_mass, model.disk_scale_radius, radius);
    return (halo_speed_squared + disk_speed_squared) / radius;
}

double interpolate(std::vector<double> const &radius, std::vector<double> const &values, double r) {
    if (r <= radius.front()) {
        return values.front();
    }
    if (r >= radius.back()) {
        return values.back();
    }
    const auto upper = std::upper_bound(radius.begin(), radius.end(), r);
    const std::size_t index = static_cast<std::size_t>(upper - radius.begin());
    const double left = radius[index - 1];
    const double right = radius[index];
    const double blend = (r - left) / (right - left);
    return values[index - 1] * (1.0 - blend) + values[index] * blend;
}

Field build_field(EquilibriumComponents const &model) {
    constexpr int kGrid = 2048;
    const double inner = 1e-4 * std::min(model.halo_scale, model.disk_scale_radius);
    const double outer = std::max(model.halo_cutoff, 40.0 * model.disk_scale_radius);
    Field field;
    field.radius.resize(kGrid);
    field.circular_speed_squared.resize(kGrid);
    field.kappa_squared.resize(kGrid);
    field.potential.resize(kGrid);
    field.halo_dispersion_squared.resize(kGrid);

    const double log_span = std::log(outer / inner);
    for (int i = 0; i < kGrid; ++i) {
        const double radius = inner * std::exp(log_span * static_cast<double>(i) / static_cast<double>(kGrid - 1));
        field.radius[static_cast<std::size_t>(i)] = radius;
        const double halo_speed_squared = model.gravitational_constant * halo_enclosed_mass(model, radius) / radius;
        const double disk_speed_squared = exponential_disk_circular_speed_squared(
            model.gravitational_constant, model.disk_mass, model.disk_scale_radius, radius);
        field.circular_speed_squared[static_cast<std::size_t>(i)] = halo_speed_squared + disk_speed_squared;
    }

    for (int i = 0; i < kGrid; ++i) {
        const int left = std::max(0, i - 1);
        const int right = std::min(kGrid - 1, i + 1);
        const double radius_left = field.radius[static_cast<std::size_t>(left)];
        const double radius_right = field.radius[static_cast<std::size_t>(right)];
        const double speed_left =
            std::sqrt(std::max(0.0, field.circular_speed_squared[static_cast<std::size_t>(left)]));
        const double speed_right =
            std::sqrt(std::max(0.0, field.circular_speed_squared[static_cast<std::size_t>(right)]));
        const double slope =
            (radius_right > radius_left) ? (speed_right - speed_left) / (radius_right - radius_left) : 0.0;
        const double radius = field.radius[static_cast<std::size_t>(i)];
        const double speed = std::sqrt(std::max(0.0, field.circular_speed_squared[static_cast<std::size_t>(i)]));
        const double omega = speed / radius;
        const double kappa_squared = 2.0 * omega * (omega + slope);
        field.kappa_squared[static_cast<std::size_t>(i)] = std::max(0.0, kappa_squared);
    }

    const double total_mass = model.halo_mass + model.disk_mass;
    field.potential[kGrid - 1] =
        -model.gravitational_constant * total_mass / field.radius[static_cast<std::size_t>(kGrid - 1)];
    for (int i = kGrid - 2; i >= 0; --i) {
        const double left = field.radius[static_cast<std::size_t>(i)];
        const double right = field.radius[static_cast<std::size_t>(i + 1)];
        const double g_left = radial_acceleration(model, left);
        const double g_right = radial_acceleration(model, right);
        field.potential[static_cast<std::size_t>(i)] =
            field.potential[static_cast<std::size_t>(i + 1)] - 0.5 * (g_left + g_right) * (right - left);
    }

    std::vector<double> weight_integral(static_cast<std::size_t>(kGrid), 0.0);
    for (int i = kGrid - 2; i >= 0; --i) {
        const double left = field.radius[static_cast<std::size_t>(i)];
        const double right = field.radius[static_cast<std::size_t>(i + 1)];
        const double integrand_left = halo_density(model, left) * radial_acceleration(model, left);
        const double integrand_right = halo_density(model, right) * radial_acceleration(model, right);
        weight_integral[static_cast<std::size_t>(i)] = weight_integral[static_cast<std::size_t>(i + 1)] +
                                                       0.5 * (integrand_left + integrand_right) * (right - left);
        const double density = halo_density(model, left);
        field.halo_dispersion_squared[static_cast<std::size_t>(i)] =
            density > 0.0 ? weight_integral[static_cast<std::size_t>(i)] / density : 0.0;
    }
    return field;
}

void validate_model(EquilibriumComponents const &model) {
    if (!(model.gravitational_constant > 0.0) || !(model.halo_mass > 0.0) || !(model.halo_scale > 0.0)) {
        throw std::runtime_error("a galaxy needs a positive gravitational constant, halo mass, and halo scale");
    }
    if (!(model.disk_mass > 0.0) || !(model.disk_scale_radius > 0.0) || !(model.disk_height > 0.0)) {
        throw std::runtime_error("a galaxy needs a positive disk mass, disk scale radius, and disk height");
    }
    if (!(model.halo_cutoff > model.halo_scale)) {
        throw std::runtime_error("halo_cutoff must be larger than halo_scale");
    }
    if (!(model.dispersion_ratio > 0.0)) {
        throw std::runtime_error("dispersion_ratio must be positive");
    }
}

double disk_vertical_dispersion_squared(EquilibriumComponents const &model, double radius) {
    return kPi * model.gravitational_constant * disk_surface_density(model, radius) * model.disk_height;
}

double epicycle_ratio(Field const &field, double radius) {
    const double speed_squared = interpolate(field.radius, field.circular_speed_squared, radius);
    if (!(speed_squared > 0.0) || !(radius > 0.0)) {
        return 1.0;
    }
    const double kappa_squared = interpolate(field.radius, field.kappa_squared, radius);
    const double omega_squared = speed_squared / (radius * radius);
    return std::clamp(kappa_squared / (4.0 * omega_squared), 0.0, 1.0);
}

Vec3 rotate_x(Vec3 value, double cosine, double sine) {
    return {value.x, value.y * cosine - value.z * sine, value.y * sine + value.z * cosine};
}

double unit_open(std::mt19937_64 &rng) {
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    return std::min(std::max(unit(rng), 1e-12), 1.0 - 1e-12);
}

Vec3 sample_direction(std::mt19937_64 &rng) {
    const double mu = 2.0 * unit_open(rng) - 1.0;
    const double phi = 2.0 * kPi * unit_open(rng);
    const double sine = std::sqrt(std::max(0.0, 1.0 - mu * mu));
    return {sine * std::cos(phi), sine * std::sin(phi), mu};
}

double sample_exponential(std::mt19937_64 &rng) { return -std::log(unit_open(rng)); }

EquilibriumComponents components_from(GalaxySpec const &spec, double gravitational_constant) {
    EquilibriumComponents model;
    model.gravitational_constant = gravitational_constant;
    model.halo_mass = spec.halo_mass;
    model.halo_scale = spec.halo_scale;
    model.halo_cutoff = spec.halo_cutoff > 0.0 ? spec.halo_cutoff : 10.0 * spec.halo_scale;
    model.disk_mass = spec.disk_mass;
    model.disk_scale_radius = spec.disk_scale_radius;
    model.disk_height = spec.disk_height > 0.0 ? spec.disk_height : 0.12 * spec.disk_scale_radius;
    model.dispersion_ratio = spec.dispersion_ratio;
    model.target_q = spec.target_q;
    return model;
}

} // namespace

std::uint64_t derive_galaxy_seed(std::uint64_t scene_seed, std::size_t index) {
    std::uint64_t mixed = scene_seed + 0x9E3779B97F4A7C15ull * (static_cast<std::uint64_t>(index) + 1ull);
    mixed = (mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9ull;
    mixed = (mixed ^ (mixed >> 27)) * 0x94D049BB133111EBull;
    return mixed ^ (mixed >> 31);
}

double exponential_disk_circular_speed_squared(double gravitational_constant, double disk_mass,
                                               double disk_scale_radius, double radius) {
    if (!(radius > 0.0) || !(disk_mass > 0.0)) {
        return 0.0;
    }
    const double y = radius / (2.0 * disk_scale_radius);
    const double central = disk_mass / (2.0 * kPi * disk_scale_radius * disk_scale_radius);
    const double first = std::cyl_bessel_i(0.0, y) * std::cyl_bessel_k(0.0, y);
    const double second = std::cyl_bessel_i(1.0, y) * std::cyl_bessel_k(1.0, y);
    const double bracket = first - second;
    if (!(bracket > 0.0) || !std::isfinite(bracket)) {
        return 0.0;
    }
    return kPi * gravitational_constant * central * (radius * radius / disk_scale_radius) * bracket;
}

double exponential_disk_potential(double gravitational_constant, double disk_mass, double disk_scale_radius,
                                  double radius) {
    if (!(radius > 0.0) || !(disk_mass > 0.0)) {
        return 0.0;
    }
    const double y = radius / (2.0 * disk_scale_radius);
    const double central = disk_mass / (2.0 * kPi * disk_scale_radius * disk_scale_radius);
    const double bracket =
        std::cyl_bessel_i(1.0, y) * std::cyl_bessel_k(0.0, y) - std::cyl_bessel_i(0.0, y) * std::cyl_bessel_k(1.0, y);
    if (!std::isfinite(bracket)) {
        return -gravitational_constant * disk_mass / radius;
    }
    return kPi * gravitational_constant * central * radius * bracket;
}

double hernquist_isotropic_dispersion_squared(double gravitational_constant, double halo_mass, double halo_scale,
                                              double radius) {
    if (!(radius > 0.0)) {
        return 0.0;
    }
    const double x = radius / halo_scale;
    const double brace = 12.0 * x * std::pow(1.0 + x, 3) * std::log((1.0 + x) / x) -
                         (x / (1.0 + x)) * (25.0 + 52.0 * x + 42.0 * x * x + 12.0 * x * x * x);
    return gravitational_constant * halo_mass / (12.0 * halo_scale) * brace;
}

double compound_circular_speed_squared(EquilibriumComponents const &model, double radius) {
    validate_model(model);
    const Field field = build_field(model);
    return std::max(0.0, interpolate(field.radius, field.circular_speed_squared, radius));
}

double compound_halo_dispersion_squared(EquilibriumComponents const &model, double radius) {
    validate_model(model);
    const Field field = build_field(model);
    return std::max(0.0, interpolate(field.radius, field.halo_dispersion_squared, radius));
}

double compound_streaming_speed(EquilibriumComponents const &model, double radius) {
    validate_model(model);
    const Field field = build_field(model);
    const double speed_squared = std::max(0.0, interpolate(field.radius, field.circular_speed_squared, radius));
    const double sigma_r_squared =
        model.dispersion_ratio * model.dispersion_ratio * disk_vertical_dispersion_squared(model, radius);
    const double ratio = epicycle_ratio(field, radius);
    // σ_R^2 ∝ exp(-R/Rd), so the asymmetric-drift term carries 2 R/Rd.
    const double streamed = speed_squared + sigma_r_squared * (1.0 - ratio - 2.0 * radius / model.disk_scale_radius);
    return streamed > 0.0 ? std::sqrt(streamed) : 0.0;
}

struct LiveField {
    std::vector<double> spherical_radius;
    std::vector<double> halo_dispersion_squared;
    std::vector<double> bulge_dispersion_squared;
    std::vector<double> spherical_potential;
    std::vector<double> cylindrical_radius;
    std::vector<double> circular_speed_squared;
    std::vector<double> kappa_squared;
    std::vector<double> sigma_z_squared;
    std::vector<double> midplane_potential;
    std::vector<double> guiding_angular_momentum;
    std::size_t guiding_bins = 0;
    std::vector<double> vertical_height;
    std::vector<std::vector<double>> vertical_variance;
};

std::vector<double> vertical_jeans_variance(double height, std::vector<double> const &z,
                                            std::vector<double> const &restoring) {
    if (!(height > 0) || !std::isfinite(height) || z.size() < 2 || z.size() != restoring.size())
        throw std::invalid_argument("invalid vertical Jeans grid");
    std::vector<double> density(z.size()), result(z.size());
    for (std::size_t i = 0; i < z.size(); ++i) {
        if (!std::isfinite(z[i]) || z[i] < 0 || z[i] / height > 50 || (i && z[i] <= z[i - 1]) ||
            !std::isfinite(restoring[i]) || restoring[i] < 0)
            throw std::invalid_argument("invalid vertical Jeans field");
        density[i] = 1 / std::pow(std::cosh(z[i] / height), 2);
    }
    // Exponential tail of sech^2, with slowly varying acceleration at the upper boundary.
    double integral = 0.5 * height * density.back() * restoring.back();
    result.back() = integral / density.back();
    for (std::size_t i = z.size() - 1; i > 0; --i) {
        integral += 0.5 * (density[i] * restoring[i] + density[i - 1] * restoring[i - 1]) * (z[i] - z[i - 1]);
        result[i - 1] = integral / density[i - 1];
    }
    return result;
}

void measure_vertical_support(LiveField &field, std::vector<Vec3> const &positions, std::vector<double> const &masses,
                              EquilibriumComponents const &model, double epsilon) {
    constexpr int nz = 65, angles = 16;
    const auto nr = field.cylindrical_radius.size();
    double cosine[angles], sine[angles];
    for (int a = 0; a < angles; ++a) {
        cosine[a] = std::cos(2 * kPi * a / angles);
        sine[a] = std::sin(2 * kPi * a / angles);
    }
    field.vertical_height.resize(nz);
    for (int k = 0; k < nz; ++k)
        field.vertical_height[k] = 8 * model.disk_height * k / (nz - 1);
    field.vertical_variance.resize(nr);
    // Average rotated copies and their midplane reflections. This removes an individual
    // neighbor's noisy az without changing the force law used by the actual simulation.
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
    for (int slot = 0; slot < static_cast<int>(nr); ++slot) {
        const double r = field.cylindrical_radius[slot];
        std::vector<double> force(nz);
        for (int k = 1; k < nz; ++k) {
            const double z = field.vertical_height[k];
            double sum = 0;
            for (std::size_t j = 0; j < positions.size(); ++j) {
                const auto p = positions[j];
                for (int a = 0; a < angles; ++a) {
                    const double dx = r - (p.x * cosine[a] - p.y * sine[a]);
                    const double dy = p.x * sine[a] + p.y * cosine[a];
                    const double horizontal = dx * dx + dy * dy + epsilon * epsilon;
                    for (double sign : {-1.0, 1.0}) {
                        const double dz = z - sign * p.z;
                        const double d2 = horizontal + dz * dz;
                        sum += masses[j] * dz / (d2 * std::sqrt(d2));
                    }
                }
            }
            force[k] = std::max(0.0, model.gravitational_constant * sum / (2 * angles));
        }
        field.vertical_variance[slot] = vertical_jeans_variance(model.disk_height, field.vertical_height, force);
    }
}

double vertical_variance_at(LiveField const &field, double r, double z) {
    const auto &radii = field.cylindrical_radius;
    const auto upper = std::upper_bound(radii.begin(), radii.end(), r);
    const auto hi = std::clamp<std::size_t>(static_cast<std::size_t>(upper - radii.begin()), 1, radii.size() - 1);
    const double blend = std::clamp((r - radii[hi - 1]) / (radii[hi] - radii[hi - 1]), 0.0, 1.0);
    return (1 - blend) * interpolate(field.vertical_height, field.vertical_variance[hi - 1], std::abs(z)) +
           blend * interpolate(field.vertical_height, field.vertical_variance[hi], std::abs(z));
}

void smooth_values(std::vector<double> &values) {
    if (values.size() < 3) {
        return;
    }
    const std::vector<double> original = values;
    for (std::size_t index = 1; index + 1 < values.size(); ++index) {
        values[index] = 0.25 * original[index - 1] + 0.5 * original[index] + 0.25 * original[index + 1];
    }
}

void fill_empty_bins(std::vector<double> const &radius, std::vector<double> &values, std::vector<int> const &counts) {
    const int bin_count = static_cast<int>(radius.size());
    for (int index = 0; index < bin_count; ++index) {
        if (counts[static_cast<std::size_t>(index)] > 0) {
            continue;
        }
        int left = index - 1;
        while (left >= 0 && counts[static_cast<std::size_t>(left)] == 0) {
            --left;
        }
        int right = index + 1;
        while (right < bin_count && counts[static_cast<std::size_t>(right)] == 0) {
            ++right;
        }
        if (left >= 0 && right < bin_count) {
            const double span = radius[static_cast<std::size_t>(right)] - radius[static_cast<std::size_t>(left)];
            const double blend =
                span == 0.0 ? 0.0
                            : (radius[static_cast<std::size_t>(index)] - radius[static_cast<std::size_t>(left)]) / span;
            values[static_cast<std::size_t>(index)] = values[static_cast<std::size_t>(left)] * (1.0 - blend) +
                                                      values[static_cast<std::size_t>(right)] * blend;
        } else if (left >= 0) {
            values[static_cast<std::size_t>(index)] = values[static_cast<std::size_t>(left)];
        } else if (right < bin_count) {
            values[static_cast<std::size_t>(index)] = values[static_cast<std::size_t>(right)];
        }
    }
}

int log_bin(double radius, double inner, double outer, int bin_count) {
    if (!(radius > inner)) {
        return 0;
    }
    if (radius >= outer) {
        return bin_count - 1;
    }
    const double unit = std::log(radius / inner) / std::log(outer / inner);
    return std::clamp(static_cast<int>(unit * static_cast<double>(bin_count - 1)), 0, bin_count - 1);
}

// Gravity ignores velocity, so the softened field of a frozen set of positions
// is measured once. The Jeans equation and the Shu function use that field.
LiveField measure_live_field(std::vector<Vec3> const &position, std::vector<char> const &is_disk,
                             std::vector<double> const &masses, EquilibriumComponents const &model, double bulge_mass,
                             double bulge_scale, double bulge_cutoff, double softening_length) {
    State state;
    double total_mass = 0.0;
    for (std::size_t index = 0; index < position.size(); ++index) {
        total_mass += masses[index];
        state.add(Particle{masses[index], position[index], {}});
    }
    ForceSet forces;
    forces.gravity = true;
    forces.gravitational_constant = model.gravitational_constant;
    forces.softening_length = softening_length;
    Accelerations accelerations;
    compute_accelerations(state, accelerations, forces);

    constexpr int kBins = 36;
    const double spherical_inner = 1e-3 * model.halo_scale;
    const double spherical_outer = model.halo_cutoff;
    const double cylindrical_inner = 1e-3 * model.disk_scale_radius;
    const double cylindrical_outer = 12.0 * model.disk_scale_radius;

    LiveField field;
    field.spherical_radius.resize(kBins);
    field.cylindrical_radius.resize(kBins);
    std::vector<double> inward(kBins, 0.0);
    std::vector<double> circular(kBins, 0.0);
    std::vector<int> spherical_counts(kBins, 0);
    std::vector<int> circular_counts(kBins, 0);
    std::vector<std::vector<double>> vertical_samples(kBins);

    const double log_sphere = std::log(spherical_outer / spherical_inner);
    const double log_disk = std::log(cylindrical_outer / cylindrical_inner);
    for (int bin = 0; bin < kBins; ++bin) {
        const double unit = static_cast<double>(bin) / static_cast<double>(kBins - 1);
        field.spherical_radius[static_cast<std::size_t>(bin)] = spherical_inner * std::exp(log_sphere * unit);
        field.cylindrical_radius[static_cast<std::size_t>(bin)] = cylindrical_inner * std::exp(log_disk * unit);
    }

    for (std::size_t index = 0; index < position.size(); ++index) {
        const Vec3 point = position[index];
        const double spherical = norm(point);
        if (spherical > 0.0) {
            const double radial = (accelerations.ax[index] * point.x + accelerations.ay[index] * point.y +
                                   accelerations.az[index] * point.z) /
                                  spherical;
            const int bin = log_bin(spherical, spherical_inner, spherical_outer, kBins);
            inward[static_cast<std::size_t>(bin)] += -radial;
            ++spherical_counts[static_cast<std::size_t>(bin)];
        }
        const double cylindrical = std::sqrt(point.x * point.x + point.y * point.y);
        if (cylindrical > 0.0 && std::abs(point.z) <= 2.0 * model.disk_height) {
            const double radial = (accelerations.ax[index] * point.x + accelerations.ay[index] * point.y) / cylindrical;
            const int bin = log_bin(cylindrical, cylindrical_inner, cylindrical_outer, kBins);
            circular[static_cast<std::size_t>(bin)] += -cylindrical * radial;
            ++circular_counts[static_cast<std::size_t>(bin)];
        }
        if (is_disk[index] != 0 && cylindrical > 0.0) {
            const double height_ratio = point.z / model.disk_height;
            const double tangent = std::tanh(height_ratio);
            if (std::abs(height_ratio) > 0.35 && std::abs(height_ratio) < 2.5 && std::abs(tangent) > 0.1) {
                const double dispersion = accelerations.az[index] * model.disk_height / (-2.0 * tangent);
                if (dispersion > 0.0 && std::isfinite(dispersion)) {
                    const int bin = log_bin(cylindrical, cylindrical_inner, cylindrical_outer, kBins);
                    vertical_samples[static_cast<std::size_t>(bin)].push_back(dispersion);
                }
            }
        }
    }

    for (int bin = 0; bin < kBins; ++bin) {
        if (spherical_counts[static_cast<std::size_t>(bin)] > 0) {
            inward[static_cast<std::size_t>(bin)] /=
                static_cast<double>(spherical_counts[static_cast<std::size_t>(bin)]);
        }
        if (circular_counts[static_cast<std::size_t>(bin)] > 0) {
            circular[static_cast<std::size_t>(bin)] /=
                static_cast<double>(circular_counts[static_cast<std::size_t>(bin)]);
        }
        inward[static_cast<std::size_t>(bin)] = std::max(0.0, inward[static_cast<std::size_t>(bin)]);
        circular[static_cast<std::size_t>(bin)] = std::max(0.0, circular[static_cast<std::size_t>(bin)]);
    }
    fill_empty_bins(field.spherical_radius, inward, spherical_counts);
    fill_empty_bins(field.cylindrical_radius, circular, circular_counts);
    smooth_values(inward);
    smooth_values(circular);

    field.halo_dispersion_squared.resize(kBins);
    field.bulge_dispersion_squared.assign(static_cast<std::size_t>(kBins), 0.0);
    field.spherical_potential.resize(kBins);
    field.spherical_potential[kBins - 1] =
        -model.gravitational_constant * total_mass / field.spherical_radius[static_cast<std::size_t>(kBins - 1)];
    for (int bin = kBins - 2; bin >= 0; --bin) {
        const double left = field.spherical_radius[static_cast<std::size_t>(bin)];
        const double right = field.spherical_radius[static_cast<std::size_t>(bin + 1)];
        field.spherical_potential[static_cast<std::size_t>(bin)] =
            field.spherical_potential[static_cast<std::size_t>(bin + 1)] -
            0.5 * (inward[static_cast<std::size_t>(bin)] + inward[static_cast<std::size_t>(bin + 1)]) * (right - left);
    }
    std::vector<double> weight(kBins, 0.0);
    for (int bin = kBins - 2; bin >= 0; --bin) {
        const double left = field.spherical_radius[static_cast<std::size_t>(bin)];
        const double right = field.spherical_radius[static_cast<std::size_t>(bin + 1)];
        const double density_left = halo_density(model, left);
        const double density_right = halo_density(model, right);
        weight[static_cast<std::size_t>(bin)] = weight[static_cast<std::size_t>(bin + 1)] +
                                                0.5 *
                                                    (density_left * inward[static_cast<std::size_t>(bin)] +
                                                     density_right * inward[static_cast<std::size_t>(bin + 1)]) *
                                                    (right - left);
        field.halo_dispersion_squared[static_cast<std::size_t>(bin)] =
            density_left > 0.0 ? std::max(0.0, weight[static_cast<std::size_t>(bin)] / density_left) : 0.0;
    }
    if (bulge_mass > 0.0 && bulge_scale > 0.0 && bulge_cutoff > bulge_scale) {
        std::vector<double> bulge_weight(static_cast<std::size_t>(kBins), 0.0);
        for (int bin = kBins - 2; bin >= 0; --bin) {
            const double left = field.spherical_radius[static_cast<std::size_t>(bin)];
            const double right = field.spherical_radius[static_cast<std::size_t>(bin + 1)];
            const double density_left = truncated_hernquist_density(bulge_mass, bulge_scale, bulge_cutoff, left);
            const double density_right = truncated_hernquist_density(bulge_mass, bulge_scale, bulge_cutoff, right);
            bulge_weight[static_cast<std::size_t>(bin)] =
                bulge_weight[static_cast<std::size_t>(bin + 1)] +
                0.5 *
                    (density_left * inward[static_cast<std::size_t>(bin)] +
                     density_right * inward[static_cast<std::size_t>(bin + 1)]) *
                    (right - left);
            field.bulge_dispersion_squared[static_cast<std::size_t>(bin)] =
                density_left > 0.0 ? std::max(0.0, bulge_weight[static_cast<std::size_t>(bin)] / density_left) : 0.0;
        }
    }

    field.circular_speed_squared = circular;
    field.kappa_squared.resize(kBins);
    field.sigma_z_squared.resize(kBins);
    std::vector<int> vertical_counts(kBins, 0);
    for (int bin = 0; bin < kBins; ++bin) {
        const std::size_t index = static_cast<std::size_t>(bin);
        const int left = std::max(0, bin - 1);
        const int right = std::min(kBins - 1, bin + 1);
        const double speed_left = std::sqrt(circular[static_cast<std::size_t>(left)]);
        const double speed_right = std::sqrt(circular[static_cast<std::size_t>(right)]);
        const double radius_left = field.cylindrical_radius[static_cast<std::size_t>(left)];
        const double radius_right = field.cylindrical_radius[static_cast<std::size_t>(right)];
        const double slope =
            radius_right > radius_left ? (speed_right - speed_left) / (radius_right - radius_left) : 0.0;
        const double speed = std::sqrt(circular[index]);
        const double omega = field.cylindrical_radius[index] > 0.0 ? speed / field.cylindrical_radius[index] : 0.0;
        field.kappa_squared[index] = std::max(0.0, 2.0 * omega * (omega + slope));

        std::vector<double> &samples = vertical_samples[index];
        if (samples.size() >= 6) {
            std::sort(samples.begin(), samples.end());
            field.sigma_z_squared[index] = samples[samples.size() / 2];
            vertical_counts[index] = static_cast<int>(samples.size());
        } else {
            field.sigma_z_squared[index] = disk_vertical_dispersion_squared(model, field.cylindrical_radius[index]);
            vertical_counts[index] = 1;
        }
    }
    fill_empty_bins(field.cylindrical_radius, field.sigma_z_squared, vertical_counts);
    for (int bin = 0; bin < kBins; ++bin) {
        const std::size_t index = static_cast<std::size_t>(bin);
        const double analytic = disk_vertical_dispersion_squared(model, field.cylindrical_radius[index]);
        if (!(field.sigma_z_squared[index] > 0.0) || !std::isfinite(field.sigma_z_squared[index])) {
            field.sigma_z_squared[index] = analytic;
        } else {
            field.sigma_z_squared[index] = std::clamp(field.sigma_z_squared[index], 0.25 * analytic, 4.0 * analytic);
        }
    }
    smooth_values(field.sigma_z_squared);

    field.midplane_potential.resize(kBins);
    field.guiding_angular_momentum.assign(static_cast<std::size_t>(kBins), 0.0);
    field.midplane_potential[static_cast<std::size_t>(kBins - 1)] =
        -model.gravitational_constant * total_mass / field.cylindrical_radius[static_cast<std::size_t>(kBins - 1)];
    for (int bin = kBins - 2; bin >= 0; --bin) {
        const std::size_t index = static_cast<std::size_t>(bin);
        const double left = field.cylindrical_radius[index];
        const double right = field.cylindrical_radius[index + 1];
        const double slope_left = left > 0.0 ? field.circular_speed_squared[index] / left : 0.0;
        const double slope_right = right > 0.0 ? field.circular_speed_squared[index + 1] / right : 0.0;
        field.midplane_potential[index] =
            field.midplane_potential[index + 1] - 0.5 * (slope_left + slope_right) * (right - left);
    }
    field.guiding_bins = 0;
    double previous_angular = 0.0;
    for (int bin = 0; bin < kBins; ++bin) {
        const std::size_t index = static_cast<std::size_t>(bin);
        const double speed = std::sqrt(std::max(0.0, field.circular_speed_squared[index]));
        const double angular = field.cylindrical_radius[index] * speed;
        if (field.guiding_bins > 0 && !(angular > previous_angular)) {
            break;
        }
        field.guiding_angular_momentum[index] = angular;
        previous_angular = angular;
        ++field.guiding_bins;
    }
    return field;
}

double live_epicycle_ratio(LiveField const &field, double radius) {
    const double speed_squared = interpolate(field.cylindrical_radius, field.circular_speed_squared, radius);
    if (!(speed_squared > 0.0) || !(radius > 0.0)) {
        return 1.0;
    }
    const double kappa_squared = interpolate(field.cylindrical_radius, field.kappa_squared, radius);
    return std::clamp(kappa_squared / (4.0 * speed_squared / (radius * radius)), 0.0, 1.0);
}

double compound_toomre_q(EquilibriumComponents const &model, double radius) {
    validate_model(model);
    if (!(radius > 0.0)) {
        return 0.0;
    }
    const Field field = build_field(model);
    const double surface = disk_surface_density(model, radius);
    const double sigma_r = model.dispersion_ratio * std::sqrt(disk_vertical_dispersion_squared(model, radius));
    const double kappa = std::sqrt(std::max(0.0, interpolate(field.radius, field.kappa_squared, radius)));
    return sigma_r * kappa / (3.36 * model.gravitational_constant * surface);
}

void split_collisionless(std::size_t total, std::vector<double> const &mass, std::vector<std::size_t> &count) {
    count.assign(mass.size(), 0);
    double mass_sum = 0.0;
    int positive = 0;
    std::size_t largest = 0;
    for (std::size_t index = 0; index < mass.size(); ++index) {
        if (mass[index] > 0.0) {
            mass_sum += mass[index];
            ++positive;
            if (mass[index] > mass[largest]) {
                largest = index;
            }
        }
    }
    if (positive == 0 || !(mass_sum > 0.0)) {
        throw std::runtime_error("a galaxy needs a positive halo or disk mass");
    }
    if (total < static_cast<std::size_t>(positive) * 2) {
        throw std::runtime_error("a galaxy needs at least two particles in each component that has mass");
    }
    std::size_t assigned = 0;
    for (std::size_t index = 0; index < mass.size(); ++index) {
        if (!(mass[index] > 0.0)) {
            continue;
        }
        count[index] = std::max<std::size_t>(2, static_cast<std::size_t>(std::lround(total * mass[index] / mass_sum)));
        assigned += count[index];
    }
    while (assigned > total) {
        std::size_t pick = mass.size();
        for (std::size_t index = 0; index < count.size(); ++index) {
            if (count[index] > 2 && (pick == mass.size() || count[index] > count[pick])) {
                pick = index;
            }
        }
        if (pick == mass.size()) {
            throw std::runtime_error("a galaxy could not split its particles across components");
        }
        --count[pick];
        --assigned;
    }
    count[largest] += total - assigned;
}

double guiding_radius(LiveField const &field, double angular_momentum) {
    if (field.guiding_bins < 2 || !(angular_momentum > 0.0)) {
        return 0.0;
    }
    const std::size_t last = field.guiding_bins - 1;
    if (angular_momentum < field.guiding_angular_momentum.front() ||
        angular_momentum > field.guiding_angular_momentum[last]) {
        return 0.0;
    }
    const auto begin = field.guiding_angular_momentum.begin();
    const auto upper =
        std::upper_bound(begin, begin + static_cast<std::ptrdiff_t>(field.guiding_bins), angular_momentum);
    const std::size_t index = static_cast<std::size_t>(std::max<std::ptrdiff_t>(1, upper - begin));
    const double left = field.guiding_angular_momentum[index - 1];
    const double right = field.guiding_angular_momentum[index];
    const double span = right - left;
    const double blend = span > 0.0 ? (angular_momentum - left) / span : 0.0;
    const double radius_left = field.cylindrical_radius[index - 1];
    const double radius_right = field.cylindrical_radius[index];
    return radius_left * (1.0 - blend) + radius_right * blend;
}

double radial_variance(LiveField const &field, EquilibriumComponents const &model, double radius) {
    if (model.target_q > 0) {
        const double kappa2 = std::max(1e-8, interpolate(field.cylindrical_radius, field.kappa_squared, radius));
        const double sigma = 3.36 * model.gravitational_constant * disk_surface_density(model, radius) * model.target_q;
        return sigma * sigma / kappa2;
    }
    return model.dispersion_ratio * model.dispersion_ratio *
           std::max(0.0, interpolate(field.cylindrical_radius, field.sigma_z_squared, radius));
}

// In-plane speeds are an independence-sampler draw from Shu's distribution function.
// Vertical speeds use either the legacy isothermal estimate or the optional Jeans table.
void sample_shu_disk(LiveField const &field, EquilibriumComponents const &model, Vec3 point, double escape,
                     std::mt19937_64 &rng, std::normal_distribution<double> &normal, double &v_radial,
                     double &v_azimuth, double &v_vertical) {
    const double cylindrical = std::hypot(point.x, point.y);
    const double sigma_z_squared =
        std::max(0.0, interpolate(field.cylindrical_radius, field.sigma_z_squared, cylindrical));
    const double sigma_r = model.target_q > 0 ? std::sqrt(radial_variance(field, model, cylindrical))
                                             : model.dispersion_ratio * std::sqrt(sigma_z_squared);
    const double ratio = live_epicycle_ratio(field, cylindrical);
    const double sigma_phi = std::max(1e-8, sigma_r * std::sqrt(std::max(ratio, 1e-8)));
    const double speed_squared =
        std::max(0.0, interpolate(field.cylindrical_radius, field.circular_speed_squared, cylindrical));
    const double sigma_up = model.target_q > 0 ? radial_variance(field, model, cylindrical * 1.05)
        : interpolate(field.cylindrical_radius, field.sigma_z_squared, cylindrical * 1.05);
    const double sigma_down = model.target_q > 0 ? radial_variance(field, model, cylindrical * 0.95)
        : interpolate(field.cylindrical_radius, field.sigma_z_squared, cylindrical * 0.95);
    const double dispersion_slope =
        sigma_up > 0.0 && sigma_down > 0.0 ? std::log(sigma_up / sigma_down) / std::log(1.05 / 0.95) : 0.0;
    const double streamed =
        speed_squared + sigma_r * sigma_r * (1.0 - ratio - cylindrical / model.disk_scale_radius + dispersion_slope);
    const double streaming = streamed > 0.0 ? std::sqrt(streamed) : 0.0;
    const double vertical_variance =
        field.vertical_variance.empty() ? sigma_z_squared : vertical_variance_at(field, cylindrical, point.z);
    v_vertical = std::sqrt(std::max(0.0, vertical_variance)) * normal(rng);

    auto log_proposal = [&](double radial, double azimuth) {
        const double radial_unit = radial / std::max(sigma_r, 1e-8);
        const double azimuth_unit = (azimuth - streaming) / sigma_phi;
        return -0.5 * (radial_unit * radial_unit + azimuth_unit * azimuth_unit);
    };
    auto log_target = [&](double radial, double azimuth) {
        if (!(cylindrical > 0.0) || !(azimuth > 0.0)) {
            return -1.0e300;
        }
        const double speed = std::sqrt(radial * radial + azimuth * azimuth + v_vertical * v_vertical);
        if (escape > 0.0 && speed > escape) {
            return -1.0e300;
        }
        const double radius = guiding_radius(field, cylindrical * azimuth);
        if (!(radius > 0.0)) {
            return -1.0e300;
        }
        const double circular =
            std::sqrt(std::max(0.0, interpolate(field.cylindrical_radius, field.circular_speed_squared, radius)));
        const double sigma_squared = model.target_q > 0 ? std::max(1e-12, radial_variance(field, model, radius))
            : model.dispersion_ratio * model.dispersion_ratio *
                std::max(1e-12, interpolate(field.cylindrical_radius, field.sigma_z_squared, radius));
        const double kappa_squared = interpolate(field.cylindrical_radius, field.kappa_squared, radius);
        const double kappa = std::sqrt(std::max(kappa_squared, 0.0));
        const double omega = radius > 0.0 ? circular / radius : 0.0;
        const double surface = disk_surface_density(model, radius);
        if (!(kappa > 0.0) || !(omega > 0.0) || !(surface > 0.0) || !(sigma_squared > 0.0)) {
            return -1.0e300;
        }
        const double circular_energy =
            interpolate(field.cylindrical_radius, field.midplane_potential, radius) + 0.5 * circular * circular;
        const double energy = interpolate(field.cylindrical_radius, field.midplane_potential, cylindrical) +
                              0.5 * (radial * radial + azimuth * azimuth);
        return std::log(omega) - std::log(kappa) - std::log(sigma_squared) + std::log(surface) +
               (circular_energy - energy) / sigma_squared;
    };

    v_radial = sigma_r * normal(rng);
    v_azimuth = streaming + sigma_phi * normal(rng);
    double log_f = log_target(v_radial, v_azimuth);
    double log_g = log_proposal(v_radial, v_azimuth);
    for (int draw = 0; draw < 64 && log_f < -1.0e200; ++draw) {
        v_radial = sigma_r * normal(rng);
        v_azimuth = std::max(1e-6, streaming + sigma_phi * normal(rng));
        log_f = log_target(v_radial, v_azimuth);
        log_g = log_proposal(v_radial, v_azimuth);
    }
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    for (int step = 0; step < 32; ++step) {
        const double trial_radial = sigma_r * normal(rng);
        const double trial_azimuth = streaming + sigma_phi * normal(rng);
        const double trial_f = log_target(trial_radial, trial_azimuth);
        const double trial_g = log_proposal(trial_radial, trial_azimuth);
        if (trial_f < -1.0e200) {
            continue;
        }
        const double accept = (trial_f - trial_g) - (log_f - log_g);
        if (std::log(std::max(unit(rng), 1e-16)) < accept) {
            v_radial = trial_radial;
            v_azimuth = trial_azimuth;
            log_f = trial_f;
            log_g = trial_g;
        }
    }
    const double speed = std::sqrt(v_radial * v_radial + v_azimuth * v_azimuth + v_vertical * v_vertical);
    if (escape > 0.0 && speed > escape) {
        const double scale = 0.99 * escape / speed;
        v_radial *= scale;
        v_azimuth *= scale;
        v_vertical *= scale;
    }
}

double spiral_azimuth(double uniform_angle, double radius, GalaxySpec const &spec, bool gas) {
    if (spec.spiral_strength == 0) return uniform_angle;
    const double r = radius / spec.disk_scale_radius;
    // Fade through the bulge and into the outer disk; no singular logarithm at the origin.
    const double taper = (1 - std::exp(-r * r / 0.36)) * std::exp(-std::pow(r / 7, 4));
    const double amplitude = std::min(0.85, spec.spiral_strength * (gas ? 1.3 : 1.0)) * taper;
    const double phase = spec.spiral_phase_degrees * kPi / 180 -
                         spec.spin * std::log(std::max(r, 0.05)) / std::tan(spec.spiral_pitch_degrees * kPi / 180);
    const double m = static_cast<double>(spec.spiral_arms);
    // Integral from zero ensures a uniform variate maps to the prescribed positive density.
    double low = 0, high = 2 * kPi;
    for (int iteration = 0; iteration < 48; ++iteration) {
        const double phi = 0.5 * (low + high);
        const double cdf = phi + amplitude / m * (std::sin(m * (phi - phase)) + std::sin(m * phase));
        if (cdf < uniform_angle) low = phi;
        else high = phi;
    }
    return 0.5 * (low + high);
}

std::vector<BodyDesc> realize_galaxy(GalaxySpec const &spec, double gravitational_constant, double softening_length) {
    if (!std::isfinite(spec.target_q) || spec.target_q < 0 || spec.target_q > 5 ||
        !std::isfinite(spec.disk_particle_fraction) || spec.disk_particle_fraction < 0 ||
        spec.disk_particle_fraction > 0.9 || !std::isfinite(spec.spiral_strength) ||
        spec.spiral_strength < 0 || spec.spiral_strength > 0.85 ||
        (spec.spiral_arms != 2 && spec.spiral_arms != 4) || !std::isfinite(spec.spiral_pitch_degrees) ||
        spec.spiral_pitch_degrees < 5 || spec.spiral_pitch_degrees > 60 || !std::isfinite(spec.spiral_phase_degrees))
        throw std::runtime_error("invalid disk allocation, target Q, or spiral parameters");
    if (!std::isfinite(spec.population_variation) || spec.population_variation < 0 || spec.population_variation > 1)
        throw std::runtime_error("population_variation must lie in [0,1]");
    if (!std::isfinite(spec.initial_young_fraction) || spec.initial_young_fraction < 0 || spec.initial_young_fraction > 1 ||
        !std::isfinite(spec.initial_young_age) || spec.initial_young_age < 0 ||
        !std::isfinite(spec.young_arm_contrast) || spec.young_arm_contrast < 0 || spec.young_arm_contrast > 1 ||
        !std::isfinite(spec.star_formation_temperature) || spec.star_formation_temperature < 1)
        throw std::runtime_error("invalid initial stellar population or star-formation temperature");
    if (spec.name.empty()) {
        throw std::runtime_error("a galaxy needs a name");
    }
    if (spec.particles < 4 || spec.particles > 1000000) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs between 4 and 1000000 particles");
    }
    if (!(softening_length > 0.0)) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs softening > 0 to measure its own field");
    }
    if (!(spec.display_radius > 0.0)) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs a positive display_radius");
    }
    if (spec.roughness < 0.0 || spec.roughness > 1.0 || spec.emission < 0.0 || spec.halo_emission < 0.0 ||
        spec.bulge_emission < 0.0 || spec.gas_emission < 0.0 || spec.young_star_emission < 0.0) {
        throw std::runtime_error("galaxy '" + spec.name + "' has a roughness or emission outside its range");
    }
    if (spec.bulge_mass < 0.0 || spec.gas_mass < 0.0 || spec.hot_gas_mass < 0.0) {
        throw std::runtime_error("galaxy '" + spec.name + "' has a negative bulge or gas mass");
    }
    if (spec.hot_gas_mass > 0.0 && !(spec.gas_mass > 0.0)) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs a cold gas disk before a hot halo");
    }
    if (spec.hot_emission < 0.0) {
        throw std::runtime_error("galaxy '" + spec.name + "' has a negative hot-gas emission");
    }

    EquilibriumComponents model = components_from(spec, gravitational_constant);
    validate_model(model);
    const double bulge_scale = spec.bulge_scale > 0.0 ? spec.bulge_scale : 0.2 * model.disk_scale_radius;
    const double bulge_cutoff = spec.bulge_cutoff > 0.0 ? spec.bulge_cutoff : 8.0 * bulge_scale;
    if (spec.bulge_mass > 0.0 && !(bulge_cutoff > bulge_scale)) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs bulge_cutoff larger than bulge_scale");
    }
    const double gas_scale = spec.gas_scale_radius > 0.0 ? spec.gas_scale_radius : model.disk_scale_radius;
    const double gas_height = spec.gas_height > 0.0 ? spec.gas_height : 0.25 * model.disk_height;
    if (spec.gas_mass > 0.0 &&
        (!(spec.sound_speed > 0.0) || spec.cooling_time < 0.0 || !(spec.star_formation_time > 0.0))) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs a positive sound speed and star-formation time");
    }
    if (spec.gas_mass > 0.0 && (spec.feedback_energy < 0.0 || spec.feedback_time < 0.0)) {
        throw std::runtime_error("galaxy '" + spec.name + "' needs non-negative feedback energy and time");
    }

    const double stellar_mass = model.halo_mass + spec.bulge_mass + model.disk_mass;
    std::size_t halo_count = 0;
    std::size_t bulge_count = 0;
    std::size_t disk_count = 0;
    if (!(spec.bulge_mass > 0.0)) {
        halo_count = static_cast<std::size_t>(spec.particles * model.halo_mass / stellar_mass + 0.5);
        halo_count = std::clamp(halo_count, std::size_t{2}, spec.particles - 2);
        disk_count = spec.particles - halo_count;
    } else {
        std::vector<std::size_t> counts;
        split_collisionless(spec.particles, {model.halo_mass, spec.bulge_mass, model.disk_mass}, counts);
        halo_count = counts[0];
        bulge_count = counts[1];
        disk_count = counts[2];
    }
    const double particle_mass = stellar_mass / static_cast<double>(spec.particles);
    if (spec.disk_particle_fraction > 0) {
        const std::size_t minimum_spheroid = spec.bulge_mass > 0 ? 4 : 2;
        if (spec.particles < minimum_spheroid + 2)
            throw std::runtime_error("disk allocation needs at least two particles per component");
        disk_count = std::clamp(static_cast<std::size_t>(std::lround(spec.particles * spec.disk_particle_fraction)),
                                std::size_t{2}, spec.particles - minimum_spheroid);
        const auto remaining = spec.particles - disk_count;
        bulge_count = spec.bulge_mass > 0
            ? std::clamp(static_cast<std::size_t>(std::lround(remaining * spec.bulge_mass /
                                                            (model.halo_mass + spec.bulge_mass))),
                         std::size_t{2}, remaining - 2) : 0;
        halo_count = remaining - bulge_count;
    } else {
        model.halo_mass = static_cast<double>(halo_count) * particle_mass;
        model.disk_mass = static_cast<double>(disk_count) * particle_mass;
    }
    const double bulge_mass = spec.disk_particle_fraction > 0 ? spec.bulge_mass : bulge_count * particle_mass;
    const double halo_particle_mass = spec.disk_particle_fraction > 0 ? model.halo_mass / halo_count : particle_mass;
    const double disk_particle_mass = spec.disk_particle_fraction > 0 ? model.disk_mass / disk_count : particle_mass;
    const double bulge_particle_mass = spec.disk_particle_fraction > 0
        ? (bulge_count > 0 ? bulge_mass / bulge_count : 0) : particle_mass;

    std::size_t gas_count = 0;
    if (spec.gas_mass > 0.0) {
        if (spec.gas_particles == 0) {
            gas_count = static_cast<std::size_t>(std::lround(spec.particles * spec.gas_mass / stellar_mass));
            gas_count = std::max<std::size_t>(gas_count, 64);
        } else {
            gas_count = spec.gas_particles;
        }
        if (gas_count < 48) {
            throw std::runtime_error("galaxy '" + spec.name +
                                     "' needs at least 48 gas particles for smoothed-particle hydrodynamics");
        }
    }
    const double gas_particle_mass = gas_count > 0 ? spec.gas_mass / static_cast<double>(gas_count) : 0.0;
    std::size_t hot_count = 0;
    if (spec.hot_gas_mass > 0.0) {
        hot_count = spec.hot_gas_particles == 0 ? std::size_t{64} : spec.hot_gas_particles;
        if (hot_count < 48) {
            throw std::runtime_error("galaxy '" + spec.name +
                                     "' needs at least 48 hot-gas particles for smoothed-particle hydrodynamics");
        }
    }
    const double hot_particle_mass = hot_count > 0 ? spec.hot_gas_mass / static_cast<double>(hot_count) : 0.0;
    const std::size_t total_count = spec.particles + gas_count + hot_count;

    const Vec3 halo_color = spec.halo_color;
    const Vec3 bulge_color = spec.bulge_color_set ? spec.bulge_color : spec.bulge_color;
    const Vec3 gas_color = spec.gas_color_set ? spec.gas_color : spec.gas_color;
    const double cosine = std::cos(spec.inclination_degrees * kPi / 180.0);
    const double sine = std::sin(spec.inclination_degrees * kPi / 180.0);
    if (spec.spin != 1 && spec.spin != -1)
        throw std::runtime_error("spin must be -1 or 1");
    const double node = spec.node_degrees * kPi / 180.0;
    auto orient = [&](Vec3 value) {
        value = rotate_x(value, cosine, sine);
        return Vec3{std::cos(node) * value.x - std::sin(node) * value.y,
                    std::sin(node) * value.x + std::cos(node) * value.y, value.z};
    };
    const double gas_energy = (spec.sound_speed * spec.sound_speed) / ((5.0 / 3.0) * (5.0 / 3.0 - 1.0));

    std::mt19937_64 rng(spec.seed);
    std::vector<Vec3> local_position;
    std::vector<double> masses;
    std::vector<char> is_disk(total_count, 0);
    local_position.reserve(total_count);
    masses.reserve(total_count);

    const double cutoff_fraction = hernquist_cdf(model.halo_cutoff, model.halo_scale);
    for (std::size_t index = 0; index < halo_count; ++index) {
        const double draw = unit_open(rng) * cutoff_fraction;
        const double root = std::sqrt(draw);
        const double radius = model.halo_scale * root / (1.0 - root);
        local_position.push_back(sample_direction(rng) * radius);
        masses.push_back(halo_particle_mass);
    }
    const double bulge_fraction = hernquist_cdf(bulge_cutoff, bulge_scale);
    for (std::size_t index = 0; index < bulge_count; ++index) {
        const double draw = unit_open(rng) * bulge_fraction;
        const double root = std::sqrt(draw);
        const double radius = bulge_scale * root / (1.0 - root);
        local_position.push_back(sample_direction(rng) * radius);
        masses.push_back(bulge_particle_mass);
    }
    for (std::size_t index = 0; index < disk_count; ++index) {
        double cylindrical = model.disk_scale_radius * (sample_exponential(rng) + sample_exponential(rng));
        for (int attempt = 0; attempt < 100 && cylindrical > 15.0 * model.disk_scale_radius; ++attempt) {
            cylindrical = model.disk_scale_radius * (sample_exponential(rng) + sample_exponential(rng));
        }
        const double phi = spiral_azimuth(2.0 * kPi * unit_open(rng), cylindrical, spec);
        const double height_draw = 2.0 * unit_open(rng) - 1.0;
        const double height = model.disk_height * std::atanh(std::clamp(height_draw, -1.0 + 1e-8, 1.0 - 1e-8));
        local_position.push_back(Vec3{cylindrical * std::cos(phi), cylindrical * std::sin(phi), height});
        masses.push_back(disk_particle_mass);
        is_disk[halo_count + bulge_count + index] = 1;
    }
    for (std::size_t index = 0; index < gas_count; ++index) {
        double cylindrical = gas_scale * (sample_exponential(rng) + sample_exponential(rng));
        for (int attempt = 0; attempt < 100 && cylindrical > 15.0 * gas_scale; ++attempt) {
            cylindrical = gas_scale * (sample_exponential(rng) + sample_exponential(rng));
        }
        const double phi = spiral_azimuth(2.0 * kPi * unit_open(rng), cylindrical, spec, true);
        const double height_draw = 2.0 * unit_open(rng) - 1.0;
        const double height = gas_height * std::atanh(std::clamp(height_draw, -1.0 + 1e-8, 1.0 - 1e-8));
        local_position.push_back(Vec3{cylindrical * std::cos(phi), cylindrical * std::sin(phi), height});
        masses.push_back(gas_particle_mass);
    }

    LiveField field = measure_live_field(local_position, is_disk, masses, model, bulge_mass, bulge_scale, bulge_cutoff,
                                         softening_length);
    double hot_energy = gas_energy;
    if (hot_count > 0) {
        // One e-folding of density from the center out to the halo scale, and never
        // as cold as the disk. Pressure holds the atmosphere up; the speeds stay small.
        const double phi_inner = field.spherical_potential.front();
        const double phi_scale = interpolate(field.spherical_radius, field.spherical_potential, model.halo_scale);
        const double drop = std::max(0.0, phi_scale - phi_inner);
        hot_energy = std::max(80.0 * gas_energy, drop / (5.0 / 3.0 - 1.0));
        const double support = (5.0 / 3.0 - 1.0) * hot_energy;
        std::vector<double> cumulative(field.spherical_radius.size(), 0.0);
        double sum = 0.0;
        for (std::size_t bin = 0; bin < field.spherical_radius.size(); ++bin) {
            const double radius = field.spherical_radius[bin];
            const double phi = field.spherical_potential[bin];
            const double weight = std::exp(-std::max(0.0, phi - phi_inner) / support);
            double width = 0.0;
            if (bin + 1 < field.spherical_radius.size()) {
                width = field.spherical_radius[bin + 1] - radius;
            } else if (bin > 0) {
                width = radius - field.spherical_radius[bin - 1];
            }
            sum += weight * radius * radius * std::max(width, 0.0);
            cumulative[bin] = sum;
        }
        if (!(sum > 0.0)) {
            throw std::runtime_error("galaxy '" + spec.name + "' could not place a hot halo");
        }
        for (std::size_t index = 0; index < hot_count; ++index) {
            const double draw = unit_open(rng) * sum;
            const auto bin = static_cast<std::size_t>(std::lower_bound(cumulative.begin(), cumulative.end(), draw) -
                                                      cumulative.begin());
            const std::size_t slot = std::min(bin, cumulative.size() - 1);
            local_position.push_back(sample_direction(rng) * field.spherical_radius[slot]);
            masses.push_back(hot_particle_mass);
        }
    }
    if (spec.jeans_vertical)
        measure_vertical_support(field, local_position, masses, model, softening_length);
    std::normal_distribution<double> normal(0.0, 1.0);
    std::vector<BodyDesc> bodies;
    bodies.reserve(total_count);

    auto place = [&](std::size_t index, Vec3 local_velocity, Vec3 color, double emission, Component component,
                     std::string const &kind, std::size_t kind_index) {
        BodyDesc body;
        body.name = spec.name + "." + kind + "." + std::to_string(kind_index);
        body.particle.mass = masses[index];
        body.particle.component = component;
        body.particle.position = spec.center + orient(local_position[index]);
        body.particle.velocity = spec.velocity + orient(local_velocity);
        body.display_radius = spec.display_radius;
        body.color = color;
        if (component == Component::Disk && spec.population_variation > 0) {
            // Persistent population labels: an outer, cooler mix and a warmer inner disk.
            // Hashing consumes no dynamics RNG; colors travel with stars into tidal tails.
            const auto hash = derive_galaxy_seed(spec.seed, kind_index);
            const double draw = static_cast<double>(hash & 65535) / 65535.0;
            const double radius =
                std::hypot(local_position[index].x, local_position[index].y) / model.disk_scale_radius;
            const double cool = std::clamp(0.10 + 0.22 * radius + 0.25 * (draw - 0.5), 0.0, 0.9);
            const Vec3 tint = Vec3{1.0, 0.76, 0.48} * (1 - cool) + Vec3{0.45, 0.65, 1.0} * cool;
            const double luminance = dot(color, {0.2126, 0.7152, 0.0722});
            const auto normalized_tint = tint * (luminance / dot(tint, {0.2126, 0.7152, 0.0722}));
            body.color = color * (1 - spec.population_variation) + normalized_tint * spec.population_variation;
        }
        if (component == Component::Disk && spec.initial_young_fraction > 0) {
            const auto p = local_position[index];
            const double r = std::hypot(p.x, p.y) / model.disk_scale_radius;
            const double phase = spec.spiral_phase_degrees * kPi / 180 -
                spec.spin * std::log(std::max(r, 0.05)) / std::tan(spec.spiral_pitch_degrees * kPi / 180);
            const double crest = spec.spiral_strength > 0 ?
                std::pow(0.5 + 0.5 * std::cos(spec.spiral_arms * (std::atan2(p.y, p.x) - phase)), 2) : 1;
            body.initial_young_fraction = spec.initial_young_fraction *
                (1 - spec.young_arm_contrast + spec.young_arm_contrast * crest);
            const auto hash = derive_galaxy_seed(spec.seed ^ 0xA56B91ull, kind_index);
            body.initial_young_age = spec.initial_young_age * static_cast<double>(hash >> 11) / 9007199254740992.0;
            body.formed_star_color = spec.young_star_color;
            body.formed_star_emission = spec.young_star_emission;
        }
        body.roughness = spec.roughness;
        body.emission = emission;
        if (component == Component::Gas) {
            body.particle.internal_energy = gas_energy;
            body.formed_star_color = spec.young_star_color;
            body.formed_star_emission = spec.young_star_emission;
        }
        bodies.push_back(std::move(body));
    };

    auto escape_speed = [&](Vec3 point) {
        const double potential = interpolate(field.spherical_radius, field.spherical_potential, norm(point));
        return std::sqrt(std::max(0.0, -2.0 * potential));
    };
    auto isotropic = [&](std::size_t index, std::vector<double> const &dispersion, Vec3 color, double emission,
                         Component component, std::string const &kind) {
        const double radius = norm(local_position[index]);
        const double sigma = std::sqrt(std::max(0.0, interpolate(field.spherical_radius, dispersion, radius)));
        const double escape = escape_speed(local_position[index]);
        Vec3 velocity{sigma * normal(rng), sigma * normal(rng), sigma * normal(rng)};
        for (int attempt = 0; attempt < 32 && norm(velocity) > escape; ++attempt) {
            velocity = {sigma * normal(rng), sigma * normal(rng), sigma * normal(rng)};
        }
        if (escape > 0.0 && norm(velocity) > escape) {
            velocity = normalized(velocity) * (0.99 * escape);
        }
        const std::size_t kind_index = component == Component::Bulge ? index - halo_count : index;
        place(index, velocity, color, emission, component, kind, kind_index);
    };

    for (std::size_t index = 0; index < halo_count; ++index) {
        isotropic(index, field.halo_dispersion_squared, halo_color, spec.halo_emission, Component::Halo, "halo");
    }
    for (std::size_t index = halo_count; index < halo_count + bulge_count; ++index) {
        isotropic(index, field.bulge_dispersion_squared, bulge_color, spec.bulge_emission, Component::Bulge, "bulge");
    }
    for (std::size_t disk_index = 0; disk_index < disk_count; ++disk_index) {
        const std::size_t index = halo_count + bulge_count + disk_index;
        const Vec3 point = local_position[index];
        const double phi = std::atan2(point.y, point.x);
        const double escape = escape_speed(point);
        double v_radial = 0.0;
        double v_azimuth = 0.0;
        double v_vertical = 0.0;
        sample_shu_disk(field, model, point, escape, rng, normal, v_radial, v_azimuth, v_vertical);
        v_azimuth *= spec.spin;
        const Vec3 planar{v_radial * std::cos(phi) - v_azimuth * std::sin(phi),
                          v_radial * std::sin(phi) + v_azimuth * std::cos(phi), v_vertical};
        place(index, planar, spec.color, spec.emission, Component::Disk, "disk", disk_index);
    }
    for (std::size_t gas_index = 0; gas_index < gas_count; ++gas_index) {
        const std::size_t index = spec.particles + gas_index;
        const Vec3 point = local_position[index];
        const double cylindrical = std::hypot(point.x, point.y);
        const double phi = std::atan2(point.y, point.x);
        const double circular =
            spec.spin *
            std::sqrt(std::max(0.0, interpolate(field.cylindrical_radius, field.circular_speed_squared, cylindrical)));
        const double radial = 0.3 * spec.sound_speed * normal(rng);
        const double vertical = 0.3 * spec.sound_speed * normal(rng);
        const Vec3 planar{radial * std::cos(phi) - circular * std::sin(phi),
                          radial * std::sin(phi) + circular * std::cos(phi), vertical};
        place(index, planar, gas_color, spec.gas_emission, Component::Gas, "gas", gas_index);
    }
    if (hot_count > 0) {
        const double thermal = std::sqrt((5.0 / 3.0) * (5.0 / 3.0 - 1.0) * hot_energy);
        for (std::size_t hot_index = 0; hot_index < hot_count; ++hot_index) {
            const std::size_t index = spec.particles + gas_count + hot_index;
            const Vec3 point = local_position[index];
            const double escape = escape_speed(point);
            Vec3 velocity{0.2 * thermal * normal(rng), 0.2 * thermal * normal(rng), 0.2 * thermal * normal(rng)};
            if (escape > 0.0 && norm(velocity) > escape) {
                velocity = normalized(velocity) * (0.99 * escape);
            }
            BodyDesc body;
            body.name = spec.name + ".hot." + std::to_string(hot_index);
            body.particle.mass = masses[index];
            body.particle.component = Component::HotGas;
            body.particle.position = spec.center + orient(point);
            body.particle.velocity = spec.velocity + orient(velocity);
            body.particle.internal_energy = hot_energy;
            body.display_radius = spec.display_radius;
            body.color = spec.hot_color;
            body.roughness = spec.roughness;
            body.emission = spec.hot_emission;
            bodies.push_back(std::move(body));
        }
    }

    return bodies;
}

} // namespace physics
