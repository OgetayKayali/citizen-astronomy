#include "forces/hydro.hpp"
#include "simulation/galaxy.hpp"
#include "simulation/scene.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace physics {
namespace {

std::string trim(std::string const& text) {
    std::size_t begin = 0;
    while (begin < text.size() && std::isspace(static_cast<unsigned char>(text[begin]))) {
        ++begin;
    }
    std::size_t end = text.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1]))) {
        --end;
    }
    return text.substr(begin, end - begin);
}

std::vector<std::string> tokenize(std::string const& text) {
    std::istringstream input(text);
    std::vector<std::string> tokens;
    std::string word;
    while (input >> word) {
        tokens.push_back(word);
    }
    return tokens;
}

double parse_number(std::string const& token, int line) {
    try {
        std::size_t used = 0;
        const double value = std::stod(token, &used);
        if (used != token.size() || !std::isfinite(value)) {
            throw std::runtime_error("bad");
        }
        return value;
    } catch (std::exception const&) {
        throw std::runtime_error("line " + std::to_string(line) + ": expected a finite number, got '" + token + "'");
    }
}

std::uint64_t parse_seed(std::string const& token, int line) {
    try {
        std::size_t used = 0;
        const unsigned long long value = std::stoull(token, &used, 10);
        if (used != token.size()) {
            throw std::runtime_error("bad");
        }
        return static_cast<std::uint64_t>(value);
    } catch (std::exception const&) {
        throw std::runtime_error("line " + std::to_string(line) + ": expected a non-negative integer seed, got '" +
                                 token + "'");
    }
}

std::size_t parse_count(std::string const& token, int line, std::string const& name) {
    const double value = parse_number(token, line);
    if (value < 1.0 || std::floor(value) != value) {
        throw std::runtime_error("line " + std::to_string(line) + ": " + name + " must be a positive integer");
    }
    return static_cast<std::size_t>(value);
}

void require_count(std::vector<std::string> const& tokens, std::size_t count, int line) {
    if (tokens.size() != count) {
        throw std::runtime_error("line " + std::to_string(line) + ": expected " + std::to_string(count - 1) +
                                 " values");
    }
}

enum class BlockKind { None, Body, Galaxy, Gravity };

Vec3 palette(std::size_t index) {
    static constexpr Vec3 colors[] = {
        {0.93, 0.55, 0.28}, {0.36, 0.62, 0.95}, {0.45, 0.78, 0.48}, {0.86, 0.76, 0.32}, {0.72, 0.48, 0.86},
    };
    return colors[index % 5];
}

}  // namespace

State Scene::make_state() const {
    State state;
    for (BodyDesc const& body : bodies) {
        state.add(body.particle);
    }
    return state;
}

