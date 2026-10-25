// Educational surface/radiation models. No stellar interior or viscous-disk PDE solver.
#include "variable.hpp"
#include "vendor/src/photometry/binary.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <functional>

namespace variable {
constexpr double pi=photometry::pi, sigma=5.670374419e-8, rs=6.957e8, pc=3.0856775814913673e16;
double dot(V a,V b){return a.x*b.x+a.y*b.y+a.z*b.z;}
V cross(V a,V b){return{a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
double length(V a){return std::sqrt(dot(a,a));}
V unit(V a){return a*(1/std::max(1e-30,length(a)));}
V orient(V a,double phase,double inclination,double node){
    double c=std::cos(phase),s=std::sin(phase),i=inclination*pi/180,n=node*pi/180;
    double u=a.x*c-a.y*s,v=a.x*s+a.y*c;
    V b{u,v*std::cos(i)-a.z*std::sin(i),v*std::sin(i)+a.z*std::cos(i)};
    return{b.x*std::cos(n)-b.y*std::sin(n),b.x*std::sin(n)+b.y*std::cos(n),b.z};
}
void sphere(Mesh& mesh,V center,int lat,int lon,const std::function<Vertex(V)>& fn){
    int offset=int(mesh.v.size());
    for(int i=0;i<=lat;++i)for(int j=0;j<=lon;++j){
        double t=pi*i/lat,f=2*pi*j/lon; V n{std::sin(t)*std::cos(f),std::sin(t)*std::sin(f),std::cos(t)};
        auto v=fn(n);v.p=v.p+center;mesh.v.push_back(v);
    }
    for(int i=0;i<lat;++i)for(int j=0;j<lon;++j){
        int a=offset+i*(lon+1)+j,b=a+lon+1;
        if(i>0)mesh.faces.push_back({a,b,a+1});
        if(i<lat-1)mesh.faces.push_back({a+1,b,b+1});
    }
}
Model::Model(int f,const double* params,const double* extras,int quality):family(f),resolution(quality){
    if(f<1||f>16||quality<64||quality>512)throw std::runtime_error("Unknown variable-star model or integration resolution.");
    std::copy_n(params,17,p.begin());std::copy_n(extras,ExtraCount,x.begin());
    for(double v:p)if(!std::isfinite(v))throw std::runtime_error("Non-finite model parameter.");
    for(double v:x)if(!std::isfinite(v))throw std::runtime_error("Non-finite variability parameter.");
    if(p[0]<=0||p[1]<=0||p[2]<500||p[4]<=0||p[5]<=0||p[6]<500||p[8]<=0||p[13]<=0||p[14]<0||p[14]>=1||p[15]<0||p[16]<0||p[16]>3||p[16]!=int(p[16])||x[Period]<=0||x[Rise]<=0||x[Decay]<=0||x[RadiusAmp]<0||x[RadiusAmp]>=1||x[TempAmp]<0||x[TempAmp]>=1)
        throw std::runtime_error("Invalid physical model parameter.");
    for(int j=0;j<1024;++j){double t=500*std::exp(j*std::log(1000.)/1023); spectrum[j]=std::log(photometry::surfaceFlux({1,1,t,0},photometry::Band(int(p[16]))));}
    extent=p[1]*1.65*(1+x[RadiusAmp]);
    if(family<=2){base=rocheMesh();extent=0;for(auto v:base.v)extent=std::max(extent,length(v.p)*1.12);}
    if(family==14)extent=p[8]*.78;
    if(family==15)extent=p[1]*std::sqrt(1+x[EventAmp])/(.35*.35)*1.35;
    auto baseline=raw(0,true);reference=baseline[0]+baseline[1];
    if(!(reference>0)||!std::isfinite(reference))throw std::runtime_error("The reference luminosity is invalid.");
}
double Model::surface(double temperature)const{
    if(int(p[16])==0)return sigma*std::pow(temperature,4);
    double u=std::clamp(std::log(temperature/500.)/std::log(1000.)*1023,0.,1023.);
    int i=std::min(1022,int(u));return std::exp(spectrum[i]*(1-(u-i))+spectrum[i+1]*(u-i));
}
double Model::omega(double a,double b,double c)const{
    double q=p[4]/p[0];return 1/std::sqrt(a*a+b*b+c*c)+q/std::sqrt((a-1)*(a-1)+b*b+c*c)+.5*(1+q)*(a*a+b*b)-q*a;
}
V Model::gradient(V v)const{
    double q=p[4]/p[0],r=std::pow(length(v),3),s=std::pow(length(v-V{1,0,0}),3);
    return{-v.x/r-q*(v.x-1)/s+(1+q)*v.x-q,-v.y/r-q*v.y/s+(1+q)*v.y,-v.z/r-q*v.z/s};
}
double Model::root(double lo,double hi)const{
    double sign=gradient({lo,0,0}).x;
    for(int i=0;i<70;++i){double mid=(lo+hi)*.5;if(gradient({mid,0,0}).x*sign>0)lo=mid;else hi=mid;}return(lo+hi)*.5;
}
double Model::radialRoot(V c,V n,double target,double cap)const{
    double lo=1e-7,hi=lo;
    for(int k=1;k<=240;++k){hi=cap*k/240;auto v=c+n*hi;if(omega(v.x,v.y,v.z)<=target)break;lo=hi;}
    if(lo==hi)return cap; // The L1-facing limiting ray of a critically filled lobe.
    for(int k=0;k<42;++k){double mid=(lo+hi)*.5;auto v=c+n*mid;if(omega(v.x,v.y,v.z)>target)lo=mid;else hi=mid;}
    return(lo+hi)*.5;
}
Mesh Model::rocheMesh(){
    Mesh mesh;double q=p[4]/p[0],bary=q/(1+q);l1=root(1e-6,1-1e-6);
    double inner=omega(l1,0,0),outer=std::max(omega(root(1+1e-6,4),0,0),omega(root(-4,-1e-6),0,0));
    potential=family==2?inner-x[Fillout]*(inner-outer):inner;
    double pole[2],gp[2];
    for(int j=0;j<2;++j){pole[j]=radialRoot({double(j),0,0},{0,0,1},inner);
        if(family==1)pole[j]*=x[j==0?FillA:FillB];
        gp[j]=length(gradient({double(j),0,pole[j]}));}
    auto vertex=[&](V point,int j){
        V normal=unit(gradient(point)*-1);double g=length(gradient(point));
        double temperature=p[j*4+2]*std::pow(std::max(.015,g/gp[j]),x[GravityBeta]);
        if(family==1&&x[Albedo]>0){
            V to=V{double(1-j),0,0}-point;double d=length(to),cosine=std::max(0.,dot(normal,unit(to)));
            temperature=std::pow(std::pow(temperature,4)+x[Albedo]*std::pow(p[(1-j)*4+2],4)*pole[1-j]*pole[1-j]/(d*d)*cosine, .25);
        }
        return Vertex{(point-V{bary,0,0})*p[8],normal,temperature,p[j*4+3],surface(temperature),j};
    };
    if(family==2){
        sphere(mesh,{},48,96,[&](V n){V c{l1,0,0};auto point=c+n*radialRoot(c,n,potential);return vertex(point,point.x<l1?0:1);});
    }else for(int j=0;j<2;++j){
        double target=omega(j,0,pole[j]);
        sphere(mesh,{},32,64,[&](V n){double cap=2.;if(j==0&&n.x>0)cap=std::min(cap,l1/n.x);if(j==1&&n.x<0)cap=std::min(cap,(l1-1)/n.x);
            V c{double(j),0,0};return vertex(c+n*radialRoot(c,n,target,cap),j);});
    }
    // Volume-equivalent radii for the UI, including each half of the common envelope.
    double volumes[2]={0,0};
    for(auto face:mesh.faces){auto a=mesh.v[face[0]],b=mesh.v[face[1]],c=mesh.v[face[2]];int j=a.component;
        V center{(j-bary)*p[8],0,0};volumes[j]+=dot(a.p-center,cross(b.p-center,c.p-center))/6;}
    volumeRadiusA=std::cbrt(std::abs(volumes[0])*3/(4*pi));volumeRadiusB=std::cbrt(std::abs(volumes[1])*3/(4*pi));
    return mesh;
}
double Model::opacity(double t)const{
    if(family!=6||int(p[16])==0)return 1;
    double weight=int(p[16])==1?.7:int(p[16])==2?1.:.45;
    return std::exp(-std::min(40.,x[Molecular]*weight*std::pow(3000/t,8)));
}
std::array<double,2> Model::pulse(double cycles)const{
    auto wave=[&](double t){double a=2*pi*t;
        if(family==11)return(std::cos(2*a)+x[Alternation]*std::cos(a))/(1+x[Alternation]);
        if(family==7||family==8||family==12)return(std::cos(a)+x[SecondaryAmp]*std::cos(a*x[FrequencyRatio]+.7))/(1+x[SecondaryAmp]);
        return(std::cos(a)+x[Harmonic]*std::cos(2*a+1.1))/(1+x[Harmonic]);};
    return{p[1]*(1+x[RadiusAmp]*wave(cycles+x[Lag])),p[2]*(1+x[TempAmp]*wave(cycles))};
}
double Model::event(double days)const{
    double d=days-x[EventStart];if(d<0)return 0;
    if(family==14&&x[Recurrence]>0)d=std::fmod(d,x[Recurrence]);
    double peak=x[Rise]*std::log1p(x[Decay]/x[Rise]);
    double norm=(-std::expm1(-peak/x[Rise]))*std::exp(-peak/x[Decay]);
    return std::clamp((-std::expm1(-d/x[Rise]))*std::exp(-d/x[Decay])/norm,0.,1.);
}
Mesh Model::dynamicMesh(double cycles,bool quiet){
    Mesh mesh;double phase=2*pi*cycles;
    auto rt=pulse(quiet?0:cycles);if(quiet)rt={p[1],p[2]};
    if(family==15){double e=quiet?0:event(cycles*x[Period]),ratio=.35+.65*std::pow(1-e,.4);rt={p[1]*std::sqrt(1+x[EventAmp]*e)/(ratio*ratio),p[2]*ratio};}
    int stars=family==14?2:1;
    double orbitalPeriod=2*pi*std::sqrt(std::pow(p[8]*rs,3)/(6.67430e-11*1.98847e30*(p[0]+p[4])))/86400;
    double orbital=2*pi*cycles*x[Period]/orbitalPeriod+pi/2;
    for(int j=0;j<stars;++j){
        V center{};if(stars==2)center={(j-p[4]/(p[0]+p[4]))*p[8],0,0};
        sphere(mesh,{},stars==1?48:32,stars==1?96:64,[&](V n){
            double r=stars==2?p[j*4+1]:rt[0],t=stars==2?p[j*4+2]:rt[1];
            if(family==9||family==10){
                double a=quiet?0:std::cos(phase)*(n.x*n.x-n.y*n.y)+x[SecondaryAmp]*std::cos(phase*x[FrequencyRatio]+.7)*2*n.y*n.z;
                a/=1+x[SecondaryAmp];r=p[1]*(1+x[RadiusAmp]*a);t=p[2]*(1+x[TempAmp]*a);
            }
            if(family==13){
                double latitude=x[SpotLatitude]*pi/180;V spot{std::cos(latitude),0,std::sin(latitude)};
                double angle=std::acos(std::clamp(dot(n,spot),-1.,1.))*180/pi;
                double coverage=quiet?0:std::clamp((x[SpotSize]-angle)/2+.5,0.,1.);
                t=p[2]*(1-coverage*(1-x[SpotTemp]));r=p[1];
            }
            if(family==16){
                double angle=std::acos(std::clamp(n.z,-1.,1.))*180/pi;
                double coverage=std::clamp((x[SpotSize]-angle)/2+.5,0.,1.);
                t=p[2]*std::pow(1+x[EventAmp]*(quiet?0:event(cycles*x[Period]))*coverage,.25);r=p[1];
            }
            V pos=n*r+center,normal=n;
            double rotation=family==14?orbital:family==13?phase:0;
            double inclination=family==16?0:p[10];
            pos=orient(pos,rotation,inclination,p[12]);normal=orient(normal,rotation,inclination,p[12]);
            return Vertex{pos,normal,t,p[j*4+3],surface(t)*opacity(t),j};
        });
    }
    if(family==14){
        // Optically thick, steady annuli responding to a prescribed accretion history.
        double mdot=x[Accretion]*1.98847e30/(365.25*86400)*(1+x[EventAmp]*(quiet?0:event(cycles*x[Period])));
        double inner=p[1],outer=x[DiskRadius],bary=p[4]/(p[0]+p[4]);
        for(int side:{-1,1}){int offset=int(mesh.v.size());
            for(int i=0;i<=32;++i)for(int j=0;j<=96;++j){
                double r=inner*std::pow(outer/inner,i/32.),a=2*pi*j/96;
                double t=std::pow(std::max(1e12,3*6.67430e-11*p[0]*1.98847e30*mdot/(8*pi*sigma*std::pow(r*rs,3))*(1-std::sqrt(inner/r))),.25);
                V v{r*std::cos(a)-bary*p[8],r*std::sin(a),side*.002*outer};
                mesh.v.push_back({orient(v,orbital,p[10],p[12]),orient({0,0,double(side)},orbital,p[10],p[12]),t,0,surface(t),0});
            }
            for(int i=0;i<32;++i)for(int j=0;j<96;++j){int a=offset+i*97+j,b=a+97;
                mesh.faces.push_back(side>0?std::array<int,3>{a,b,a+1}:std::array<int,3>{a,a+1,b});
                mesh.faces.push_back(side>0?std::array<int,3>{a+1,b,b+1}:std::array<int,3>{a+1,b+1,b});}
        }
    }
    return mesh;
}
Mesh Model::frame(double cycles){
    if(family>2)return dynamicMesh(cycles);
    auto mesh=base;for(auto& v:mesh.v){v.p=orient(v.p,2*pi*cycles+p[11]*pi/180,p[10],p[12]);v.n=orient(v.n,2*pi*cycles+p[11]*pi/180,p[10],p[12]);}return mesh;
}
std::array<double,2> Model::raw(double cycles,bool quiet){
    if((family>=3&&family<=8)||family==11||family==12||family==15){
        auto rt=quiet?std::array<double,2>{p[1],p[2]}:pulse(cycles);
        if(family==15&&!quiet){double e=event(cycles*x[Period]),ratio=.35+.65*std::pow(1-e,.4);rt={p[1]*std::sqrt(1+x[EventAmp]*e)/(ratio*ratio),p[2]*ratio};}
        return{surface(rt[1])*rt[0]*rt[0]*opacity(rt[1]),0};
    }
    auto mesh=family<=2?frame(cycles):dynamicMesh(cycles,quiet);std::array<double,2> flux{0,0};
    if(family>2&&family!=14){
        for(auto f:mesh.faces){auto a=mesh.v[f[0]],b=mesh.v[f[1]],c=mesh.v[f[2]];
            V area=cross(b.p-a.p,c.p-a.p)*.5;double mu=unit(a.n+b.n+c.n).z;
            if(mu<=0)continue;double projected=std::abs(area.z);
            flux[0]+=(a.flux+b.flux+c.flux)/3*projected*(1-a.limb+a.limb*mu)/(pi*(1-a.limb/3));
        }return flux;
    }
    // Orthographic z-buffer integration resolves self-occultation and companion eclipses.
    int n=resolution;double span=family<=2?2*extent:p[8]*1.65,scale=n/span,area=1/(scale*scale);
    std::vector<double> depth(n*n,-1e100),light(n*n,0);std::vector<int> component(n*n,0);
    for(auto f:mesh.faces){auto a=mesh.v[f[0]],b=mesh.v[f[1]],c=mesh.v[f[2]];
        if(a.n.z+b.n.z+c.n.z<=0)continue;
        double ax=a.p.x*scale+n*.5,ay=a.p.y*scale+n*.5,bx=b.p.x*scale+n*.5,by=b.p.y*scale+n*.5,cx=c.p.x*scale+n*.5,cy=c.p.y*scale+n*.5;
        double den=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy);if(std::abs(den)<1e-12)continue;
        int x0=std::max(0,int(std::floor(std::min({ax,bx,cx})))),x1=std::min(n-1,int(std::ceil(std::max({ax,bx,cx}))));
        int y0=std::max(0,int(std::floor(std::min({ay,by,cy})))),y1=std::min(n-1,int(std::ceil(std::max({ay,by,cy}))));
        for(int y=y0;y<=y1;++y)for(int x0p=x0;x0p<=x1;++x0p){
            double u=((by-cy)*(x0p+.5-cx)+(cx-bx)*(y+.5-cy))/den,v=((cy-ay)*(x0p+.5-cx)+(ax-cx)*(y+.5-cy))/den,w=1-u-v;
            if(std::min({u,v,w})<0)continue;double z=u*a.p.z+v*b.p.z+w*c.p.z;int k=y*n+x0p;if(z<=depth[k])continue;
            depth[k]=z;double mu=std::max(0.,unit(a.n*u+b.n*v+c.n*w).z),limb=a.limb*u+b.limb*v+c.limb*w;
            light[k]=(a.flux*u+b.flux*v+c.flux*w)*(1-limb+limb*mu)/(pi*(1-limb/3));component[k]=a.component;
        }
    }
    for(int k=0;k<n*n;++k)flux[component[k]]+=light[k]*area;return flux;
}
std::array<double,5> Model::observe(double cycles){
    int count=p[15]>0?25:1;std::array<double,2> sum{0,0};
    for(int k=0;k<count;++k){double phase=cycles+((k+.5)/count-.5)*p[15]/(x[Period]*86400);auto f=raw(phase);sum[0]+=f[0]/count;sum[1]+=f[1]/count;}
    double third=reference*p[14]/(1-p[14]),total=sum[0]+sum[1]+third,base=reference+third,flux=std::max(1e-15,total/base);
    return{flux,-2.5*std::log10(flux),total*rs*rs/std::pow(p[13]*pc,2),sum[0]/base,sum[1]/base};
}
}
