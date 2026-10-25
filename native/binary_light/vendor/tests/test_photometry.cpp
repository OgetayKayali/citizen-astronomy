#include <doctest/doctest.h>
#include "photometry/binary.hpp"
#include <cmath>
#include <filesystem>
#include <fstream>
using namespace photometry;
TEST_CASE("Kepler orbit preserves barycenter, period and periastron") {
    Binary b; b.eccentricity=.6; b.setPeriodDays(8);
    CHECK(b.periodDays()==doctest::Approx(8));
    for(double p:{0.,.17,.5,.92}) {
        auto g=geometry(b,p); auto h=geometry(b,p+1);
        CHECK(g.position[0].x==doctest::Approx(h.position[0].x));
        CHECK(g.position[0].x*b.stars[0].mass+g.position[1].x*b.stars[1].mass==doctest::Approx(0));
        CHECK(g.position[0].z*b.stars[0].mass+g.position[1].z*b.stars[1].mass==doctest::Approx(0));
    }
    CHECK(geometry(b,0).separation==doctest::Approx(b.separation*.4));
    CHECK(geometry(b,.5).separation==doctest::Approx(b.separation*1.6));
}
TEST_CASE("Uniform disk occultation agrees with analytic circle overlap") {
    for(double d:{.01,.4,1.,1.7,1.999}) {
        double area=2*std::acos(d/2)-.5*d*std::sqrt(4-d*d);
        CHECK(blockedFraction(1,1,d,0)==doctest::Approx(area/pi).epsilon(2e-5));
    }
    CHECK(blockedFraction(1,.5,0,0)==doctest::Approx(.25).epsilon(1e-5));
    CHECK(blockedFraction(1,2,.5,.8)==1);
    CHECK(blockedFraction(1,1,2,.6)==0);
}
TEST_CASE("Linear limb darkening has the correct central occultation integral") {
    double r=.4,u=.7;
    double analytic=((1-u)*r*r+2*u/3*(1-std::pow(1-r*r,1.5)))/(1-u/3);
    CHECK(blockedFraction(1,r,0,u)==doctest::Approx(analytic).epsilon(1e-5));
    CHECK(blockedFraction(1,.4,.85,u,512)==doctest::Approx(blockedFraction(1,.4,.85,u,4096)).epsilon(1e-5));
}
TEST_CASE("Equal stars give half flux at central eclipse and flat face-on light") {
    Binary b; b.stars[1]=b.stars[0]; b.inclination=90;
    CHECK(instantaneous(b,0).flux==doctest::Approx(.5));
    CHECK(instantaneous(b,.5).flux==doctest::Approx(.5));
    CHECK(instantaneous(b,.25).flux==1);
    b.inclination=0;
    for(int i=0;i<100;++i) CHECK(instantaneous(b,i/100.).flux==1);
}
TEST_CASE("Temperatures, passbands, third light and distance affect the appropriate observables") {
    Binary b; b.inclination=90; auto a=instantaneous(b,0);
    CHECK(a.flux<instantaneous(b,.5).flux);
    b.distance*=2; auto far=instantaneous(b,0);
    CHECK(far.flux==a.flux); CHECK(far.irradiance==doctest::Approx(a.irradiance/4));
    b.thirdLight=.3; CHECK(instantaneous(b,0).flux==doctest::Approx(.3+.7*a.flux));
    b.thirdLight=0; b.band=Band::Blue; auto blue=instantaneous(b,0);
    b.band=Band::Red; CHECK(blue.flux<instantaneous(b,0).flux);
    b.node=63; CHECK(instantaneous(b,0).flux==doctest::Approx(observe(b,0).flux));
    CHECK(a.magnitude==doctest::Approx(-2.5*std::log10(a.flux)));
}
TEST_CASE("Finite exposure integrates flux across ingress, not magnitude") {
    Binary b; b.exposure=b.periodDays()*86400*.04;
    double phase=.04,sum=0; int n=10000;
    for(int k=0;k<n;++k)sum+=instantaneous(b,phase+((k+.5)/n-.5)*.04,128).flux/n;
    CHECK(observe(b,phase).flux==doctest::Approx(sum).epsilon(2e-4));
    CHECK(observe(b,.25).flux==doctest::Approx(1));
}
TEST_CASE("Presets are valid, eccentric eclipse is displaced, intersections rejected") {
    for(auto const& p:presets()) {
        CHECK(p.system.invalidReason().empty());
        for(int j=0;j<100;++j) { auto m=observe(p.system,j/100.); CHECK(m.flux>0); CHECK(m.flux<=1.0000001); }
    }
    auto b=presets()[2].system; double phaseMin=0,minF=1;
    for(int k=200;k<950;++k){double p=k/1000.;auto m=instantaneous(b,p);if(m.flux<minF){minF=m.flux;phaseMin=p;}}
    CHECK(std::abs(phaseMin-.5)>.03);
    b.separation=1; CHECK_FALSE(b.invalidReason().empty());
}
TEST_CASE("Systems roundtrip and malformed files are rejected") {
    auto path=std::filesystem::temp_directory_path()/"binary_light_lab_test.binary";
    auto b=presets()[2].system; b.exposure=30; saveSystem(b,path.string());
    auto c=loadSystem(path.string()); CHECK(c.eccentricity==b.eccentricity); CHECK(c.exposure==30);
    CHECK(observe(b,.133).flux==observe(c,.133).flux);
    {std::ofstream f(path);f<<"BINARY_LIGHT_LAB 1\n";}
    CHECK_THROWS(loadSystem(path.string())); std::filesystem::remove(path);
}