Scene load_scene(std::string const& text) {
    Scene scene;
    bool dt_set = false;
    bool steps_set = false;
    bool integrator_set = false;
    bool gravity_requested = false;
    bool gravitational_constant_set = false;
    bool softening_set = false;
    std::uint64_t scene_seed = 1;

    BlockKind block = BlockKind::None;
    std::vector<GalaxySpec> galaxies;

    std::istringstream input(text);
    std::string line_text;
    int line_number = 0;
    while (std::getline(input, line_text)) {
        ++line_number;
        const auto comment = line_text.find('#');
        if (comment != std::string::npos) {
            line_text = line_text.substr(0, comment);
        }
        line_text = trim(line_text);
        if (line_text.empty()) {
            continue;
        }
        const std::vector<std::string> tokens = tokenize(line_text);
        const std::string& key = tokens[0];

        auto in_body = [&]() -> BodyDesc& {
            if (block != BlockKind::Body || scene.bodies.empty()) {
                throw std::runtime_error("line " + std::to_string(line_number) + ": '" + key +
                                         "' belongs inside a body block");
            }
            return scene.bodies.back();
        };
        auto in_galaxy = [&]() -> GalaxySpec& {
            if (block != BlockKind::Galaxy || galaxies.empty()) {
                throw std::runtime_error("line " + std::to_string(line_number) + ": '" + key +
                                         "' belongs inside a galaxy block");
            }
            return galaxies.back();
        };

        if (key == "integrator") {
            require_count(tokens, 2, line_number);
            block = BlockKind::None;
            if (tokens[1] == "velocity_verlet") {
                scene.config.integrator = IntegratorKind::VelocityVerlet;
            } else {
                throw std::runtime_error("line " + std::to_string(line_number) + ": unknown integrator '" +
                                         tokens[1] + "'");
            }
            integrator_set = true;
        } else if (key == "dt") {
            require_count(tokens, 2, line_number);
            block = BlockKind::None;
            scene.config.dt = parse_number(tokens[1], line_number);
            dt_set = true;
        } else if (key == "steps") {
            require_count(tokens, 2, line_number);
            block = BlockKind::None;
            const double value = parse_number(tokens[1], line_number);
            if (value < 1.0 || std::floor(value) != value) {
                throw std::runtime_error("line " + std::to_string(line_number) + ": steps must be a positive integer");
            }
            scene.config.steps = static_cast<std::size_t>(value);
            steps_set = true;
        } else if (key == "sample_every") {
            require_count(tokens, 2, line_number);
            block = BlockKind::None;
            const double value = parse_number(tokens[1], line_number);
            if (value < 1.0 || std::floor(value) != value) {
                throw std::runtime_error("line " + std::to_string(line_number) +
                                         ": sample_every must be a positive integer");
            }
            scene.config.sample_every = static_cast<std::size_t>(value);
        } else if (key == "gravitational_constant") {
            require_count(tokens, 2, line_number);
            block = BlockKind::None;
            scene.config.forces.gravitational_constant = parse_number(tokens[1], line_number);
            gravitational_constant_set = true;
        } else if (key == "seed") {
            require_count(tokens, 2, line_number);
            const std::uint64_t value = parse_seed(tokens[1], line_number);
            if (block == BlockKind::Galaxy) {
                in_galaxy().seed = value;
            } else if (block == BlockKind::None) {
                scene_seed = value;
            } else {
                throw std::runtime_error("line " + std::to_string(line_number) +
                                         ": seed belongs at the top of the scene, or inside a galaxy");
            }
        } else if (key == "body") {
            require_count(tokens, 2, line_number);
            BodyDesc body;
            body.name = tokens[1];
            body.color = palette(scene.bodies.size());
            scene.bodies.push_back(std::move(body));
            block = BlockKind::Body;
        } else if (key == "galaxy") {
            require_count(tokens, 2, line_number);
            GalaxySpec galaxy;
            galaxy.name = tokens[1];
            galaxy.seed = derive_galaxy_seed(scene_seed, galaxies.size());
            galaxies.push_back(std::move(galaxy));
            block = BlockKind::Galaxy;
        } else if (key == "force") {
            require_count(tokens, 2, line_number);
            block = BlockKind::None;
            if (tokens[1] == "gravity") {
                if (gravity_requested) {
                    throw std::runtime_error("line " + std::to_string(line_number) + ": gravity is already enabled");
                }
                gravity_requested = true;
                block = BlockKind::Gravity;
            } else {
                throw std::runtime_error("line " + std::to_string(line_number) + ": unknown force '" + tokens[1] +
                                         "'");
            }
        } else if (key == "mass") {
            require_count(tokens, 2, line_number);
            const double value = parse_number(tokens[1], line_number);
            if (block == BlockKind::Galaxy) {
                throw std::runtime_error("line " + std::to_string(line_number) +
                                         ": a galaxy uses halo_mass and disk_mass");
            }
            in_body().particle.mass = value;
        } else if (key == "display_radius") {
            require_count(tokens, 2, line_number);
            const double value = parse_number(tokens[1], line_number);
            if (block == BlockKind::Galaxy) {
                in_galaxy().display_radius = value;
            } else {
                in_body().display_radius = value;
            }
        } else if (key == "color") {
            require_count(tokens, 4, line_number);
            const Vec3 value{parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                             parse_number(tokens[3], line_number)};
            if (block == BlockKind::Galaxy) {
                in_galaxy().color = value;
            } else {
                in_body().color = value;
            }
        } else if (key == "halo_color") {
            require_count(tokens, 4, line_number);
            GalaxySpec& galaxy = in_galaxy();
            galaxy.halo_color = {parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                                 parse_number(tokens[3], line_number)};
            galaxy.halo_color_set = true;
        } else if (key == "position" || key == "center") {
            require_count(tokens, 4, line_number);
            const Vec3 value{parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                             parse_number(tokens[3], line_number)};
            if (block == BlockKind::Galaxy) {
                in_galaxy().center = value;
            } else if (key == "position") {
                in_body().particle.position = value;
            } else {
                throw std::runtime_error("line " + std::to_string(line_number) + ": center belongs in a galaxy block");
            }
        } else if (key == "velocity") {
            require_count(tokens, 4, line_number);
            const Vec3 value{parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                             parse_number(tokens[3], line_number)};
            if (block == BlockKind::Galaxy) {
                in_galaxy().velocity = value;
            } else {
                in_body().particle.velocity = value;
            }
        } else if (key == "roughness") {
            require_count(tokens, 2, line_number);
            const double value = parse_number(tokens[1], line_number);
            if (block == BlockKind::Galaxy) {
                in_galaxy().roughness = value;
            } else {
                in_body().roughness = value;
            }
        } else if (key == "emission") {
            require_count(tokens, 2, line_number);
            const double value = parse_number(tokens[1], line_number);
            if (block == BlockKind::Galaxy) {
                in_galaxy().emission = value;
            } else {
                in_body().emission = value;
            }
        } else if (key == "halo_emission") {
            require_count(tokens, 2, line_number);
            in_galaxy().halo_emission = parse_number(tokens[1], line_number);
        } else if (key == "particles") {
            require_count(tokens, 2, line_number);
            in_galaxy().particles = parse_count(tokens[1], line_number, "particles");
        } else if (key == "halo_mass") {
            require_count(tokens, 2, line_number);
            in_galaxy().halo_mass = parse_number(tokens[1], line_number);
        } else if (key == "halo_scale") {
            require_count(tokens, 2, line_number);
            in_galaxy().halo_scale = parse_number(tokens[1], line_number);
        } else if (key == "halo_cutoff") {
            require_count(tokens, 2, line_number);
            in_galaxy().halo_cutoff = parse_number(tokens[1], line_number);
        } else if (key == "disk_mass") {
            require_count(tokens, 2, line_number);
            in_galaxy().disk_mass = parse_number(tokens[1], line_number);
        } else if (key == "dispersion_ratio") {
            require_count(tokens, 2, line_number);
            in_galaxy().dispersion_ratio = parse_number(tokens[1], line_number);
        } else if (key == "target_q" || key == "disk_particle_fraction" || key == "spiral_strength" ||
                   key == "spiral_pitch" || key == "spiral_phase" || key == "spiral_arms") {
            require_count(tokens, 2, line_number);
            const double value = parse_number(tokens[1], line_number);
            auto &galaxy = in_galaxy();
            if (key == "target_q") galaxy.target_q = value;
            else if (key == "disk_particle_fraction") galaxy.disk_particle_fraction = value;
            else if (key == "spiral_strength") galaxy.spiral_strength = value;
            else if (key == "spiral_pitch") galaxy.spiral_pitch_degrees = value;
            else if (key == "spiral_phase") galaxy.spiral_phase_degrees = value;
            else {
                if (value != 2 && value != 4) throw std::runtime_error("spiral_arms must be 2 or 4");
                galaxy.spiral_arms = static_cast<int>(value);
            }
        } else if (key == "vertical_support") {
            require_count(tokens, 2, line_number);
            if (tokens[1] != "jeans" && tokens[1] != "legacy")
                throw std::runtime_error("vertical_support must be jeans or legacy");
            in_galaxy().jeans_vertical = tokens[1] == "jeans";
        } else if (key == "initial_young_fraction") {
            require_count(tokens, 2, line_number);
            in_galaxy().initial_young_fraction = parse_number(tokens[1], line_number);
        } else if (key == "initial_young_age") {
            require_count(tokens, 2, line_number);
            in_galaxy().initial_young_age = parse_number(tokens[1], line_number);
        } else if (key == "young_arm_contrast") {
            require_count(tokens, 2, line_number);
            in_galaxy().young_arm_contrast = parse_number(tokens[1], line_number);
        } else if (key == "star_formation_temperature") {
            require_count(tokens, 2, line_number);
            in_galaxy().star_formation_temperature = parse_number(tokens[1], line_number);
        } else if (key == "star_formation_mode") {
            require_count(tokens, 2, line_number);
            if (tokens[1] != "continuous" && tokens[1] != "stochastic")
                throw std::runtime_error("star_formation_mode expects continuous or stochastic");
            in_galaxy().continuous_star_formation = tokens[1] == "continuous";
        } else if (key == "star_formation_converging") {
            require_count(tokens, 2, line_number);
            if (tokens[1] != "true" && tokens[1] != "false")
                throw std::runtime_error("star_formation_converging expects true or false");
            in_galaxy().star_formation_converging = tokens[1] == "true";
        } else if (key == "population_variation") {
            require_count(tokens, 2, line_number);
            in_galaxy().population_variation = parse_number(tokens[1], line_number);
        } else if (key == "scale_radius" || key == "disk_fraction") {
            throw std::runtime_error("line " + std::to_string(line_number) +
                                     ": galaxy initial conditions now use halo_mass, halo_scale, and disk_mass");
        } else if (key == "disk_scale_radius") {
            require_count(tokens, 2, line_number);
            in_galaxy().disk_scale_radius = parse_number(tokens[1], line_number);
        } else if (key == "disk_height") {
            require_count(tokens, 2, line_number);
            in_galaxy().disk_height = parse_number(tokens[1], line_number);
        } else if (key == "inclination") {
            require_count(tokens, 2, line_number);
            in_galaxy().inclination_degrees = parse_number(tokens[1], line_number);
        } else if (key == "node") {
            require_count(tokens, 2, line_number);
            in_galaxy().node_degrees = parse_number(tokens[1], line_number);
        } else if (key == "spin") {
            require_count(tokens, 2, line_number);
            const double spin = parse_number(tokens[1], line_number);
            if (spin != -1.0 && spin != 1.0) {
                throw std::runtime_error("spin must be -1 or 1");
            }
            in_galaxy().spin = spin;
        } else if (key == "bulge_mass") {
            require_count(tokens, 2, line_number);
            in_galaxy().bulge_mass = parse_number(tokens[1], line_number);
        } else if (key == "bulge_scale") {
            require_count(tokens, 2, line_number);
            in_galaxy().bulge_scale = parse_number(tokens[1], line_number);
        } else if (key == "bulge_cutoff") {
            require_count(tokens, 2, line_number);
            in_galaxy().bulge_cutoff = parse_number(tokens[1], line_number);
        } else if (key == "bulge_emission") {
            require_count(tokens, 2, line_number);
            in_galaxy().bulge_emission = parse_number(tokens[1], line_number);
        } else if (key == "bulge_color") {
            require_count(tokens, 4, line_number);
            GalaxySpec& galaxy = in_galaxy();
            galaxy.bulge_color = {parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                                  parse_number(tokens[3], line_number)};
            galaxy.bulge_color_set = true;
        } else if (key == "gas_mass") {
            require_count(tokens, 2, line_number);
            in_galaxy().gas_mass = parse_number(tokens[1], line_number);
        } else if (key == "gas_particles") {
            require_count(tokens, 2, line_number);
            in_galaxy().gas_particles = parse_count(tokens[1], line_number, "gas_particles");
        } else if (key == "hot_gas_mass") {
            require_count(tokens, 2, line_number);
            in_galaxy().hot_gas_mass = parse_number(tokens[1], line_number);
        } else if (key == "hot_gas_particles") {
            require_count(tokens, 2, line_number);
            in_galaxy().hot_gas_particles = parse_count(tokens[1], line_number, "hot_gas_particles");
        } else if (key == "hot_emission") {
            require_count(tokens, 2, line_number);
            in_galaxy().hot_emission = parse_number(tokens[1], line_number);
        } else if (key == "hot_color") {
            require_count(tokens, 4, line_number);
            GalaxySpec& galaxy = in_galaxy();
            galaxy.hot_color = {parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                                parse_number(tokens[3], line_number)};
            galaxy.hot_color_set = true;
        } else if (key == "gas_scale_radius") {
            require_count(tokens, 2, line_number);
            in_galaxy().gas_scale_radius = parse_number(tokens[1], line_number);
        } else if (key == "gas_height") {
            require_count(tokens, 2, line_number);
            in_galaxy().gas_height = parse_number(tokens[1], line_number);
        } else if (key == "gas_emission") {
            require_count(tokens, 2, line_number);
            in_galaxy().gas_emission = parse_number(tokens[1], line_number);
        } else if (key == "gas_color") {
            require_count(tokens, 4, line_number);
            GalaxySpec& galaxy = in_galaxy();
            galaxy.gas_color = {parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                                parse_number(tokens[3], line_number)};
            galaxy.gas_color_set = true;
        } else if (key == "young_emission") {
            require_count(tokens, 2, line_number);
            in_galaxy().young_star_emission = parse_number(tokens[1], line_number);
        } else if (key == "young_color") {
            require_count(tokens, 4, line_number);
            GalaxySpec& galaxy = in_galaxy();
            galaxy.young_star_color = {parse_number(tokens[1], line_number), parse_number(tokens[2], line_number),
                                       parse_number(tokens[3], line_number)};
            galaxy.young_color_set = true;
        } else if (key == "sound_speed") {
            require_count(tokens, 2, line_number);
            in_galaxy().sound_speed = parse_number(tokens[1], line_number);
        } else if (key == "cooling_time") {
            require_count(tokens, 2, line_number);
            const double value = parse_number(tokens[1], line_number);
            in_galaxy().cooling_time = value;
        } else if (key == "star_formation_time") {
            require_count(tokens, 2, line_number);
            in_galaxy().star_formation_time = parse_number(tokens[1], line_number);
        } else if (key == "star_formation_density") {
            require_count(tokens, 2, line_number);
            in_galaxy().star_formation_density = parse_number(tokens[1], line_number);
        } else if (key == "feedback_energy") {
            require_count(tokens, 2, line_number);
            in_galaxy().feedback_energy = parse_number(tokens[1], line_number);
        } else if (key == "feedback_time") {
            require_count(tokens, 2, line_number);
            in_galaxy().feedback_time = parse_number(tokens[1], line_number);
        } else if (key == "softening") {
            require_count(tokens, 2, line_number);
            if (block != BlockKind::Gravity) {
                throw std::runtime_error("line " + std::to_string(line_number) + ": softening belongs in a gravity block");
            }
            if (softening_set) {
                throw std::runtime_error("line " + std::to_string(line_number) + ": softening is already set");
            }
            const double value = parse_number(tokens[1], line_number);
            if (value < 0.0) {
                throw std::runtime_error("line " + std::to_string(line_number) + ": softening must be non-negative");
            }
            scene.config.forces.softening_length = value;
            softening_set = true;
        } else {
            throw std::runtime_error("line " + std::to_string(line_number) + ": unknown key '" + key + "'");
        }
    }

    if (!integrator_set || !dt_set || !steps_set) {
        throw std::runtime_error("scene requires integrator, dt, and steps");
    }
    if (!(scene.config.dt > 0.0)) {
        throw std::runtime_error("dt must be positive");
    }
    scene.config.forces.gravity = gravity_requested;
    if (gravity_requested) {
        if (!gravitational_constant_set || !(scene.config.forces.gravitational_constant > 0.0)) {
            throw std::runtime_error("gravity requires a positive gravitational_constant");
        }
    }
    scene.authored_bodies = scene.bodies.size();
    scene.galaxies = std::move(galaxies);
    apply_galaxies(scene);

    return scene;
}

