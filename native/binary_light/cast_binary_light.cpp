// CAst bridge. The physical solver and stellar shaders in vendor/ are unmodified.
#include "vendor/src/photometry/binary.hpp"
#include "vendor/apps/photometry/shaders.hpp"
#include <algorithm>
#include <random>
#include <string>
#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C" __attribute__((visibility("default")))
#endif
using namespace photometry;
namespace {
Binary unpack(const double* p) {
    Binary b;
    for(int j=0;j<2;++j) b.stars[j]={p[j*4],p[j*4+1],p[j*4+2],p[j*4+3]};
    b.separation=p[8]; b.eccentricity=p[9]; b.inclination=p[10]; b.omega=p[11]; b.node=p[12];
    b.distance=p[13]; b.thirdLight=p[14]; b.exposure=p[15]; b.band=Band(int(p[16])); return b;
}
void pack(const Binary& b,double* p) {
    for(int j=0;j<2;++j){auto s=b.stars[j];p[j*4]=s.mass;p[j*4+1]=s.radius;p[j*4+2]=s.temperature;p[j*4+3]=s.limb;}
    p[8]=b.separation;p[9]=b.eccentricity;p[10]=b.inclination;p[11]=b.omega;p[12]=b.node;
    p[13]=b.distance;p[14]=b.thirdLight;p[15]=b.exposure;p[16]=int(b.band);
}
thread_local std::string error;
}
API const char* bl_validate(const double* p) {error=unpack(p).invalidReason();return error.c_str();}
API void bl_preset(int n,double* p){pack(presets().at(n).system,p);}
API const char* bl_preset_text(int n,int description){auto& p=presets().at(n);return description?p.description:p.name;}
API const char* bl_type_text(int n,int description){auto& t=variableTypes().at(n);return description?t.description:t.name;}
API int bl_type_count(){return int(variableTypes().size());}
API double bl_period(const double* p){return unpack(p).periodDays();}
API double bl_set_period(const double* p,double days){auto b=unpack(p);b.setPeriodDays(days);return b.separation;}
API double bl_roche(const double* p,int j){return unpack(p).rocheFraction(j);}
API void bl_observe(const double* p,double phase,double* out){auto m=observe(unpack(p),phase);out[0]=m.flux;out[1]=m.magnitude;out[2]=m.irradiance;out[3]=m.visible[0];out[4]=m.visible[1];}
API void bl_curve(const double* p,int count,double* out){auto b=unpack(p);for(int k=0;k<count;++k){auto m=observe(b,double(k)/(count-1));out[k*5]=m.flux;out[k*5+1]=m.magnitude;out[k*5+2]=m.irradiance;out[k*5+3]=m.visible[0];out[k*5+4]=m.visible[1];}}
API void bl_geometry(const double* p,double phase,double* out){auto g=geometry(unpack(p),phase);for(int j=0;j<2;++j){out[j*3]=g.position[j].x;out[j*3+1]=g.position[j].y;out[j*3+2]=g.position[j].z;}out[6]=g.separation;out[7]=g.projected;out[8]=g.front;}
API double bl_blocked(double r,double o,double d,double u,int rings){return blockedFraction(r,o,d,u,rings);}
API const char* bl_shader(int fragment){return fragment?binaryFragment:binaryVertex;}
// Match the source app's mt19937 and per-sample normal_distribution exactly.
API void* bl_rng_create(){try{return new std::mt19937(2026);}catch(...){return nullptr;}}
API void bl_rng_delete(void* rng){delete static_cast<std::mt19937*>(rng);}
API void bl_rng_reset(void* rng){static_cast<std::mt19937*>(rng)->seed(2026);}
API double bl_noise(void* rng,double sigma){return std::normal_distribution<double>(0,sigma)(*static_cast<std::mt19937*>(rng));}
