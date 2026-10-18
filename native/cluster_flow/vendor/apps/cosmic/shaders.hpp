#pragma once

inline constexpr char const* cosmicVertex=R"GLSL(#version 330 core
out vec2 uv;
void main() {
    vec2 p=vec2((gl_VertexID<<1)&2, gl_VertexID&2);
    uv=p; gl_Position=vec4(p*2.0-1.0,0.0,1.0);
}
)GLSL";

inline constexpr char const* cosmicFragment=R"GLSL(#version 330 core
in vec2 uv;
out vec4 outputColor;
uniform sampler3D field;
uniform vec3 eye, forward, right, up;
uniform vec2 viewport;
uniform float exposure, sliceWidth, slicePosition, backgroundDensity, boxScale;
uniform int mode, samples;

vec3 palette(float t) {
    vec3 a=vec3(.035,.12,.27),b=vec3(.12,.53,.72),c=vec3(1.0,.48,.16),d=vec3(1.0,.93,.72);
    if(t<.35) return mix(a,b,t/.35);
    if(t<.7) return mix(b,c,(t-.35)/.35);
    return mix(c,d,clamp((t-.7)/.3,0.,1.));
}
float hash(vec2 p) { return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
void main() {
    vec2 screen=(uv*2.-1.)*vec2(viewport.x/viewport.y,1.);
    vec3 ray=normalize(forward+right*screen.x*.40+up*screen.y*.40);
    vec3 safeRay=sign(ray)*max(abs(ray),vec3(1e-7));
    // Zero direction components are rare but explicitly safe at the default view.
    safeRay=vec3(safeRay.x==0.?1e-7:safeRay.x,safeRay.y==0.?1e-7:safeRay.y,safeRay.z==0.?1e-7:safeRay.z);
    vec3 t0=(vec3(-1.)-eye)/safeRay,t1=(vec3(1.)-eye)/safeRay;
    vec3 nearV=min(t0,t1),farV=max(t0,t1);
    float enter=max(max(nearV.x,nearV.y),nearV.z),leave=min(min(farV.x,farV.y),farV.z);
    vec3 background=vec3(.00025,.0005,.0012)*(1.-.25*length(uv-.5));
    vec3 light=vec3(0.); float transmission=1.;
    if(leave>max(enter,0.)) {
        enter=max(enter,0.);
        float ds=(leave-enter)/float(samples);
        float t=enter+ds*hash(gl_FragCoord.xy);
        for(int i=0;i<512;++i) {
            if(i>=samples || transmission<.015) break;
            vec3 p=eye+ray*t;
            if(abs(p.z-slicePosition)<=sliceWidth) {
                vec4 f=texture(field,p*.5+.5);
                float rho=max(0.,f.r-backgroundDensity);
                float density=pow(rho,.85);
                float value=clamp(log(1.+rho*15.)/3.5,0.,1.);
                vec3 color=palette(value);
                if(mode==1) {
                    value=clamp(f.g/.16,0.,1.);
                    color=palette(value);
                    density*=.3+value*1.8;
                }
                if(mode==2) { value=clamp(log(1.+f.b*600.)/3.8,0.,1.); color=palette(value); }
                if(mode==3) { value=clamp(f.a*.8,0.,1.); color=palette(value); density*=.15+value*2.; }
                float alpha=1.-exp(-density*ds*boxScale*4.0);
                light+=transmission*alpha*color*(.7+value*.7);
                transmission*=1.-alpha;
            }
            t+=ds;
        }
    }
    vec3 color=background+light*exposure;
    color=vec3(1.)-exp(-color);
    color=pow(color,vec3(1./2.2));
    outputColor=vec4(color,1.);
}
)GLSL";

inline constexpr char const* tracerVertex=R"GLSL(#version 330 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 quantities;
layout(location=2) in vec3 velocity;
uniform vec3 eye,forward,right,up;
uniform vec2 viewport;
uniform float sliceWidth,slicePosition;
out vec3 values;
out vec2 direction;
void main() {
    vec3 p=position-eye;
    float depth=dot(p,forward);
    gl_Position=vec4(dot(p,right)/(.40*viewport.x/viewport.y),dot(p,up)/.40,0.,depth);
    if(abs(position.z-slicePosition)>sliceWidth || depth<.05) gl_Position=vec4(2.,2.,2.,1.);
    gl_PointSize=clamp(12./max(depth,.05),1.5,10.);
    values=quantities;
    vec2 v=vec2(dot(velocity,right),-dot(velocity,up));
    direction=length(v)>.00001?normalize(v):vec2(1.,0.);
}
)GLSL";
inline constexpr char const* tracerFragment=R"GLSL(#version 330 core
in vec3 values;
in vec2 direction;
out vec4 outputColor;
uniform int mode;
uniform float tracerOpacity;
vec3 palette(float t) {
    vec3 a=vec3(.035,.18,.32),b=vec3(.16,.58,.73),c=vec3(1.,.44,.12),d=vec3(1.,.91,.68);
    if(t<.35) return mix(a,b,t/.35);
    if(t<.7) return mix(b,c,(t-.35)/.35);
    return mix(c,d,clamp((t-.7)/.3,0.,1.));
}
void main() {
    vec2 p=gl_PointCoord*2.-1.;
    vec2 q=vec2(dot(p,direction),dot(p,vec2(-direction.y,direction.x)));
    float glow=exp(-4.*(q.x*q.x+q.y*q.y*3.));
    if(glow<.015) discard;
    float value=clamp(log(1.+values.x*15.)/3.5,0.,1.);
    if(mode==1) value=clamp(values.y/.16,0.,1.);
    if(mode==2) value=clamp(log(1.+values.z*600.)/3.8,0.,1.);
    vec3 color=palette(value);
    float strength=tracerOpacity*glow;
    outputColor=vec4(color*strength,strength);
}
)GLSL";
