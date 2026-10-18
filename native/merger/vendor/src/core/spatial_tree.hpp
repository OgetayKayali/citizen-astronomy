#pragma once
#include "core/vec3.hpp"
#include <algorithm>
#include <limits>
#include <queue>
#include <utility>
#include <vector>

namespace physics {
// Immutable median-split tree. Exact nearest neighbors; optional monopole
// gravity for display tracers only. Simulation gravity remains direct.
class SpatialTree {
    struct Node {
        Vec3 low{}, high{}, center{};
        double mass = 0, width = 0;
        std::size_t begin = 0, end = 0;
        int left = -1, right = -1;
    };
    std::vector<Vec3> const &points_;
    std::vector<double> const *masses_;
    std::vector<std::size_t> order_;
    std::vector<Node> nodes_;
    static double coordinate(Vec3 p, int axis) { return axis == 0 ? p.x : axis == 1 ? p.y : p.z; }
    static double box_distance(Vec3 p, Node const &n) {
        const double x = std::max({n.low.x - p.x, 0.0, p.x - n.high.x});
        const double y = std::max({n.low.y - p.y, 0.0, p.y - n.high.y});
        const double z = std::max({n.low.z - p.z, 0.0, p.z - n.high.z});
        return x * x + y * y + z * z;
    }
    int build(std::size_t begin, std::size_t end) {
        const int index = static_cast<int>(nodes_.size());
        nodes_.push_back({});
        Node node;
        node.begin = begin;
        node.end = end;
        node.low = node.high = points_[order_[begin]];
        for (auto k = begin; k < end; ++k) {
            const auto i = order_[k];
            const auto p = points_[i];
            node.low = {std::min(node.low.x, p.x), std::min(node.low.y, p.y), std::min(node.low.z, p.z)};
            node.high = {std::max(node.high.x, p.x), std::max(node.high.y, p.y), std::max(node.high.z, p.z)};
            const double m = masses_ ? (*masses_)[i] : 1;
            node.mass += m;
            node.center += p * m;
        }
        if (node.mass > 0)
            node.center = node.center / node.mass;
        const auto extent = node.high - node.low;
        node.width = std::max({extent.x, extent.y, extent.z});
        if (end - begin > 12) {
            const int axis = extent.x >= extent.y && extent.x >= extent.z ? 0 : extent.y >= extent.z ? 1 : 2;
            const auto middle = begin + (end - begin) / 2;
            std::nth_element(order_.begin() + begin, order_.begin() + middle, order_.begin() + end,
                             [&](auto a, auto b) {
                                 const double x = coordinate(points_[a], axis), y = coordinate(points_[b], axis);
                                 return x == y ? a < b : x < y;
                             });
            node.left = build(begin, middle);
            node.right = build(middle, end);
        }
        nodes_[index] = node;
        return index;
    }
    using Hit = std::pair<double, std::size_t>;
    void search(int index, Vec3 p, std::size_t count, std::size_t skip, std::priority_queue<Hit> &hits) const {
        const auto &node = nodes_[index];
        if (hits.size() == count && box_distance(p, node) > hits.top().first)
            return;
        if (node.left < 0) {
            for (auto k = node.begin; k < node.end; ++k) {
                const auto i = order_[k];
                if (i == skip)
                    continue;
                const auto d = points_[i] - p;
                hits.emplace(dot(d, d), i);
                if (hits.size() > count)
                    hits.pop();
            }
        } else {
            int first = node.left, second = node.right;
            if (box_distance(p, nodes_[first]) > box_distance(p, nodes_[second]))
                std::swap(first, second);
            search(first, p, count, skip, hits);
            search(second, p, count, skip, hits);
        }
    }
    Vec3 force(int index, Vec3 p, double epsilon2, double theta2) const {
        const auto &node = nodes_[index];
        const auto d = node.center - p;
        const double r2 = dot(d, d);
        if (node.left >= 0 && node.width * node.width < theta2 * r2 && box_distance(p, node) > 0) {
            const double softened = r2 + epsilon2;
            return d * (node.mass / (softened * std::sqrt(softened)));
        }
        if (node.left >= 0)
            return force(node.left, p, epsilon2, theta2) + force(node.right, p, epsilon2, theta2);
        Vec3 a{};
        for (auto k = node.begin; k < node.end; ++k) {
            const auto i = order_[k];
            const auto delta = points_[i] - p;
            const double softened = dot(delta, delta) + epsilon2;
            const double m = masses_ ? (*masses_)[i] : 1;
            if (softened > 0)
                a += delta * (m / (softened * std::sqrt(softened)));
        }
        return a;
    }

  public:
    SpatialTree(std::vector<Vec3> const &points, std::vector<std::size_t> indices,
                std::vector<double> const *masses = nullptr)
        : points_(points), masses_(masses), order_(std::move(indices)) {
        nodes_.reserve(order_.size() * 2);
        if (!order_.empty())
            build(0, order_.size());
    }
    std::vector<Hit> nearest(Vec3 p, std::size_t count,
                             std::size_t skip = std::numeric_limits<std::size_t>::max()) const {
        std::priority_queue<Hit> hits;
        if (!nodes_.empty() && count)
            search(0, p, count, skip, hits);
        std::vector<Hit> result(hits.size());
        for (auto i = hits.size(); i > 0; --i) {
            result[i - 1] = hits.top();
            hits.pop();
        }
        return result;
    }
    Vec3 acceleration(Vec3 p, double gravitational_constant, double softening, double theta = 0.4) const {
        return nodes_.empty() ? Vec3{} : force(0, p, softening * softening, theta * theta) * gravitational_constant;
    }
};
} // namespace physics
