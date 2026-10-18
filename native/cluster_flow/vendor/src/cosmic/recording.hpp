#pragma once
#include "cosmic/flow.hpp"
#include <filesystem>
#include <fstream>
#include <string>
namespace cosmic {
struct Frame {
    std::vector<float> volume;
    std::vector<Tracer> tracers;
    Statistics stats;
    Cosmology cosmology;
    int grid=64;
    double time=0,scaleFactor=1;
    float gasDensity=1,clusterBox=2;
};
Frame capture(Flow const& flow,int grid,int tracerLimit,float gasDensity=1);
struct CacheInfo {
    int grid=0,tracers=0,frames=0;
    float gasDensity=1;
    Cosmology cosmology;
    std::string description;
    float clusterBox=2;
    std::uint64_t frameBytes() const;
};
class CacheWriter {
public:
    CacheWriter(std::filesystem::path const& path,Frame const& first,std::string description);
    void append(Frame const& frame);
    [[nodiscard]] int count() const { return info_.frames; }
private:
    std::ofstream file_;
    CacheInfo info_;
};
class CacheReader {
public:
    explicit CacheReader(std::filesystem::path const& path);
    [[nodiscard]] CacheInfo const& info() const { return info_; }
    Frame read(int index);
private:
    std::ifstream file_;
    CacheInfo info_;
    std::uint64_t offset_=0;
};
}
