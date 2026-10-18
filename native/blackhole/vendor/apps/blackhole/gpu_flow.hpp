#pragma once
#include "blackhole/flow.hpp"
#include "gl_loader.hpp"
#include "gpu_flow_shaders.hpp"
#include <GLFW/glfw3.h>
#include <algorithm>
#include <cmath>
#include <vector>
#include <utility>

namespace blackhole_view {
// OpenGL 3.3 numerical passes over RGBA32F state. All cell updates, fluxes,
// CFL reductions, tracer advection and history generation execute on the GPU.
// Only a scalar clock/health record returns to the CPU per batch. Readback of full
// state/accounting is an explicit validation operation, never a playback path.
class GpuFlow {
    struct Level {GLuint id;int w,h;};
    using Layer=void(APIENTRY*)(GLenum,GLenum,GLuint,GLint,GLint);
    Layer layer_=nullptr;
    blackhole::FlowConfig c_;
    GLuint programs_[11]{},markerProgram_=0,fbo_=0,vao_=0;
    GLuint gas_[2]{},budgets_[2]{},markers_[2]{},rows_=0,forcing_=0,rf_=0,pf_=0;
    GLuint clocks_[2]{};
    std::vector<GLuint> textures_;
    std::vector<Level> reduction_;
    double time_=0,initialMass_=0,initialAngular_=0;
    int ticks_=0;
    GLint stopLocations_[11]{};
    float stopTime_=0;
    static GLuint compile(GLenum type,char const* source) {
        GLuint shader=gl_create_shader(type);gl_shader_source(shader,1,&source,nullptr);gl_compile_shader(shader);
        GLint ok=0;gl_get_shader_iv(shader,GL_COMPILE_STATUS,&ok);
        if(!ok){char log[8192]{};gl_get_shader_info_log(shader,sizeof(log),nullptr,log);gl_delete_shader(shader);throw std::runtime_error(std::string("GPU flow shader: ")+log);}
        return shader;
    }
    static GLuint link(char const* vertexSource,char const* fragment) {
        GLuint v=compile(GL_VERTEX_SHADER,vertexSource),f=0,p=0;
        try {
            f=compile(GL_FRAGMENT_SHADER,fragment);p=gl_create_program();gl_attach_shader(p,v);gl_attach_shader(p,f);gl_link_program(p);
            GLint ok=0;gl_get_program_iv(p,GL_LINK_STATUS,&ok);
            if(!ok){char log[8192]{};gl_get_program_info_log(p,sizeof(log),nullptr,log);throw std::runtime_error(std::string("GPU flow link: ")+log);}
        }catch(...){gl_delete_shader(v);if(f)gl_delete_shader(f);if(p)gl_delete_program(p);throw;}
        gl_delete_shader(v);gl_delete_shader(f);return p;
    }
    static void integer(GLuint p,char const* key,int value){gl_uniform_1i(gl_get_uniform_location(p,key),value);}
    static void real(GLuint p,char const* key,double value){gl_uniform_1f(gl_get_uniform_location(p,key),static_cast<float>(value));}
    GLuint texture(int w,int h,void const* data=nullptr) {
        GLuint id=0;glGenTextures(1,&id);textures_.push_back(id);
        gl_active_texture(0x84C0);glBindTexture(GL_TEXTURE_2D,id);
        glTexImage2D(GL_TEXTURE_2D,0,0x8814,w,h,0,GL_RGBA,GL_FLOAT,data); // RGBA32F
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,0x812F);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,0x812F);
        return id;
    }
    static void bind(GLuint id,int slot){gl_active_texture(0x84C0+slot);glBindTexture(GL_TEXTURE_2D,id);}
    void inputs() {
        bind(gas_[0],0);bind(rows_,1);bind(rf_,2);bind(pf_,3);bind(forcing_,4);bind(budgets_[0],5);bind(markers_[0],6);
        bind(clocks_[0],8);bind(reduction_.back().id,9);
        gl_bind_vertex_array(vao_);
        glDisable(GL_BLEND);glDisable(GL_DEPTH_TEST);glDisable(GL_SCISSOR_TEST);glDisable(GL_CULL_FACE);
    }
    void destination(GLuint id,int w,int h,GLuint second=0,int layer=-1) {
        gl_bind_framebuffer(GL_FRAMEBUFFER,fbo_);
        if(layer<0)gl_framebuffer_texture_2d(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,id,0);
        else layer_(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,id,0,layer);
        gl_framebuffer_texture_2d(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0+1,GL_TEXTURE_2D,second,0);
        GLenum buffers[]{GL_COLOR_ATTACHMENT0,GL_COLOR_ATTACHMENT0+1};gl_draw_buffers(second?2:1,buffers);
        glViewport(0,0,w,h);
    }
    void pass(int operation,GLuint out,int w,int h,GLuint second=0,int layer=-1) {
        // Do not leave a destination attached to an active sampler, even in
        // shader branches that do not fetch it (framebuffer feedback rules).
        GLuint ids[]{rf_,pf_,forcing_};
        for(int k=0;k<3;++k)if(out==ids[k])bind(0,k+2);
        bool cflOutput=out==reduction_.back().id;if(cflOutput)bind(0,9);
        destination(out,w,h,second,layer);gl_use_program(programs_[operation]);gl_uniform_1f(stopLocations_[operation],stopTime_);glDrawArrays(GL_TRIANGLES,0,3);
        for(int k=0;k<3;++k)if(out==ids[k])bind(ids[k],k+2);
        if(cflOutput)bind(out,9);
    }
    std::vector<float> read(GLuint texture,int w,int h) {
        destination(texture,w,h);glReadBuffer(GL_COLOR_ATTACHMENT0);
        std::vector<float> result(static_cast<size_t>(w)*h*4);
        glReadPixels(0,0,w,h,GL_RGBA,GL_FLOAT,result.data());return result;
    }
    void cleanup() {
        if(!textures_.empty())glDeleteTextures(static_cast<GLsizei>(textures_.size()),textures_.data());
        for(auto p:programs_)if(p)gl_delete_program(p);if(markerProgram_)gl_delete_program(markerProgram_);
        if(fbo_)gl_delete_framebuffers(1,&fbo_);if(vao_)gl_delete_vertex_arrays(1,&vao_);
    }
