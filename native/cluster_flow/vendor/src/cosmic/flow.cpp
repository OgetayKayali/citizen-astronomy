#include "cosmic/flow.hpp"
#include "cosmic/cluster_field.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <random>
#include <stdexcept>

namespace cosmic {
namespace {
constexpr float gamma=5.f/3.f;
void range(float value,float low,float high,char const* name) {
    if(!std::isfinite(value) || value<low || value>high) throw std::invalid_argument(name);
}
float dot(Vector a,Vector b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
float pressure(Cell const& q,float numericalScale) {
    return std::max(1.e-9f*numericalScale,(gamma-1.f)*(q[4]-0.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0]));
}
Cell flux(Cell const& a,Cell const& b,int axis,float numericalScale) {
    float pa=pressure(a,numericalScale),pb=pressure(b,numericalScale);
    float va=a[axis+1]/a[0],vb=b[axis+1]/b[0];
    float speed=std::max(std::abs(va)+std::sqrt(gamma*pa/a[0]),std::abs(vb)+std::sqrt(gamma*pb/b[0]));
    Cell f{};
    for(int k=0;k<5;++k) f[k]=0.5f*(va*a[k]+vb*b[k]-speed*(b[k]-a[k]));
    f[axis+1]+=0.5f*(pa+pb);
    f[4]+=0.5f*(pa*va+pb*vb);
    return f;
}
float limited(float a,float b) {
    if(a*b<=0) return 0;
    float magnitude=std::min({1.5f*std::abs(a),1.5f*std::abs(b),.5f*std::abs(a+b)});
    return std::copysign(magnitude,a);
}
Cell reconstructed(Cell const& previous,Cell const& center,Cell const& next,float side,float numericalScale) {
    Cell q;
    for(int k=0;k<5;++k) q[k]=center[k]+side*.5f*limited(center[k]-previous[k],next[k]-center[k]);
    if(q[0]<=1.e-8f || q[4]-.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0]<=1.e-10f*numericalScale) return center;
    return q;
}
}
float length(Vector v) { return std::sqrt(dot(v,v)); }
Vector normalized(Vector v) { return v*(1.f/std::max(length(v),1.e-10f)); }
Flow::Flow(int resolution,std::uint32_t seed,InitialConditions initial):n_(resolution),h_(2.f/static_cast<float>(resolution)),initial_(initial),universe_(initial.cosmology) {
    if(n_<12 || n_>160) throw std::invalid_argument("Grid resolution must be between 12 and 160.");
    range(initial.gasDensity,.1f,4.f,"Gas density multiplier must be 0.1 to 4.");
    range(initial.temperature,.25f,4.f,"Initial temperature multiplier must be 0.25 to 4.");
    range(initial.inflow,0.f,3.f,"Inflow multiplier must be 0 to 3.");
    range(initial.turbulence,0.f,3.f,"Perturbation multiplier must be 0 to 3.");
    range(initial.filamentWidth,.5f,2.f,"Filament width multiplier must be 0.5 to 2.");
    range(initial.haloSoftening,.03f,.15f,"Halo softening must be 0.03 to 0.15.");
    range(initial.clusterBox,2.f,8.f,"Cluster cube side must be 2 to 8 code units.");
    h_=boxSize()/static_cast<float>(n_);
    // Cosmological velocity units vary with box size. Scale absolute numerical
    // floors with those units, so a larger volume does not artificially heat gas.
    if(initial.cosmology.enabled) numericalScale_=static_cast<float>(std::pow(.001022712165/(initial.cosmology.boxMpc*.5),2));
    cells_.resize(static_cast<std::size_t>(n_)*n_*n_);
    next_.resize(cells_.size());
    stage_.resize(cells_.size());
    acceleration_.resize(cells_.size());
    reset(seed);
}
int Flow::index(int x,int y,int z) const {
    // Periodic box: flux leaving one face enters the opposite face.
    x=(x+n_)%n_; y=(y+n_)%n_; z=(z+n_)%n_;
    return (z*n_+y)*n_+x;
}
Vector Flow::position(int x,int y,int z) const {
    float half=boxSize()*.5f;
    return {-half+(static_cast<float>(x)+.5f)*h_,-half+(static_cast<float>(y)+.5f)*h_,-half+(static_cast<float>(z)+.5f)*h_};
}
void Flow::reset(std::uint32_t seed) {
    time_=0; nextBurst_=.45; nodes_.clear(); edges_.clear(); tracers_.clear(); seed_=seed;
    previousBurstInterval_=burstInterval; lastAutomaticBurst_=.45-burstInterval;
    if(initial_.cosmology.enabled) {
        universe_.initialize(n_,seed,cells_,initial_.temperature);
        automaticBursts=false; cooling=0;
        return;
    }
    std::mt19937 rng(seed);
    // Explicit conversion keeps the scene repeatable across standard libraries.
    auto random=[&] { return static_cast<float>(rng()>>8)*(1.f/16777216.f); };
    nodes_.push_back({{-.14f,.03f,.02f},1.8f});
    nodes_.push_back({{.32f,.15f,-.12f},1.1f});
    while(nodes_.size()<38) {
        Vector p={(random()*2.f-1.f)*.84f,(random()*2.f-1.f)*.78f,(random()*2.f-1.f)*.78f};
        bool separate=true;
        for(auto const& node:nodes_) if(length(p-node.position)<.22f) separate=false;
        if(separate) nodes_.push_back({p,.15f+random()*.4f});
    }
    // Connected spatial graph; no independent noise-driven motion.
    for(int i=1;i<static_cast<int>(nodes_.size());++i) {
        int best=0; float distance=10;
        for(int j=0;j<i;++j) { float d=length(nodes_[i].position-nodes_[j].position); if(d<distance) { distance=d; best=j; } }
        edges_.push_back({i,best});
        if(i>3) {
            int second=-1; distance=.65f;
            for(int j=0;j<i;++j) if(j!=best) { float d=length(nodes_[i].position-nodes_[j].position); if(d<distance) { distance=d; second=j; } }
            if(second>=0 && i%2==0) edges_.push_back({i,second});
        }
    }
    bool expanded=boxSize()>2.f;
    if(expanded) {
        // Keep the original realization in the central cube. Populate only the
        // newly added volume, at the original number of clumps per unit volume.
        int target=clusterClumpCount(boxSize());
        while(static_cast<int>(nodes_.size())<target) {
            Vector p={(random()-.5f)*boxSize(),(random()-.5f)*boxSize(),(random()-.5f)*boxSize()};
            if(std::abs(p.x)<1 && std::abs(p.y)<1 && std::abs(p.z)<1) continue;
            bool separate=true;
            for(auto const& node:nodes_) if(length(detail::minimumImage(p-node.position,boxSize()))<.22f) { separate=false; break; }
            if(separate) { float mass=nodes_.size()%19==0?1.1f+random()*.7f:.15f+random()*.4f; nodes_.push_back({p,mass}); }
        }
        // Grow a minimum-distance spanning network outward from the old core,
        // rather than connecting far-away regions in random insertion order.
        std::vector<float> nearest(nodes_.size(),std::numeric_limits<float>::max());
        std::vector<int> parent(nodes_.size(),-1); std::vector<bool> connected(nodes_.size(),false);
        for(int i=0;i<38;++i) connected[i]=true;
        auto update=[&](int from) {
            for(int j=38;j<target;++j) if(!connected[j]) {
                float d=length(detail::minimumImage(nodes_[from].position-nodes_[j].position,boxSize()));
                if(d<nearest[j]) { nearest[j]=d; parent[j]=from; }
            }
        };
        for(int i=0;i<38;++i) update(i);
        for(int count=38;count<target;++count) {
            int next=-1;
            for(int j=38;j<target;++j) if(!connected[j] && (next<0 || nearest[j]<nearest[next])) next=j;
            edges_.push_back({next,parent[next]});
            if(next%2==0) {
                int second=-1; float distance=.65f;
                for(int j=0;j<target;++j) if(connected[j] && j!=parent[next]) {
                    float d=length(detail::minimumImage(nodes_[j].position-nodes_[next].position,boxSize()));
                    if(d<distance) { distance=d; second=j; }
                }
                if(second>=0) edges_.push_back({next,second});
            }
            connected[next]=true; update(next);
        }
    }
    detail::ClusterBins edgeBins(boxSize()),nodeBins(boxSize());
    detail::ClusterHaloTree haloTree(nodes_,boxSize(),initial_.haloSoftening);
    std::vector<int> allEdges(edges_.size()),allNodes(nodes_.size());
    std::iota(allEdges.begin(),allEdges.end(),0); std::iota(allNodes.begin(),allNodes.end(),0);
    if(expanded) {
        for(int i:allNodes) {
            Vector p=nodes_[i].position,pad{.35f,.35f,.35f}; nodeBins.add(i,p-pad,p+pad);
        }
        for(int i:allEdges) {
            auto const& edge=edges_[i]; Vector start=nodes_[edge[0]].position;
            Vector end=start+detail::minimumImage(nodes_[edge[1]].position-start,boxSize());
            float phase=static_cast<float>(edge[0])*1.7f,tail=.198f*initial_.filamentWidth;
            Vector pad{std::abs(std::sin(phase)*.10f)+tail,std::abs(std::cos(phase*1.3f)*.10f)+tail,std::abs(std::sin(phase*.7f)*.08f)+tail};
            edgeBins.add(i,Vector{std::min(start.x,end.x),std::min(start.y,end.y),std::min(start.z,end.z)}-pad,Vector{std::max(start.x,end.x),std::max(start.y,end.y),std::max(start.z,end.z)}+pad);
        }
        edgeBins.finish(); nodeBins.finish();
    }
    #pragma omp parallel for schedule(static)
    for(int z=0;z<n_;++z) for(int y=0;y<n_;++y) for(int x=0;x<n_;++x) {
        Vector p=position(x,y,z),a{},velocity{};
        float rho=.003f,weight=0;
        for(int edgeIndex:expanded?edgeBins.at(p):allEdges) {
            auto const& edge=edges_[edgeIndex];
            Vector start=nodes_[edge[0]].position,end=nodes_[edge[1]].position;
            Vector ab=end-start;
            Vector local=p;
            if(expanded) { ab=detail::minimumImage(ab,boxSize()); Vector middle=start+ab*.5f; local=middle+detail::minimumImage(p-middle,boxSize()); }
            float t=std::clamp(dot(local-start,ab)/dot(ab,ab),0.f,1.f);
            float phase=static_cast<float>(edge[0])*1.7f;
            Vector bend{std::sin(phase)*.10f,std::cos(phase*1.3f)*.10f,std::sin(phase*.7f)*.08f};
            Vector nearest=start+ab*t+bend*std::sin(t*3.14159f);
            float d=length(local-nearest);
            float width=(.020f+.013f*std::sin(t*3.14159f))*initial_.filamentWidth;
            float strand=std::exp(-d*d/(2.f*width*width));
            float knots=.85f+.15f*std::sin(t*16.f+phase);
            rho+=strand*.55f*knots;
            Vector direction=normalized(ab+bend*(3.14159f*std::cos(t*3.14159f)));
            if(nodes_[edge[0]].mass>nodes_[edge[1]].mass) direction=direction*(-1.f);
            velocity=velocity+(direction*.09f+(nearest-local)*.6f)*strand;
            weight+=strand;
        }
        for(int nodeIndex:expanded?nodeBins.at(p):allNodes) {
            auto const& node=nodes_[nodeIndex];
            Vector d=node.position-p;
            // Minimum-image separation keeps the analytic potential periodic.
            float side=boxSize();
            d.x-=side*std::round(d.x/side); d.y-=side*std::round(d.y/side); d.z-=side*std::round(d.z/side);
            float r2=dot(d,d),soft=initial_.haloSoftening;
            if(!expanded) a=a+d*(.0012f*node.mass/std::pow(r2+soft*soft,1.5f));
            rho+=node.mass*1.1f*std::exp(-r2/.004f);
        }
        if(expanded) a=haloTree.acceleration(p);
        velocity=velocity*(initial_.inflow/(weight+.08f));
        // Small coherent transverse perturbations seed asymmetric gas structures.
        velocity=velocity+Vector{std::sin(p.y*13.f+p.z*9.f),std::sin(p.z*11.f+p.x*8.f),std::sin(p.x*12.f+p.y*7.f)}*(.009f*initial_.turbulence);
        float thermal=.00045f*initial_.temperature;
        rho*=initial_.gasDensity;
        cells_[index(x,y,z)]={rho,rho*velocity.x,rho*velocity.y,rho*velocity.z,rho*thermal/(gamma-1.f)+.5f*rho*dot(velocity,velocity)};
        acceleration_[index(x,y,z)]=a;
    }
    maxAcceleration_=0;
    for(auto const a:acceleration_) maxAcceleration_=std::max(maxAcceleration_,length(a));
}
void Flow::transport(std::vector<Cell> const& input,std::vector<Cell>& output,float dt) {
    #pragma omp parallel for schedule(static)
    for(int z=0;z<n_;++z) for(int y=0;y<n_;++y) for(int x=0;x<n_;++x) {
        int i=index(x,y,z); Cell q=input[i];
        for(int axis=0;axis<3;++axis) {
            int dx=axis==0,dy=axis==1,dz=axis==2;
            auto const& a=input[index(x-2*dx,y-2*dy,z-2*dz)];
            auto const& b=input[index(x-dx,y-dy,z-dz)];
            auto const& c=input[i];
            auto const& d=input[index(x+dx,y+dy,z+dz)];
            auto const& e=input[index(x+2*dx,y+2*dy,z+2*dz)];
            Cell right=secondOrder?flux(reconstructed(b,c,d,1,numericalScale_),reconstructed(c,d,e,-1,numericalScale_),axis,numericalScale_):flux(c,d,axis,numericalScale_);
            Cell left=secondOrder?flux(reconstructed(a,b,c,1,numericalScale_),reconstructed(b,c,d,-1,numericalScale_),axis,numericalScale_):flux(b,c,axis,numericalScale_);
            for(int k=0;k<5;++k) q[k]-=dt/h_*(right[k]-left[k]);
        }
        output[i]=q;
    }
}
float Flow::step(float maximumDt) {
    if(!std::isfinite(maximumDt) || maximumDt<=0) throw std::invalid_argument("Step duration must be positive and finite.");
    validateParameters();
    bool expanding=initial_.cosmology.enabled;
    float scale=expanding?static_cast<float>(universe_.a):1.f;
    if(expanding) {
        universe_.gravity(cells_,n_);
        #pragma omp parallel for schedule(static)
        for(int z=0;z<n_;++z) for(int y=0;y<n_;++y) for(int x=0;x<n_;++x) acceleration_[index(x,y,z)]=universe_.acceleration(position(x,y,z));
        maxAcceleration_=0; for(auto const force:acceleration_) maxAcceleration_=std::max(maxAcceleration_,length(force));
    }
    float maxSignal=.001f;
    for(int i=0;i<static_cast<int>(cells_.size());++i) {
        auto const& q=cells_[i];
        float signal=(std::abs(q[1])+std::abs(q[2])+std::abs(q[3]))/q[0]+3.f*std::sqrt(gamma*pressure(q,numericalScale_)/q[0]);
        maxSignal=std::max(maxSignal,signal);
    }
    if(expanding) maxSignal=std::max(maxSignal,universe_.signalSpeed());
    float dt=std::min(maximumDt,courant*h_*scale/maxSignal);
    // Also bound the gravitational kick for a nearly stationary initial state.
    float gravityFactor=expanding?1.f:gravity;
    if(gravityFactor>0 && maxAcceleration_>0) dt=std::min(dt,std::sqrt(.2f*h_*scale/(gravityFactor*maxAcceleration_)));
    if(expanding) dt=std::min(dt,static_cast<float>(.02/initial_.cosmology.hubbleGyr(universe_.a)));
    if(!(dt>0) || !std::isfinite(dt) || time_+dt==time_) throw std::runtime_error("Simulation timestep is too small to advance. Reduce the duration or revise the physical parameters.");
    float transportDt=dt/scale;
    if(secondOrder) { transport(cells_,stage_,transportDt); transport(stage_,next_,transportDt); }
    else transport(cells_,next_,transportDt);
    #pragma omp parallel for schedule(static)
    for(int z=0;z<n_;++z) for(int y=0;y<n_;++y) for(int x=0;x<n_;++x) {
        int i=index(x,y,z); Cell q{};
        for(int k=0;k<5;++k) q[k]=secondOrder?.5f*(cells_[i][k]+next_[i][k]):next_[i][k];
        float kineticBefore=.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0];
        Vector kick=acceleration_[i]*(gravityFactor*dt*q[0]);
        q[1]+=kick.x; q[2]+=kick.y; q[3]+=kick.z;
        float kineticAfter=.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0];
        float internal=std::max(q[4]-kineticBefore,1.e-8f*numericalScale_*q[0]);
        float floor=q[0]*coolingFloor/(gamma-1.f);
        if(!expanding && internal>floor) internal=floor+(internal-floor)*std::exp(-cooling*dt);
        // Keep thermal energy representable beside the kinetic energy in float32.
        // Strong kicks in cold gas can otherwise round total energy to kinetic energy.
        internal=std::max(internal,8.f*std::numeric_limits<float>::epsilon()*kineticAfter);
        q[4]=internal+kineticAfter;
        next_[i]=q;
    }
    cells_.swap(next_); time_+=dt;
    moveTracers(transportDt);
    if(expanding) {
        universe_.advance(dt);
        float ratio=scale/static_cast<float>(universe_.a);
        #pragma omp parallel for schedule(static)
        for(int i=0;i<static_cast<int>(cells_.size());++i) {
            auto& q=cells_[i]; q[1]*=ratio; q[2]*=ratio; q[3]*=ratio; q[4]*=ratio*ratio;
        }
        return dt;
    }
    if(burstInterval!=previousBurstInterval_) {
        nextBurst_=std::max(time_,lastAutomaticBurst_+burstInterval);
        previousBurstInterval_=burstInterval;
    }
    if(automaticBursts && time_>=nextBurst_) { burst(); lastAutomaticBurst_=time_; nextBurst_=time_+burstInterval; }
    return dt;
}
void Flow::validateParameters() const {
    range(gravity,0,4,"Halo gravity must be 0 to 4."); range(feedback,0,5,"Feedback must be 0 to 5.");
    range(cooling,0,3,"Cooling must be 0 to 3."); range(courant,.05f,.30f,"Courant number must be 0.05 to 0.30.");
    range(coolingFloor,.00001f,.001f,"Cooling floor must be 0.00001 to 0.001.");
    range(burstInterval,.2f,10,"Outburst interval must be 0.2 to 10.");
    range(burstRadius,.04f,.16f,"Outburst radius must be 0.04 to 0.16.");
    range(burstHeat,0,.2f,"Outburst heating must be 0 to 0.2.");
    range(burstKick,0,.6f,"Outburst kick must be 0 to 0.6.");
    range(collimation,0,1,"Collimation must be 0 to 1.");
}
Cell Flow::sample(Vector p) const {
    float half=boxSize()*.5f;
    float fx=(p.x+half)/h_-.5f,fy=(p.y+half)/h_-.5f,fz=(p.z+half)/h_-.5f;
    int x=static_cast<int>(std::floor(fx)),y=static_cast<int>(std::floor(fy)),z=static_cast<int>(std::floor(fz));
    fx-=static_cast<float>(x); fy-=static_cast<float>(y); fz-=static_cast<float>(z);
    Cell q{};
    for(int dz=0;dz<2;++dz) for(int dy=0;dy<2;++dy) for(int dx=0;dx<2;++dx) {
        float w=(dx?fx:1-fx)*(dy?fy:1-fy)*(dz?fz:1-fz);
        auto const& c=cells_[index(x+dx,y+dy,z+dz)];
        for(int k=0;k<5;++k) q[k]+=c[k]*w;
    }
    return q;
}
void Flow::seedTracers(int count) {
    if(count<0 || count>2000000) throw std::invalid_argument("Tracer count must be between zero and two million.");
    std::vector<double> cdf(cells_.size()); double total=0;
    float background=initial_.cosmology.enabled?0.f:.003f*initial_.gasDensity;
    for(std::size_t i=0;i<cells_.size();++i) { total+=std::max(0.f,cells_[i][0]-background); cdf[i]=total; }
    std::mt19937 rng(seed_+71);
    auto random=[&] { return static_cast<float>(rng()>>8)*(1.f/16777216.f); };
    tracers_.resize(count);
    for(auto& tracer:tracers_) {
        int indexCell=static_cast<int>(std::lower_bound(cdf.begin(),cdf.end(),random()*total)-cdf.begin());
        int x=indexCell%n_,y=(indexCell/n_)%n_,z=indexCell/(n_*n_);
        tracer.position=position(x,y,z)+Vector{random()-.5f,random()-.5f,random()-.5f}*h_;
    }
    moveTracers(0);
}
void Flow::moveTracers(float dt) {
    #pragma omp parallel for schedule(static)
    for(int i=0;i<static_cast<int>(tracers_.size());++i) {
        auto& tracer=tracers_[i];
        Cell q=sample(tracer.position);
        Vector velocity{q[1]/q[0],q[2]/q[0],q[3]/q[0]};
        tracer.position=tracer.position+velocity*dt;
        float side=boxSize(),half=side*.5f;
        tracer.position.x-=side*std::floor((tracer.position.x+half)/side);
        tracer.position.y-=side*std::floor((tracer.position.y+half)/side);
        tracer.position.z-=side*std::floor((tracer.position.z+half)/side);
        tracer.density=q[0]; tracer.velocity=velocity; tracer.speed=length(velocity); tracer.temperature=pressure(q,numericalScale_)/q[0];
    }
}
void Flow::burst() {
    validateParameters();
    if(initial_.cosmology.enabled) return; // No black-hole/star formation model in cosmology mode.
    if(feedback<=0) return;
    #pragma omp parallel for schedule(static)
    for(int z=0;z<n_;++z) for(int y=0;y<n_;++y) for(int x=0;x<n_;++x) {
        Vector p=position(x,y,z);
        Cell& q=cells_[index(x,y,z)];
        for(int j=0;j<2;++j) {
            Vector d=p-nodes_[j].position;
            float r=length(d);
            if(r>2.263f*burstRadius) continue;
            Vector radial=normalized(d),axis=normalized(Vector{.25f,1.f,.35f});
            float shape=(1.f-collimation)+collimation*std::pow(std::abs(dot(radial,axis)),4.f);
            float envelope=std::exp(-r*r/(burstRadius*burstRadius))*shape*feedback;
            float before=.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0];
            Vector dv=radial*(burstKick*envelope);
            q[1]+=q[0]*dv.x; q[2]+=q[0]*dv.y; q[3]+=q[0]*dv.z;
            float after=.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0];
            q[4]+=after-before+q[0]*burstHeat*envelope;
        }
    }
}
Statistics Flow::statistics() const {
    Statistics s; s.minDensity=std::numeric_limits<float>::max(); s.minPressure=s.minDensity;
    for(auto const& q:cells_) {
        for(float v:q) if(!std::isfinite(v)) s.finite=false;
        s.mass+=q[0]*static_cast<double>(h_*h_*h_); s.energy+=q[4]*static_cast<double>(h_*h_*h_);
        s.minDensity=std::min(s.minDensity,q[0]); s.minPressure=std::min(s.minPressure,(gamma-1.f)*(q[4]-.5f*(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0]));
        s.maxSpeed=std::max(s.maxSpeed,std::sqrt(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0]);
    }
    return s;
}
std::vector<float> Flow::volume() const {
    std::vector<float> result(cells_.size()*4);
    #pragma omp parallel for schedule(static)
    for(int z=0;z<n_;++z) for(int y=0;y<n_;++y) for(int x=0;x<n_;++x) {
        int i=index(x,y,z); auto const& q=cells_[i];
        float densityScale=initial_.cosmology.enabled?.08f/initial_.cosmology.omegaB:1.f;
        float velocityScale=initial_.cosmology.enabled?universe_.physicalVelocityUnit()/1500.f:1.f;
        result[4*i]=q[0]*densityScale;
        result[4*i+1]=std::sqrt(q[1]*q[1]+q[2]*q[2]+q[3]*q[3])/q[0]*velocityScale;
        result[4*i+2]=pressure(q,numericalScale_)/q[0]*velocityScale*velocityScale;
        float divergence=0;
        for(int axis=0;axis<3;++axis) {
            int dx=axis==0,dy=axis==1,dz=axis==2;
            auto const& a=cells_[index(x-dx,y-dy,z-dz)]; auto const& b=cells_[index(x+dx,y+dy,z+dz)];
            divergence+=(b[axis+1]/b[0]-a[axis+1]/a[0])/(2.f*h_);
        }
        result[4*i+3]=std::max(0.f,-divergence);
    }
    return result;
}
}
