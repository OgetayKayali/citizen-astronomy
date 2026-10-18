#pragma once
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>

namespace blackhole_view {
inline float cameraHorizon(float spin) {return 1+std::sqrt(std::max(0.f,1-spin*spin));}
inline float cameraMinimum(float spin) {return cameraHorizon(spin)+.02f;}
struct CameraPose { float distance=60, inclination=80, yaw=0, fov=45, pan=0, tilt=0, roll=0; };
enum class Shot { still, orbit, push, pull, flyby, reveal, custom, slingshot };
inline constexpr char const* shotIds[]={"still","orbit","push","pull","flyby","reveal","custom","slingshot"};
inline Shot parseShot(std::string const& name) {
    for(int i=0;i<8;++i) if(name==shotIds[i]) return static_cast<Shot>(i);
    throw std::runtime_error("Unknown camera shot: "+name);
}
struct CameraPath {
    Shot shot=Shot::orbit;
    CameraPose end;
    float sweep=90,travel=1;
    float altitude=8,impact=30,bank=18,framing=.7f,arc=160;
    bool left=false;
    bool ease=true;
};
inline CameraPose smoothCamera(CameraPose current,CameraPose target,double dt,float seconds) {
    float a=seconds<=0?1.f:static_cast<float>(-std::expm1(-std::max(0.,dt)/seconds));
    auto linear=[&](float x,float y){return x+(y-x)*a;};
    auto angle=[&](float x,float y){return std::remainder(x+std::remainder(y-x,360.f)*a,360.f);};
    return {linear(current.distance,target.distance),linear(current.inclination,target.inclination),angle(current.yaw,target.yaw),
        linear(current.fov,target.fov),angle(current.pan,target.pan),linear(current.tilt,target.tilt),angle(current.roll,target.roll)};
}
struct CameraVector { double x,y,z; };
inline CameraVector operator+(CameraVector a,CameraVector b) {return {a.x+b.x,a.y+b.y,a.z+b.z};}
inline CameraVector operator-(CameraVector a,CameraVector b) {return {a.x-b.x,a.y-b.y,a.z-b.z};}
inline CameraVector operator*(CameraVector a,double s) {return {a.x*s,a.y*s,a.z*s};}
inline double cameraDot(CameraVector a,CameraVector b) {return a.x*b.x+a.y*b.y+a.z*b.z;}
struct CameraFrame { CameraVector forward,right,up; };
inline CameraFrame cameraFrame(CameraPose p) {
    constexpr double rad=3.14159265358979323846/180;
    double y=p.pan*rad,t=p.tilt*rad,r=p.roll*rad;
    CameraVector forward={std::sin(y)*std::cos(t),std::sin(t),std::cos(y)*std::cos(t)};
    CameraVector right={std::cos(y),0,-std::sin(y)};
    CameraVector up={-std::sin(y)*std::sin(t),std::cos(t),-std::cos(y)*std::sin(t)};
    return {forward,right*std::cos(r)+up*std::sin(r),up*std::cos(r)-right*std::sin(r)};
}
// Move in the camera's own axes while preserving its world viewing direction.
inline CameraPose moveCamera(CameraPose p,double rightStep,double upStep,double forwardStep,float minimum=2.02f) {
    constexpr double rad=3.14159265358979323846/180;
    auto basis=[](CameraPose q) {
        constexpr double r=3.14159265358979323846/180;double y=q.yaw*r,i=q.inclination*r;
        return CameraFrame{{-std::sin(i)*std::sin(y),-std::cos(i),-std::sin(i)*std::cos(y)},
            {std::cos(y),0,-std::sin(y)},{-std::cos(i)*std::sin(y),std::sin(i),-std::cos(i)*std::cos(y)}};
    };
    auto b=basis(p);auto f=cameraFrame(p);
    auto world=[&](CameraVector v){return b.right*v.x+b.up*v.y+b.forward*v.z;};
    auto forward=world(f.forward),right=world(f.right),up=world(f.up);
    auto position=b.forward*(-p.distance)+right*rightStep+up*upStep+forward*forwardStep;
    double distance=std::sqrt(cameraDot(position,position));
    if(distance<1e-6)return p;
    p.distance=static_cast<float>(std::clamp(distance,double(minimum),200.));
    p.inclination=static_cast<float>(std::clamp(std::acos(std::clamp(position.y/distance,-1.,1.))/rad,1.,179.));
    p.yaw=static_cast<float>(std::atan2(position.x,position.z)/rad);
    auto next=basis(p);
    p.pan=static_cast<float>(std::atan2(cameraDot(forward,next.right),cameraDot(forward,next.forward))/rad);
    p.tilt=static_cast<float>(std::clamp(std::asin(std::clamp(cameraDot(forward,next.up),-1.,1.))/rad,-85.,85.));
    p.roll=0;
    auto local=cameraFrame(p);
    CameraVector unrolledRight=next.right*local.right.x+next.up*local.right.y+next.forward*local.right.z;
    CameraVector unrolledUp=next.right*local.up.x+next.up*local.up.y+next.forward*local.up.z;
    p.roll=static_cast<float>(std::atan2(cameraDot(right,unrolledUp),cameraDot(right,unrolledRight))/rad);
    return p;
}
inline CameraPose cameraAt(CameraPose base,CameraPath const& path,double progress,float minimum=2.02f) {
    constexpr double radians=3.14159265358979323846/180.;
    double t=std::clamp(progress,0.,1.);
    if(path.ease) t=t*t*t*(t*(t*6-15)+10);
    auto mix=[&](float a,float b){return static_cast<float>(a+(b-a)*t);};
    CameraPose p=base;
    switch(path.shot) {
    case Shot::still: break;
    case Shot::orbit: p.yaw+=path.sweep*static_cast<float>(t);break;
    case Shot::push: p.distance=mix(base.distance,std::max(minimum,base.distance/(1+.8f*path.travel)));break;
    case Shot::pull: p.distance=mix(base.distance,std::min(200.f,base.distance*(1+.8f*path.travel)));break;
    case Shot::flyby:
    case Shot::slingshot: {
        double side=path.left?-1.:1.,yaw=base.yaw*radians,height=path.altitude;
        double x,z;
        if(path.shot==Shot::flyby) {
            // Constant-altitude line over the disk plane, passing the hole on
            // one side instead of orbiting a centered target.
            double span=std::min(base.distance*1.4*path.travel,std::sqrt(std::max(0.,40000.-path.impact*path.impact-height*height)));
            double along=(1-2*t)*span;
            x=side*path.impact*std::cos(yaw)+along*std::sin(yaw);
            z=-side*path.impact*std::sin(yaw)+along*std::cos(yaw);
            p.pan+=static_cast<float>(side*path.framing*base.fov*.4*(2*t-1));
            p.roll+=static_cast<float>(side*path.bank*std::sin(2*3.14159265358979323846*t)*.35);
        } else {
            // Authored approach, banked turn and climb-out, not a spacecraft
            // geodesic. All heights remain above the disk's equatorial plane.
            double crest=std::pow(std::sin(3.14159265358979323846*t),2);
            height+=12*path.travel*t*t*(3-2*t);
            double approach=std::max(double(path.impact),std::min(base.distance*(1+.6*path.travel),std::sqrt(40000.-height*height)));
            double radius=path.impact+(approach-path.impact)*std::pow(2*t-1,2);
            double bearing=yaw+side*(t-.5)*path.arc*radians;
            x=radius*std::sin(bearing);z=radius*std::cos(bearing);
            p.pan+=static_cast<float>(side*path.framing*base.fov*.42*crest);
            p.roll+=static_cast<float>(side*path.bank*crest);
        }
        p.distance=static_cast<float>(std::sqrt(x*x+height*height+z*z));
        p.inclination=static_cast<float>(std::acos(height/p.distance)/radians);
        p.yaw=static_cast<float>(std::atan2(x,z)/radians);
        p.tilt+=path.framing*base.fov*.10f;
        break;
    }
    case Shot::reveal:
        p.inclination=mix(std::clamp(base.inclination+25*path.travel,1.f,179.f),std::clamp(base.inclination-40*path.travel,1.f,179.f));
        p.yaw+=path.sweep*.25f*static_cast<float>(t);break;
    case Shot::custom:
        p.distance=mix(base.distance,path.end.distance);p.inclination=mix(base.inclination,path.end.inclination);
        p.yaw=base.yaw+static_cast<float>(t)*std::remainder(path.end.yaw-base.yaw,360.f);p.fov=mix(base.fov,path.end.fov);break;
    }
    if(path.shot==Shot::custom) {
        p.pan=base.pan+static_cast<float>(t)*std::remainder(path.end.pan-base.pan,360.f);
        p.tilt=mix(base.tilt,path.end.tilt);p.roll=base.roll+static_cast<float>(t)*std::remainder(path.end.roll-base.roll,360.f);
    }
    p.pan=std::remainder(p.pan,360.f);p.tilt=std::clamp(p.tilt,-85.f,85.f);p.roll=std::remainder(p.roll,360.f);
    p.distance=std::clamp(p.distance,minimum,200.f);p.inclination=std::clamp(p.inclination,1.f,179.f);
    p.fov=std::clamp(p.fov,15.f,80.f);p.yaw=std::remainder(p.yaw,360.f);
    return p;
}
}
