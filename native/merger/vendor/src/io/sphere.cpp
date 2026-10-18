#include "io/sphere.hpp"

#include <cmath>
#include <stdexcept>

namespace physics {

SphereMesh make_uv_sphere(int stacks, int slices, SphereUp up) {
    if (stacks < 2 || slices < 3) {
        throw std::invalid_argument("sphere needs at least 2 stacks and 3 slices");
    }
    SphereMesh mesh;
    const int rows = stacks + 1;
    const int columns = slices + 1;
    mesh.positions.reserve(static_cast<std::size_t>(rows * columns) * 3);
    mesh.normals.reserve(static_cast<std::size_t>(rows * columns) * 3);

    constexpr double pi = 3.14159265358979323846;
    for (int stack = 0; stack <= stacks; ++stack) {
        const double v = static_cast<double>(stack) / static_cast<double>(stacks);
        const double phi = pi * v;
        for (int slice = 0; slice <= slices; ++slice) {
            const double u = static_cast<double>(slice) / static_cast<double>(slices);
            const double theta = 2.0 * pi * u;
            const double sin_phi = std::sin(phi);
            const double x = sin_phi * std::cos(theta);
            const double y = sin_phi * std::sin(theta);
            const double z = std::cos(phi);
            float px = static_cast<float>(x);
            float py = static_cast<float>(y);
            float pz = static_cast<float>(z);
            if (up == SphereUp::Y) {
                // Rotate the Z-up sphere so +Z becomes glTF +Y.
                py = static_cast<float>(z);
                pz = static_cast<float>(-y);
            }
            mesh.positions.insert(mesh.positions.end(), {px, py, pz});
            mesh.normals.insert(mesh.normals.end(), {px, py, pz});
        }
    }

    for (int stack = 0; stack < stacks; ++stack) {
        for (int slice = 0; slice < slices; ++slice) {
            const auto row = static_cast<std::uint32_t>(columns);
            const auto i0 = static_cast<std::uint32_t>(stack) * row + static_cast<std::uint32_t>(slice);
            const auto i1 = i0 + row;
            mesh.indices.insert(mesh.indices.end(), {i0, i1, i0 + 1, i0 + 1, i1, i1 + 1});
        }
    }
    return mesh;
}

}  // namespace physics
