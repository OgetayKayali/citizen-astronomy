#pragma once
#include "simulation/scene.hpp"
#include <limits>

namespace physics {
// Extract actual realized particles, rather than drawing a different random galaxy.
// Preserve the parent's gas reference density and star-formation threshold.
Scene isolated_control(Scene const &source, std::size_t galaxy);
struct DiskProfile {
    double inner = 0, outer = 0, surface_density = 0, height50 = 0;
    double rotation = 0, sigma_r = 0, sigma_z = 0;
    double a2 = 0, a4 = 0, phase2 = 0, phase4 = 0, noise = 0;
    double circular_speed = std::numeric_limits<double>::quiet_NaN();
    double kappa = std::numeric_limits<double>::quiet_NaN();
    double q = std::numeric_limits<double>::quiet_NaN();
    std::size_t count = 0;
};
// Original stellar disk, centered and aligned with its instantaneous angular momentum.
// Empty annuli have count=0; their other values must not be interpreted as measurements.
std::vector<DiskProfile> disk_profiles(Sample const &sample, std::vector<BodyDesc> const &bodies,
                                       std::vector<double> const &edges, ForceSet const *forces = nullptr);
} // namespace physics