void apply_galaxies(Scene& scene) {
    if (scene.authored_bodies > scene.bodies.size()) {
        throw std::runtime_error("authored body count is larger than the scene");
    }
    scene.bodies.resize(scene.authored_bodies);
    if (!scene.galaxies.empty()) {
        if (!scene.config.forces.gravity) {
            throw std::runtime_error("a galaxy requires force gravity");
        }
        if (!(scene.config.forces.softening_length > 0.0)) {
            throw std::runtime_error("a galaxy requires softening > 0, because close pairs are expected");
        }
        bool saw_gas = false;
        GalaxySpec gas_law;
        for (GalaxySpec const& galaxy : scene.galaxies) {
            const std::vector<BodyDesc> realized =
                realize_galaxy(galaxy, scene.config.forces.gravitational_constant,
                               scene.config.forces.softening_length);
            scene.bodies.insert(scene.bodies.end(), realized.begin(), realized.end());
            if (!(galaxy.gas_mass > 0.0)) {
                continue;
            }
            if (saw_gas) {
                const bool same = galaxy.sound_speed == gas_law.sound_speed &&
                                  galaxy.cooling_time == gas_law.cooling_time &&
                                  galaxy.star_formation_time == gas_law.star_formation_time &&
                                  galaxy.star_formation_density == gas_law.star_formation_density &&
                                  galaxy.continuous_star_formation == gas_law.continuous_star_formation &&
                                  galaxy.star_formation_temperature == gas_law.star_formation_temperature &&
                                  galaxy.star_formation_converging == gas_law.star_formation_converging &&
                                  galaxy.feedback_energy == gas_law.feedback_energy &&
                                  galaxy.feedback_time == gas_law.feedback_time;
                if (!same) {
                    throw std::runtime_error(
                        "two galaxies share one cooling law; sound speed, star formation, and feedback have to match");
                }
            }
            gas_law = galaxy;
            saw_gas = true;
        }
        scene.config.forces.gas_reference_density = 0.0;
        scene.config.forces.gas_resolved_star_density = 0.0;
        scene.config.forces.gas_hot_kernel_limit = 0.0;
        if (saw_gas) {
            scene.config.forces.gas_sound_speed = gas_law.sound_speed;
            scene.config.forces.gas_cooling_time = gas_law.cooling_time;
            scene.config.forces.gas_star_time = gas_law.star_formation_time;
            scene.config.forces.gas_star_density = gas_law.star_formation_density;
            scene.config.forces.gas_continuous_stars = gas_law.continuous_star_formation;
            scene.config.forces.gas_star_temperature = gas_law.star_formation_temperature;
            scene.config.forces.gas_star_converging = gas_law.star_formation_converging;
            scene.config.forces.gas_feedback_energy = gas_law.feedback_energy;
            scene.config.forces.gas_feedback_time = gas_law.feedback_time;
            double hot_kernel = 0.0;
            for (GalaxySpec const& galaxy : scene.galaxies) {
                if (galaxy.hot_gas_mass > 0.0) {
                    hot_kernel = std::max(hot_kernel, galaxy.halo_scale);
                }
            }
            scene.config.forces.gas_hot_kernel_limit = hot_kernel;
            scene.config.forces.gas_seed = gas_law.seed;
        } else {
            scene.config.forces.gas_sound_speed = 0.0;
            scene.config.forces.gas_cooling_time = 0.0;
            scene.config.forces.gas_star_time = 0.0;
            scene.config.forces.gas_star_density = 0.0;
            scene.config.forces.gas_feedback_energy = 0.0;
            scene.config.forces.gas_feedback_time = 0.0;
        }
    }
    if (scene.bodies.empty()) {
        throw std::runtime_error("scene has no bodies");
    }
    for (BodyDesc const& body : scene.bodies) {
        if (!(body.particle.mass > 0.0)) {
            throw std::runtime_error("body '" + body.name + "' needs a positive mass");
        }
        if (!(body.display_radius > 0.0)) {
            throw std::runtime_error("body '" + body.name + "' needs a positive display_radius");
        }
        if (body.roughness < 0.0 || body.roughness > 1.0) {
            throw std::runtime_error("body '" + body.name + "' roughness must be between 0 and 1");
        }
        if (body.emission < 0.0) {
            throw std::runtime_error("body '" + body.name + "' emission must be non-negative");
        }
    }
}

