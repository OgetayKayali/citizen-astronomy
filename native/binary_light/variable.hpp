#pragma once
#include <array>
#include <vector>
#include <string>

namespace variable {
// p uses the original binary's 17 values. x is the versioned extended model array.
enum Extra { Period, RadiusAmp, TempAmp, Lag, Harmonic, FrequencyRatio, SecondaryAmp,
    Alternation, FillA, FillB, Fillout, GravityBeta, Albedo, SpotSize, SpotTemp, SpotLatitude,
    EventStart, Rise, Decay, EventAmp, Recurrence, DiskRadius, Accretion, Molecular, ExtraCount };
struct V { double x=0,y=0,z=0; V operator+(V b)const{return{x+b.x,y+b.y,z+b.z};}
    V operator-(V b)const{return{x-b.x,y-b.y,z-b.z};} V operator*(double s)const{return{x*s,y*s,z*s};} };
struct Vertex { V p,n; double temperature=0,limb=0,flux=0; int component=0; };
struct Mesh { std::vector<Vertex> v; std::vector<std::array<int,3>> faces; };
class Model {
public:
    int family, resolution;
    std::array<double,17> p;
    std::array<double,ExtraCount> x;
    double reference=1, extent=1, l1=0, potential=0, volumeRadiusA=0, volumeRadiusB=0;
    Model(int family,const double* params,const double* extras,int resolution);
    std::array<double,5> observe(double cycles);
    Mesh frame(double cycles);
    double surface(double temperature)const;
private:
    Mesh base;
    std::array<double,1024> spectrum;
    double omega(double px,double py,double pz)const;
    V gradient(V v)const;
    double root(double lo,double hi)const;
    double radialRoot(V center,V direction,double target,double cap=2)const;
    Mesh rocheMesh();
    Mesh dynamicMesh(double cycles,bool quiet=false);
    std::array<double,2> raw(double cycles,bool quiet=false);
    std::array<double,2> pulse(double cycles)const;
    double event(double days)const;
    double opacity(double temperature)const;
};
}
