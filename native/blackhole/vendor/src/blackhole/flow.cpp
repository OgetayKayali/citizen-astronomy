#include "flow.hpp"
#include "geodesic.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <limits>

namespace blackhole {
namespace {
constexpr double gammaGas=5./3;
constexpr std::array<double,6> modes{5,9,14,23,37,59};
Gas add(Gas a,Gas b) { return {a.density+b.density,a.radial+b.radial,a.angular+b.angular,a.energy+b.energy}; }
Gas mul(Gas a,double v) { return {a.density*v,a.radial*v,a.angular*v,a.energy*v}; }
Gas sub(Gas a,Gas b) { return add(a,mul(b,-1)); }
double minmod(double a,double b) { return a*b<=0?0:std::copysign(std::min(std::abs(a),std::abs(b)),a); }
Gas slope(Gas a,Gas b,Gas c) {
    auto l=sub(b,a),r=sub(c,b);
    return {minmod(l.density,r.density),minmod(l.radial,r.radial),minmod(l.angular,r.angular),minmod(l.energy,r.energy)};
}

double centeredLimiter(double a,double b) { return minmod(.5*(a+b),minmod(2*a,2*b)); }
Gas orbitalSlope(Gas a,Gas b,Gas c) {
    auto l=sub(b,a),r=sub(c,b);
    return {centeredLimiter(l.density,r.density),centeredLimiter(l.radial,r.radial),
        centeredLimiter(l.angular,r.angular),centeredLimiter(l.energy,r.energy)};
}
int wrap(int j,int n) { j%=n; return j<0?j+n:j; }
double random(std::uint32_t& state) { state=1664525*state+1013904223; return state/4294967296.; }
double pressure(Gas s) { return (gammaGas-1)*s.energy; }
Gas flux(Gas a,Gas b,double radius,double orbital,bool azimuthal) {
    double va=azimuthal?a.angular/(a.density*radius)-orbital:a.radial/a.density;
    double vb=azimuthal?b.angular/(b.density*radius)-orbital:b.radial/b.density;
    double pa=pressure(a),pb=pressure(b);
    double wave=std::max(std::abs(va)+std::sqrt(gammaGas*pa/a.density),std::abs(vb)+std::sqrt(gammaGas*pb/b.density));
    auto fa=mul(a,va),fb=mul(b,vb);
    if(azimuthal) { fa.angular+=radius*pa; fb.angular+=radius*pb; }
    else { fa.radial+=pa; fb.radial+=pb; }
    return mul(sub(add(fa,fb),mul(sub(b,a),wave)),.5);
}
}
void advectRing(std::span<Gas const> source,std::span<Gas> destination,double shift) {
    int n=static_cast<int>(source.size());
    if(n<3 || source.size()!=destination.size() || !std::isfinite(shift) || source.data()==destination.data())
        throw std::invalid_argument("Invalid orbital remap buffers or shift.");
    shift=std::fmod(shift,static_cast<double>(n));
    int whole=static_cast<int>(std::floor(shift));double f=shift-whole;
    auto get=[&](int j) {return source[wrap(j,n)];};
    for(int j=0;j<n;++j) {
        int k=j-whole;Gas a=get(k-1),b=get(k);
        auto sa=orbitalSlope(get(k-2),a,b),sb=orbitalSlope(a,b,get(k+1));
        destination[j]=add(add(mul(b,1-f),mul(a,f)),mul(sub(sa,sb),.5*f*(1-f)));
    }
}
DiskFlow::DiskFlow(FlowConfig c):c_(c) {
    if(c.radial<16 || c.radial>256 || c.azimuthal<32 || c.azimuthal>512 || !std::isfinite(c.inner) || c.inner<2
        || !std::isfinite(c.outer) || c.outer<=c.inner+1 || c.outer>40 || !std::isfinite(c.spin) || c.spin<0 || c.spin>.9
        || !std::isfinite(c.alpha) || c.alpha<0 || c.alpha>.3 || !std::isfinite(c.aspect) || c.aspect<.02 || c.aspect>.15
        || !std::isfinite(c.stirring) || c.stirring<0 || c.stirring>1 || !std::isfinite(c.cooling) || c.cooling<1 || c.cooling>30)
        throw std::invalid_argument("Invalid thin-disk flow configuration.");
    dr_=(c.outer-c.inner)/c.radial; dp_=2*pi/c.azimuthal;
    forcingBasis_.resize(c.azimuthal);
    for(int j=0;j<c.azimuthal;++j) for(int k=0;k<6;++k) {
        forcingBasis_[j][2*k]=std::sin(modes[k]*(j+.5)*dp_);
        forcingBasis_[j][2*k+1]=std::cos(modes[k]*(j+.5)*dp_);
    }
    size_t count=static_cast<size_t>(c.radial)*c.azimuthal;
    gas_.resize(count);work_.resize(count);next_.resize(count);rf_.resize(count+c.azimuthal);pf_.resize(count);
    radius_.resize(c.radial);omega_.resize(c.radial);thermal_.resize(c.radial);viscosity_.resize(c.radial);
    for(int i=0;i<c.radial;++i) {
        double r=c.inner+(i+.5)*dr_,omega=1/(std::pow(r,1.5)+c.spin),cs=c.aspect*r*omega;
        radius_[i]=r;omega_[i]=omega; thermal_[i]=cs*cs/(gammaGas*(gammaGas-1));viscosity_[i]=c.alpha*cs*cs/omega;
        for(int j=0;j<c.azimuthal;++j) gas_[static_cast<size_t>(i)*c.azimuthal+j]=reservoir(i,j);
    }
    // Localized initial disturbances, with different scales and no periodic radial bands.
    if(c.perturbed) {
        std::uint32_t rng=c.seed;
        struct Patch { double r,p,width,amp; }; std::vector<Patch> patches;
        for(int k=0;k<60;++k) patches.push_back({c.inner+(c.outer-c.inner)*random(rng),2*pi*random(rng),.25+1.2*random(rng),-.6+1.5*random(rng)});
        for(int i=0;i<c.radial;++i) for(int j=0;j<c.azimuthal;++j) {
            double r=radius_[i],p=(j+.5)*dp_,perturb=0;
            for(auto const& patch:patches) {
                double dy=std::remainder(p-patch.p,2*pi)*r,dx=r-patch.r;
                perturb+=patch.amp*std::exp(-(dx*dx+dy*dy)/(2*patch.width*patch.width));
            }
            auto& cell=gas_[static_cast<size_t>(i)*c.azimuthal+j]; cell=mul(cell,std::exp(std::clamp(perturb,-1.2,1.2)));
        }
    }
    auto initial=totals();initialMass_=initial.mass;initialAngular_=initial.angular;
    std::uint32_t rng=c.seed+19;
    for(int k=0;k<3500;++k) tracers_.push_back({std::sqrt(c.inner*c.inner+(c.outer*c.outer-c.inner*c.inner)*random(rng)),2*pi*random(rng)});
}
Gas DiskFlow::reservoir(int i,int j) const {
    double r=radius_[i],rho=std::pow(r/c_.inner,-.7);
    // A nonaxisymmetric external reservoir supplies disturbed gas at the outer edge.
    if(c_.perturbed) rho*=1+.18*std::sin(3*(j+.5)*dp_-.013*time_)+.1*std::sin(7*(j+.5)*dp_+.021*time_);
    double radial=c_.closed?0:-1.5*viscosity_[i]/r;
    double vphi=r*omega_[i]*std::sqrt(1-1.7*c_.aspect*c_.aspect/gammaGas);
    return {rho,rho*radial,rho*r*vphi,rho*thermal_[i]};
}
void DiskFlow::orbit(double dt) {
    // Monotonized-central orbital remap preserves resolved filaments with less
    // damping than minmod. Hydrodynamic face reconstruction remains minmod.
    int n=c_.azimuthal;
    for(int i=0;i<c_.radial;++i) {
        auto start=static_cast<size_t>(i)*n;
        advectRing(std::span<Gas const>(gas_.data()+start,n),std::span<Gas>(work_.data()+start,n),omega_[i]*dt/dp_);
    }
    gas_.swap(work_);
}
double DiskFlow::stableStep() const {
    double dt=.25;int n=c_.azimuthal;
    for(int i=0;i<c_.radial;++i) for(int j=0;j<n;++j) {
        auto const& s=gas_[static_cast<size_t>(i)*n+j];
        double cs=std::sqrt(gammaGas*pressure(s)/s.density),vr=s.radial/s.density;
        double residual=s.angular/(s.density*radius_[i])-radius_[i]*omega_[i];
        dt=std::min(dt,.32/((std::abs(vr)+cs)/dr_+(std::abs(residual)+cs)/(radius_[i]*dp_)+4*viscosity_[i]/(dr_*dr_)));
    }
    return dt;
}
void DiskFlow::hydro(double dt) {
    int n=c_.azimuthal,nr=c_.radial;
    auto get=[&](int i,int j) { return gas_[static_cast<size_t>(std::clamp(i,0,nr-1))*n+wrap(j,n)]; };
    // Radial faces; viscosity transports angular momentum through the same faces.
    for(int i=0;i<=nr;++i) for(int j=0;j<n;++j) {
        double face=c_.inner+i*dr_; Gas a=get(i-1,j),b=get(i,j);
        if(i>0 && i<nr) {
            a=add(a,mul(slope(get(i-2,j),a,b),.5));
            b=sub(b,mul(slope(get(i-1,j),get(i,j),get(i+1,j)),.5));
        } else if(i==0) {
            b=get(0,j); a=b;
            if(c_.closed) a.radial=-b.radial;
            else { a.radial=std::min(a.radial,0.); b.radial=a.radial; }
        } else {
            a=get(nr-1,j); b=c_.closed?a:reservoir(nr-1,j);
            if(c_.closed) b.radial=-a.radial;
            else if(a.radial>0) b=a;
        }
        Gas f=flux(a,b,face,0,false);
        if(i>0 && i<nr) {
            auto left=get(i-1,j),right=get(i,j);
            double shear=(right.angular/(right.density*radius_[i]*radius_[i])-left.angular/(left.density*radius_[i-1]*radius_[i-1]))/dr_;
            double nu=.5*(viscosity_[i]+viscosity_[i-1]);
            f.angular-=nu*.5*(a.density+b.density)*face*face*shear;
        }
        if(c_.closed && (i==0 || i==nr)) { f.density=0;f.angular=0;f.energy=0; }
        rf_[static_cast<size_t>(i)*n+j]=mul(f,face);
    }
    for(int i=0;i<nr;++i) for(int j=0;j<n;++j) {
        auto a=get(i,j),b=get(i,j+1);
        auto left=add(a,mul(slope(get(i,j-1),a,b),.5));
        auto right=sub(b,mul(slope(a,b,get(i,j+2)),.5));
        pf_[static_cast<size_t>(i)*n+j]=flux(left,right,radius_[i],radius_[i]*omega_[i],true);
    }
    double massFlux=0,angularFlux=0,torque=0;
    for(int j=0;j<n;++j) {
        auto inner=rf_[j],outer=rf_[static_cast<size_t>(nr)*n+j];
        massFlux+=(inner.density-outer.density)*dp_;
        angularFlux+=(inner.angular-outer.angular)*dp_;
    }
    for(int i=0;i<nr;++i) {
      // Only the row phases change with time. Cache their trigonometry outside
      // the cell loop; azimuthal basis functions were cached at initialization.
      std::array<double,12> forcing{};
      double epoch=std::floor(time_/80),age=time_-epoch*80,blend=age/80;
      blend=blend*blend*(3-2*blend);
      constexpr std::array<double,6> amplitude{.45,.65,.6,.5,.35,.2};
      for(int k=0;k<6;++k) {
        // Suppress unresolved forcing on coarser diagnostic grids.
        double weight=amplitude[k]*std::clamp((n/3.-modes[k])/8.,0.,1.);
        for(int generation=0;generation<2;++generation) {
            double birth=epoch-1+generation,elapsed=age+80*(1-generation);
            double phase=(2.3+.23*k)*std::sin((.73+.31*k)*radius_[i]+birth*(1.37+.19*k))
                +birth*(2.4+.71*k)-modes[k]*omega_[i]*elapsed;
            double strength=weight*(generation?blend:1-blend);
            forcing[2*k]+=strength*std::cos(phase);
            forcing[2*k+1]+=strength*std::sin(phase);
        }
      }
      for(int j=0;j<n;++j) {
        size_t index=static_cast<size_t>(i)*n+j; auto s=gas_[index];double r=radius_[i];
        Gas change=add(mul(sub(rf_[index+n],rf_[index]),1/(r*dr_)),mul(sub(pf_[index],pf_[static_cast<size_t>(i)*n+wrap(j-1,n)]),1/(r*dp_)));
        Gas updated=sub(s,mul(change,dt));
        double vr=s.radial/s.density,vphi=s.angular/(s.density*r),p=pressure(s);
        updated.radial+=dt*(s.density*(vphi*vphi/r-r*omega_[i]*omega_[i])+p/r);
        auto left=get(i-1,j),right=get(i+1,j),before=get(i,j-1),after=get(i,j+1);
        double divergence=(right.radial/right.density-left.radial/left.density)/(2*dr_)+vr/r
            +(after.angular/after.density-before.angular/before.density)/(2*r*r*dp_);
        double omegaL=left.angular/(left.density*radius_[std::max(0,i-1)]*radius_[std::max(0,i-1)]);
        double omegaR=right.angular/(right.density*radius_[std::min(nr-1,i+1)]*radius_[std::min(nr-1,i+1)]);
        double shear=r*(omegaR-omegaL)/(i==0 || i==nr-1?dr_:2*dr_);
        double heat=s.density*viscosity_[i]*shear*shear;
        // Compression work, viscous heating and thermal relaxation. The energy
        // variable is internal energy; this is not a conservative total-energy solver.
        updated.energy+=dt*(-p*divergence+heat);
        double relaxation=std::exp(-dt*omega_[i]/c_.cooling);
        updated.energy=updated.density*thermal_[i]+(updated.energy-updated.density*thermal_[i])*relaxation;
        // Prescribed unresolved stirring, not self-generated MRI. Smooth modes
        // drive waves in the gas rather than drawing brightness noise.
        double cs=c_.aspect*r*omega_[i];
        double drive=4*c_.stirring*cs*omega_[i];
        // Finite-lived disturbances co-rotate with the gas. Fixed patterns are
        // swept past too quickly. Smooth overlap avoids forcing discontinuities.
        double ar=0,ap=0;
        for(int k=0;k<6;++k) {
            auto const& basis=forcingBasis_[j];
            ar+=basis[2*k]*forcing[2*k]+basis[2*k+1]*forcing[2*k+1];
            ap+=(k%2?-1:1)*.45*(basis[2*k+1]*forcing[2*k]-basis[2*k]*forcing[2*k+1]);
        }
        ar*=drive;ap*=drive;
        updated.radial+=dt*s.density*ar;updated.angular+=dt*s.density*r*ap;
        torque+=s.density*r*ap*r*dr_*dp_;
        if(!std::isfinite(updated.energy) || !std::isfinite(updated.angular) || updated.density<=1e-9 || updated.energy<=0)
            throw std::runtime_error("Disk flow lost positivity; decrease the timestep or disturbance strength.");
        next_[index]=updated;
      }
    }
    gas_.swap(next_);boundaryMass_+=dt*massFlux;boundaryAngular_+=dt*angularFlux;stirringAngular_+=dt*torque;
}
void DiskFlow::advance(double duration) {
    if(!std::isfinite(duration) || duration<0) throw std::invalid_argument("Invalid disk duration.");
    double remaining=duration;
    while(remaining>1e-10) {
        double dt=std::min(remaining,stableStep());
        if(dt<1e-7) throw std::runtime_error("Disk fluid timestep collapsed.");
        orbit(dt*.5);hydro(dt);orbit(dt*.5);
        for(auto& tracer:tracers_) {
            auto first=sample(tracer.radius,tracer.angle);
            auto midpoint=sample(tracer.radius+.5*dt*first[2],tracer.angle+.5*dt*first[3]);
            tracer.radius+=dt*midpoint[2];tracer.angle=std::fmod(tracer.angle+dt*midpoint[3]+2*pi,2*pi);
            if(tracer.radius<c_.inner || tracer.radius>=c_.outer) tracer.radius=c_.outer-dr_*.5;
        }
        time_+=dt;remaining-=dt;
    }
}
void DiskFlow::tick() { advance(.25); }
FlowTotals DiskFlow::totals() const {
    FlowTotals t;int n=c_.azimuthal;
    for(int i=0;i<c_.radial;++i) for(int j=0;j<n;++j) {
        auto s=gas_[static_cast<size_t>(i)*n+j];double area=radius_[i]*dr_*dp_;
        t.mass+=s.density*area;t.angular+=s.angular*area;
    }
    t.initialMass=initialMass_;t.initialAngular=initialAngular_;
    t.boundaryMass=boundaryMass_;t.boundaryAngular=boundaryAngular_;t.stirringAngular=stirringAngular_;
    t.massError=t.mass-initialMass_-boundaryMass_;t.angularError=t.angular-initialAngular_-boundaryAngular_-stirringAngular_;return t;
}
std::array<double,4> DiskFlow::sample(double r,double phi) const {
    double x=std::clamp((r-c_.inner)/dr_-.5,0.,static_cast<double>(c_.radial-1)),y=phi/dp_-.5;
    int i=static_cast<int>(std::floor(x)),j=static_cast<int>(std::floor(y));double a=x-i,b=y-j;
    auto get=[&](int row,int col) { return gas_[static_cast<size_t>(std::min(row,c_.radial-1))*c_.azimuthal+wrap(col,c_.azimuthal)]; };
    auto s=add(mul(add(mul(get(i,j),1-b),mul(get(i,j+1),b)),1-a),mul(add(mul(get(i+1,j),1-b),mul(get(i+1,j+1),b)),a));
    return {s.density,s.energy/s.density,s.radial/s.density,s.angular/(s.density*r*r)};
}
std::vector<float> DiskFlow::image() const {
    std::vector<float> result(gas_.size()*4);
    double reference=thermal_[0]*omega_[0]/c_.cooling;
    for(int i=0;i<c_.radial;++i) for(int j=0;j<c_.azimuthal;++j) {
        size_t k=static_cast<size_t>(i)*c_.azimuthal+j;auto s=gas_[k];
        result[k*4]=static_cast<float>(s.density);
        result[k*4+1]=static_cast<float>(s.energy*omega_[i]/(c_.cooling*reference));
        result[k*4+2]=static_cast<float>(s.radial/s.density);
        result[k*4+3]=static_cast<float>(s.angular/(s.density*radius_[i]*radius_[i]));
    }
    return result;
}
std::vector<float> DiskFlow::tracerImage() const {
    std::vector<float> result(gas_.size(),0);
    for(auto p:tracers_) {
        int i=static_cast<int>((p.radius-c_.inner)/dr_),j=static_cast<int>(p.angle/dp_);
        for(int di=-1;di<=1;++di) for(int dj=-1;dj<=1;++dj) {
            int row=i+di;if(row<0 || row>=c_.radial) continue;
            result[static_cast<size_t>(row)*c_.azimuthal+wrap(j+dj,c_.azimuthal)]+=static_cast<float>(std::exp(-1.6*(di*di+dj*dj)));
        }
    }
    return result;
}
}
