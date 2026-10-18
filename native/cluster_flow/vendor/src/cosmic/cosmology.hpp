#pragma once
#include "cosmic/types.hpp"
#include <cstdint>
#include <vector>
namespace cosmic {
struct Cosmology {
    bool enabled=false;
    float hubble=67.74f,omegaDM=.2603f,omegaB=.0486f,omegaDE=.6911f,w=-1.f;
    float boxMpc=50.f,startRedshift=9.f,sigma8=.8159f,spectralIndex=.9667f;
    int mesh=32;
    void validate() const;
    double hubbleGyr(double a) const;
    double curvature() const { return 1.-omegaDM-omegaB-omegaDE; }
};
struct DarkParticle { Vector position,velocity; };
// Periodic particle-mesh gravity. Density is comoving rho / present critical density;
// velocities are physical peculiar velocities in (boxMpc/2) Mpc per Gyr.
class Universe {
public:
    Cosmology settings;
    double a=1;
    std::vector<DarkParticle> particles;
    std::vector<float> density;
    std::vector<Vector> force;
    explicit Universe(Cosmology settings={});
    void initialize(int gasGrid,std::uint32_t seed,std::vector<Cell>& gas,float temperatureMultiplier);
    void gravity(std::vector<Cell> const& gas,int gasGrid);
    Vector acceleration(Vector p) const;
    float signalSpeed() const;
    void advance(float dt);
    float physicalVelocityUnit() const; // km/s per code velocity
private:
    int index(int x,int y,int z) const;
};
}
