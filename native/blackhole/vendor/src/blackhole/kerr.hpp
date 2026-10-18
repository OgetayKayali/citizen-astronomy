#pragma once
#include "geodesic.hpp"

namespace blackhole {
// Boyer-Lindquist radius, Mino time. Cartesian angular vectors avoid polar
// coordinate singularities. The spin axis is +z in this solver (viewer: +y).
struct KerrState { double u, du; Vec3 n, angular; double delay; };
struct KerrRay { KerrState state; double spin, lz, carter, cameraLapse; bool exteriorVisible=true; };
struct KerrResult {
    Outcome outcome=Outcome::unresolved;
    KerrState state{};
    Vec3 escape{};
    double radialError=0, carterError=0;
};
double kerrHorizon(double spin);
double kerrIsco(double spin);
KerrRay kerrCameraRay(double radius,double inclination,double yaw,double screenX,double screenY,double spin);
// Local direction is (right, up, inward). The close camera smoothly joins the
// static frame at r=6 to a ZAMO frame at r=3, remaining valid in the ergosphere.
KerrRay kerrCameraDirection(double radius,double inclination,double yaw,Vec3 local,double spin);
KerrState kerrAdvance(KerrState state,KerrRay const& ray,double step);
double kerrRadialPotential(double u,KerrRay const& ray);
double kerrCarter(KerrState const& state,KerrRay const& ray);
KerrResult traceKerr(KerrRay const& ray,bool disk=true,double outer=20,double accuracy=.01,int budget=10000);
double kerrFrequencyRatio(double radius,double spin,double backwardLz,double cameraLapse);
}
