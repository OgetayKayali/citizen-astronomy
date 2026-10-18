#pragma once

#include <cstdint>

namespace physics {

// Collisionless components stay on gravity alone. Gas is the cold disk: it feels
// pressure, cooling, and can become a young star. HotGas is the halo atmosphere.
// It feels pressure and cooling, and it does not form stars. The numeric values
// are stored in recordings.
enum class Component : std::uint8_t {
    Body = 0,
    Halo = 1,
    Bulge = 2,
    Disk = 3,
    Gas = 4,
    YoungStar = 5,
    HotGas = 6,
};

}  // namespace physics
