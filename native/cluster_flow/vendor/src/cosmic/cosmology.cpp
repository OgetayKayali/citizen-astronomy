#include "cosmic/cosmology.hpp"
#include <algorithm>
#include <cmath>
#include <complex>
#include <random>
#include <stdexcept>

namespace cosmic {
namespace {
constexpr double pi=3.14159265358979323846,kmToMpcGyr=.001022712165;
using Complex=std::complex<float>;
void fft(std::vector<Complex>& a,bool inverse) {
    int n=static_cast<int>(a.size());
    for(int i=1,j=0;i<n;++i) { int bit=n>>1; for(;j&bit;bit>>=1) j^=bit; j^=bit; if(i<j) std::swap(a[i],a[j]); }
    for(int len=2;len<=n;len*=2) {
        float angle=static_cast<float>((inverse?2:-2)*pi/len); Complex root(std::cos(angle),std::sin(angle));
        for(int i=0;i<n;i+=len) { Complex w(1,0); for(int j=0;j<len/2;++j) { Complex u=a[i+j],v=a[i+j+len/2]*w; a[i+j]=u+v; a[i+j+len/2]=u-v; w*=root; } }
    }
    if(inverse) for(auto& v:a) v/=static_cast<float>(n);
}
void fft3(std::vector<Complex>& field,int n,bool inverse) {
    for(int axis=0;axis<3;++axis) {
        #pragma omp parallel for schedule(static)
        for(int line=0;line<n*n;++line) {
            std::vector<Complex> row(n);
            int u=line%n,v=line/n;
            auto at=[&](int t) { return axis==0?(v*n+u)*n+t:axis==1?(v*n+t)*n+u:(t*n+v)*n+u; };
            for(int t=0;t<n;++t) row[t]=field[at(t)];
            fft(row,inverse);
            for(int t=0;t<n;++t) field[at(t)]=row[t];
        }
    }
}
int periodic(int x,int n) { return (x%n+n)%n; }
template<class F> void cic(Vector p,int n,F visit) {
    float f[3]={(p.x+1)*.5f*n-.5f,(p.y+1)*.5f*n-.5f,(p.z+1)*.5f*n-.5f};
    int base[3]; for(int d=0;d<3;++d) { base[d]=static_cast<int>(std::floor(f[d])); f[d]-=base[d]; }
    for(int z=0;z<2;++z) for(int y=0;y<2;++y) for(int x=0;x<2;++x) {
        int i=(periodic(base[2]+z,n)*n+periodic(base[1]+y,n))*n+periodic(base[0]+x,n);
        visit(i,(x?f[0]:1-f[0])*(y?f[1]:1-f[1])*(z?f[2]:1-f[2]));
    }
}
Vector position(int x,int y,int z,int n) { float h=2.f/n; return {-1+(x+.5f)*h,-1+(y+.5f)*h,-1+(z+.5f)*h}; }
Vector wrap(Vector p) { p.x-=2*std::floor((p.x+1)*.5f); p.y-=2*std::floor((p.y+1)*.5f); p.z-=2*std::floor((p.z+1)*.5f); return p; }
double powerShape(double k,Cosmology const& c) {
    // BBKS smooth CDM transfer with Sugiyama's baryon shape correction (no BAO).
    double h=c.hubble/100.,om=c.omegaDM+c.omegaB;
    double shape=om*h*h*std::exp(-c.omegaB*(1+std::sqrt(2*h)/om));
    double q=k/shape;
    double transfer=std::log1p(2.34*q)/(2.34*q)*std::pow(1+3.89*q+std::pow(16.1*q,2)+std::pow(5.46*q,3)+std::pow(6.71*q,4),-.25);
    return std::pow(k,c.spectralIndex)*transfer*transfer;
}
double normalization(Cosmology const& c) {
    double integral=0,lo=std::log(1.e-5),step=(std::log(1.e3)-lo)/4096,R=8/(c.hubble/100.);
    for(int i=0;i<4096;++i) { double k=std::exp(lo+(i+.5)*step),x=k*R; double window=x<.001?1-x*x/10:3*(std::sin(x)-x*std::cos(x))/(x*x*x); integral+=k*k*k*powerShape(k,c)*window*window*step/(2*pi*pi); }
    return c.sigma8*c.sigma8/integral;
}
std::array<double,2> growth(Cosmology const& c,double target) {
    // Integrate linear growth in ln(a), then normalize D(1)=1.
    double D=.001,V=.001,x=std::log(.001),end=0,step=.002,atD=0,atV=0;
    auto rhs=[&](double xx,double d,double v) {
        double a=std::exp(xx),m=(c.omegaDM+c.omegaB)/std::pow(a,3),k=c.curvature()/(a*a),de=c.omegaDE*std::pow(a,-3*(1+c.w)),e=m+k+de;
        double derivative=(-3*m-2*k-3*(1+c.w)*de)/(2*e);
        return std::array<double,2>{v,1.5*m/e*d-(2+derivative)*v};
    };
    bool captured=false; double targetX=std::log(target);
    while(x<end-1.e-10) {
        double dt=std::min(step,end-x);
        if(!captured && x<targetX && x+dt>=targetX) dt=targetX-x;
        auto k1=rhs(x,D,V),k2=rhs(x+dt/2,D+dt*k1[0]/2,V+dt*k1[1]/2),k3=rhs(x+dt/2,D+dt*k2[0]/2,V+dt*k2[1]/2),k4=rhs(x+dt,D+dt*k3[0],V+dt*k3[1]);
        D+=dt*(k1[0]+2*k2[0]+2*k3[0]+k4[0])/6; V+=dt*(k1[1]+2*k2[1]+2*k3[1]+k4[1])/6; x+=dt;
        if(!captured && x>=targetX-1.e-10) { atD=D; atV=V; captured=true; }
    }
    return {atD/D,atV/atD};
}
}
void Cosmology::validate() const {
    auto within=[](float x,float low,float high){ return std::isfinite(x)&&x>=low&&x<=high; };
    if(!within(hubble,30,100)||!within(omegaDM,0,1)||!within(omegaB,.001f,.3f)||!within(omegaDE,0,1.5f)||!within(w,-1.5f,-.5f)||!within(boxMpc,5,500)||!within(startRedshift,1,49)||!within(sigma8,0,2)||!within(spectralIndex,.7f,1.3f)||(mesh!=16&&mesh!=32&&mesh!=64&&mesh!=128)) throw std::invalid_argument("Cosmology parameter outside supported range.");
    for(double a=1./(1+startRedshift);a<=2.01;a*=1.05) if(!std::isfinite(hubbleGyr(a))) throw std::invalid_argument("This cosmology recollapses in the supported time range.");
}
double Cosmology::hubbleGyr(double a) const {
    double e=(omegaDM+omegaB)/std::pow(a,3)+curvature()/(a*a)+omegaDE*std::pow(a,-3*(1+w));
    return hubble*kmToMpcGyr*std::sqrt(e);
}
Universe::Universe(Cosmology value):settings(value) { if(settings.enabled) { settings.validate(); a=1./(1+settings.startRedshift); } }
int Universe::index(int x,int y,int z) const { int n=settings.mesh; return (periodic(z,n)*n+periodic(y,n))*n+periodic(x,n); }
void Universe::initialize(int gasGrid,std::uint32_t seed,std::vector<Cell>& gas,float temperatureMultiplier) {
    settings.validate(); a=1./(1+settings.startRedshift);
    int n=settings.mesh,count=n*n*n; particles.resize(count); density.assign(count,0); force.assign(count,{});
    std::mt19937 rng(seed);
    auto uniform=[&] { return (static_cast<double>(rng())+.5)/4294967296.; };
    std::vector<Complex> spectrum(count);
    for(auto& value:spectrum) value=static_cast<float>(std::sqrt(-2*std::log(uniform()))*std::cos(2*pi*uniform()));
    fft3(spectrum,n,false);
    double norm=normalization(settings),volume=std::pow(settings.boxMpc,3);
    for(int z=0;z<n;++z) for(int y=0;y<n;++y) for(int x=0;x<n;++x) {
        int kx=x<=n/2?x:x-n,ky=y<=n/2?y:y-n,kz=z<=n/2?z:z-n;
        double k=2*pi/settings.boxMpc*std::sqrt(double(kx*kx+ky*ky+kz*kz));
        spectrum[index(x,y,z)]*=k>0?static_cast<float>(std::sqrt(norm*powerShape(k,settings)*count/volume)):0.f;
    }
    std::vector<Vector> displacement(count);
    for(int axis=0;axis<3;++axis) {
        auto component=spectrum;
        for(int z=0;z<n;++z) for(int y=0;y<n;++y) for(int x=0;x<n;++x) {
            int wave[3]={x<=n/2?x:x-n,y<=n/2?y:y-n,z<=n/2?z:z-n};
            float k2=static_cast<float>(pi*pi*(wave[0]*wave[0]+wave[1]*wave[1]+wave[2]*wave[2]));
            float derivative=std::abs(wave[axis])==n/2?0.f:static_cast<float>(pi*wave[axis]);
            component[index(x,y,z)]*=k2>0?Complex(0,derivative/k2):Complex(0,0);
        }
        fft3(component,n,true);
        for(int i=0;i<count;++i) { if(axis==0) displacement[i].x=component[i].real(); if(axis==1) displacement[i].y=component[i].real(); if(axis==2) displacement[i].z=component[i].real(); }
    }
    auto D=growth(settings,a);
    float positionFactor=static_cast<float>(D[0]),velocityFactor=static_cast<float>(a*settings.hubbleGyr(a)*D[0]*D[1]);
    for(int z=0;z<n;++z) for(int y=0;y<n;++y) for(int x=0;x<n;++x) {
        int i=index(x,y,z); particles[i]={wrap(position(x,y,z,n)+displacement[i]*positionFactor),displacement[i]*velocityFactor};
    }
    // Deposit the same growing-mode displacement on the gas grid; baryons and DM
    // start with matching phases, but subsequently follow independent dynamics.
    gas.assign(static_cast<std::size_t>(gasGrid)*gasGrid*gasGrid,{});
    float thermal=static_cast<float>(.013756*1.e4*temperatureMultiplier*kmToMpcGyr*kmToMpcGyr/std::pow(settings.boxMpc*.5,2));
    // A uniform low-mass component ensures positive gas in unsampled fine cells.
    for(auto& q:gas) { q[0]=settings.omegaB*.05f; q[4]=q[0]*thermal/(2.f/3.f); }
    // Sample the displacement on the gas lattice rather than depositing the
    // coarser DM particles directly: sigma8=0 must stay uniform at any gas grid.
    for(int z=0;z<gasGrid;++z) for(int y=0;y<gasGrid;++y) for(int x=0;x<gasGrid;++x) {
        Vector p=position(x,y,z,gasGrid),d{};
        cic(p,n,[&](int i,float weight){ d=d+displacement[i]*weight; });
        Vector velocity=d*velocityFactor;
        cic(wrap(p+d*positionFactor),gasGrid,[&](int i,float weight) {
            float rho=settings.omegaB*.95f*weight; auto& q=gas[i];
            q[0]+=rho; q[1]+=rho*velocity.x; q[2]+=rho*velocity.y; q[3]+=rho*velocity.z;
        });
    }
    // Conservative smoothing of cell-scale deposition noise.
    int passes=1; std::vector<Cell> scratch(gas.size());
    for(int pass=0;pass<passes;++pass) {
        #pragma omp parallel for schedule(static)
        for(int z=0;z<gasGrid;++z) for(int y=0;y<gasGrid;++y) for(int x=0;x<gasGrid;++x) {
            int i=(z*gasGrid+y)*gasGrid+x;
            auto at=[&](int xx,int yy,int zz){ return (periodic(zz,gasGrid)*gasGrid+periodic(yy,gasGrid))*gasGrid+periodic(xx,gasGrid); };
            for(int k=0;k<4;++k) scratch[i][k]=.4f*gas[i][k]+.1f*(gas[at(x-1,y,z)][k]+gas[at(x+1,y,z)][k]+gas[at(x,y-1,z)][k]+gas[at(x,y+1,z)][k]+gas[at(x,y,z-1)][k]+gas[at(x,y,z+1)][k]);
        }
        gas.swap(scratch);
    }
    for(auto& q:gas) q[4]=q[0]*thermal/(2.f/3.f)+.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0];
    gravity(gas,gasGrid);
}
void Universe::gravity(std::vector<Cell> const& gas,int gasGrid) {
    int n=settings.mesh,count=n*n*n; std::fill(density.begin(),density.end(),0.f);
    float mass=settings.omegaDM*count/static_cast<float>(particles.size());
    for(auto const& p:particles) cic(p.position,n,[&](int i,float weight){ density[i]+=mass*weight; });
    if(gasGrid==n) { for(int i=0;i<count;++i) density[i]+=gas[i][0]; }
    else {
        float ratio=std::pow(static_cast<float>(n)/gasGrid,3.f);
        for(int z=0;z<gasGrid;++z) for(int y=0;y<gasGrid;++y) for(int x=0;x<gasGrid;++x) {
            float rho=gas[(z*gasGrid+y)*gasGrid+x][0]*ratio;
            cic(position(x,y,z,gasGrid),n,[&](int i,float weight){ density[i]+=rho*weight; });
        }
    }
    std::vector<Complex> spectrum(count);
    double mean=0; for(float value:density) mean+=value; mean/=count;
    for(int i=0;i<count;++i) spectrum[i]=density[i]-static_cast<float>(mean);
    fft3(spectrum,n,false);
    float coefficient=static_cast<float>(1.5*std::pow(settings.hubble*kmToMpcGyr,2)/(a*a));
    for(int axis=0;axis<3;++axis) {
        auto component=spectrum;
        for(int z=0;z<n;++z) for(int y=0;y<n;++y) for(int x=0;x<n;++x) {
            int wave[3]={x<=n/2?x:x-n,y<=n/2?y:y-n,z<=n/2?z:z-n};
            float k2=static_cast<float>(pi*pi*(wave[0]*wave[0]+wave[1]*wave[1]+wave[2]*wave[2]));
            float derivative=std::abs(wave[axis])==n/2?0.f:static_cast<float>(pi*wave[axis]);
            float smooth=std::exp(-k2/(n*n));
            component[index(x,y,z)]*=k2>0?Complex(0,coefficient*derivative/k2*smooth):Complex(0,0);
        }
        fft3(component,n,true);
        for(int i=0;i<count;++i) { if(axis==0) force[i].x=component[i].real(); if(axis==1) force[i].y=component[i].real(); if(axis==2) force[i].z=component[i].real(); }
    }
}
Vector Universe::acceleration(Vector p) const { Vector value{}; cic(p,settings.mesh,[&](int i,float weight){ value=value+force[i]*weight; }); return value; }
float Universe::signalSpeed() const { float speed=0; for(auto const& p:particles) speed=std::max(speed,length(p.velocity)); return speed; }
void Universe::advance(float dt) {
    double mid=a+.5*dt*a*settings.hubbleGyr(a),next=a+dt*mid*settings.hubbleGyr(mid);
    if(!(next>0) || !std::isfinite(next) || !std::isfinite(settings.hubbleGyr(next))) throw std::runtime_error("Cosmology reached a turnaround or invalid expansion rate. Shorten the duration or change the density parameters.");
    float damping=static_cast<float>(a/next),drift=static_cast<float>(dt/((a+next)*.5));
    #pragma omp parallel for schedule(static)
    for(int i=0;i<static_cast<int>(particles.size());++i) {
        auto& p=particles[i]; Vector old=p.velocity;
        p.velocity=(p.velocity+acceleration(p.position)*dt)*damping;
        p.position=wrap(p.position+(old+p.velocity)*(.5f*drift));
    }
    a=next;
}
float Universe::physicalVelocityUnit() const { return static_cast<float>(settings.boxMpc*.5/kmToMpcGyr); }
}
