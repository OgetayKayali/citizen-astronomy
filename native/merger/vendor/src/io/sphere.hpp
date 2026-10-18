#pragma once

#include "core/vec3.hpp"

#include <cstdint>
#include <vector>

namespace physics {

enum class SphereUp { Z, Y };

struct SphereMesh {
    std::vector<float> positions;
    std::vector<float> normals;
    std::vector<std::uint32_t> indices;
};

[[nodiscard]] SphereMesh make_uv_sphere(int stacks, int slices, SphereUp up);

}  // namespace physics
