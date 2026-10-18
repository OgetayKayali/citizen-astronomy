#pragma once
#include <array>
namespace cosmic {
struct Vector {
    float x{},y{},z{};
    Vector operator+(Vector b) const { return {x+b.x,y+b.y,z+b.z}; }
    Vector operator-(Vector b) const { return {x-b.x,y-b.y,z-b.z}; }
    Vector operator*(float s) const { return {x*s,y*s,z*s}; }
};
float length(Vector v);
Vector normalized(Vector v);
using Cell=std::array<float,5>;
}
