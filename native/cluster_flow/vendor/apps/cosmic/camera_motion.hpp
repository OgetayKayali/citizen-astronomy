#pragma once
#include "cosmic/types.hpp"
#include <cmath>

namespace cosmic_view {
struct CameraMotion {
    static constexpr double radians=3.14159265358979323846/180.;
    float yaw=static_cast<float>(.12/radians),pitch=static_cast<float>(.18/radians);
    float yawSpeed=10,pitchSpeed=5;
    bool rotateYaw=false,rotatePitch=false;
    static float wrap(double angle) { return static_cast<float>(angle-360*std::floor((angle+180)/360)); }
    CameraMotion atTime(double seconds) const {
        CameraMotion next=*this;
        if(rotateYaw) next.yaw=wrap(yaw+static_cast<double>(yawSpeed)*seconds);
        if(rotatePitch) next.pitch=wrap(pitch+static_cast<double>(pitchSpeed)*seconds);
        return next;
    }
    void stop() { rotateYaw=false; rotatePitch=false; }
    cosmic::Vector outward() const {
        double y=yaw*radians,p=pitch*radians;
        return {static_cast<float>(std::cos(p)*std::sin(y)),static_cast<float>(std::sin(p)),static_cast<float>(std::cos(p)*std::cos(y))};
    }
    // An explicit orbit basis remains continuous at the vertical poles, where
    // cross(forward, worldUp) would be singular and flip the image.
    cosmic::Vector right() const { double y=yaw*radians; return {static_cast<float>(std::cos(y)),0,static_cast<float>(-std::sin(y))}; }
    cosmic::Vector up() const {
        double y=yaw*radians,p=pitch*radians;
        return {static_cast<float>(-std::sin(p)*std::sin(y)),static_cast<float>(std::cos(p)),static_cast<float>(-std::sin(p)*std::cos(y))};
    }
};
}
