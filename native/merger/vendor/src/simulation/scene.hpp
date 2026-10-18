#pragma once

#include "core/component.hpp"
#include "integrators/integrators.hpp"

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

namespace physics {

struct BodyDesc {
    std::string name;
    Particle particle;
    double display_radius = 0.05;
    Vec3 color{0.75, 0.78, 0.82};
    // 0 is mirror-smooth, 1 is fully rough. The viewer maps this onto the specular lobe.
    double roughness = 0.45;
    // 0 draws a lit sphere. Above 0 the viewer draws an emissive star sprite.
    double emission = 0;
    // Used when a gas particle becomes a star during the run.
    Vec3 formed_star_color{0.8, 0.92, 1.0};
    double formed_star_emission = 0;
    double initial_young_fraction = 0;
    double initial_young_age = 0;
};

// One galaxy's initial conditions. Zero halo_cutoff or disk_height selects a default.
struct GalaxySpec {
    std::string name;
    std::size_t particles = 0;
    double halo_mass = 0;
    double halo_scale = 0;
    double halo_cutoff = 0;
    double disk_mass = 0;
    double disk_scale_radius = 0;
    double disk_height = 0;
    double dispersion_ratio = 2;
    double target_q = 0; // 0 keeps dispersion_ratio; otherwise sets radial Shu scale independently of height.
    double disk_particle_fraction = 0; // 0 keeps equal masses; otherwise allocate this fraction to the disk.
    double spiral_strength = 0; // 0..0.85 sinusoidal surface-density perturbation, initial conditions only.
    int spiral_arms = 2;
    double spiral_pitch_degrees = 24;
    double spiral_phase_degrees = 0;
    bool jeans_vertical = false; // Opt-in height-dependent support in an azimuth-averaged softened field.
    double population_variation = 0; // 0..1: persistent optical color diversity; no effect on dynamics.
    double initial_young_fraction = 0; // Peak fraction of existing disk mass in recent stars.
    double initial_young_age = 10; // Uniform age range [0, age] in code time units.
    double young_arm_contrast = 0.8; // 0 uniform, 1 concentrates recent stars at initial arm crests.
    double inclination_degrees = 0;
    double node_degrees = 0;
    double spin = 1;  // +1 or -1, independent of disk orientation.
    Vec3 center{};
    Vec3 velocity{};
    // Optical picture of one stellar population. Disk light is cream, the bulge is
    // redder, and the halo is faint. These do not enter the force.
    Vec3 color{0.98, 0.91, 0.72};
    Vec3 halo_color{0.72, 0.66, 0.58};
    bool halo_color_set = false;
    double display_radius = 0.45;
    double roughness = 0.55;
    double emission = 1.7;
    double halo_emission = 0.10;
    // Zero bulge_mass or gas_mass leaves that component out. Zero scales select defaults.
    double bulge_mass = 0;
    double bulge_scale = 0;
    double bulge_cutoff = 0;
    Vec3 bulge_color{1.00, 0.62, 0.36};
    bool bulge_color_set = false;
    double bulge_emission = 2.6;
    double gas_mass = 0;
    // Zero gas_particles keeps the gas particle mass near the stellar mass, with at least 64.
    std::size_t gas_particles = 0;
    double gas_scale_radius = 0;
    double gas_height = 0;
    Vec3 gas_color{0.96, 0.42, 0.55};
    bool gas_color_set = false;
    double gas_emission = 0.40;
    Vec3 young_star_color{0.64, 0.80, 1.00};
    bool young_color_set = false;
    double young_star_emission = 6.5;
    // Code units. The floor sound speed is the cold phase. Cooling time is the
    // time at the initial median density of that cold disk; denser gas cools faster.
    // Gas much hotter than the floor cools more slowly, so a hot halo can stay hot.
    double sound_speed = 0.05;
    double cooling_time = 1.0;
    // Zero hot_gas_mass leaves the hot halo out. Zero hot_gas_particles selects 64.
    double hot_gas_mass = 0;
    std::size_t hot_gas_particles = 0;
    Vec3 hot_color{0.62, 0.74, 0.86};
    bool hot_color_set = false;
    double hot_emission = 0.22;
    double star_formation_time = 5.0;
    // Zero selects four times the initial median gas density.
    double star_formation_density = 0;
    bool continuous_star_formation = false; // Fractional, co-moving gas/star particles.
    double star_formation_temperature = 1.5; // Maximum internal energy / cold floor.
    bool star_formation_converging = true;
    // Energy per unit stellar mass returned to neighboring gas, and how long that
    // gas skips cooling. Zero energy disables feedback.
    double feedback_energy = 4.0;
    double feedback_time = 1.0;
    std::uint64_t seed = 1;
};

struct SimulationConfig {
    IntegratorKind integrator = IntegratorKind::VelocityVerlet;
    double dt = 0;
    std::size_t steps = 0;
    std::size_t sample_every = 1;
    ForceSet forces{};
};

struct Scene {
    SimulationConfig config{};
    // Bodies written by hand. Galaxy particles are appended after these.
    std::size_t authored_bodies = 0;
    std::vector<GalaxySpec> galaxies;
    std::vector<BodyDesc> bodies;

