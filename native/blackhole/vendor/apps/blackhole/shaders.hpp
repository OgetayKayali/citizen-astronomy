#pragma once
#include <string>
#include "kerr_shaders.hpp"
#include "volume_shaders.hpp"

namespace blackhole_view {
inline constexpr char vertex[] = R"GLSL(#version 330 core
out vec2 uv;
void main() {
    vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);
    uv=p; gl_Position=vec4(p*2.0-1.0,0,1);
}
)GLSL";

inline const std::string rayFragment = std::string(R"GLSL(#version 330 core
in vec2 uv;
out vec4 color;
uniform vec2 resolution;
uniform vec3 outward;
uniform vec3 rightAxis;
uniform vec3 upAxis;
// View orientation in the observer's local (right, up, inward) tetrad.
// Position-dependent orbital/metric axes above remain independent of aim.
uniform vec3 viewForward,viewRight,viewUp;
uniform float distanceRg, fov, outerRadius, timeRg, temperature;
uniform float stepSize;
uniform float spin, innerRadius, cameraLapse;
float observerEnergy;
uniform int metric;
uniform sampler3D fluidHistory,tracerHistory;
uniform sampler2D radialProfile;
uniform sampler2D milkyWayTexture;
uniform int milkyWayEnabled,cinematicGrade,particlesEnabled;
uniform float skyBrightness,skyRotation,particleStrength,spaceBlend,skyWidth;
uniform float particleBrightness,particleSize,particleSizeVariety,particleLifetime,particleWhiteness;
uniform float skyContrast,skySaturation,skyBlackPoint,skyWarmth;
uniform int starsEnabled;
uniform float starBrightness,starDensity,starSize,starColor,starFollow;
uniform int starCatalog,starGrid;
uniform float starMagnitudeLimit;
uniform sampler2D starCells,starData,starColors;
uniform float detailContrast;
uniform float outerFade;
uniform int fluidModel,fluidEnabled,fluidView,volumeModel;
uniform float diskThickness,diskOpacity,flowRadialCells;
uniform float historyOldest,historyNewest,flowInner,flowOuter;
uniform int physical, backgroundMode, diskEnabled, delays, samples, diagnostic;
const float PI=3.141592653589793;

// Rendering envelope inside the finite gas boundary. The squared quintic
// has zero slope/curvature at both ends and suppresses a tone-mapped rim.
float outerEnvelope(float r,float inner,float outer) {
    float width=max(.001,outerFade*(outer-inner));
    float x=clamp((outer-r)/width,0.0,1.0);
    float fade=x*x*x*(x*(6.0*x-15.0)+10.0);
    return fade*fade;
}

// Null geodesics in their orbital plane. u=1/r in units GM/c^2.
// (du/dphi)^2 + u^2 - 2u^3 = 1/b^2; u''=-u+3u^2.
vec3 rhs(vec3 s,float b) {
    return vec3(s.y,-s.x+3.0*s.x*s.x,
        1.0/(b*max(s.x*s.x,1e-12)*max(1.0-2.0*s.x,1e-6)));
}
vec3 rk4(vec3 s,float b,float h) {
    vec3 a=rhs(s,b),c=rhs(s+h*.5*a,b),d=rhs(s+h*.5*c,b),e=rhs(s+h*d,b);
    return s+h*(a+2.0*c+2.0*d+e)/6.0;
}
float hash(vec3 p) {
    p=fract(p*.1031); p+=dot(p,p.yzx+33.33);
    return fract((p.x+p.y)*p.z);
}
float noise(vec3 p) {
    vec3 i=floor(p),f=fract(p); f=f*f*(3.0-2.0*f);
    return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),
                   mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),
               mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),
                   mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);
}
)GLSL" /* CAst: MSVC caps a single literal at 16 KB */ R"GLSL(
// CAst: Milky Way grading. Black level removes the panorama's noise floor.
// Contrast is a power curve on luminance around a mid-grey pivot, so hue is
// kept; warmth shifts color balance at constant luminance.
vec3 gradeSky(vec3 c) {
    const vec3 luma=vec3(.2126,.7152,.0722);
    c=max(c-skyBlackPoint,0.0)/max(1.0-skyBlackPoint,1e-3);
    float l=dot(c,luma);
    float graded=.25*pow(l/.25,skyContrast);
    c*=graded/max(l,1e-6);
    c=max(mix(vec3(graded),c,skySaturation),0.0);
    vec3 warmth=vec3(1.0+.2*skyWarmth,1.0,1.0-.2*skyWarmth);
    return c*warmth/dot(warmth,luma);
}
// CAst: sky direction and visible fraction of the last escaped ray, recorded
// by sky() and compositeDiskSky() for the screen-space star pass in main().
vec3 skyDirection;
vec2 skyPanorama;
float skyWeight;
vec3 sky(vec3 d) {
    if(backgroundMode==2) return vec3(0);
    if(backgroundMode==1) {
        float longitude=atan(d.z,d.x),latitude=asin(clamp(d.y,-1.0,1.0));
        vec2 cell=vec2(longitude,latitude)*12.0/PI;
        vec2 edge=abs(fract(cell+.5)-.5);
        float line=1.0-smoothstep(.018,.042,min(edge.x,edge.y));
        float checker=mod(floor(cell.x)+floor(cell.y),2.0);
        return mix(vec3(.012,.024,.04),vec3(.045,.08,.11),checker)+line*vec3(.16,.32,.4);
    }
    if(milkyWayEnabled!=0) {
        // Rotate an actual all-sky panorama in world space, then sample using
        // the escaped ray direction: the same gravitational lens as the disk.
        float bend=acos(clamp(dot(d,-outward),-1.0,1.0));
        float c=cos(skyRotation),s=sin(skyRotation);
        d=vec3(c*d.x+s*d.z,d.y,-s*d.x+c*d.z);
        d=vec3(d.x,.906307*d.y+.422618*d.z,-.422618*d.y+.906307*d.z);
        // CAst: d is a galactic direction (l=-atan(z,x), b=asin(y)), the frame of
        // the original panorama. Sample CAst's equatorial (RA/Dec) Milky Way.
        vec3 g=vec3(d.x,-d.z,d.y);
        vec3 e=vec3(-.0548755604*g.x+.4941094279*g.y-.8676661490*g.z,
                    -.8734370902*g.x-.4448296300*g.y-.1980763734*g.z,
                    -.4838350155*g.x+.7469822445*g.y+.4559837762*g.z);
        vec2 p=vec2(fract(.5-atan(e.y,e.x)/(2.0*PI)),.5-asin(clamp(e.z,-1.0,1.0))/PI);
        // Explicit LOD avoids undefined derivatives inside divergent ray loops.
        float lod=max(0.0,log2(skyWidth*fov/(2.0*PI*resolution.y)));
        // Broaden the footprint where strong bending stretches individual stars.
        // This conservative artistic filter keeps the panorama from aliasing into rings.
        lod+=2.5*smoothstep(.1,1.0,bend);
        skyDirection=starCatalog!=0?e:d;skyPanorama=p;skyWeight=1.0;
        return gradeSky(textureLod(milkyWayTexture,p,lod).rgb)*skyBrightness;
    }
    // Procedural finite-sized stars on a cube sky; not a calibrated catalog.
    vec3 ad=abs(d); vec2 p; float face;
    if(ad.x>=ad.y && ad.x>=ad.z) { p=d.yz/ad.x; face=d.x>0.0?1.0:2.0; }
    else if(ad.y>=ad.z) { p=d.xz/ad.y; face=d.y>0.0?3.0:4.0; }
    else { p=d.xy/ad.z; face=d.z>0.0?5.0:6.0; }
    vec2 q=p*140.0,id=floor(q),f=fract(q);
    float h=hash(vec3(id,face));
    vec2 center=.2+.6*vec2(hash(vec3(id,face+9.0)),hash(vec3(id,face+17.0)));
    float star=exp(-dot(f-center,f-center)/(h>.993?.015:.004))*step(.967,h);
    float band=exp(-pow(dot(d,normalize(vec3(.2,1,.4)))*8.0,2.0));
    float cloud=noise(d*8.0)*noise(d*23.0);
    return vec3(.0015,.002,.004)+vec3(.013,.017,.025)*band*cloud
        +star*mix(vec3(.34,.49,.8),vec3(1.1,.9,.64),hash(vec3(id,face+5.0)));
}
vec3 blackbody(float kelvin) {
    // Three Planck wavelength samples (610,550,460nm), normalized for color.
    // This is an RGB approximation, not a spectral instrument response.
    vec3 wavelength=vec3(610,550,460);
    vec3 x=1.4387769e7/(wavelength*max(kelvin,500.0));
    vec3 spectrum=pow(vec3(550)/wavelength,vec3(5))/(exp(min(x,vec3(70)))-1.0);
    return spectrum/max(max(spectrum.r,spectrum.g),spectrum.b);
}
float diskFrequency(float r,float backwardLz) {
    float r32=pow(r,1.5),ut=(r32+spin)/sqrt(r*r*r-3.0*r*r+2.0*spin*r32);
    return 1.0/(observerEnergy*ut*(1.0+backwardLz/(r32+spin)));
}
void flowCoordinates(vec2 p,float emissionTime,out vec3 lowerPoint,out vec3 upperPoint,out float blend) {
    float t=clamp(emissionTime,historyOldest,historyNewest)/4.0;
    float lower=floor(t),upper=min(lower+1.0,historyNewest/4.0);
    lowerPoint=vec3(p,(mod(lower,129.0)+.5)/129.0);
    upperPoint=vec3(p,(mod(upper,129.0)+.5)/129.0);blend=fract(t);
    // Reconstruct at the emitting point by following gas motion to both stored
    // snapshots. Plain temporal blending smears inner orbits by many texels.
    vec4 predictor=mix(texture(fluidHistory,lowerPoint),texture(fluidHistory,upperPoint),blend);
    vec2 velocity=vec2(predictor.w/(2.0*PI),predictor.z/(flowOuter-flowInner));
    lowerPoint.xy-=velocity*((t-lower)*4.0);
    upperPoint.xy+=velocity*((upper-t)*4.0);
}
vec4 flowSample(vec2 p,float emissionTime) {
    vec3 lowerPoint,upperPoint;float blend;
    flowCoordinates(p,emissionTime,lowerPoint,upperPoint,blend);
    return mix(texture(fluidHistory,lowerPoint),texture(fluidHistory,upperPoint),blend);
}
// Photon frequency in a fluid frame with zero polar velocity. The gas dynamics
// remain approximate, but this normalization uses the local Kerr metric.
float fluidFrequency(vec4 gas,vec3 n,float r,float l,float pr) {
    float sigma=r*r+spin*spin*n.y*n.y,st2=1.0-n.y*n.y;
    float delta=r*r-2.0*r+spin*spin,omega=gas.w,vr=gas.z;
    float norm=1.0-2.0*r/sigma+4.0*spin*r*st2/sigma*omega
        -(r*r+spin*spin+2.0*spin*spin*r*st2/sigma)*st2*omega*omega-sigma/delta*vr*vr;
    return norm>0.0?(1.0+l*omega+pr*vr)/sqrt(norm):-1.0;
}
vec3 cinematicTint(float r) {
    float radial=clamp((r-innerRadius)/(outerRadius-innerRadius),0.0,1.0);
    float hot=exp(-4.5*radial);
    vec3 warm=mix(vec3(1.0,.105,.008),vec3(1.0,.40,.045),sqrt(hot));
    return mix(warm,vec3(1.0,.88,.36),hot*hot);
}
float cinematicGain(float r) {
    return .7+2.6*exp(-4.0*max(0.0,(r-innerRadius)/(outerRadius-innerRadius)));
}
// Shrink the gas layer as well as its surface brightness near the outer edge.
// A small numerical floor keeps integration well-conditioned; density and light
// vanish exactly at the boundary, so it cannot form a visible vertical wall.
float diskHeightTaper(float r) {
    return max(.04,sqrt(outerEnvelope(r,innerRadius,outerRadius)));
}
// CAst: flaring glints, like Iridium satellite flares. Each particle is about
// one pixel across at any camera distance, has its own relative size, and
// rides its orbit while it flares: a quick brighten to a peak, then a longer
// fade. Lifetimes are long-tailed so a few glints linger far longer than most;
// every flare reappears at a new spot in its orbiting cell. particleStrength
// sets how many cells flare, not how bright they are. Shape is an isotropic
// Gaussian in local cylindrical world coordinates; thin surfaces use
// height=1000. Purely decorative emissivity, not simulated matter.
float glintPixel() {
    return distanceRg*2.0*tan(fov*.5)/resolution.y;
}
float glintBase() {
    return max(.012,glintPixel()*.45)*particleSize;
}
float glintSize(float h) {
    return max(.3,mix(1.13,mix(.8,1.8,h*h),particleSizeVariety));
}
vec3 glintColor(vec3 tint) {
    return mix(tint,vec3(1.0,.93,.82)*max(max(tint.r,tint.g),tint.b),particleWhiteness);
}
// Smallest glint width; volumeStep resolves it along each ray.
// Widths stay above half a pixel so moving glints do not flicker between pixels.
float particleWidth(float r) {
    return max(glintBase()*glintSize(0.0),glintPixel()*.55);
}
// Bound on |height| of any glint's support at cylindrical radius r: centers
// lie within 2 scale heights of their row (rows are at most .1 from r and the
// taper decreases outward), widths are at most .16 of a .2 cell, support 2.5 widths.
float glintBand(float r) {
    return 2.0*diskThickness*(r+.1)*diskHeightTaper(r-.1)+.08;
}
float fineParticles(float r,float phi,float emissionTime,float worldHeight) {
    if(particlesEnabled==0) return 0.0;
    bool surface=worldHeight>100.0;
    if(!surface) r=sqrt(max(r*r-worldHeight*worldHeight,.01));
    float result=0.0;
    float pixel=glintPixel();
    float base=glintBase();
    for(int layer=0;layer<2;++layer) {
        float radialScale=layer==0?5.0:7.0;
        float cell=1.0/radialScale,row=floor(r*radialScale);
        float seed=float(layer)*71.0;
        float orbitRadius=(row+.5)*cell;
        float omega=1.0/(pow(orbitRadius,1.5)+spin);
        float columns=floor(2.0*PI*orbitRadius/cell);
        float phase=phi-emissionTime*omega/(2.0*PI);
        float q=fract(phase+hash(vec3(row,seed,3)))*columns;
        vec2 id=vec2(row,floor(q));
        // Lifetimes in r_g/c: most flares last a few, a long tail lasts ~20.
        float life=mix(2.5,22.0,pow(hash(vec3(id,seed+51.0)),2.5))*particleLifetime;
        float duty=mix(.35,.65,hash(vec3(id,seed+71.0)));
        float cycle=emissionTime*duty/life+hash(vec3(id,seed+61.0));
        float flashIndex=floor(cycle);
        float s=fract(cycle)/duty;
        if(s>=1.0) continue;
        float key=seed+flashIndex*13.17;
        if(hash(vec3(id,key))>=.3*particleStrength) continue;
        float peak=mix(.12,.3,hash(vec3(id,key+31.0)));
        float rise=smoothstep(0.0,peak,s);
        float flash=s<peak?rise*rise
            :exp(-3.5*(s-peak)/(1.0-peak))*(1.0-smoothstep(.8,1.0,s));
        float size=glintSize(hash(vec3(id,key+5.0)));
        flash*=mix(.6,1.4,hash(vec3(id,key+15.0)));
        vec2 center=.25+.5*vec2(hash(vec3(id,key+11.0)),hash(vec3(id,key+21.0)));
        vec2 delta=(vec2(fract(r*radialScale),fract(q))-center)*vec2(cell,2.0*PI*r/columns);
        float width=min(max(base*size,pixel*.55),cell*.16);
        float centerHeight=(hash(vec3(id,key+41.0))-.5)*4.0
            *diskThickness*orbitRadius*diskHeightTaper(orbitRadius);
        float dz=surface?0.0:worldHeight-centerHeight;
        float d2=(dot(delta,delta)+dz*dz)/(width*width);
        // Compact support keeps adjacent cells from clipping a visible Gaussian.
        float shape=exp(-.5*d2)*(1.0-smoothstep(4.0,6.25,d2));
        float visible=1.0-smoothstep(.6,1.4,pixel/cell);
        result+=shape*flash*.8*size*visible;
    }
    return result*particleBrightness*18.0;
}
)GLSL" /* CAst: MSVC caps a single literal at 16 KB */ R"GLSL(
vec3 fluidLight(vec4 gas,vec2 p,float emissionTime,float g,float opacity,float edge,float worldHeight) {
    float rho=max(gas.x,0.0),radiated=max(gas.y,0.0);
    float localTemperature=temperature*pow(max(radiated,.00001),.25);
    if(fluidView==1) return mix(vec3(.015,.04,.13),vec3(1.4,.55,.12),clamp(rho/1.8,0.0,1.0))*edge;
    if(fluidView==2) return blackbody(localTemperature)*pow(radiated,.4)*edge;
    if(g<=0.0) return vec3(4,0,4);
    float emission=radiated;
    if(physical==0 && detailContrast>1.0) {
        float t=clamp(emissionTime,historyOldest,historyNewest)/4.0;
        float lo=floor(t),hi=min(lo+1.0,historyNewest/4.0);
        float meanFlux=mix(texture(radialProfile,vec2(p.y,(mod(lo,129.0)+.5)/129.0)).r,
            texture(radialProfile,vec2(p.y,(mod(hi,129.0)+.5)/129.0)).r,fract(t));
        // Emphasize actual gas fluctuations around each ring's mean. This is
        // an explicit cinematic grading control, not extra simulated heating.
        emission=max(meanFlux,1e-6)*pow(radiated/max(meanFlux,1e-6),detailContrast);
    }
    vec3 tint=physical!=0?blackbody(localTemperature*g):mix(vec3(1,.19,.035),vec3(1,.8,.48),clamp(localTemperature/temperature,0.0,1.0));
    vec3 light=tint*(physical!=0?1.4*radiated*pow(g,4.0):.5*pow(emission,.85))*opacity*edge;
    if(physical==0 && fluidView==0) {
        float r=mix(flowInner,flowOuter,p.y);
        if(cinematicGrade!=0) { tint=cinematicTint(r);light=tint*.5*pow(emission,.85)*cinematicGain(r)*opacity*edge; }
        light+=glintColor(tint)*.65*pow(emission,.65)*fineParticles(r,p.x,emissionTime,worldHeight)*opacity*edge;
    }
    if(fluidView==3) {
        vec3 lowerPoint,upperPoint;float blend;
        flowCoordinates(p,emissionTime,lowerPoint,upperPoint,blend);
        float dots=mix(texture(tracerHistory,lowerPoint).r,texture(tracerHistory,upperPoint).r,blend);
        light=light*.35+vec3(.15,1.4,2.0)*dots*edge;
    }
    return light;
}
vec3 fluidDisk(vec3 n,float r,float backwardLz,float delay,float radialMomentum) {
    if(fluidEnabled==0 || r<flowInner || r>flowOuter) return vec3(0);
    vec2 p=vec2(atan(n.x,n.z)/(2.0*PI),(r-flowInner)/(flowOuter-flowInner));
    float emissionTime=timeRg-(delays!=0?delay:0.0);
    vec4 gas=flowSample(p,emissionTime);
    float edge=smoothstep(flowInner,flowInner+.15,r)*outerEnvelope(r,flowInner,flowOuter);
    float opacity=1.0-exp(-2.8*max(gas.x,0.0));
    float g=physical!=0?1.0/(observerEnergy*fluidFrequency(gas,n,r,backwardLz,radialMomentum)):1.0;
    return fluidLight(gas,p,emissionTime,g,opacity,edge,1000.0);
}
vec3 disk(vec3 n,float r,float backwardLz,float delay,float radialMomentum) {
    if(fluidModel!=0) return fluidDisk(n,r,backwardLz,delay,radialMomentum);
    float emissionTime=timeRg-(delays!=0?delay:0.0);
    float omega=1.0/(pow(r,1.5)+spin);
    float azimuth=atan(n.z,n.x)+emissionTime*omega;
    vec3 pattern=vec3(cos(azimuth)*5.0,sin(azimuth)*5.0,r*1.8);
    float clumps=noise(pattern)*.5+noise(pattern*2.7)*.32+noise(pattern*7.0)*.18;
    float bands=.72+.28*sin(r*8.0+5.0*noise(pattern*.45));
    float wisps=pow(max(.1,clumps),2.0)*3.0;
    float structure=(.18+wisps)*bands;
    float edges=smoothstep(innerRadius,innerRadius*1.108333,r)*outerEnvelope(r,innerRadius,outerRadius);
    float brightness=3.2*pow((innerRadius*4.0/3.0)/r,2.4)*edges*structure;
    if(physical==0) {
        vec3 tint=mix(vec3(1.0,.22,.035),vec3(1.0,.79,.4),clamp((20.0-r)/14.0,0.0,1.0));
        if(cinematicGrade!=0) { tint=cinematicTint(r);brightness*=cinematicGain(r); }
        return brightness*(tint+glintColor(tint)*fineParticles(r,atan(n.x,n.z)/(2.0*PI),emissionTime,1000.0));
    }
    // Circular geodesic emitter, static local observer at the camera.
    float g=diskFrequency(r,backwardLz);
    float peak=innerRadius*49.0/36.0;
    float maximum=(1.0-sqrt(innerRadius/peak))/pow(peak,3.0);
    float profile=pow(max((1.0-sqrt(innerRadius/r))/pow(r,3.0),0.0)/maximum,.25);
    return blackbody(temperature*profile*g)*brightness*pow(g,4.0);
}
)GLSL") + volumeCode + R"GLSL(
vec3 traceSchwarzschild(vec2 pixel) {
    vec2 screen=(2.0*pixel/resolution-1.0)*vec2(resolution.x/resolution.y,1);
    vec3 local=normalize(viewForward+tan(fov*.5)*(screen.x*viewRight+screen.y*viewUp));
    vec3 direction=local.x*rightAxis+local.y*upAxis-local.z*outward;
    float radial=dot(direction,outward);
    vec3 tangent=direction-radial*outward;
    float sine=length(tangent);
    bool volume=volumeActive();
    if(sine<1e-7) {
        if(!volume) return radial<0.0?vec3(0):sky(direction);
        tangent=rightAxis;sine=1e-7;
    } else tangent/=sine;
    float b=distanceRg*sine/sqrt(1.0-2.0/distanceRg);
    float normalY=cross(outward,tangent).y;
    vec3 state=vec3(1.0/distanceRg,-radial/b,0);
    float phi=0.0;
    // All equatorial intersections lie pi apart in the orbital plane.
    // Step exactly onto each crossing; never skip a thin disk between steps.
    float crossing=mod(atan(-outward.y,tangent.y),PI);
    if(crossing<1e-6) crossing+=PI;
    bool coplanar=abs(outward.y)+abs(tangent.y)<1e-7;
    vec3 light=vec3(0);float transmission=1.0;
    int skySteps=0;
    for(int i=0;i<2048;++i) {
        float h=min(stepSize,.04/max(abs(state.y),1e-6));
        if(volume && diskOpacity>0.0) {
            // Limit inward radial jumps. On escape, allow the original solver
            // to cross u=0 instead of approaching infinity asymptotically.
            if(state.y>0.0) h=min(h,.08*state.x/max(abs(state.y),1e-8));
            h=volumeStep(1.0/state.x,(outward*cos(phi)+tangent*sin(phi)).y,
                -state.y/(state.x*state.x),1.0,(-outward*sin(phi)+tangent*cos(phi)).y,h);
        }
        bool atDisk=phi+h>=crossing;
        if(atDisk) h=crossing-phi;
        vec3 next=rk4(state,b,h);
        if(next.x<=0.0) {
            float escapePhi=phi+h*state.x/(state.x-next.x);
            vec3 escaped=outward*cos(escapePhi)+tangent*sin(escapePhi);
            return diagnostic!=0?vec3(1):compositeDiskSky(light,transmission,escaped);
        }
        if(next.x>=.5) return light;
        if(volume && volumeNear(1.0/state.x,(outward*cos(phi)+tangent*sin(phi)).y)) {
            vec3 mid=rk4(state,b,h*.5);float r=1.0/mid.x;
            volumeSample(outward*cos(phi+h*.5)+tangent*sin(phi+h*.5),r,b*normalY,
                -b*mid.y/(1.0-2.0*mid.x),mid.z,h*r*r/b,light,transmission);
            if(transmission<.001) {
                if(!needsSkyContinuation(light)) return light;
                phi+=h;state=next;skySteps=2047-i;break;
            }
        }
        phi+=h; state=next;
        if(atDisk) {
            crossing+=PI;
            float r=1.0/state.x;
            if(!volume && diskEnabled!=0 && !coplanar && r>=6.0 && r<=outerRadius) {
                if(diagnostic!=0) return vec3(1,0,0);
                return disk(outward*cos(phi)+tangent*sin(phi),r,b*normalY,state.z,-b*state.y/(1.0-2.0*state.x));
            }
        }
    }
    // Once emission is opaque, only the escaped sky direction is needed.
    for(int i=0;i<skySteps;++i) {
        float h=min(stepSize,.04/max(abs(state.y),1e-6));
        vec3 next=rk4(state,b,h);
        if(next.x<=0.0) {
            float escapePhi=phi+h*state.x/(state.x-next.x);
            return compositeDiskSky(light,transmission,outward*cos(escapePhi)+tangent*sin(escapePhi));
        }
        if(next.x>=.5) return light;
        phi+=h;state=next;
    }
    // Unresolved rays remain visible in diagnostics instead of pretending capture.
    return diagnostic!=0?vec3(1,0,1):light;
}
)GLSL" + kerrCode + R"GLSL(
vec3 traceRay(vec2 pixel) {
    observerEnergy=cameraLapse;
    skyWeight=0.0;skyDirection=vec3(0);
    return metric==0?traceSchwarzschild(pixel):traceKerrRay(pixel);
}
// CAst: decorative stars over the Milky Way, drawn after tracing. Each star is
// a point on the lensed sky: screen derivatives of the escaped direction map
// its offset into pixels, so it is drawn about one pixel wide wherever lensing
// moves it, and brightened by the lensing magnification (clamped). Three cell
// layers on an equal-angle cube map hold bright, medium and faint stars; density
// can follow the panorama's coarse brightness. Positions are not a catalog.
// Equal-angle cube map: face 1..6 for +x,-x,+y,-y,+z,-z and a in [-1,1]^2.
// Python's star bucketing (blackhole_stars.py) uses the same convention.
float cubeFace(vec3 d,out vec2 a) {
    vec3 ad=abs(d); vec2 p; float face;
    if(ad.x>=ad.y && ad.x>=ad.z) { p=d.yz/ad.x; face=d.x>0.0?1.0:2.0; }
    else if(ad.y>=ad.z) { p=d.xz/ad.y; face=d.y>0.0?3.0:4.0; }
    else { p=d.xy/ad.z; face=d.z>0.0?5.0:6.0; }
    a=atan(p)*(4.0/PI);
    return face;
}
vec3 cubeDirection(float face,vec2 a) {
    vec2 p=tan(a*(PI/4.0));
    if(face<2.5) return normalize(vec3(face<1.5?1.0:-1.0,p));
    if(face<4.5) return normalize(vec3(p.x,face<3.5?1.0:-1.0,p.y));
    return normalize(vec3(p,face<5.5?1.0:-1.0));
}
// Least-squares pixel offset of a sky direction from the pixel's own one.
vec2 skyPixels(vec3 offset,vec3 dx,vec3 dy,float a11,float a12,float a22,float det) {
    float b1=dot(dx,offset),b2=dot(dy,offset);
    return vec2(a22*b1-a12*b2,a11*b2-a12*b1)/det;
}
// Real stars from CAst's Sky Atlas cache. Each cube cell lists the stars
// within starMargin of it, brightest first, so one cell holds every star that
// can reach this pixel unless lensing shrinks the sky so far that a star's
// image would exceed the margin; those stars fade out. Display flux compresses
// the magnitude scale (0.3 dex per magnitude instead of 0.4).
const float starMargin=.008;
vec3 catalogStars(vec3 e,vec3 dx,vec3 dy,float footprint,float a11,float a12,float a22,float det) {
    vec2 a;
    float face=cubeFace(e,a);
    ivec2 q=clamp(ivec2((a*.5+.5)*float(starGrid)),0,starGrid-1);
    vec2 cell=texelFetch(starCells,ivec2(int(face-1.0)*starGrid+q.x,q.y),0).rg;
    int first=int(cell.x),count=int(cell.y);
    vec3 result=vec3(0);
    for(int k=0;k<512;++k) {
        if(k>=count) break;
        int index=first+k;
        ivec2 texel=ivec2(index%4096,index/4096);
        vec4 star=texelFetch(starData,texel,0);
        if(star.w>starMagnitudeLimit) break;
        float flux=min(3.0*pow(10.0,-.2*star.w),8.0);
        float width=.6*starSize*(1.0+.35*sqrt(min(flux,4.0)));
        float reach=2.5*width*footprint/starMargin;
        if(reach>=1.0) continue;
        vec2 pixels=skyPixels(star.xyz-e,dx,dy,a11,a12,a22,det);
        float d2=dot(pixels,pixels)/(width*width);
        if(d2>=6.25) continue;
        float shape=exp(-.5*d2)*(1.0-smoothstep(4.0,6.25,d2))*(1.0-smoothstep(.7,1.0,reach));
        result+=mix(vec3(1),texelFetch(starColors,texel,0).rgb,starColor)*flux*shape;
    }
    return result;
}
vec3 starOverlay(vec3 d,vec3 dx,vec3 dy,vec2 panorama,float unlensedArea) {
    float a11=dot(dx,dx),a12=dot(dx,dy),a22=dot(dy,dy),det=a11*a22-a12*a12;
    if(det<=1e-24) return vec3(0);
    float footprint=sqrt(sqrt(det));
    float magnification=clamp(unlensedArea/sqrt(det),.15,6.0);
    if(starCatalog!=0)
        return catalogStars(d,dx,dy,footprint,a11,a12,a22,det)*starBrightness*magnification;
    vec2 a;
    float face=cubeFace(d,a);
    float glow=dot(textureLod(milkyWayTexture,panorama,max(0.0,log2(skyWidth/256.0))).rgb,
        vec3(.2126,.7152,.0722));
    float follow=mix(1.0,.25+3.0*smoothstep(0.0,.4,glow),starFollow);
    vec3 result=vec3(0);
    for(int layer=0;layer<3;++layer) {
        float cells=layer==0?24.0:layer==1?70.0:200.0;
        float visible=1.0-smoothstep(.15,.3,footprint*cells/(.5*PI));
        vec2 q=(a*.5+.5)*cells;
        vec3 key=vec3(floor(q),face+float(layer)*7.0);
        if(visible<=0.0 || hash(key)>=(layer==0?.25:layer==1?.18:.22)*starDensity*follow) continue;
        vec2 center=.2+.6*vec2(hash(key+vec3(0,0,11)),hash(key+vec3(0,0,23)));
        vec2 pixels=skyPixels(cubeDirection(face,(floor(q)+center)/cells*2.0-1.0)-d,dx,dy,a11,a12,a22,det);
        float h=hash(key+vec3(0,0,31));
        float flux=layer==0?mix(.5,4.0,h*h*h):layer==1?mix(.1,.5,h*h):mix(.02,.12,h*h);
        float width=.6*starSize*(1.0+.35*sqrt(flux));
        float d2=dot(pixels,pixels)/(width*width);
        if(d2>=6.25) continue;
        float shape=exp(-.5*d2)*(1.0-smoothstep(4.0,6.25,d2));
        float t=hash(key+vec3(0,0,43));
        vec3 tint=t<.5?mix(vec3(.62,.76,1.0),vec3(1),t*2.0):mix(vec3(1),vec3(1.0,.76,.5),t*2.0-1.0);
        result+=mix(vec3(1),tint,starColor)*flux*shape*visible;
    }
    return result*starBrightness*magnification;
}
void main() {
    vec2 p=uv*resolution;
    if(diagnostic==4) {color=vec4(flowSample(uv,timeRg).xyz,1);return;}
    if(diagnostic==5) {
        float r=mix(innerRadius,outerRadius+1.0,uv.x);
        float edge=outerEnvelope(r,innerRadius,outerRadius);
        vec3 light=vec3(0);float transmission=1.0;
        transferSlab(vec3(edge),pow(10.0,-3.0+6.0*uv.y)*edge,light,transmission);
        color=vec4(edge,light.r,transmission,1);return;
    }
    if(diagnostic==3) {
        // Analytic two-slab benchmark, varied optical depth and subdivision.
        float tau=pow(10.0,-5.0+7.0*uv.x),transmission=1.0;
        int count=1+int(uv.y*127.0);vec3 light=vec3(0);
        for(int i=0;i<128;++i) {
            if(i>=count) break;
            transferSlab(vec3(.8,.2,.1),tau/float(count),light,transmission);
        }
        for(int i=0;i<128;++i) {
            if(i>=count) break;
            transferSlab(vec3(.1,.3,.9),.7/float(count),light,transmission);
        }
        color=vec4(light+transmission*vec3(.2,.6,.4),1);return;
    }
    vec3 result;
    if(samples==4) {
        result=(traceRay(p+vec2(-.25,-.25))+traceRay(p+vec2(.25,-.25))
            +traceRay(p+vec2(-.25,.25))+traceRay(p+vec2(.25,.25)))*.25;
    } else result=traceRay(p);
    if(starsEnabled!=0) {
        // Derivatives stay in uniform control flow. Skip quads that straddle
        // the shadow, the disk or the sky edge, where they are meaningless.
        vec3 dx=dFdx(skyDirection),dy=dFdy(skyDirection);
        float edge=fwidth(skyWeight);
        // Solid angle of this pixel without lensing (gnomonic projection).
        vec2 screen=(2.0*p/resolution-1.0)*vec2(resolution.x/resolution.y,1);
        float slope=tan(fov*.5),pixel=2.0*slope/resolution.y;
        float unlensedArea=pixel*pixel*pow(1.0+slope*slope*dot(screen,screen),-1.5);
        if(skyWeight>0.0 && edge<.5)
            result+=skyWeight*(1.0-2.0*edge)*starOverlay(skyDirection,dx,dy,skyPanorama,unlensedArea);
    }
    color=vec4(result,1);
}
)GLSL";

