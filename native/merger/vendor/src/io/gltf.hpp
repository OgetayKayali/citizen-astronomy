#pragma once

#include "simulation/scene.hpp"

#include <string>
#include <vector>

namespace physics {

struct GltfExport {
    std::string json;
    std::size_t body_count = 0;
    std::size_t animated_bodies = 0;
    // Per body, glTF translations (x, z, -y) packed xyz.
    std::vector<std::vector<float>> translations;
};

[[nodiscard]] GltfExport build_gltf(Trajectory const& trajectory);
void write_gltf(Trajectory const& trajectory, std::string const& path);

}  // namespace physics
