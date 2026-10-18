#pragma once
#include "blackhole/flow_history.hpp"
#include "gpu_flow.hpp"
#include "gl_loader.hpp"
#include <GLFW/glfw3.h>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <memory>
#include <deque>
#include <algorithm>
#include <cmath>
#include <chrono>

namespace blackhole_view {
class FlowTexture {
    using SubImage3D=void(APIENTRY*)(GLenum,GLint,GLint,GLint,GLint,GLsizei,GLsizei,GLsizei,GLenum,GLenum,void const*);
    SubImage3D upload_;
    std::unique_ptr<FlowWorker> worker_;
    std::unique_ptr<GpuFlow> gpu_;
    int backend_=0,publishedTick_=-1;
    std::string backendName_="Preparing";
    GLuint gas_=0,tracers_=0,profile_=0;
    blackhole::FlowConfig config_;
    bool initialized_=false;
    double oldest_=0,newest_=-1e30;
    void allocate(GLuint texture,GLint format,GLenum components) {
        glBindTexture(0x806F,texture);
        gl_tex_image_3d(0x806F,0,format,config_.azimuthal,config_.radial,historyLayers,0,components,GL_FLOAT,nullptr);
        glTexParameteri(0x806F,GL_TEXTURE_MIN_FILTER,GL_LINEAR);glTexParameteri(0x806F,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        glTexParameteri(0x806F,GL_TEXTURE_WRAP_S,GL_REPEAT);glTexParameteri(0x806F,GL_TEXTURE_WRAP_T,0x812F);glTexParameteri(0x806F,0x8072,0x812F);
    }
public:
    FlowTexture() {
        upload_=reinterpret_cast<SubImage3D>(glfwGetProcAddress("glTexSubImage3D"));
        if(!upload_) throw std::runtime_error("3D texture updates unavailable.");
        glGenTextures(1,&gas_);glGenTextures(1,&tracers_);glGenTextures(1,&profile_);
    }
    ~FlowTexture() {worker_.reset();glDeleteTextures(1,&gas_);glDeleteTextures(1,&tracers_);glDeleteTextures(1,&profile_);}
    double newest() const {return newest_;}
    std::string const& backendName() const {return backendName_;}
    bool usingGpu() const {return gpu_!=nullptr;}
    bool ready(double time) const {return newest_>=time;}
    bool advance(double& time,double step) const {
        double candidate=time+std::clamp(step,0.,historySpacing);
        if(!ready(candidate)) return false;
        time=candidate;return true;
    }
    void update(blackhole::FlowConfig const& config,double time,bool synchronous,bool reset,int backend=0) {
        // Keep two snapshots ahead during interactive playback. Export retains
        // its fixed one-snapshot lookahead for reproducible history bounds.
        double target=std::ceil(time/historySpacing)*historySpacing+historySpacing*(synchronous?1:2);
        if(!initialized_ || !(config==config_) || reset || backend!=backend_) {
            gpu_.reset();worker_.reset();backend_=backend;publishedTick_=-1;
            config_=config;oldest_=-historySpan;newest_=-1e30;
            gl_active_texture(0x84C0+2);allocate(gas_,0x881A,GL_RGBA);allocate(tracers_,0x822D,GL_RED); // RGBA16F and R16F
            gl_active_texture(0x84C0+4);glBindTexture(GL_TEXTURE_2D,profile_);
            glTexImage2D(GL_TEXTURE_2D,0,0x822D,config.radial,historyLayers,0,GL_RED,GL_FLOAT,nullptr);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,0x812F);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,0x812F);
            if(backend!=2) {
                try {gpu_=std::make_unique<GpuFlow>(config);backendName_="GPU";}
                catch(std::exception const& e) {
                    if(backend==1)throw;
                    backendName_=std::string("CPU fallback: ")+e.what();
                }
            } else backendName_="CPU reference";
            if(!gpu_) {worker_=std::make_unique<FlowWorker>();worker_->reset(config,target);}
            initialized_=true;
        }
        if(gpu_) {
            // The first solver readback also waits for the previous scene draw.
            // Charge only fluid work to its budget: a more expensive sky/disk
            // must not consume the whole allowance before preparation starts.
            // advanceTo already synchronizes; this moves that wait before timing.
            if(!synchronous && newest_<target) glFinish();
            auto started=std::chrono::steady_clock::now();
            while(newest_<target) {
                if(gpu_->ticks()%16==0 && publishedTick_!=gpu_->ticks() && gpu_->time()==gpu_->ticks()*.25) {
                    int index=gpu_->ticks()/16-128,layer=(index%historyLayers+historyLayers)%historyLayers;
                    gpu_->snapshot(gas_,tracers_,profile_,layer);publishedTick_=gpu_->ticks();
                    newest_=index*historySpacing;oldest_=std::max(-historySpan,newest_-historySpan);
                    if(newest_>=target)break;
                }
                double nextSnapshot=(gpu_->ticks()/16+1)*historySpacing;
                // During startup amortize the synchronous clock read over eight
                // substeps. Once current history exists, retain small batches.
                gpu_->advanceTo(nextSnapshot,synchronous || newest_<time?8:2);
                // Best-effort four-ms budget, checked between indivisible GPU
                // substeps. No unbounded startup bake on the interactive path.
                if(!synchronous && std::chrono::steady_clock::now()-started>=std::chrono::milliseconds(4))break;
            }
            return;
        }
        worker_->request(target);
        if(synchronous) worker_->wait(target);
        auto snapshots=worker_->take();
        for(auto& snapshot:snapshots) {
            int layer=(snapshot.index%historyLayers+historyLayers)%historyLayers;
            gl_active_texture(0x84C0+2);glBindTexture(0x806F,gas_);
            upload_(0x806F,0,0,0,layer,config_.azimuthal,config_.radial,1,GL_RGBA,GL_FLOAT,snapshot.gas.data());
            glBindTexture(0x806F,tracers_);
            upload_(0x806F,0,0,0,layer,config_.azimuthal,config_.radial,1,GL_RED,GL_FLOAT,snapshot.tracers.data());
            gl_active_texture(0x84C0+4);glBindTexture(GL_TEXTURE_2D,profile_);
            glTexSubImage2D(GL_TEXTURE_2D,0,0,layer,config_.radial,1,GL_RED,GL_FLOAT,snapshot.meanFlux.data());
            newest_=snapshot.index*historySpacing;oldest_=std::max(-historySpan,newest_-historySpan);
        }
    }
    void bind(GLuint program,int view,double time) const {
        auto integer=[&](char const* name,int value){gl_uniform_1i(gl_get_uniform_location(program,name),value);};
        auto real=[&](char const* name,float value){gl_uniform_1f(gl_get_uniform_location(program,name),value);};
        // Partial prehistory is not a playable frame: clamping delayed rays to
        // it makes the disk jump, then freeze region by region during startup.
        bool available=ready(time);
        integer("fluidEnabled",available?1:0);integer("fluidView",view);
        integer("fluidHistory",2);integer("tracerHistory",3);
        integer("radialProfile",4);
        real("historyOldest",static_cast<float>(oldest_));real("historyNewest",static_cast<float>(newest_));
        real("flowInner",static_cast<float>(config_.inner));real("flowOuter",static_cast<float>(config_.outer));
        gl_active_texture(0x84C0+2);glBindTexture(0x806F,gas_);gl_active_texture(0x84C0+3);glBindTexture(0x806F,tracers_);
        gl_active_texture(0x84C0+4);glBindTexture(GL_TEXTURE_2D,profile_);
    }
};
}
