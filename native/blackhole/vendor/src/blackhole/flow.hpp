#pragma once
#include <array>
#include <vector>
#include <cstdint>
#include <span>

namespace blackhole {
struct FlowConfig {
    int radial=128, azimuthal=256;
    double inner=3.8290694188,outer=20,spin=.6;
    double alpha=.08,aspect=.075,stirring=.4,cooling=8;
    unsigned seed=7;
    bool closed=false,perturbed=true;
    bool operator==(FlowConfig const&) const = default;
};
struct Gas { double density,radial,angular,energy; };
// Conservative monotone periodic remap. Source and destination must not alias.
void advectRing(std::span<Gas const> source,std::span<Gas> destination,double shift);
struct Tracer { double radius,angle; };
struct FlowTotals {
    double mass=0,angular=0,initialMass=0,initialAngular=0;
    double boundaryMass=0,boundaryAngular=0,stirringAngular=0;
    double massError=0,angularError=0;
};
// Vertically integrated effective-potential hydrodynamics, not GRMHD.
class DiskFlow {
public:
    explicit DiskFlow(FlowConfig config);
    void advance(double duration);
    void tick(); // deterministic 0.25 tg tick, CFL-subcycled
    [[nodiscard]] FlowTotals totals() const;
    [[nodiscard]] double time() const { return time_; }
    [[nodiscard]] FlowConfig const& config() const { return c_; }
    [[nodiscard]] std::vector<Gas> const& cells() const { return gas_; }
    [[nodiscard]] std::vector<Tracer> const& tracers() const { return tracers_; }
    [[nodiscard]] std::array<double,4> sample(double radius,double angle) const;
    // GPU payload: density, radiative flux proxy, radial velocity, angular velocity.
    [[nodiscard]] std::vector<float> image() const;
    [[nodiscard]] std::vector<float> tracerImage() const;
private:
    FlowConfig c_;
    double dr_,dp_,time_=0,initialMass_=0,initialAngular_=0;
    double boundaryMass_=0,boundaryAngular_=0,stirringAngular_=0;
    std::vector<Gas> gas_,work_,next_,rf_,pf_;
    std::vector<double> radius_,omega_,thermal_,viscosity_;
    std::vector<std::array<double,12>> forcingBasis_;
    std::vector<Tracer> tracers_;
    void orbit(double dt);
    double stableStep() const;
    void hydro(double dt);
    Gas reservoir(int row,int azimuth) const;
};
}
