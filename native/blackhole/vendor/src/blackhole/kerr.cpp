#include "kerr.hpp"
#include <algorithm>
#include <stdexcept>

namespace blackhole {
namespace {
Vec3 add(Vec3 a,Vec3 b) { return {a.x+b.x,a.y+b.y,a.z+b.z}; }
Vec3 mul(Vec3 a,double b) { return {a.x*b,a.y*b,a.z*b}; }
double dot(Vec3 a,Vec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
Vec3 cross(Vec3 a,Vec3 b) { return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x}; }
Vec3 normalized(Vec3 a) { return mul(a,1/std::sqrt(dot(a,a))); }
void validateSpin(double a) {
    if(!std::isfinite(a) || a<0 || a>.9) throw std::invalid_argument("Supported Kerr spin range is 0..0.9.");
}
KerrState plus(KerrState s,KerrState d,double h) {
    return {s.u+h*d.u,s.du+h*d.du,add(s.n,mul(d.n,h)),add(s.angular,mul(d.angular,h)),s.delay+h*d.delay};
}
KerrState rhs(KerrState s,KerrRay const& ray) {
    double a=ray.spin,l=ray.lz,q=ray.carter,u=s.u;
    double k=(l+a)*(l+a)+q,d=1-2*u+a*a*u*u;
    double p=1+(a*a+a*l)*u*u,drag=-a*(p/d-1);
    Vec3 axis{0,0,1};
    return {s.du,(a*a-l*l-q)*u+3*k*u*u-2*a*a*q*u*u*u,
        add(cross(s.angular,s.n),mul(cross(axis,s.n),drag)),
        add(mul(cross(s.n,axis),a*a*s.n.z),mul(cross(axis,s.angular),drag)),
        (1+a*a*u*u)*p/(std::max(u*u,1e-24)*d)-a*l-a*a*(1-s.n.z*s.n.z)};
}
}
double kerrHorizon(double a) { validateSpin(a); return 1+std::sqrt(1-a*a); }
double kerrIsco(double a) {
    validateSpin(a);
    double z1=1+std::cbrt(1-a*a)*(std::cbrt(1+a)+std::cbrt(1-a));
    double z2=std::sqrt(3*a*a+z1*z1);
    return 3+z2-std::sqrt(std::max(0.,(3-z1)*(3+z1+2*z2)));
}
KerrRay kerrCameraRay(double r,double inc,double yaw,double sx,double sy,double a) {
    return kerrCameraDirection(r,inc,yaw,{sx,sy,1},a);
}
KerrRay kerrCameraDirection(double r,double inc,double yaw,Vec3 local,double a) {
    validateSpin(a);
    if(!std::isfinite(r) || r<=kerrHorizon(a)+.005 || !std::isfinite(inc) || inc<=0 || inc>=pi
        || !std::isfinite(yaw) || !std::isfinite(local.x) || !std::isfinite(local.y) || !std::isfinite(local.z) || dot(local,local)<1e-20)
        throw std::invalid_argument("Invalid Kerr camera.");
    local=normalized(local);
    double st=std::sin(inc),ct=std::cos(inc),sp=std::sin(yaw),cp=std::cos(yaw);
    double sigma=r*r+a*a*ct*ct,delta=r*r-2*r+a*a,f=1-2*r/sigma;
    double gtphi=-2*a*r*st*st/sigma;
    double gphi=(r*r+a*a+2*a*a*r*st*st/sigma)*st*st;
    Vec3 n{st*cp,st*sp,ct},theta{ct*cp,ct*sp,-st},phi{-sp,cp,0};
    double l,ptheta,radial,energy;
    if(r>=6) {
        l=-gtphi/f+local.x*std::sqrt((gphi+gtphi*gtphi/f)/f);
        ptheta=-local.y*std::sqrt(sigma/f);
        radial=local.z*std::sqrt(sigma*delta/f)/(r*r);energy=std::sqrt(f);
    } else {
        double area=(r*r+a*a)*(r*r+a*a)-a*a*delta*st*st;
        double alpha=std::sqrt(sigma*delta/area),omega=2*a*r/area;
        double blend=std::clamp((r-3)/3,0.,1.);blend=blend*blend*(3-2*blend);
        double v=-blend*omega*std::sqrt(gphi)/alpha,gamma=1/std::sqrt(1-v*v);
        double azimuth=gamma*(local.x-v);
        energy=alpha*gamma*(1-v*local.x)-omega*std::sqrt(gphi)*azimuth;
        if(energy<=1e-7)return {{1/r,0,n,{},0},a,0,0,0,false};
        l=std::sqrt(gphi)*azimuth/energy;ptheta=-local.y*std::sqrt(sigma)/energy;
        radial=local.z*std::sqrt(sigma*delta)/(energy*r*r);
    }
    Vec3 angular=add(mul(phi,ptheta),mul(theta,-l/st));
    double q=ptheta*ptheta+ct*ct*(l*l/(st*st)-a*a);
    return {{1/r,radial,n,angular,0},a,l,q,energy};
}
double kerrRadialPotential(double u,KerrRay const& ray) {
    double a=ray.spin,l=ray.lz,q=ray.carter,k=(l+a)*(l+a)+q;
    return 1+(a*a-l*l-q)*u*u+2*k*u*u*u-a*a*q*u*u*u*u;
}
double kerrCarter(KerrState const& s,KerrRay const& ray) {
    return dot(s.angular,s.angular)-ray.lz*ray.lz-ray.spin*ray.spin*s.n.z*s.n.z;
}
KerrState kerrAdvance(KerrState s,KerrRay const& ray,double h) {
    auto a=rhs(s,ray),b=rhs(plus(s,a,h*.5),ray),c=rhs(plus(s,b,h*.5),ray),d=rhs(plus(s,c,h),ray);
    return plus(plus(plus(plus(s,a,h/6),b,h/3),c,h/3),d,h/6);
}
KerrResult traceKerr(KerrRay const& ray,bool disk,double outer,double accuracy,int budget) {
    validateSpin(ray.spin);
    double inner=kerrIsco(ray.spin),horizon=kerrHorizon(ray.spin),capture=1/(horizon+.005);
    if(!std::isfinite(outer) || outer<=inner || !std::isfinite(accuracy) || accuracy<=0 || accuracy>.1 || budget<1)
        throw std::invalid_argument("Invalid Kerr trace parameters.");
    KerrState s=ray.state; KerrResult result;
    if(!ray.exteriorVisible) {result.outcome=Outcome::captured;result.state=s;return result;}
    for(int i=0;i<budget;++i) {
        double h=accuracy/std::max(1.,std::sqrt(dot(s.angular,s.angular)));
        h=std::min(h,.08*std::max(s.u,1e-5)/std::max(std::abs(s.du),1e-9));
        if(s.du>0) h=std::min(h,.25*(1/horizon-s.u)/s.du);
        auto next=kerrAdvance(s,ray,h);
        result.radialError=std::max(result.radialError,std::abs(next.du*next.du-kerrRadialPotential(next.u,ray)));
        result.carterError=std::max(result.carterError,std::abs(kerrCarter(next,ray)-ray.carter));
        if(disk && s.n.z*next.n.z<0) {
            double lo=0,hi=h;
            for(int j=0;j<28;++j) {
                double mid=(lo+hi)*.5;
                if(s.n.z*kerrAdvance(s,ray,mid).n.z>0) lo=mid; else hi=mid;
            }
            auto hit=kerrAdvance(s,ray,(lo+hi)*.5);
            double r=1/hit.u;
            if(r>=inner && r<=outer) { result.outcome=Outcome::disk; result.state=hit; return result; }
        }
        s=next;
        if(!std::isfinite(s.u) || !std::isfinite(s.delay)) break;
        if(s.u>=capture) { result.outcome=Outcome::captured; break; }
        if(s.u<1e-5 && s.du<0) {
            result.outcome=Outcome::escaped;
            result.escape=normalized(add(s.n,mul(cross(s.angular,s.n),s.u)));
            break;
        }
    }
    result.state=s; return result;
}
double kerrFrequencyRatio(double r,double a,double l,double lapse) {
    validateSpin(a);
    if(!std::isfinite(r) || r<kerrIsco(a)-1e-10 || !std::isfinite(l) || !std::isfinite(lapse) || lapse<=0)
        throw std::invalid_argument("Invalid Kerr emission parameters.");
    double r32=r*std::sqrt(r),omega=1/(r32+a);
    double ut=(r32+a)/std::sqrt(r*r*r-3*r*r+2*a*r32);
    double denominator=lapse*ut*(1+omega*l);
    if(denominator<=0) throw std::invalid_argument("Nonphysical Kerr photon momentum.");
    return 1/denominator;
}
}