Scene load_scene_file(std::string const& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("could not open scene '" + path + "'");
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return load_scene(buffer.str());
}

SimulationOutcome simulate_interruptible(State state, std::vector<BodyDesc> const& bodies, SimulationConfig const& config,
                                         std::size_t step_offset, std::atomic<bool> const* cancel,
                                         std::atomic<std::size_t>* step_counter) {
    if (state.size() != bodies.size()) {
        throw std::invalid_argument("body metadata does not match the state");
    }
    if (!(config.dt > 0.0) || config.steps == 0 || config.sample_every == 0) {
        throw std::invalid_argument("dt, steps, and sample_every must be positive");
    }
    if (step_offset > config.steps) {
        throw std::invalid_argument("step offset is past the end of the run");
    }

    SimulationOutcome outcome;
    outcome.trajectory.bodies.reserve(bodies.size());
    for (BodyDesc const& body : bodies) {
        BodyRecord record;
        record.name = body.name;
        record.mass = body.particle.mass;
        record.display_radius = body.display_radius;
        record.color = body.color;
        record.roughness = body.roughness;
        record.emission = body.emission;
        record.formed_star_color = body.formed_star_color;
        record.formed_star_emission = body.formed_star_emission;
        record.initial_young_fraction = body.initial_young_fraction;
        record.initial_young_age = body.initial_young_age;
        outcome.trajectory.bodies.push_back(std::move(record));
    }

    if (any_gas(state) && config.integrator != IntegratorKind::VelocityVerlet) {
        throw std::runtime_error("gas cooling and star formation use velocity Verlet");
    }
    SimulationConfig running = config;
    if (any_gas(state) && !(running.forces.gas_reference_density > 0.0)) {
        prepare_gas(state, running.forces);
    }

    Accelerations accelerations;
    if (running.integrator == IntegratorKind::VelocityVerlet) {
        compute_accelerations(state, accelerations, running.forces);
    }

    auto capture = [&](double time) {
        Sample sample;
        sample.time = time;
        sample.diagnostics = diagnose(state, running.forces);
        sample.birth_time = state.birth_time;
        sample.gas_density = state.density;
        sample.internal_energy = state.internal_energy;
        sample.gas_fraction = state.gas_fraction;
        sample.position.reserve(state.size());
        sample.velocity.reserve(state.size());
        sample.component.reserve(state.size());
        for (std::size_t index = 0; index < state.size(); ++index) {
            sample.position.push_back(state.position(index));
            sample.velocity.push_back(state.velocity(index));
            const std::uint8_t component =
                index < state.component.size() ? state.component[index] : static_cast<std::uint8_t>(Component::Body);
            sample.component.push_back(component);
        }
        outcome.trajectory.samples.push_back(std::move(sample));
    };

    if (step_counter != nullptr) {
        step_counter->store(step_offset, std::memory_order_relaxed);
    }
    if (step_offset == 0) {
        capture(0.0);
    }

    std::size_t step = step_offset;
    for (step = step_offset + 1; step <= config.steps; ++step) {
        if (cancel != nullptr && cancel->load(std::memory_order_relaxed)) {
            --step;
            break;
        }
        if (running.integrator == IntegratorKind::VelocityVerlet) {
            const int formed = form_stars(state, running.forces, running.dt, step);
            if (formed > 0) {
                compute_accelerations(state, accelerations, running.forces);
            }
        }
        velocity_verlet_step(state, accelerations, running.dt, running.forces);
        if (step_counter != nullptr) {
            step_counter->store(step, std::memory_order_relaxed);
        }
        if (step % config.sample_every == 0 || step == config.steps) {
            capture(static_cast<double>(step) * config.dt);
        }
    }
    if (step > config.steps) {
        step = config.steps;
    }
    outcome.steps_completed = step;
    outcome.stopped = step < config.steps;
    outcome.trajectory.stellar_cohorts = state.stellar_cohorts;
    outcome.state = std::move(state);
    return outcome;
}

Trajectory simulate(State state, std::vector<BodyDesc> const& bodies, SimulationConfig const& config) {
    return simulate_interruptible(std::move(state), bodies, config, 0, nullptr, nullptr).trajectory;
}

Trajectory run_scene(Scene const& scene) {

    return simulate(scene.make_state(), scene.bodies, scene.config);
}

}  // namespace physics
