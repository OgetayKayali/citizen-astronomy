#pragma once

#include "simulation/scene.hpp"

#include <string>

namespace physics {

void write_trajectory(Trajectory const& trajectory, std::string const& path);
[[nodiscard]] Trajectory read_trajectory(std::string const& path);

}  // namespace physics