public:
    explicit GpuFlow(blackhole::FlowConfig const& config):c_(config) {
        try {
            // Share the exact seeded initial condition, independently of GPU
            // transcendental rounding. DiskFlow also validates the configuration.
            blackhole::DiskFlow initial(c_);auto t=initial.totals();initialMass_=t.mass;initialAngular_=t.angular;
            layer_=reinterpret_cast<Layer>(glfwGetProcAddress("glFramebufferTextureLayer"));
            if(!layer_)throw std::runtime_error("GPU history layer attachment unavailable");
            // Compile each operation separately: a giant dynamic branch shader
            // allocates resources for its most expensive path in every pass.
            for(int op=0;op<11;++op){
                std::string source=flowFragment,needle="uniform int operation,nr,np,closed,perturbed;";
                source.replace(source.find(needle),needle.size(),"#define operation "+std::to_string(op)+"\nuniform int nr,np,closed,perturbed;");
                programs_[op]=link(flowVertex,source.c_str());
                stopLocations_[op]=gl_get_uniform_location(programs_[op],"stopTime");
            }
            markerProgram_=link(markerVertex,markerFragment);
            gl_gen_framebuffers(1,&fbo_);gl_gen_vertex_arrays(1,&vao_);
            std::vector<float> state,positions(64*55*4,0),rows;
            for(auto const& s:initial.cells()){state.push_back(static_cast<float>(s.density));state.push_back(static_cast<float>(s.radial));state.push_back(static_cast<float>(s.angular));state.push_back(static_cast<float>(s.energy));}
            double dr=(c_.outer-c_.inner)/c_.radial,dp=2*3.141592653589793/c_.azimuthal;
            for(int i=0;i<c_.radial;++i){double r=c_.inner+(i+.5)*dr,omega=1/(std::pow(r,1.5)+c_.spin),cs=c_.aspect*r*omega;
                for(double x:{r,omega,cs*cs/((5./3)*(2./3)),c_.alpha*cs*cs/omega})rows.push_back(static_cast<float>(x));}
            int k=0;for(auto p:initial.tracers()){positions[k++]=static_cast<float>(p.radius);positions[k++]=static_cast<float>(p.angle);k+=2;}
            gas_[0]=texture(c_.azimuthal,c_.radial,state.data());gas_[1]=texture(c_.azimuthal,c_.radial);
            std::fill(state.begin(),state.end(),0.f);
            for(int i=0;i<2;++i){budgets_[i]=texture(c_.azimuthal,c_.radial,state.data());markers_[i]=texture(64,55,positions.data());}
            rows_=texture(1,c_.radial,rows.data());forcing_=texture(3,c_.radial);
            rf_=texture(c_.azimuthal,c_.radial+1);pf_=texture(c_.azimuthal,c_.radial);
            int w=c_.azimuthal,h=c_.radial;
            for(;;){reduction_.push_back({texture(w,h),w,h});if(w==1&&h==1)break;w=(w+1)/2;h=(h+1)/2;}
            float clock[]{0,0,.25f,0};for(auto& id:clocks_)id=texture(1,1,clock);
            std::vector<GLuint> allPrograms(std::begin(programs_),std::end(programs_));allPrograms.push_back(markerProgram_);
            for(GLuint p:allPrograms){
                gl_use_program(p);integer(p,"nr",c_.radial);integer(p,"np",c_.azimuthal);integer(p,"closed",c_.closed);integer(p,"perturbed",c_.perturbed);
                real(p,"dr",dr);real(p,"dp",dp);real(p,"inner",c_.inner);real(p,"outer",c_.outer);real(p,"spin",c_.spin);
                real(p,"aspect",c_.aspect);real(p,"stirring",c_.stirring);real(p,"cooling",c_.cooling);
                char const* names[]{"gas","rows","radialFlux","azimuthFlux","forcing","budget","markers","aux","clockState","cfl"};
                for(int i=0;i<10;++i)integer(p,names[i],i);
            }
            destination(gas_[1],c_.azimuthal,c_.radial,budgets_[1]);
            if(gl_check_framebuffer_status(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)throw std::runtime_error("GPU flow floating-point framebuffer unavailable");
            stableStep(); // Check allocation, renderability and initial validity.
            if(glGetError()!=GL_NO_ERROR)throw std::runtime_error("GPU flow initialization failed");
        }catch(...){cleanup();throw;}
    }
    ~GpuFlow(){cleanup();}
    GpuFlow(GpuFlow const&)=delete;GpuFlow& operator=(GpuFlow const&)=delete;
    int ticks() const{return ticks_;}
    double time() const{return time_;}
    void reduceStep() {
        inputs();auto first=reduction_.front();pass(1,first.id,first.w,first.h);
        for(size_t i=1;i<reduction_.size();++i){bind(reduction_[i-1].id,7);auto d=reduction_[i];pass(2,d.id,d.w,d.h);}
    }
    float stableStep() {
        reduceStep();
        float result=0;glReadBuffer(GL_COLOR_ATTACHMENT0);glReadPixels(0,0,1,1,GL_RED,GL_FLOAT,&result);
        if(!std::isfinite(result)||result<1e-7f)throw std::runtime_error("GPU disk lost positivity or its timestep collapsed");
        return result;
    }
    void substep() {
        reduceStep();
        pass(0,gas_[1],c_.azimuthal,c_.radial);std::swap(gas_[0],gas_[1]);bind(gas_[0],0);
        pass(3,forcing_,3,c_.radial);pass(4,rf_,c_.azimuthal,c_.radial+1);pass(5,pf_,c_.azimuthal,c_.radial);
        pass(6,gas_[1],c_.azimuthal,c_.radial,budgets_[1]);std::swap(gas_[0],gas_[1]);std::swap(budgets_[0],budgets_[1]);bind(gas_[0],0);bind(budgets_[0],5);
        pass(0,gas_[1],c_.azimuthal,c_.radial);std::swap(gas_[0],gas_[1]);bind(gas_[0],0);
        pass(9,markers_[1],64,55);std::swap(markers_[0],markers_[1]);
        pass(10,clocks_[1],1,1);std::swap(clocks_[0],clocks_[1]);
    }
    void advanceTo(double target,int batch=8) {
        if(!std::isfinite(target)||target<time_||target*4!=std::floor(target*4))throw std::invalid_argument("GPU target must be a future fixed tick");
        double previous=time_;
        inputs();stopTime_=static_cast<float>(target);
        for(int k=0;k<batch;++k)substep();
        auto clock=read(clocks_[0],1,1);
        if(!std::isfinite(clock[2])||clock[2]<1e-7f)throw std::runtime_error("GPU disk lost positivity or its timestep collapsed");
        ticks_=static_cast<int>(clock[0]);time_=ticks_*.25+clock[1];
        if(time_<=previous && time_<target)throw std::runtime_error("GPU fluid clock stopped before its target");
    }
    void tick(){int target=ticks_+1;while(ticks_<target)advanceTo(target*.25,1);}
    void snapshot(GLuint gasHistory,GLuint tracerHistory,GLuint profile,int layer) {
        stableStep(); // Never publish invalid final-substep results.
        pass(7,gasHistory,c_.azimuthal,c_.radial,0,layer);
        if(gl_check_framebuffer_status(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)throw std::runtime_error("GPU gas history attachment failed");
        destination(profile,c_.radial,1);glViewport(0,layer,c_.radial,1);gl_use_program(programs_[8]);glDrawArrays(GL_TRIANGLES,0,3);
        if(gl_check_framebuffer_status(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)throw std::runtime_error("GPU radial profile attachment failed");
        destination(tracerHistory,c_.azimuthal,c_.radial,0,layer);glClearColor(0,0,0,0);glClear(GL_COLOR_BUFFER_BIT);
        if(gl_check_framebuffer_status(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)throw std::runtime_error("GPU tracer history attachment failed");
        bind(markers_[0],6);gl_use_program(markerProgram_);glEnable(GL_BLEND);glBlendFunc(GL_ONE,GL_ONE);
        glDrawArrays(GL_POINTS,0,3500*9);glDisable(GL_BLEND);
    }
    std::vector<blackhole::Gas> cells() {
        auto raw=read(gas_[0],c_.azimuthal,c_.radial);std::vector<blackhole::Gas> result;
        for(size_t k=0;k<raw.size();k+=4)result.push_back({raw[k],raw[k+1],raw[k+2],raw[k+3]});return result;
    }
    std::vector<blackhole::Tracer> tracers() {
        auto raw=read(markers_[0],64,55);std::vector<blackhole::Tracer> result;
        for(int k=0;k<3500;++k)result.push_back({raw[4*k],raw[4*k+1]});return result;
    }
    blackhole::FlowTotals totals() {
        auto state=cells();auto account=read(budgets_[0],c_.azimuthal,c_.radial);blackhole::FlowTotals t;
        double dr=(c_.outer-c_.inner)/c_.radial,dp=2*3.141592653589793/c_.azimuthal;
        for(int i=0;i<c_.radial;++i)for(int j=0;j<c_.azimuthal;++j){size_t k=static_cast<size_t>(i)*c_.azimuthal+j;double area=(c_.inner+(i+.5)*dr)*dr*dp;
            t.mass+=state[k].density*area;t.angular+=state[k].angular*area;t.boundaryMass+=account[4*k];t.boundaryAngular+=account[4*k+1];t.stirringAngular+=account[4*k+2];}
        t.initialMass=initialMass_;t.initialAngular=initialAngular_;t.massError=t.mass-t.initialMass-t.boundaryMass;
        t.angularError=t.angular-t.initialAngular-t.boundaryAngular-t.stirringAngular;return t;
    }
};
}
