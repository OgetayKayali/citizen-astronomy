#include "cosmic/recording.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <stdexcept>

namespace cosmic {
namespace {
static_assert(std::endian::native==std::endian::little,"Cache format requires little-endian host.");
template<class T> void put(std::ostream& out,T value) { out.write(reinterpret_cast<char const*>(&value),sizeof(value)); }
template<class T> T get(std::istream& in) { T value{}; if(!in.read(reinterpret_cast<char*>(&value),sizeof(value))) throw std::runtime_error("Truncated Cosmic Flow cache."); return value; }
std::uint16_t pack(float f) {
    if(!std::isfinite(f)) throw std::runtime_error("Cannot cache nonfinite fields.");
    auto bits=std::bit_cast<std::uint32_t>(f); unsigned sign=(bits>>16)&0x8000;
    int e=static_cast<int>((bits>>23)&255)-127+15; unsigned m=bits&0x7fffff;
    if(e>=31) return static_cast<std::uint16_t>(sign|0x7bff);
    if(e<=0) { if(e<-10) return static_cast<std::uint16_t>(sign); m=(m|0x800000)>>(1-e); return static_cast<std::uint16_t>(sign+((m+0x1000)>>13)); }
    unsigned rounded=(static_cast<unsigned>(e)<<10)+((m+0x1000)>>13);
    return static_cast<std::uint16_t>(sign|std::min(rounded,0x7bffu));
}
float unpack(std::uint16_t bits) {
    unsigned sign=static_cast<unsigned>(bits&0x8000)<<16,m=bits&1023; int e=(bits>>10)&31;
    if(e==31) throw std::runtime_error("Invalid numeric data in cache.");
    if(e==0) return std::copysign(static_cast<float>(m)*std::ldexp(1.f,-24),sign?-1.f:1.f);
    return std::bit_cast<float>(sign|(static_cast<unsigned>(e+112)<<23)|(m<<13));
}
std::uint32_t checksum(std::vector<std::uint16_t> const& data) {
    std::uint32_t h=2166136261u;
    for(auto value:data) { h=(h^(value&255))*16777619u; h=(h^(value>>8))*16777619u; }
    return h;
}
void putCosmology(std::ostream& out,Cosmology const& c) {
    put(out,std::uint32_t(c.enabled)); put(out,c.hubble); put(out,c.omegaDM); put(out,c.omegaB); put(out,c.omegaDE); put(out,c.w);
    put(out,c.boxMpc); put(out,c.startRedshift); put(out,c.sigma8); put(out,c.spectralIndex); put(out,std::uint32_t(c.mesh));
}
Cosmology getCosmology(std::istream& in) {
    Cosmology c; c.enabled=get<std::uint32_t>(in)!=0; c.hubble=get<float>(in); c.omegaDM=get<float>(in); c.omegaB=get<float>(in); c.omegaDE=get<float>(in); c.w=get<float>(in);
    c.boxMpc=get<float>(in); c.startRedshift=get<float>(in); c.sigma8=get<float>(in); c.spectralIndex=get<float>(in); c.mesh=static_cast<int>(get<std::uint32_t>(in));
    c.validate(); return c;
}
}
Frame capture(Flow const& flow,int grid,int tracerLimit,float gasDensity) {
    Frame frame; int n=flow.resolution(); frame.grid=std::clamp(grid,12,n);
    frame.time=flow.time(); frame.scaleFactor=flow.scaleFactor(); frame.cosmology=flow.cosmology(); frame.gasDensity=gasDensity; frame.stats=flow.statistics();
    frame.clusterBox=flow.boxSize();
    auto source=flow.volume();
    if(frame.grid==n) frame.volume=std::move(source);
    else {
        int g=frame.grid; frame.volume.resize(static_cast<std::size_t>(g)*g*g*4);
        #pragma omp parallel for schedule(static)
        for(int z=0;z<g;++z) for(int y=0;y<g;++y) for(int x=0;x<g;++x) {
            float fx=(x+.5f)*n/g-.5f,fy=(y+.5f)*n/g-.5f,fz=(z+.5f)*n/g-.5f;
            int ix=static_cast<int>(fx),iy=static_cast<int>(fy),iz=static_cast<int>(fz); fx-=ix; fy-=iy; fz-=iz;
            for(int k=0;k<4;++k) {
                float v=0;
                for(int dz=0;dz<2;++dz) for(int dy=0;dy<2;++dy) for(int dx=0;dx<2;++dx) {
                    int i=((std::min(iz+dz,n-1)*n+std::min(iy+dy,n-1))*n+std::min(ix+dx,n-1))*4+k;
                    v+=source[i]*(dx?fx:1-fx)*(dy?fy:1-fy)*(dz?fz:1-fz);
                }
                frame.volume[((z*g+y)*g+x)*4+k]=v;
            }
        }
    }
    int count=std::min(std::max(0,tracerLimit),static_cast<int>(flow.tracers().size())); frame.tracers.resize(count);
    for(int i=0;i<count;++i) {
        auto tracer=flow.tracers()[static_cast<std::size_t>(i)*flow.tracers().size()/count];
        // Rendering and cache coordinates always span [-1,1]; solver positions
        // remain in cluster code units so changing box size affects dynamics.
        tracer.position=tracer.position*(2.f/flow.boxSize());
        if(frame.cosmology.enabled) {
            float velocityScale=frame.cosmology.boxMpc*.5f/.001022712165f/1500.f;
            tracer.density*=.08f/frame.cosmology.omegaB; tracer.speed*=velocityScale; tracer.temperature*=velocityScale*velocityScale;
        }
        frame.tracers[i]=tracer;
    }
    return frame;
}
std::uint64_t CacheInfo::frameBytes() const { return 20+std::uint64_t(grid)*grid*grid*8+std::uint64_t(tracers)*18; }
CacheWriter::CacheWriter(std::filesystem::path const& path,Frame const& first,std::string description) {
    if(description.size()>65536) throw std::runtime_error("Cache metadata too long.");
    if(path.has_parent_path()) std::filesystem::create_directories(path.parent_path());
    file_.open(path,std::ios::binary|std::ios::trunc); if(!file_) throw std::runtime_error("Cannot create cache: "+path.string());
    info_={first.grid,static_cast<int>(first.tracers.size()),0,first.gasDensity,first.cosmology,std::move(description)};
    info_.clusterBox=first.clusterBox;
    file_.write("CFLOW004",8); put(file_,std::uint32_t(0)); put(file_,std::uint32_t(info_.grid)); put(file_,std::uint32_t(info_.tracers)); put(file_,info_.gasDensity);
    put(file_,info_.clusterBox);
    putCosmology(file_,info_.cosmology); put(file_,std::uint32_t(info_.description.size())); file_.write(info_.description.data(),static_cast<std::streamsize>(info_.description.size()));
    if(!file_) throw std::runtime_error("Could not write cache header.");
}
void CacheWriter::append(Frame const& frame) {
    if(frame.grid!=info_.grid || frame.clusterBox!=info_.clusterBox || frame.tracers.size()!=static_cast<std::size_t>(info_.tracers) || frame.volume.size()!=std::size_t(info_.grid)*info_.grid*info_.grid*4) throw std::runtime_error("Cache frame dimensions changed.");
    if(!std::isfinite(frame.time)||!std::isfinite(frame.scaleFactor)||frame.scaleFactor<=0) throw std::runtime_error("Invalid cache time.");
    std::vector<std::uint16_t> packed; packed.reserve(frame.volume.size()+frame.tracers.size()*9);
    for(float v:frame.volume) packed.push_back(pack(v));
    for(auto const& t:frame.tracers) for(float v:{t.position.x,t.position.y,t.position.z,t.density,t.speed,t.temperature,t.velocity.x,t.velocity.y,t.velocity.z}) packed.push_back(pack(v));
    put(file_,frame.time); put(file_,frame.scaleFactor); put(file_,checksum(packed));
    file_.write(reinterpret_cast<char const*>(packed.data()),static_cast<std::streamsize>(packed.size()*2));
    file_.flush(); if(!file_) throw std::runtime_error("Cache write failed (disk full or unavailable).");
    ++info_.frames; auto end=file_.tellp(); file_.seekp(8); put(file_,std::uint32_t(info_.frames)); file_.flush(); file_.seekp(end);
    if(!file_) throw std::runtime_error("Could not finalize cache frame.");
}
CacheReader::CacheReader(std::filesystem::path const& path) {
    file_.open(path,std::ios::binary); if(!file_) throw std::runtime_error("Cannot open cache: "+path.string());
    char magic[8]{}; file_.read(magic,8); bool legacy=std::memcmp(magic,"CFLOW003",8)==0;
    if(!legacy && std::memcmp(magic,"CFLOW004",8)!=0) throw std::runtime_error("Not a supported Cosmic Flow cache.");
    info_.frames=static_cast<int>(get<std::uint32_t>(file_)); info_.grid=static_cast<int>(get<std::uint32_t>(file_)); info_.tracers=static_cast<int>(get<std::uint32_t>(file_)); info_.gasDensity=get<float>(file_);
    info_.clusterBox=legacy?2.f:get<float>(file_);
    if(!std::isfinite(info_.clusterBox)||info_.clusterBox<2||info_.clusterBox>8) throw std::runtime_error("Invalid cached cluster volume.");
    if(info_.frames<1||info_.frames>100000||info_.grid<12||info_.grid>160||info_.tracers<0||info_.tracers>1000000||!std::isfinite(info_.gasDensity)||info_.gasDensity<=0) throw std::runtime_error("Invalid cache header.");
    info_.cosmology=getCosmology(file_); auto bytes=get<std::uint32_t>(file_); if(bytes>65536) throw std::runtime_error("Invalid cache metadata.");
    info_.description.resize(bytes); file_.read(info_.description.data(),bytes); offset_=static_cast<std::uint64_t>(file_.tellg());
    if(!file_ || std::filesystem::file_size(path)<offset_+info_.frameBytes()*info_.frames) throw std::runtime_error("Incomplete cache file.");
}
Frame CacheReader::read(int index) {
    if(index<0||index>=info_.frames) throw std::out_of_range("Cache frame out of range.");
    file_.clear(); file_.seekg(static_cast<std::streamoff>(offset_+info_.frameBytes()*index));
    Frame frame; frame.grid=info_.grid; frame.gasDensity=info_.gasDensity; frame.cosmology=info_.cosmology;
    frame.clusterBox=info_.clusterBox;
    frame.time=get<double>(file_); frame.scaleFactor=get<double>(file_); auto expected=get<std::uint32_t>(file_);
    if(!std::isfinite(frame.time)||!std::isfinite(frame.scaleFactor)||frame.scaleFactor<=0) throw std::runtime_error("Invalid cached time.");
    std::vector<std::uint16_t> packed(static_cast<std::size_t>((info_.frameBytes()-20)/2));
    file_.read(reinterpret_cast<char*>(packed.data()),static_cast<std::streamsize>(packed.size()*2));
    if(!file_ || checksum(packed)!=expected) throw std::runtime_error("Damaged cache frame.");
    frame.volume.resize(static_cast<std::size_t>(info_.grid)*info_.grid*info_.grid*4); frame.tracers.resize(info_.tracers);
    std::size_t at=0; for(auto& value:frame.volume) value=unpack(packed[at++]);
    for(auto& t:frame.tracers) { t.position={unpack(packed[at]),unpack(packed[at+1]),unpack(packed[at+2])}; t.density=unpack(packed[at+3]); t.speed=unpack(packed[at+4]); t.temperature=unpack(packed[at+5]); t.velocity={unpack(packed[at+6]),unpack(packed[at+7]),unpack(packed[at+8])}; at+=9; }
    return frame;
}
}
