#include "geodesic.hpp"
#include <algorithm>
#include <stdexcept>

namespace blackhole {
namespace {
void validate(double radius, double angle) {
    if (!std::isfinite(radius) || radius <= 2 || !std::isfinite(angle) || angle < 0 || angle > pi)
        throw std::invalid_argument("Camera must be outside r=2; ray angle must be in [0, pi].");
}
State derivative(State s, double b) {
    return {s.slope, -s.u + 3*s.u*s.u,
        1/(b*std::max(s.u*s.u, 1e-30)*std::max(1-2*s.u, 1e-12))};
}
State add(State a, State b, double scale) {
    return {a.u+scale*b.u, a.slope+scale*b.slope, a.delay+scale*b.delay};
}
}
double impactParameter(double radius, double angle) {
    validate(radius, angle);
    return radius*std::sin(angle)/std::sqrt(1-2/radius);
}
State initialState(double radius, double angle) {
    double b=impactParameter(radius, angle);
    if (b < 1e-12) throw std::invalid_argument("Radial rays have no orbital angle parameter.");
    return {1/radius, std::cos(angle)/b, 0};
}
double invariant(State s) { return s.slope*s.slope+s.u*s.u-2*s.u*s.u*s.u; }
State advance(State s, double b, double h) {
    auto a=derivative(s,b), c=derivative(add(s,a,h*.5),b);
    auto d=derivative(add(s,c,h*.5),b), e=derivative(add(s,d,h),b);
    return {s.u+h*(a.u+2*c.u+2*d.u+e.u)/6,
        s.slope+h*(a.slope+2*c.slope+2*d.slope+e.slope)/6,
        s.delay+h*(a.delay+2*c.delay+2*d.delay+e.delay)/6};
}
Result trace(double radius, double angle, double step, int maxSteps) {
    validate(radius,angle);
    if (!std::isfinite(step) || step<=0 || step>.1 || maxSteps<1)
        throw std::invalid_argument("Invalid integration budget.");
    double b=impactParameter(radius,angle);
    if (b<1e-12) return {angle<pi*.5 ? Outcome::captured : Outcome::escaped,{1/radius,0,0},0,0};
    State s=initialState(radius,angle);
    Result result; double phi=0, initial=invariant(s);
    for(int i=0;i<maxSteps;++i) {
        double h=std::min(step,.04/std::max(std::abs(s.slope),1e-9));
        State next=advance(s,b,h);
        result.invariantError=std::max(result.invariantError,std::abs(invariant(next)-initial));
        if (next.u>=.5 || next.u<=0) {
            double target=next.u>=.5 ? .5 : 0;
            // Bisect the terminating step for a stable escape angle.
            double lo=0,hi=h;
            for(int j=0;j<40;++j) {
                double mid=(lo+hi)*.5;
                bool crossed=target==0 ? advance(s,b,mid).u<=0 : advance(s,b,mid).u>=.5;
                if(crossed) hi=mid; else lo=mid;
            }
            double event=(lo+hi)*.5;
            result.state=advance(s,b,event); result.angle=phi+event;
            result.outcome=target==0 ? Outcome::escaped : Outcome::captured;
            return result;
        }
        phi+=h; s=next;
    }
    result.state=s; result.angle=phi; return result;
}
double shadowAngle(double radius) {
    validate(radius,0);
    double a=std::asin(std::clamp(criticalImpact*std::sqrt(1-2/radius)/radius,0.,1.));
    return radius<3 ? pi-a : a;
}
Result traceDisk(double radius,double angle,double outwardY,double tangentY,double outer,double step) {
    validate(radius,angle);
    if(!std::isfinite(outwardY) || !std::isfinite(tangentY) || std::abs(outwardY)>1 || std::abs(tangentY)>1
        || !std::isfinite(outer) || outer<=6 || !std::isfinite(step) || step<=0 || step>.1)
        throw std::invalid_argument("Invalid disk or ray-plane geometry.");
    double b=impactParameter(radius,angle);
    if(b<1e-12) return trace(radius,angle,step);
    State s=initialState(radius,angle);
    double phi=0,initial=invariant(s),error=0;
    double crossing=std::fmod(std::atan2(-outwardY,tangentY)+pi,pi);
    if(crossing<1e-6) crossing+=pi;
    bool coplanar=std::abs(outwardY)+std::abs(tangentY)<1e-7;
    for(int i=0;i<10000;++i) {
        double h=std::min(step,.04/std::max(std::abs(s.slope),1e-9));
        bool atDisk=phi+h>=crossing;
        if(atDisk) h=crossing-phi;
        s=advance(s,b,h); phi+=h;
        error=std::max(error,std::abs(invariant(s)-initial));
        if(s.u>=.5) return {Outcome::captured,s,phi,error};
        if(s.u<=0) return {Outcome::escaped,s,phi,error};
        if(atDisk) {
            crossing+=pi;
            if(!coplanar && s.u<=1./6 && s.u>=1/outer) return {Outcome::disk,s,phi,error};
        }
    }
    return {Outcome::unresolved,s,phi,error};
}
double frequencyRatio(double r,double camera,double lz) {
    if (!std::isfinite(r) || r<6 || !std::isfinite(camera) || camera<=2 || !std::isfinite(lz))
        throw std::invalid_argument("Circular disk emission requires r >= 6 and an exterior camera.");
    double denominator=std::sqrt(1-2/camera)*(1+lz/std::pow(r,1.5));
    if(denominator<=0) throw std::invalid_argument("Nonphysical photon angular momentum.");
    return std::sqrt(1-3/r)/denominator;
}
double gravitationalSeconds(double mass) {
    if(!std::isfinite(mass) || mass<=0) throw std::invalid_argument("Mass must be positive.");
    return 4.925490947e-6*mass;
}
Vec3 cameraOutward(double yaw,double inclination) {
    return {std::sin(inclination)*std::sin(yaw),std::cos(inclination),std::sin(inclination)*std::cos(yaw)};
}
}
