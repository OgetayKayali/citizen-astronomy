#pragma once
#include "simulation/scene.hpp"
#include <functional>

namespace physics::visual {
struct TracerOptions {
    std::size_t per_star = 4;
    double dt = 0.02;
    double softening = 0.08;
    double theta = 0.4; // Zero evaluates the exact softened sum.
};
// Offline, independent massless orbits in the interpolated recorded potential.
// Source positions, velocities, masses and diagnostics are never changed.
Trajectory refine_starlight(Trajectory const &source, double gravitational_constant, TracerOptions const &options,
                            std::function<void(std::size_t, std::size_t)> progress = {});
} // namespace physics::visual
