#pragma once
inline constexpr const char* binaryVertex=R"GLSL(#version 330 core
out vec2 uv;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);uv=p;gl_Position=vec4(p*2.-1.,0,1);}
)GLSL";
inline constexpr const char* binaryFragment=R"GLSL(#version 330 core
in vec2 uv;
out vec4 outputColor;
uniform vec2 viewport;
uniform vec3 center0,center1,tint0,tint1;
uniform float radius0,radius1,temp0,temp1,limb0,limb1,extent,time,exposure,glow,detail,sky;
uniform int customTint;
float hash(vec3 p){p=fract(p*.1031);p+=dot(p,p.yzx+33.33);return fract((p.x+p.y)*p.z);}
float noise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
float fbm(vec3 p){float v=0,a=.5;for(int j=0;j<5;++j){v+=a*noise(p);p=p*2.03+7.1;a*=.5;}return v;}
vec3 color(float t){
    // Temperature-driven artistic RGB, separate from Planck-band photometry.
    vec3 warm=vec3(1.,.27,.065),sun=vec3(1.,.82,.49),white=vec3(.91,.95,1.);
    return t<5800.?mix(warm,sun,clamp((t-2000.)/3800.,0.,1.)):mix(sun,white,clamp((t-5800.)/6500.,0.,1.));
}
vec3 surface(vec3 n,float T,float limb,vec3 tint,float seed){
    float mu=n.z;
    float a=time*.035+seed; n.xz=mat2(cos(a),-sin(a),sin(a),cos(a))*n.xz;
    vec3 p=n*45.;
    float granule=noise(p*3.+fbm(p)*2.);
    float cell=pow(clamp(granule*1.5-.2,0.,1.),.65);
    float bands=fbm(n*7.+seed);
    float spots=smoothstep(.62,.78,fbm(n*12.+seed*3.));
    float tex=mix(1.,(.72+.48*cell+.18*bands)*(1.-spots*.55),detail);
    return tint*tex*(1.-limb+limb*mu)*pow(T/5800.,.42);
}
vec3 corona(vec2 p,vec3 c,float r,vec3 tint){
    vec2 q=(p-c.xy)/r; float d=length(q); if(d<1.)return vec3(0);
    float ang=atan(q.y,q.x),h=d-1.;
    float streamer=.65+.35*sin(ang*19.+fbm(vec3(q*3.,time*.02))*9.);
    float rim=exp(-h*24.)*.45+exp(-h*5.)*.17*streamer+exp(-h*1.7)*.025;
    float loops=pow(max(0.,sin(ang*11.+time*.06+h*16.)),12.)*exp(-h*14.)*.15;
    return tint*(rim+loops)*glow;
}
void main(){
    vec2 p=(uv*2.-1.)*vec2(viewport.x/viewport.y,1.)*extent;
    vec3 col=vec3(.002,.003,.009);
    vec2 screen=uv*viewport;
    vec2 grid=floor(screen/42.),f=fract(screen/42.);
    vec2 starCenter=vec2(hash(vec3(grid,2.)),hash(vec3(grid,3.)));
    float rnd=hash(vec3(grid,17.)),star=exp(-length(f-starCenter)*190.);
    col+=sky*star*step(.72,rnd)*mix(vec3(.26,.43,.7),vec3(.85,.7,.48),rnd)*1.8;
    col+=sky*.006*vec3(.35,.45,1.)*pow(fbm(vec3(uv*5.,4.)),3.);
    vec3 ca=customTint==1?tint0:color(temp0),cb=customTint==1?tint1:color(temp1);
    vec2 a=(p-center0.xy)/radius0,b=(p-center1.xy)/radius1;
    float da=dot(a,a),db=dot(b,b),za=-1e20,zb=-1e20;
    if(da<1.)za=center0.z+radius0*sqrt(1.-da);
    if(db<1.)zb=center1.z+radius1*sqrt(1.-db);
    if(za>-1e19||zb>-1e19){
        if(za>zb)col=surface(vec3(a,sqrt(1.-da)),temp0,limb0,ca,1.);
        else col=surface(vec3(b,sqrt(1.-db)),temp1,limb1,cb,5.);
    }else{
        col+=corona(p,center0,radius0,ca)+corona(p,center1,radius1,cb);
    }
    // Mild filmic rolloff preserves photosphere texture instead of clipping to a white disk.
    col=vec3(1.)-exp(-col*exposure);
    col=pow(col,vec3(1./2.2));
    outputColor=vec4(col,1.);
}
)GLSL";
