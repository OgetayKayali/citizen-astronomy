#pragma once
namespace blackhole_view {
inline constexpr char flowVertex[]=R"GLSL(#version 330 core
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2-1,0,1);}
)GLSL";
inline constexpr char flowFragment[]=R"GLSL(#version 330 core
layout(location=0) out vec4 result;
layout(location=1) out vec4 accounting;
uniform sampler2D gas,rows,radialFlux,azimuthFlux,forcing,budget,markers,aux,clockState,cfl;
uniform int operation,nr,np,closed,perturbed;
uniform float dr,dp,inner,outer,spin,aspect,stirring,cooling,stopTime;
float dt,time;
const float PI=3.14159265358979323846, gammaGas=5.0/3.0;
int wrap(int j){return (j%np+np)%np;}
vec4 get(int i,int j){return texelFetch(gas,ivec2(wrap(j),clamp(i,0,nr-1)),0);}
vec4 row(int i){return texelFetch(rows,ivec2(0,clamp(i,0,nr-1)),0);}
vec4 mm(vec4 a,vec4 b){return mix(vec4(0),sign(a)*min(abs(a),abs(b)),greaterThan(a*b,vec4(0)));}
vec4 slope(vec4 a,vec4 b,vec4 c){return mm(b-a,c-b);}
vec4 mc(vec4 a,vec4 b,vec4 c){return mm(.5*(c-a),mm(2*(b-a),2*(c-b)));}
vec4 flux(vec4 a,vec4 b,float r,float orbital,bool azimuthal){
    float va=azimuthal?a.z/(a.x*r)-orbital:a.y/a.x;
    float vb=azimuthal?b.z/(b.x*r)-orbital:b.y/b.x;
    float pa=(gammaGas-1)*a.w,pb=(gammaGas-1)*b.w;
    float wave=max(abs(va)+sqrt(gammaGas*pa/a.x),abs(vb)+sqrt(gammaGas*pb/b.x));
    vec4 fa=a*va,fb=b*vb;
    if(azimuthal){fa.z+=r*pa;fb.z+=r*pb;}else{fa.y+=pa;fb.y+=pb;}
    return .5*(fa+fb-(b-a)*wave);
}
vec4 reservoir(int i,int j){
    vec4 q=row(i);float r=q.x,rho=pow(r/inner,-.7);
    if(perturbed!=0)rho*=1+.18*sin(3*(j+.5)*dp-.013*time)+.1*sin(7*(j+.5)*dp+.021*time);
    float vr=closed!=0?0:-1.5*q.w/r;
    float vphi=r*q.y*sqrt(1-1.7*aspect*aspect/gammaGas);
    return vec4(rho,rho*vr,rho*r*vphi,rho*q.z);
}
vec2 velocity(float r,float phi){
    float x=clamp((r-inner)/dr-.5,0.0,float(nr-1)),y=phi/dp-.5;
    int i=int(floor(x)),j=int(floor(y));
    vec4 s=mix(mix(get(i,j),get(i,j+1),fract(y)),mix(get(i+1,j),get(i+1,j+1),fract(y)),fract(x));
    return vec2(s.y/s.x,s.z/(s.x*r*r));
}
vec4 payload(int i,int j){
    vec4 s=get(i,j),q=row(i),ref=row(0);
    return vec4(s.x,s.w*q.y/(ref.z*ref.y),s.y/s.x,s.z/(s.x*q.x*q.x));
}
void main(){
    ivec2 cell=ivec2(gl_FragCoord.xy);int j=cell.x,i=cell.y;
    vec4 clock=texelFetch(clockState,ivec2(0),0);
    time=clock.x*.25+clock.y;
    // Subtract integer tick counts before the fractional remainder. Subtracting
    // two large absolute float times can round a near-boundary step to zero.
    float remaining=(stopTime*4-clock.x)*.25-clock.y;
    dt=max(0,min(texelFetch(cfl,ivec2(0),0).x,min(.25-clock.y,remaining)));
    if(dt==0 && (operation==0||operation==6)){result=get(i,j);accounting=texelFetch(budget,cell,0);return;}
    if(dt==0 && operation==9){result=texelFetch(markers,cell,0);return;}
    if(operation==10){
        float bound=texelFetch(cfl,ivec2(0),0).x;
        clock.z=min(clock.z,bound);clock.y+=dt;
        if(clock.y>=.25-1e-7){clock.x+=1;clock.y=0;}
        result=clock;return;
    }
    if(operation==0){ // Conservative orbital remap, half step.
        float shift=mod(row(i).y*dt*.5/dp,float(np)),f=fract(shift);int k=j-int(floor(shift));
        vec4 a=get(i,k-1),b=get(i,k);
        result=b*(1-f)+a*f+.5*f*(1-f)*(mc(get(i,k-2),a,b)-mc(a,b,get(i,k+1)));return;
    }
    if(operation==1){ // CFL bound and positivity/finite check.
        vec4 s=get(i,j),q=row(i);
        bool valid=!any(isnan(s))&&!any(isinf(s))&&s.x>1e-9&&s.w>0;
        float cs=sqrt(gammaGas*(gammaGas-1)*s.w/s.x);
        float residual=s.z/(s.x*q.x)-q.x*q.y;
        float bound=.32/((abs(s.y/s.x)+cs)/dr+(abs(residual)+cs)/(q.x*dp)+4*q.w/(dr*dr));
        result=vec4(valid?min(.25,bound):0);return;
    }
    if(operation==2){ // Exact min reduction, including odd dimensions.
        ivec2 size=textureSize(aux,0);float value=.25;
        for(int y=0;y<2;++y)for(int x=0;x<2;++x){ivec2 p=2*cell+ivec2(x,y);if(all(lessThan(p,size)))value=min(value,texelFetch(aux,p,0).x);}
        result=vec4(value);return;
    }
    if(operation==3){ // Six forcing phases, once per radius and substep.
        const float modes[6]=float[6](5,9,14,23,37,59);
        const float amps[6]=float[6](.45,.65,.6,.5,.35,.2);
        float epoch=floor(time/80),age=time-epoch*80,blend=smoothstep(0,80,age);
        vec4 q=row(i);result=vec4(0);
        for(int pair=0;pair<2;++pair){int k=2*j+pair;vec2 sum=vec2(0);
            float weight=amps[k]*clamp((np/3.0-modes[k])/8,0,1);
            for(int generation=0;generation<2;++generation){
                float birth=epoch-1+generation,elapsed=age+80*(1-generation);
                float phase=(2.3+.23*k)*sin((.73+.31*k)*q.x+birth*(1.37+.19*k))+birth*(2.4+.71*k)-modes[k]*q.y*elapsed;
                sum+=weight*(generation!=0?blend:1-blend)*vec2(cos(phase),sin(phase));
            }
            result[2*pair]=sum.x;result[2*pair+1]=sum.y;
        }return;
    }
    if(operation==4){ // One shared flux per radial face.
        float face=inner+i*dr;vec4 a=get(i-1,j),b=get(i,j);
        if(i>0&&i<nr){a+=.5*slope(get(i-2,j),a,b);b-=.5*slope(get(i-1,j),get(i,j),get(i+1,j));}
        else if(i==0){b=get(0,j);a=b;if(closed!=0)a.y=-b.y;else{a.y=min(a.y,0);b.y=a.y;}}
        else{a=get(nr-1,j);b=closed!=0?a:reservoir(nr-1,j);if(closed!=0)b.y=-a.y;else if(a.y>0)b=a;}
        vec4 f=flux(a,b,face,0,false);
        if(i>0&&i<nr){vec4 l=get(i-1,j),r=get(i,j),ql=row(i-1),qr=row(i);
            float shear=(r.z/(r.x*qr.x*qr.x)-l.z/(l.x*ql.x*ql.x))/dr;
            f.z-=.5*(ql.w+qr.w)*.5*(a.x+b.x)*face*face*shear;}
        if(closed!=0&&(i==0||i==nr)){f.x=0;f.z=0;f.w=0;}
        result=f*face;return;
    }
    if(operation==5){
        vec4 a=get(i,j),b=get(i,j+1),q=row(i);
        result=flux(a+.5*slope(get(i,j-1),a,b),b-.5*slope(a,b,get(i,j+2)),q.x,q.x*q.y,true);return;
    }
    if(operation==6){
        vec4 s=get(i,j),q=row(i);float r=q.x;
        vec4 rf0=texelFetch(radialFlux,cell,0),rf1=texelFetch(radialFlux,cell+ivec2(0,1),0);
        vec4 change=(rf1-rf0)/(r*dr)+(texelFetch(azimuthFlux,cell,0)-texelFetch(azimuthFlux,ivec2(wrap(j-1),i),0))/(r*dp);
        vec4 updated=s-dt*change;
        float vr=s.y/s.x,vphi=s.z/(s.x*r),p=(gammaGas-1)*s.w;
        updated.y+=dt*(s.x*(vphi*vphi/r-r*q.y*q.y)+p/r);
        vec4 left=get(i-1,j),right=get(i+1,j),before=get(i,j-1),after=get(i,j+1);
        float divergence=(right.y/right.x-left.y/left.x)/(2*dr)+vr/r+(after.z/after.x-before.z/before.x)/(2*r*r*dp);
        float rl=row(i-1).x,rr=row(i+1).x;
        float shear=r*(right.z/(right.x*rr*rr)-left.z/(left.x*rl*rl))/((i==0||i==nr-1)?dr:2*dr);
        updated.w+=dt*(-p*divergence+s.x*q.w*shear*shear);
        updated.w=updated.x*q.z+(updated.w-updated.x*q.z)*exp(-dt*q.y/cooling);
        const float modes[6]=float[6](5,9,14,23,37,59);float ar=0,ap=0;
        for(int k=0;k<6;++k){vec4 f=texelFetch(forcing,ivec2(k/2,i),0);vec2 phase=k%2==0?f.xy:f.zw;
            float angle=modes[k]*(j+.5)*dp,si=sin(angle),co=cos(angle);
            ar+=si*phase.x+co*phase.y;ap+=(k%2!=0?-1:1)*.45*(co*phase.x-si*phase.y);}
        float drive=4*stirring*aspect*r*q.y*q.y;ar*=drive;ap*=drive;
        updated.y+=dt*s.x*ar;updated.z+=dt*s.x*r*ap;result=updated;
        vec4 b=texelFetch(budget,cell,0);
        if(i==0)b.xy+=dt*dp*rf0.xz;
        if(i==nr-1)b.xy-=dt*dp*rf1.xz;
        b.z+=dt*s.x*r*ap*r*dr*dp;accounting=b;return;
    }
    if(operation==7){result=payload(i,j);return;}
    if(operation==8){float sum=0;for(int k=0;k<np;++k)sum+=payload(j,k).y;result=vec4(sum/np);return;}
    if(operation==9){
        vec2 p=texelFetch(markers,cell,0).xy;
        if(cell.y*64+cell.x>=3500){result=vec4(0);return;}
        vec2 first=velocity(p.x,p.y),midpoint=velocity(p.x+.5*dt*first.x,p.y+.5*dt*first.y);
        p+=dt*midpoint;p.y=mod(p.y+2*PI,2*PI);
        if(p.x<inner||p.x>=outer)p.x=outer-.5*dr;
        result=vec4(p,0,0);return;
    }
    result=vec4(0);
}
)GLSL";
inline constexpr char markerVertex[]=R"GLSL(#version 330 core
uniform sampler2D markers;
uniform float inner,dr,dp;
uniform int nr,np;
out float weight;
void main(){
    int marker=gl_VertexID/9,offset=gl_VertexID%9;
    vec2 p=texelFetch(markers,ivec2(marker%64,marker/64),0).xy;
    int di=offset/3-1,dj=offset%3-1;
    int row=int((p.x-inner)/dr)+di,col=(int(p.y/dp)+dj+np)%np;
    weight=exp(-1.6*float(di*di+dj*dj));
    if(row<0||row>=nr)gl_Position=vec4(2,2,0,1);
    else gl_Position=vec4(2*(vec2(col,row)+.5)/vec2(np,nr)-1,0,1);
    gl_PointSize=1;
}
)GLSL";
inline constexpr char markerFragment[]=R"GLSL(#version 330 core
in float weight;out vec4 result;void main(){result=vec4(weight);}
)GLSL";
}
