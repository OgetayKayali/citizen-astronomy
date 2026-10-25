#include "binary.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <stdexcept>

namespace photometry {
namespace {
constexpr double solarRadius=6.957e8, solarMass=1.98847e30, G=6.67430e-11;
constexpr double sigma=5.670374419e-8, parsec=3.0856775814913673e16;
bool finite(double x) { return std::isfinite(x); }
}
double Binary::periodDays() const {
    return 2*pi*std::sqrt(std::pow(separation*solarRadius,3)/(G*solarMass*(stars[0].mass+stars[1].mass)))/86400;
}
void Binary::setPeriodDays(double days) {
    separation=std::cbrt(G*solarMass*(stars[0].mass+stars[1].mass)*std::pow(days*86400/(2*pi),2))/solarRadius;
}
std::string Binary::invalidReason() const {
    for(auto const& s:stars) {
        if(!finite(s.mass)||s.mass<.01||s.mass>100) return "Mass must be 0.01 to 100 solar masses.";
        if(!finite(s.radius)||s.radius<.01||s.radius>100) return "Radius must be 0.01 to 100 solar radii.";
        if(!finite(s.temperature)||s.temperature<2000||s.temperature>50000) return "Temperature must be 2000 to 50000 K.";
        if(!finite(s.limb)||s.limb<0||s.limb>1) return "Linear limb darkening must be between 0 and 1.";
    }
    if(!finite(separation)||separation<.1||separation>2000) return "Semi-major axis must be 0.1 to 2000 solar radii.";
    if(!finite(eccentricity)||eccentricity<0||eccentricity>.9) return "Eccentricity must be between 0 and 0.9.";
    if(separation*(1-eccentricity)<=stars[0].radius+stars[1].radius) return "Stars intersect at periastron. Increase separation or reduce radii / eccentricity.";
    if(!finite(inclination)||inclination<0||inclination>90||!finite(omega)||std::abs(omega)>360||!finite(node)||std::abs(node)>360) return "Invalid orbital orientation.";
    if(!finite(distance)||distance<1||distance>100000) return "Observer distance must be 1 to 100000 parsecs.";
    if(!finite(thirdLight)||thirdLight<0||thirdLight>.95) return "Third-light fraction must be 0 to 0.95.";
    if(!finite(exposure)||exposure<0||exposure>periodDays()*86400*.05) return "Exposure must be between zero and 5% of the orbital period.";
    if(int(band)<0||int(band)>3) return "Unknown photometric band.";
    return {};
}
double Binary::rocheFraction(int star) const {
    double q=stars[star].mass/stars[1-star].mass, q13=std::cbrt(q);
    double lobe=separation*(1-eccentricity)*.49*q13*q13/(.6*q13*q13+std::log1p(q13));
    return stars[star].radius/lobe;
}
Geometry geometry(const Binary& b,double phase) {
    double M=2*pi*(phase-std::floor(phase)), E=M;
    for(int k=0;k<20;++k) { double d=(E-b.eccentricity*std::sin(E)-M)/(1-b.eccentricity*std::cos(E)); E-=d; if(std::abs(d)<1e-13) break; }
    double x=b.separation*(std::cos(E)-b.eccentricity), y=b.separation*std::sqrt(1-b.eccentricity*b.eccentricity)*std::sin(E);
    double w=b.omega*pi/180, i=b.inclination*pi/180, n=b.node*pi/180;
    double u=x*std::cos(w)-y*std::sin(w), v=x*std::sin(w)+y*std::cos(w);
    Vec r{u*std::cos(n)-v*std::cos(i)*std::sin(n),u*std::sin(n)+v*std::cos(i)*std::cos(n),v*std::sin(i)};
    double f=b.stars[1].mass/(b.stars[0].mass+b.stars[1].mass);
    return {{{{-f*r.x,-f*r.y,-f*r.z},{(1-f)*r.x,(1-f)*r.y,(1-f)*r.z}}},std::hypot(x,y),std::hypot(r.x,r.y),r.z>=0?1:0};
}
double surfaceFlux(const Star& s,Band band) {
    if(band==Band::Bolometric) return sigma*std::pow(s.temperature,4);
    // Ideal rectangular energy-response bands; deliberately not calibrated Johnson filters.
    double lo=band==Band::Blue?390:band==Band::Visual?500:600;
    double hi=band==Band::Blue?490:band==Band::Visual?600:750;
    double sum=0, step=(hi-lo)*1e-9/96;
    for(int k=0;k<96;++k) { double l=lo*1e-9+(k+.5)*step;
        sum+=2*6.62607015e-34*299792458.*299792458./(std::pow(l,5)*std::expm1(.01438776877/(l*s.temperature)));
    }
    return pi*sum*step;
}
double blockedFraction(double R,double r,double d,double u,int rings) {
    if(d>=R+r) return 0;
    if(r>=d+R) return 1;
    // Integrate only the occulted radial interval. sin^2 mapping resolves contact endpoints.
    double lo=std::max(0.,d-r), hi=std::min(R,d+r), sum=0;
    rings=std::max(rings,16);
    for(int k=0;k<rings;++k) {
        double t=(k+.5)*pi/(2*rings), sn=std::sin(t), rho=lo+(hi-lo)*sn*sn;
        double fraction;
        if(d<1e-14) fraction=rho<r?1:0;
        else fraction=std::acos(std::clamp((rho*rho+d*d-r*r)/(2*rho*d),-1.,1.))/pi;
        double intensity=1-u+u*std::sqrt(std::max(0.,1-rho*rho/(R*R)));
        sum+=fraction*intensity*2*rho*(hi-lo)*std::sin(2*t)*pi/(2*rings);
    }
    return std::clamp(sum/(R*R*(1-u/3)),0.,1.);
}
Measurement instantaneous(const Binary& b,double phase,int rings) {
    auto g=geometry(b,phase); Measurement m;
    int back=1-g.front;
    m.visible[back]=1-blockedFraction(b.stars[back].radius,b.stars[g.front].radius,g.projected,b.stars[back].limb,rings);
    double a=surfaceFlux(b.stars[0],b.band)*std::pow(b.stars[0].radius*solarRadius,2);
    double c=surfaceFlux(b.stars[1],b.band)*std::pow(b.stars[1].radius*solarRadius,2);
    double third=(a+c)*b.thirdLight/(1-b.thirdLight);
    m.flux=(a*m.visible[0]+c*m.visible[1]+third)/(a+c+third);
    m.magnitude=-2.5*std::log10(m.flux);
    m.irradiance=(a*m.visible[0]+c*m.visible[1]+third)/std::pow(b.distance*parsec,2);
    return m;
}
Measurement observe(const Binary& b,double phase,int rings) {
    if(b.exposure<=0) return instantaneous(b,phase,rings);
    Measurement m; m.flux=0; m.visible={0,0};
    constexpr int count=25;
    for(int k=0;k<count;++k) {
        auto s=instantaneous(b,phase+((k+.5)/count-.5)*b.exposure/(b.periodDays()*86400),rings);
        m.flux+=s.flux/count; m.irradiance+=s.irradiance/count;
        for(int j=0;j<2;++j) m.visible[j]+=s.visible[j]/count;
    }
    m.magnitude=-2.5*std::log10(m.flux); return m;
}
const std::vector<Preset>& presets() {
    static const auto items=[] {
        Binary algol;
        Binary twins; twins.stars={{{2.3,2.4,9200,.4},{2.2,2.3,9000,.4}}}; twins.separation=18; twins.inclination=89;
        Binary eccentric; eccentric.stars={{{1.6,1.7,7300,.5},{1.1,1.1,5700,.6}}}; eccentric.separation=24; eccentric.eccentricity=.45; eccentric.omega=35; eccentric.inclination=89;
        Binary grazing=algol; grazing.inclination=76;
        Binary transit; transit.stars={{{1,1,5772,.6},{.2,.22,3100,.7}}}; transit.separation=7; transit.inclination=90;
        Binary face=twins; face.inclination=20;
        return std::vector<Preset>{
            {"Algol-like / unequal temperatures","Illustrative detached EA: a hot primary and cooler companion produce unequal eclipse depths. Not a fit to Algol.",algol},
            {"Beta Aurigae-like / twin stars","Illustrative detached EA: similar surface brightness produces similar eclipse depths. Not a fit to Beta Aurigae.",twins},
            {"Eccentric / displaced secondary","Periastron at phase zero; eccentricity and periastron angle move the secondary eclipse away from half a cycle.",eccentric},
            {"Grazing / partial eclipses","A tilted orbit only clips the stellar limbs, producing shallower, rounded minima.",grazing},
            {"Small red companion / total occultation","A cool dwarf crosses a Sun-like primary, then disappears behind it. Compare blue and red bands.",transit},
            {"Low inclination / no eclipses","The same binary can show a flat light curve when neither star crosses the other from our line of sight.",face}
        };
    }(); return items;
}
const std::vector<VariableType>& variableTypes() {
    static const std::vector<VariableType> items={
        {"EA / detached eclipsing binaries","Available: spherical stars in a Kepler orbit, with limb-darkened eclipses.",true},
        {"EB / Beta Lyrae","Planned: distorted, often interacting binaries; requires Roche surfaces and irradiation.",false},
        {"EW / W Ursae Majoris","Planned: contact binaries sharing an envelope; requires contact-surface geometry.",false},
        {"Classical & Type II Cepheids","Planned: radial pulsation coupling radius and temperature over the cycle.",false},
        {"RR Lyrae","Planned: short-period radial pulsation with mode-dependent profiles.",false},
        {"Mira","Planned: long-period, large-amplitude pulsation of cool giants with molecular opacity.",false},
        {"Delta Scuti & SX Phoenicis","Planned: short-period, often multi-mode pulsation.",false},
        {"Beta Cephei & SPB","Planned: pulsation of hot stars, including multiple modes.",false},
        {"RV Tauri & semiregular giants","Planned: alternating minima and less regular giant-star pulsation.",false},
        {"Rotational / spotted variables","Planned: rotating surface spots and magnetic activity.",false},
        {"Cataclysmic & eruptive variables","Planned: accretion outbursts and other nonperiodic changes.",false}
    }; return items;
}
void saveSystem(const Binary& b,const std::string& path) {
    if(auto error=b.invalidReason();!error.empty()) throw std::runtime_error(error);
    std::ofstream f(path); if(!f) throw std::runtime_error("Cannot create system file.");
    f<<std::setprecision(17)<<"BINARY_LIGHT_LAB 1\n";
    for(auto s:b.stars) f<<s.mass<<' '<<s.radius<<' '<<s.temperature<<' '<<s.limb<<'\n';
    f<<b.separation<<' '<<b.eccentricity<<' '<<b.inclination<<' '<<b.omega<<' '<<b.node<<'\n';
    f<<b.distance<<' '<<b.thirdLight<<' '<<b.exposure<<' '<<int(b.band)<<'\n';
    if(!f) throw std::runtime_error("System write failed.");
}
Binary loadSystem(const std::string& path) {
    std::ifstream f(path); std::string magic; int version=0, band=0; Binary b;
    f>>magic>>version; if(magic!="BINARY_LIGHT_LAB"||version!=1) throw std::runtime_error("Not a supported Binary Light Lab system.");
    for(auto& s:b.stars) f>>s.mass>>s.radius>>s.temperature>>s.limb;
    f>>b.separation>>b.eccentricity>>b.inclination>>b.omega>>b.node>>b.distance>>b.thirdLight>>b.exposure>>band;
    if(!f) throw std::runtime_error("Incomplete system file.");
    b.band=static_cast<Band>(band); if(auto error=b.invalidReason();!error.empty()) throw std::runtime_error(error);
    return b;
}
void exportCurve(const Binary& b,const std::string& path,int samples) {
    if(auto error=b.invalidReason();!error.empty()) throw std::runtime_error(error);
    if(samples<2) throw std::runtime_error("At least two samples are required.");
    std::ofstream f(path); if(!f) throw std::runtime_error("Cannot create CSV.");
    f<<std::setprecision(12)<<"# Binary Light Lab; ideal model; phase 0 = periastron (circular: reference epoch)\n";
    f<<"# period_days="<<b.periodDays()<<"; a_Rsun="<<b.separation<<"; eccentricity="<<b.eccentricity<<"; inclination_deg="<<b.inclination<<"; omega_deg="<<b.omega<<"; node_deg="<<b.node<<"\n";
    for(int j=0;j<2;++j) { auto s=b.stars[j]; f<<"# star="<<j+1<<"; mass_Msun="<<s.mass<<"; radius_Rsun="<<s.radius<<"; temperature_K="<<s.temperature<<"; linear_limb="<<s.limb<<"\n"; }
    f<<"# band="<<int(b.band)<<"; distance_pc="<<b.distance<<"; third_light_fraction="<<b.thirdLight<<"; exposure_s="<<b.exposure<<"\n";
    f<<"phase,time_days,relative_flux,delta_magnitude,irradiance_W_m2,visible_primary_flux,visible_secondary_flux\n";
    for(int k=0;k<samples;++k) { double phase=double(k)/(samples-1); auto m=observe(b,phase); f<<phase<<','<<phase*b.periodDays()<<','<<m.flux<<','<<m.magnitude<<','<<m.irradiance<<','<<m.visible[0]<<','<<m.visible[1]<<'\n'; }
    if(!f) throw std::runtime_error("CSV write failed.");
}
}