inline constexpr char blurFragment[] = R"GLSL(#version 330 core
in vec2 uv; out vec4 color;
uniform sampler2D source;
uniform vec2 direction;
uniform int extractBright;
vec3 sampleLight(vec2 p) {
    vec3 v=texture(source,p).rgb;
    return extractBright!=0?max(v-vec3(.8),vec3(0)):v;
}
void main() {
    vec3 v=sampleLight(uv)*.227027;
    v+=(sampleLight(uv+direction*1.384615)+sampleLight(uv-direction*1.384615))*.316216;
    v+=(sampleLight(uv+direction*3.230769)+sampleLight(uv-direction*3.230769))*.070270;
    color=vec4(v,1);
}
)GLSL";
inline constexpr char postFragment[] = R"GLSL(#version 330 core
in vec2 uv; out vec4 color;
uniform sampler2D scene,bloomTexture;
uniform float exposure,bloom;
uniform int diagnostic;
void main() {
    vec3 v=texture(scene,uv).rgb;
    if(diagnostic==0) {
        v=(v+bloom*texture(bloomTexture,uv).rgb)*exposure;
        v=(v*(2.51*v+.03))/(v*(2.43*v+.59)+.14);
        v=pow(clamp(v,0.0,1.0),vec3(1.0/2.2));
    }
    color=vec4(v,1);
}
)GLSL";
}
