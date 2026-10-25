#pragma once
#include <array>
#include <string>
#include <vector>

namespace photometry {
constexpr double pi = 3.14159265358979323846;
struct Vec { double x=0, y=0, z=0; };
struct Star { double mass=1, radius=1, temperature=5772, limb=0.6; };
enum class Band { Bolometric, Blue, Visual, Red };
struct Binary {
    std::array<Star,2> stars{{{2.5,2.1,10000,.4},{1,1.3,4800,.65}}};
    double separation=12, eccentricity=0, inclination=88, omega=90, node=0;
    double distance=100, thirdLight=0, exposure=0;
    Band band=Band::Visual;
    double periodDays() const;
    void setPeriodDays(double days);
    std::string invalidReason() const;
    double rocheFraction(int star) const;
};
struct Geometry { std::array<Vec,2> position; double separation=0, projected=0; int front=0; };
struct Measurement {
    double flux=1, magnitude=0, irradiance=0;
    std::array<double,2> visible{{1,1}};
};
struct Preset { const char* name; const char* description; Binary system; };
struct VariableType { const char* name; const char* description; bool available; };
const std::vector<Preset>& presets();
const std::vector<VariableType>& variableTypes();
Geometry geometry(const Binary&, double phase);
double surfaceFlux(const Star&, Band);
double blockedFraction(double radius, double occultor, double distance, double limb, int rings=512);
Measurement instantaneous(const Binary&, double phase, int rings=512);
Measurement observe(const Binary&, double phase, int rings=512);
void saveSystem(const Binary&, const std::string& path);
Binary loadSystem(const std::string& path);
void exportCurve(const Binary&, const std::string& path, int samples=1001);
}
