#include "variable.hpp"
#include <algorithm>
#include <cmath>
#include <string>
#include <exception>
#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C" __attribute__((visibility("default")))
#endif
namespace { thread_local std::string error; }
API const char* vl_error(){return error.c_str();}
API void* vl_create(int family,const double* p,const double* x,int quality){
    try { error.clear();return new variable::Model(family,p,x,quality); }
    catch(const std::exception& e){error=e.what();return nullptr;}
}
API void vl_delete(void* h){delete static_cast<variable::Model*>(h);}
API int vl_observe(void* h,double cycles,double* out){
    try {auto v=static_cast<variable::Model*>(h)->observe(cycles);std::copy(v.begin(),v.end(),out);return 1;}
    catch(const std::exception& e){error=e.what();return 0;}
}
API int vl_curve(void* h,double start,double stop,int count,double* out){
    try {for(int i=0;i<count;++i){auto v=static_cast<variable::Model*>(h)->observe(start+(stop-start)*i/(count-1));std::copy(v.begin(),v.end(),out+i*5);}return 1;}
    catch(const std::exception& e){error=e.what();return 0;}
}
API void vl_info(void* h,double* out){auto& m=*static_cast<variable::Model*>(h);out[0]=m.extent;out[1]=m.l1;out[2]=m.potential;out[3]=m.volumeRadiusA;out[4]=m.volumeRadiusB;}
// Interleaved triangle vertices: position, outward normal, temperature, limb, radiance, component.
API int vl_mesh(void* h,double cycles,float* out,int capacity){
    try {auto& m=*static_cast<variable::Model*>(h);auto mesh=m.frame(cycles);int count=int(mesh.faces.size())*3;
        if(capacity<count)return count;
        double reference=m.surface(m.p[2]);int k=0;
        for(auto face:mesh.faces)for(int index:face){auto v=mesh.v[index];
            for(double value:{v.p.x,v.p.y,v.p.z,v.n.x,v.n.y,v.n.z,v.temperature,v.limb,v.flux/reference,double(v.component)})out[k++]=float(value);}
        return count;
    }catch(const std::exception& e){error=e.what();return -1;}
}
