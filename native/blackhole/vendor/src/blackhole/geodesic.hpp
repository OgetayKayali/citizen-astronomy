#pragma once

#include <cmath>

namespace blackhole {
// Geometrized units: G = c = M = 1, so r_g = 1 and the horizon is r = 2.
inline constexpr double pi = 3.14159265358979323846;
inline constexpr double criticalImpact = 5.19615242270663188;
struct State { double u, slope, delay; }; // u = 1/r, slope = du/dphi
struct Vec3 { double x, y, z; };
enum class Outcome { captured, escaped, disk, unresolved };
struct Result {
    Outcome outcome = Outcome::unresolved;
    State state{};
    double angle = 0;
    double invariantError = 0;
};

double impactParameter(double radius, double inwardAngle);
State initialState(double radius, double inwardAngle);
double invariant(State state); // slope^2 + u^2 - 2u^3 = 1/b^2
State advance(State state, double impact, double step);
Result trace(double radius, double inwardAngle, double step = .01, int maxSteps = 10000);
Result traceDisk(double radius, double inwardAngle, double outwardY, double tangentY,
    double outerRadius, double step = .005);
double shadowAngle(double radius);
// Frequency ratio for circular equatorial emission and a static camera.
// backwardLz is the conserved axial angular momentum / energy of the backward ray.
double frequencyRatio(double emissionRadius, double cameraRadius, double backwardLz);
double gravitationalSeconds(double solarMasses);
Vec3 cameraOutward(double yaw, double inclination);
}
