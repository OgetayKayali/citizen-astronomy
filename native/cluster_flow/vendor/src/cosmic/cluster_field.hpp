#pragma once
#include "cosmic/flow.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>

namespace cosmic::detail {
inline Vector minimumImage(Vector d,float side) {
    d.x-=side*std::round(d.x/side); d.y-=side*std::round(d.y/side); d.z-=side*std::round(d.z/side); return d;
}
// Periodic lookup of local density primitives. Distant Gaussian tails are
// negligible; gravity is evaluated separately and retains distant halos.
class ClusterBins {
public:
    explicit ClusterBins(float side):side_(side),n_(static_cast<int>(std::ceil(side/.4f))),bins_(n_*n_*n_) {}
    void add(int id,Vector lo,Vector hi) {
        for(int z=cell(lo.z);z<=cell(hi.z);++z) for(int y=cell(lo.y);y<=cell(hi.y);++y) for(int x=cell(lo.x);x<=cell(hi.x);++x) bins_[index(x,y,z)].push_back(id);
    }
    void finish() { for(auto& bin:bins_) { std::sort(bin.begin(),bin.end()); bin.erase(std::unique(bin.begin(),bin.end()),bin.end()); } }
    std::vector<int> const& at(Vector p) const { return bins_[index(cell(p.x),cell(p.y),cell(p.z))]; }
private:
    float side_; int n_; std::vector<std::vector<int>> bins_;
    int cell(float x) const { return static_cast<int>(std::floor((x+side_*.5f)*n_/side_)); }
    int index(int x,int y,int z) const { auto wrap=[&](int t){ return (t%n_+n_)%n_; }; return (wrap(z)*n_+wrap(y))*n_+wrap(x); }
};
// Static Barnes-Hut field for expanded cluster volumes. Default-size scenes
// retain their original direct force sum. Recurse across periodic image seams.
class ClusterHaloTree {
    struct Branch { Vector center{},com{}; float half=0,mass=0; std::array<int,8> children{-1,-1,-1,-1,-1,-1,-1,-1}; std::vector<int> leaves; };
    std::vector<Node> const& nodes_; float side_,soft_; std::vector<Branch> tree_;
    int build(std::vector<int> const& ids,Vector center,float half) {
        int index=static_cast<int>(tree_.size()); tree_.emplace_back(); tree_[index].center=center; tree_[index].half=half;
        Vector sum{}; float mass=0;
        for(int i:ids) { sum=sum+nodes_[i].position*nodes_[i].mass; mass+=nodes_[i].mass; }
        tree_[index].mass=mass; tree_[index].com=sum*(1/mass);
        if(ids.size()<=8 || half<.02f) { tree_[index].leaves=ids; return index; }
        std::array<std::vector<int>,8> buckets;
        for(int i:ids) { auto p=nodes_[i].position; int oct=(p.x>=center.x?1:0)|(p.y>=center.y?2:0)|(p.z>=center.z?4:0); buckets[oct].push_back(i); }
        for(int oct=0;oct<8;++oct) if(!buckets[oct].empty()) {
            Vector child=center+Vector{oct&1?half*.5f:-half*.5f,oct&2?half*.5f:-half*.5f,oct&4?half*.5f:-half*.5f};
            int c=build(buckets[oct],child,half*.5f); tree_[index].children[oct]=c;
        }
        return index;
    }
    Vector pull(Vector d,float mass) const { float r2=d.x*d.x+d.y*d.y+d.z*d.z+soft_*soft_; return d*(.0012f*mass/(r2*std::sqrt(r2))); }
    Vector evaluate(int index,Vector p) const {
        auto const& b=tree_[index]; Vector result{};
        if(!b.leaves.empty()) { for(int i:b.leaves) result=result+pull(minimumImage(nodes_[i].position-p,side_),nodes_[i].mass); return result; }
        auto d=minimumImage(b.com-p,side_),center=minimumImage(b.center-p,side_);
        bool sameImage=std::abs(center.x)+b.half<side_*.5f && std::abs(center.y)+b.half<side_*.5f && std::abs(center.z)+b.half<side_*.5f;
        if(sameImage && b.half*2<.4f*length(d)) return pull(d,b.mass);
        for(int c:b.children) if(c>=0) result=result+evaluate(c,p);
        return result;
    }
public:
    ClusterHaloTree(std::vector<Node> const& nodes,float side,float soft):nodes_(nodes),side_(side),soft_(soft) {
        std::vector<int> ids(nodes.size()); std::iota(ids.begin(),ids.end(),0); build(ids,{},side*.5f);
    }
    Vector acceleration(Vector p) const { return evaluate(0,p); }
};
}
