#pragma once

inline constexpr char kOpticalVertex[] = R"(#version 330 core
layout(location=0) in vec3 a_position;
layout(location=1) in vec2 a_uv;
layout(location=2) in vec3 a_color;
layout(location=3) in vec2 a_weight;
uniform mat4 u_view_projection;
out vec2 v_uv;
out vec3 v_color;
out vec2 v_weight;
void main() {
    gl_Position=u_view_projection*vec4(a_position,1);
    v_uv=a_uv;v_color=a_color;v_weight=a_weight;
}
)";

inline constexpr char kOpticalFragment[] = R"(#version 330 core
in vec2 v_uv;
in vec3 v_color;
in vec2 v_weight;
uniform int u_dust_pass;
uniform float u_reddening;
out vec4 fragment_color;
void main() {
    float r2=dot(v_uv,v_uv);
    if(r2>=1.0) discard;
    float kernel=(1.0-r2)*(1.0-r2);
    if(u_dust_pass==1) {
        vec3 extinction=mix(vec3(1),vec3(0.80,1.00,1.30),u_reddening);
        // Multiplicative blend attenuates only the already drawn, more distant light.
        fragment_color=vec4(exp(-extinction*v_weight.y*kernel),1);
    } else {
        fragment_color=vec4(v_color*v_weight.x*kernel,0);
    }
}
)";

inline constexpr char kOpticalPost[] = R"(#version 330 core
in vec2 v_uv;
uniform sampler2D u_light;
uniform sampler2D u_bloom;
uniform float u_exposure;
uniform float u_bloom_gain;
uniform int u_extract;
out vec4 fragment_color;
void main() {
    vec3 light=texture(u_light,v_uv).rgb*u_exposure;
    float luminance=dot(light,vec3(0.2126,0.7152,0.0722));
    if(u_extract==1) {
        fragment_color=vec4(light*max(luminance-0.8,0.0)/max(luminance,0.00001),1);
        return;
    }
    light+=texture(u_bloom,v_uv).rgb*u_bloom_gain;
    // A luminance shoulder retains population colors; one final linear-to-sRGB conversion.
    luminance=dot(light,vec3(0.2126,0.7152,0.0722));
    vec3 mapped=light/(1.0+luminance);
    mapped/=max(1.0,max(mapped.r,max(mapped.g,mapped.b)));
    vec3 srgb=mix(12.92*mapped,1.055*pow(max(mapped,vec3(0)),vec3(1.0/2.4))-0.055,
                  step(vec3(0.0031308),mapped));
    // Stable sub-code-value dither prevents visible contour bands in bright smooth cores.
    // Keep empty black pixels black; the HDR light/dust calculation is untouched.
    float noise=fract(52.9829189*fract(dot(gl_FragCoord.xy,vec2(0.06711056,0.00583715))))-0.5;
    srgb=clamp(srgb+noise*(min(1.0,luminance*1000.0)/255.0),0.0,1.0);
    fragment_color=vec4(srgb,1);
}
)";