    [[nodiscard]] State make_state() const;
};

// Rebuild galaxy particles from `galaxies`, leaving authored bodies in place.
void apply_galaxies(Scene& scene);

struct Sample {
    double time = 0;
    Diagnostics diagnostics{};
    std::vector<Vec3> position;
    std::vector<Vec3> velocity;
    // Empty on recordings written before components were stored.
    std::vector<std::uint8_t> component;
    std::vector<double> birth_time;
    std::vector<double> gas_density;
    std::vector<double> internal_energy;
    std::vector<double> gas_fraction; // Empty for legacy files (gas is then entirely gaseous).
};

struct BodyRecord {
    std::string name;
    double mass = 0;  // 0 on legacy recordings: viewer estimates population weights.
    double light_weight = -1; // Negative uses physical mass. Display tracers have mass=0 and explicit light weight.
    double display_radius = 0.05;
    Vec3 color{0.75, 0.78, 0.82};
    double roughness = 0.45;
    double emission = 0;
    Vec3 formed_star_color{0.8, 0.92, 1.0};
    double formed_star_emission = 0;
    double initial_young_fraction = 0;
    double initial_young_age = 0;
};

struct Trajectory {
    double tracer_softening = 0, tracer_dt = 0, tracer_theta = 0;
    std::vector<BodyRecord> bodies;
    std::vector<Sample> samples;
    std::vector<StellarCohort> stellar_cohorts;
};

[[nodiscard]] Scene load_scene(std::string const& text);
[[nodiscard]] Scene load_scene_file(std::string const& path);
[[nodiscard]] Trajectory simulate(State state, std::vector<BodyDesc> const& bodies, SimulationConfig const& config);
[[nodiscard]] Trajectory run_scene(Scene const& scene);

// Result of a run that may have been stopped between steps. `state` is the
// phase space after `steps_completed` accepted steps. `stopped` means the
// caller asked to halt before `config.steps`.
struct SimulationOutcome {
    Trajectory trajectory;
    State state;
    std::size_t steps_completed = 0;
    bool stopped = false;
};

// Integrates through `config.steps`, beginning after `step_offset` completed
// steps. `state` must already be the state at that offset. `cancel` is read
// between steps. `step_counter` receives the last finished step.
[[nodiscard]] SimulationOutcome simulate_interruptible(State state, std::vector<BodyDesc> const& bodies,
                                                       SimulationConfig const& config, std::size_t step_offset,
                                                       std::atomic<bool> const* cancel,
                                                       std::atomic<std::size_t>* step_counter);

}  // namespace physics
