#pragma once

#include <array>
#include <cstdint>
#include <vector>
#include "cosmic/cosmology.hpp"

namespace cosmic {

// Conserved density, three momenta, total gas energy. Dimensionless code units.
struct Node { Vector position; float mass; };
struct Tracer { Vector position; float density{},speed{},temperature{}; Vector velocity; };
struct Statistics { double mass{}, energy{}; float maxSpeed{}, minDensity{}, minPressure{}; bool finite{true}; };
struct InitialConditions {
    float gasDensity=1.f,temperature=1.f,inflow=1.f,turbulence=1.f,filamentWidth=1.f,haloSoftening=.065f;
    float clusterBox=2.f;
    Cosmology cosmology;
};

class Flow {
public:
    explicit Flow(int resolution=64, std::uint32_t seed=7, InitialConditions initial={});
    void reset(std::uint32_t seed);
    // CFL-limited finite-volume Euler step; returns elapsed simulation time.
    float step(float maximumDt=0.025f);
    void burst();
    [[nodiscard]] Statistics statistics() const;
    // RGBA = density, speed, temperature proxy, compression (for the renderer).
    [[nodiscard]] std::vector<float> volume() const;
    [[nodiscard]] int resolution() const { return n_; }
    [[nodiscard]] float boxSize() const { return initial_.cosmology.enabled?2.f:initial_.clusterBox; }
    [[nodiscard]] static int clusterClumpCount(float side) { return static_cast<int>(38.f*(side*.5f)*(side*.5f)*(side*.5f)+.5f); }
    [[nodiscard]] double time() const { return time_; }
    [[nodiscard]] double scaleFactor() const { return universe_.a; }
    [[nodiscard]] Cosmology const& cosmology() const { return initial_.cosmology; }
    [[nodiscard]] std::vector<DarkParticle> const& darkMatter() const { return universe_.particles; }
    [[nodiscard]] std::vector<Cell> const& cells() const { return cells_; }
    [[nodiscard]] std::vector<Node> const& nodes() const { return nodes_; }
    [[nodiscard]] std::vector<Tracer> const& tracers() const { return tracers_; }
    void seedTracers(int count=180000);
    float gravity=1.f;
    float feedback=1.f;
    float cooling=0.25f;
    float courant=.30f;
    float coolingFloor=.00015f;
    float burstInterval=1.5f,burstRadius=.07071068f,burstHeat=.065f,burstKick=.22f,collimation=.8f;
    bool secondOrder=true;
    bool automaticBursts=true;
private:
    int n_;
    float h_;
    float numericalScale_=1.f;
    double time_{};
    double nextBurst_=0.45;
    double lastAutomaticBurst_=-1.05;
    float previousBurstInterval_=1.5f,maxAcceleration_=0;
    InitialConditions initial_;
    Universe universe_;
    std::vector<Cell> cells_, next_, stage_;
    std::vector<Tracer> tracers_;
    std::uint32_t seed_{};
    std::vector<Vector> acceleration_;
    std::vector<Node> nodes_;
    std::vector<std::array<int,2>> edges_;
    [[nodiscard]] int index(int x,int y,int z) const;
    [[nodiscard]] Vector position(int x,int y,int z) const;
    [[nodiscard]] Cell sample(Vector p) const;
    void transport(std::vector<Cell> const& input,std::vector<Cell>& output,float dt);
    void moveTracers(float dt);
    void validateParameters() const;
};
}
