#pragma once
namespace blackhole_view {
inline constexpr char volumeCode[] = R"GLSL(
bool volumeActive() {
    return volumeModel!=0 && fluidModel!=0 && diskEnabled!=0 && diagnostic==0;
}
bool blendDiskIntoSky() {
    return volumeActive() && physical==0 && fluidView==0 && backgroundMode==0 && spaceBlend>0.0;
}
bool needsSkyContinuation(vec3 light) {
    // Above the coverage threshold the gas is fully opaque in either mode.
    return blendDiskIntoSky() && max(light.r,max(light.g,light.b))<.25;
}
vec3 compositeDiskSky(vec3 light,float transmission,vec3 direction) {
    if(blendDiskIntoSky()) {
        // Artistic coverage from the integrated emitted light, not the cold
        // column density. Dark lanes/outskirts reveal the properly lensed sky;
        // bright gas stays opaque. Emission and gas-to-gas occlusion are retained.
        float glow=max(light.r,max(light.g,light.b));
        float coverage=smoothstep(0.0,.25,glow);
        float softTransmission=1.0-(1.0-transmission)*coverage;
        transmission=mix(transmission,softTransmission,spaceBlend);
    }
    vec3 background=sky(direction);
    skyWeight*=transmission;
    return light+transmission*background;
}
void transferSlab(vec3 source,float tau,inout vec3 light,inout float transmission) {
    float absorbed=tau<.001?tau*(1.0-.5*tau):1.0-exp(-tau);
    light+=transmission*absorbed*source;
    transmission*=1.0-absorbed;
}
bool volumeNear(float r,float vertical) {
    return diskOpacity>0.0 && r>=innerRadius*.85 && r<=outerRadius*1.15
        && abs(vertical)<=6.4*diskThickness+.12;
}
// Resolve the vertical profile and radial/azimuthal gas cells before entering
// the layer. Rates are with respect to each solver's integration parameter.
float volumeStep(float r,float vertical,float dr,float angularRate,float verticalRate,float h) {
    if(!volumeNear(r,vertical)) return h;
    float quality=min(1.0,stepSize/.02);
    float speed=sqrt(dr*dr+r*r*angularRate*angularRate);
    // Only motion across the layer requires thickness-sized steps. Using total
    // ray speed here exhausted the ray budget along very thin edge-on disks.
    float minHeight=.65*diskThickness*r*diskHeightTaper(r);
    float verticalSpeed=abs(dr*vertical)+r*abs(verticalRate);
    float clearance=max(0.0,abs(r*vertical)-6.4*diskThickness*r*diskHeightTaper(r));
    h=min(h,quality*max(.25*minHeight,.4*clearance)/max(verticalSpeed,1e-8));
    if(r>=innerRadius && r<=outerRadius && clearance<=0.0
        && particlesEnabled!=0 && particleStrength>0.0 && particleBrightness>0.0 && physical==0 && fluidView==0) {
        // CAst: glint light lies within glintBand of the midplane, so a ray
        // outside it may advance by its vertical distance to the band. Inside,
        // midpoint samples 1.4 Gaussian widths apart integrate each glint to
        // ~1e-4 relative error. Extra code in this loop costs GPU occupancy.
        float z=abs(r*vertical),cylinder=sqrt(max(r*r-z*z,.01));
        float gap=max(0.0,z-glintBand(cylinder));
        h=min(h,quality*(particleWidth(r)*1.4+gap)/max(speed,1e-8));
    }
    h=min(h,quality*.75*(outerRadius-innerRadius)/flowRadialCells/max(abs(dr),1e-8));
    h=min(h,quality*.75*PI/flowRadialCells/max(angularRate,1e-8));
    return h;
}
// Front-to-back grey absorption/emission. dlambda is positive affine length
// for a past-directed photon with E=-1; frequency*dlambda is fluid-frame length.
void volumeSample(vec3 n,float r,float l,float pr,float delay,float dlambda,
    inout vec3 light,inout float transmission) {
    if(fluidEnabled==0 || diskOpacity<=0.0 || r<flowInner || r>flowOuter
        || abs(n.y)>6.4*diskThickness) return;
    vec2 p=vec2(atan(n.x,n.z)/(2.0*PI),(r-flowInner)/(flowOuter-flowInner));
    float emissionTime=timeRg-(delays!=0?delay:0.0);
    vec4 gas=flowSample(p,emissionTime);
    float referenceRadius=flowInner+.5*(flowOuter-flowInner)/flowRadialCells;
    float referenceOmega=1.0/(pow(referenceRadius,1.5)+spin),omega=1.0/(pow(r,1.5)+spin);
    // Recover internal energy relative to the initial thermal profile from the
    // stored cooling-flux proxy. Hotter columns have a larger scale height.
    float thermalRatio=max(gas.y,0.0)/max(gas.x,1e-6)
        *referenceRadius*referenceRadius*pow(referenceOmega,3.0)/(r*r*pow(omega,3.0));
    float baseHeight=diskThickness*r*clamp(sqrt(thermalRatio),.65,1.6);
    float height=baseHeight*diskHeightTaper(r);
    float z=r*n.y/height;
    if(abs(z)>=4.0) return;
    float envelope=outerEnvelope(r,flowInner,flowOuter);
    float taper=smoothstep(flowInner,flowInner+max(.25,.3*height),r)*envelope;
    // Tapering removes the rendered atmosphere rather than compressing the
    // same column into a smaller height (which creates a dark absorbing rim).
    float density=max(gas.x,0.0)*exp(-.5*z*z)/(sqrt(2.0*PI)*baseHeight)
        *(1.0-smoothstep(3.0,4.0,abs(z)))*taper;
    if(density<=1e-12) return;
    float frequency=fluidFrequency(gas,n,r,l,pr);
    if(frequency<=0.0) {light+=transmission*vec3(4,0,4);transmission=0.0;return;}
    float tau=diskOpacity*density*frequency*dlambda;
    // Stable for both small and large optical depths; no step-dependent alpha.
    float g=physical!=0?1.0/(observerEnergy*frequency):1.0;
    // An optically thick column stays bright until its last cells if only
    // density is tapered. Fade its source as well for a soft emitting boundary.
    vec3 source=fluidLight(gas,p,emissionTime,g,1.0,envelope,r*n.y);
    transferSlab(source,tau,light,transmission);
}
)GLSL";
}
