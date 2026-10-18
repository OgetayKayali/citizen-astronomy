#pragma once

#include <cmath>

namespace physics {

struct Vec3 {
    double x = 0;
    double y = 0;
    double z = 0;

    constexpr Vec3() = default;
    constexpr Vec3(double x_, double y_, double z_) : x(x_), y(y_), z(z_) {}

    constexpr Vec3 operator+(Vec3 other) const { return {x + other.x, y + other.y, z + other.z}; }
    constexpr Vec3 operator-(Vec3 other) const { return {x - other.x, y - other.y, z - other.z}; }
    constexpr Vec3 operator*(double scale) const { return {x * scale, y * scale, z * scale}; }
    constexpr Vec3 operator/(double scale) const { return {x / scale, y / scale, z / scale}; }
    constexpr Vec3 operator-() const { return {-x, -y, -z}; }

    constexpr Vec3& operator+=(Vec3 other) {
        x += other.x;
        y += other.y;
        z += other.z;
        return *this;
    }
};

constexpr Vec3 operator*(double scale, Vec3 value) { return value * scale; }

constexpr double dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }

constexpr Vec3 cross(Vec3 a, Vec3 b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}

constexpr bool operator==(Vec3 a, Vec3 b) { return a.x == b.x && a.y == b.y && a.z == b.z; }

inline double norm(Vec3 value) { return std::sqrt(dot(value, value)); }

inline Vec3 normalized(Vec3 value) {
    const double length = norm(value);
    return length == 0.0 ? Vec3{} : value / length;
}

}  // namespace physics
