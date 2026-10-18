#pragma once
namespace blackhole_view {
inline constexpr char kerrCode[] = R"GLSL(
// Past-directed Kerr null rays: E=-1, conserved Lz and Carter Q.
// Mino time separates radial motion; Cartesian angular vectors avoid poles.
struct KerrState { vec2 radial; vec3 n; vec3 angular; float delay; };
KerrState kerrAdd(KerrState s,KerrState d,float h) {
    return KerrState(s.radial+h*d.radial,s.n+h*d.n,s.angular+h*d.angular,s.delay+h*d.delay);
}
KerrState kerrRhs(KerrState s,float l,float q) {
    float a=spin,u=s.radial.x,k=(l+a)*(l+a)+q;
    float delta=1.0-2.0*u+a*a*u*u,p=1.0+(a*a+a*l)*u*u;
    float drag=-a*(p/delta-1.0);
    vec3 axis=vec3(0,0,1);
    return KerrState(vec2(s.radial.y,(a*a-l*l-q)*u+3.0*k*u*u-2.0*a*a*q*u*u*u),
        cross(s.angular,s.n)+drag*cross(axis,s.n),
        a*a*s.n.z*cross(s.n,axis)+drag*cross(axis,s.angular),
        (1.0+a*a*u*u)*p/(max(u*u,1e-12)*delta)-a*l-a*a*(1.0-s.n.z*s.n.z));
}
KerrState kerrStep(KerrState s,float l,float q,float h) {
    KerrState a=kerrRhs(s,l,q),b=kerrRhs(kerrAdd(s,a,h*.5),l,q);
    KerrState c=kerrRhs(kerrAdd(s,b,h*.5),l,q),d=kerrRhs(kerrAdd(s,c,h),l,q);
    return kerrAdd(kerrAdd(kerrAdd(kerrAdd(s,a,h/6.0),b,h/3.0),c,h/3.0),d,h/6.0);
}
vec3 traceKerrRay(vec2 pixel) {
    vec2 screen=(2.0*pixel/resolution-1.0)*vec2(resolution.x/resolution.y,1)*tan(fov*.5);
    vec3 local=normalize(viewForward+screen.x*viewRight+screen.y*viewUp);
    float r=distanceRg;
    vec3 n=outward.zxy,theta=-upAxis.zxy,phiAxis=rightAxis.zxy;
    float st2=max(dot(n.xy,n.xy),1e-8),sigma=r*r+spin*spin*n.z*n.z;
    float delta=r*r-2.0*r+spin*spin,f=1.0-2.0*r/sigma;
    float gtphi=-2.0*spin*r*st2/sigma;
    float gphi=(r*r+spin*spin+2.0*spin*spin*r*st2/sigma)*st2;
    float l,pt,radial;
    if(r>=6.0) {
        l=-gtphi/f+local.x*sqrt((gphi+gtphi*gtphi/f)/f);
        pt=-local.y*sqrt(sigma/f);
        radial=local.z*sqrt(sigma*delta/f)/(r*r);
    } else {
        // A static observer is impossible in the ergosphere. Smoothly join the
        // distant static frame to the locally nonrotating (ZAMO) frame by r=3.
        float area=(r*r+spin*spin)*(r*r+spin*spin)-spin*spin*delta*st2;
        float alpha=sqrt(sigma*delta/area),omega=2.0*spin*r/area;
        float v=-smoothstep(3.0,6.0,r)*omega*sqrt(gphi)/alpha;
        float gamma=inversesqrt(1.0-v*v);
        float azimuth=gamma*(local.x-v);
        observerEnergy=alpha*gamma*(1.0-v*local.x)-omega*sqrt(gphi)*azimuth;
        // Nonpositive Killing energy cannot connect to the sky or our disk,
        // whose ISCO is outside the ergosphere for the supported spins <= .9.
        if(observerEnergy<=1e-7)return vec3(0);
        l=sqrt(gphi)*azimuth/observerEnergy;
        pt=-local.y*sqrt(sigma)/observerEnergy;
        radial=local.z*sqrt(sigma*delta)/(observerEnergy*r*r);
    }
    float q=pt*pt+n.z*n.z*(l*l/st2-spin*spin);
    KerrState state=KerrState(vec2(1.0/r,radial),n,
        pt*phiAxis-l/sqrt(st2)*theta,0.0);
    float horizon=1.0+sqrt(1.0-spin*spin),capture=1.0/(horizon+.005);
    bool volume=volumeActive();vec3 light=vec3(0);float transmission=1.0;
    int skySteps=0;
    for(int i=0;i<3072;++i) {
        float u=state.radial.x,du=state.radial.y;
        float h=stepSize/max(1.0,length(state.angular));
        h=min(h,.08*max(u,1e-5)/max(abs(du),1e-9));
        if(du>0.0) h=min(h,.25*(1.0/horizon-u)/du);
        if(volume) {
            vec3 angularRate=kerrRhs(state,l,q).n;
            h=volumeStep(1.0/u,state.n.z,-du/(u*u),length(angularRate),angularRate.z,h);
        }
        KerrState next=kerrStep(state,l,q,h);
        if(volume && volumeNear(1.0/u,state.n.z)) {
            KerrState mid=kerrStep(state,l,q,h*.5);
            float r=1.0/mid.radial.x,sigma=r*r+spin*spin*mid.n.z*mid.n.z;
            volumeSample(normalize(mid.n).yzx,r,l,-mid.radial.y*r*r/(r*r-2.0*r+spin*spin),
                mid.delay,h*sigma,light,transmission);
            if(transmission<.001) {
                if(!needsSkyContinuation(light)) return light;
                state=next;skySteps=3071-i;break;
            }
        }
        if(!volume && diskEnabled!=0 && state.n.z*next.n.z<0.0) {
            float lo=0.0,hi=h;
            // Root finding resolves the razor-thin disk between integration steps.
            for(int j=0;j<12;++j) {
                float mid=(lo+hi)*.5;
                if(state.n.z*kerrStep(state,l,q,mid).n.z>0.0) lo=mid; else hi=mid;
            }
            KerrState hit=kerrStep(state,l,q,(lo+hi)*.5);
            float hitRadius=1.0/hit.radial.x;
            if(hitRadius>=innerRadius && hitRadius<=outerRadius) {
                if(diagnostic==2) return vec3(hitRadius,hit.delay,diskFrequency(hitRadius,l));
                if(diagnostic!=0) return vec3(1,0,0);
                float u=hit.radial.x;
                return disk(hit.n.yzx,hitRadius,l,hit.delay,-hit.radial.y/(1.0-2.0*u+spin*spin*u*u));
            }
        }
        state=next;
        if(state.radial.x>=capture) return light;
        if(state.radial.x<1e-4 && state.radial.y<0.0) {
            vec3 escaped=normalize(state.n+cross(state.angular,state.n)*state.radial.x);
            return diagnostic!=0?vec3(1):compositeDiskSky(light,transmission,escaped.yzx);
        }
        if(any(isnan(state.radial)) || any(isinf(state.radial))) break;
    }
    // Finish dim opaque rays without the gas sampler in the loop. Keeping this
    // separate preserves uniform control flow in the expensive volume march.
    for(int i=0;i<skySteps;++i) {
        if(state.radial.x>=capture) return light;
        if(state.radial.x<1e-4 && state.radial.y<0.0) {
            vec3 escaped=normalize(state.n+cross(state.angular,state.n)*state.radial.x);
            return compositeDiskSky(light,transmission,escaped.yzx);
        }
        float u=state.radial.x,du=state.radial.y;
        float h=stepSize/max(1.0,length(state.angular));
        h=min(h,.08*max(u,1e-5)/max(abs(du),1e-9));
        if(du>0.0) h=min(h,.25*(1.0/horizon-u)/du);
        state=kerrStep(state,l,q,h);
        if(any(isnan(state.radial)) || any(isinf(state.radial))) break;
    }
    return diagnostic!=0?vec3(1,0,1):light;
}
)GLSL";
}
