#pragma once
#include "flow.hpp"
#include <condition_variable>
#include <mutex>
#include <thread>
#include <memory>
#include <deque>
#include <string>
#include <stdexcept>

namespace blackhole_view {
inline constexpr int historyLayers=129;
inline constexpr double historySpacing=4,historySpan=512;
struct FlowSnapshot {
    int index=0;
    std::vector<float> gas,tracers;
    blackhole::FlowTotals totals;
    std::vector<float> meanFlux;
};
// The worker never touches OpenGL. Configuration changes cancel obsolete work
// between fixed ticks; the render thread owns texture uploads and the UI.
class FlowWorker {
    std::mutex mutex_;
    std::condition_variable changed_;
    std::thread thread_;
    bool stop_=false;
    unsigned generation_=0;
    blackhole::FlowConfig config_;
    double target_=0,latest_=-1e30;
    std::string error_;
    std::deque<FlowSnapshot> snapshots_;
    void run() {
        unsigned active=0;std::unique_ptr<blackhole::DiskFlow> flow;
        int ticks=0;
        while(true) {
            blackhole::FlowConfig config;unsigned generation;double target;
            {
                std::unique_lock lock(mutex_);
                changed_.wait(lock,[&] {return stop_ || active!=generation_ || (generation_!=0 && error_.empty() && latest_<target_);});
                if(stop_) return;
                config=config_;generation=generation_;target=target_;
            }
            try {
                if(active!=generation) { active=generation;flow=std::make_unique<blackhole::DiskFlow>(config);ticks=0; }
                if(!flow) continue;
                if(ticks%16==0) {
                    int index=ticks/16-128;
                    FlowSnapshot snapshot{index,flow->image(),flow->tracerImage(),flow->totals(),{}};
                    snapshot.meanFlux.resize(config.radial);
                    for(int i=0;i<config.radial;++i) {
                        double sum=0;
                        for(int j=0;j<config.azimuthal;++j) sum+=snapshot.gas[(static_cast<size_t>(i)*config.azimuthal+j)*4+1];
                        snapshot.meanFlux[i]=static_cast<float>(sum/config.azimuthal);
                    }
                    std::lock_guard lock(mutex_);
                    if(generation_!=active) continue;
                    latest_=index*historySpacing;
                    snapshots_.push_back(std::move(snapshot));
                    while(snapshots_.size()>historyLayers) snapshots_.pop_front();
                    changed_.notify_all();
                }
                // Continue only if a later snapshot is requested; otherwise sleep.
                if(ticks*.25-historySpan>=target) {
                    std::unique_lock lock(mutex_);
                    changed_.wait(lock,[&] {return stop_ || generation_!=active || target_>ticks*.25-historySpan;});
                    if(stop_) return;
                    if(generation_!=active) continue;
                }
                flow->tick();++ticks;
            } catch(std::exception const& e) {
                std::lock_guard lock(mutex_);if(generation_==active) error_=e.what();changed_.notify_all();
            }
        }
    }
public:
    FlowWorker():thread_([this]{run();}) {}
    ~FlowWorker() { {std::lock_guard lock(mutex_);stop_=true;} changed_.notify_all();thread_.join(); }
    void reset(blackhole::FlowConfig const& c,double target) {
        std::lock_guard lock(mutex_);config_=c;target_=target;latest_=-1e30;error_.clear();snapshots_.clear();++generation_;changed_.notify_all();
    }
    void request(double target) {std::lock_guard lock(mutex_);target_=std::max(target_,target);changed_.notify_all();}
    void wait(double target) {
        std::unique_lock lock(mutex_);changed_.wait(lock,[&]{return latest_>=target || !error_.empty();});
        if(!error_.empty()) throw std::runtime_error(error_);
    }
    std::deque<FlowSnapshot> take() {
        std::lock_guard lock(mutex_);if(!error_.empty()) throw std::runtime_error(error_);
        std::deque<FlowSnapshot> result;result.swap(snapshots_);return result;
    }
};
}
