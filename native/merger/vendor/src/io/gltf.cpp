#include "io/gltf.hpp"

#include "io/sphere.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace physics {
namespace {

std::string base64_encode(unsigned char const* data, std::size_t size) {
    static constexpr char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((size + 2) / 3) * 4);
    for (std::size_t i = 0; i < size; i += 3) {
        const unsigned int b0 = data[i];
        const unsigned int b1 = i + 1 < size ? data[i + 1] : 0;
        const unsigned int b2 = i + 2 < size ? data[i + 2] : 0;
        const unsigned int triple = (b0 << 16) | (b1 << 8) | b2;
        out.push_back(table[(triple >> 18) & 63]);
        out.push_back(table[(triple >> 12) & 63]);
        out.push_back(i + 1 < size ? table[(triple >> 6) & 63] : '=');
        out.push_back(i + 2 < size ? table[triple & 63] : '=');
    }
    return out;
}

void append_f32(std::vector<unsigned char>& buffer, float value) {
    static_assert(sizeof(float) == 4, "glTF export assumes IEEE-754 binary32");
    unsigned char bytes[sizeof(float)];
    std::memcpy(bytes, &value, sizeof(value));
    buffer.insert(buffer.end(), bytes, bytes + sizeof(bytes));
}

void append_u32(std::vector<unsigned char>& buffer, std::uint32_t value) {
    buffer.push_back(static_cast<unsigned char>(value & 0xffu));
    buffer.push_back(static_cast<unsigned char>((value >> 8) & 0xffu));
    buffer.push_back(static_cast<unsigned char>((value >> 16) & 0xffu));
    buffer.push_back(static_cast<unsigned char>((value >> 24) & 0xffu));
}

std::size_t pad4(std::vector<unsigned char>& buffer) {
    while (buffer.size() % 4 != 0) {
        buffer.push_back(0);
    }
    return buffer.size();
}

std::string json_string(std::string const& text) {
    std::string out = "\"";
    for (char character : text) {
        switch (character) {
            case '\\':
                out += "\\\\";
                break;
            case '"':
                out += "\\\"";
                break;
            default:
                out.push_back(character);
                break;
        }
    }
    out.push_back('"');
    return out;
}

Vec3 to_gltf(Vec3 physics) { return {physics.x, physics.z, -physics.y}; }

}  // namespace

GltfExport build_gltf(Trajectory const& trajectory) {
    if (trajectory.bodies.empty() || trajectory.samples.empty()) {
        throw std::invalid_argument("glTF export needs at least one body and one sample");
    }

    GltfExport export_result;
    export_result.body_count = trajectory.bodies.size();
    export_result.translations.resize(trajectory.bodies.size());

    const SphereMesh sphere = make_uv_sphere(16, 24, SphereUp::Y);
    std::vector<unsigned char> buffer;
    const std::size_t position_offset = pad4(buffer);
    float min_position[3] = {1e30f, 1e30f, 1e30f};
    float max_position[3] = {-1e30f, -1e30f, -1e30f};
    for (std::size_t i = 0; i < sphere.positions.size(); i += 3) {
        for (int axis = 0; axis < 3; ++axis) {
            const float value = sphere.positions[i + static_cast<std::size_t>(axis)];
            min_position[axis] = std::min(min_position[axis], value);
            max_position[axis] = std::max(max_position[axis], value);
            append_f32(buffer, value);
        }
    }
    const std::size_t normal_offset = pad4(buffer);
    for (float value : sphere.normals) {
        append_f32(buffer, value);
    }
    const std::size_t index_offset = pad4(buffer);
    for (std::uint32_t index : sphere.indices) {
        append_u32(buffer, index);
    }

    struct TrailView {
        std::size_t offset = 0;
        std::size_t count = 0;
        float min_position[3]{};
        float max_position[3]{};
    };
    std::vector<TrailView> trails(trajectory.bodies.size());
    for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
        TrailView& trail = trails[body];
        trail.offset = pad4(buffer);
        trail.min_position[0] = trail.min_position[1] = trail.min_position[2] = 1e30f;
        trail.max_position[0] = trail.max_position[1] = trail.max_position[2] = -1e30f;
        const std::size_t segments = trajectory.samples.size() < 2 ? 1 : trajectory.samples.size() - 1;
        for (std::size_t sample_index = 0; sample_index < segments; ++sample_index) {
            const std::size_t next = std::min(sample_index + 1, trajectory.samples.size() - 1);
            const Vec3 a = to_gltf(trajectory.samples[sample_index].position[body]);
            const Vec3 b = to_gltf(trajectory.samples[next].position[body]);
            const float coordinates[6] = {static_cast<float>(a.x), static_cast<float>(a.y), static_cast<float>(a.z),
                                          static_cast<float>(b.x), static_cast<float>(b.y), static_cast<float>(b.z)};
            for (int axis = 0; axis < 3; ++axis) {
                trail.min_position[axis] = std::min(trail.min_position[axis], coordinates[axis]);
                trail.min_position[axis] = std::min(trail.min_position[axis], coordinates[axis + 3]);
                trail.max_position[axis] = std::max(trail.max_position[axis], coordinates[axis]);
                trail.max_position[axis] = std::max(trail.max_position[axis], coordinates[axis + 3]);
            }
            for (float value : coordinates) {
                append_f32(buffer, value);
            }
            trail.count += 2;
        }
    }

    const double t0 = trajectory.samples.front().time;
    std::vector<float> times;
    times.reserve(trajectory.samples.size());
    for (Sample const& sample : trajectory.samples) {
        times.push_back(static_cast<float>(sample.time - t0));
    }
    bool animate = times.size() >= 2;
    for (std::size_t i = 1; animate && i < times.size(); ++i) {
        if (!(times[i] >= times[i - 1])) {
            animate = false;
        }
    }
    const std::size_t time_offset = pad4(buffer);
    if (animate) {
        export_result.animated_bodies = trajectory.bodies.size();
        for (float time : times) {
            append_f32(buffer, time);
        }
    }
    std::vector<std::size_t> translation_offsets(trajectory.bodies.size(), 0);
    if (animate) {
        for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
            translation_offsets[body] = pad4(buffer);
            auto& packed = export_result.translations[body];
            packed.reserve(trajectory.samples.size() * 3);
            for (Sample const& sample : trajectory.samples) {
                const Vec3 translated = to_gltf(sample.position[body]);
                const float coordinates[3] = {static_cast<float>(translated.x), static_cast<float>(translated.y),
                                              static_cast<float>(translated.z)};
                for (float value : coordinates) {
                    packed.push_back(value);
                    append_f32(buffer, value);
                }
            }
        }
    }

    const std::string encoded = base64_encode(buffer.data(), buffer.size());
    const std::size_t sphere_vertices = sphere.positions.size() / 3;

    std::ostringstream json;
    json << "{\n  \"asset\": {\"version\": \"2.0\", \"generator\": \"PhysicsEngine\"},\n";
    json << "  \"scene\": 0,\n";
    const bool hdr_emission = std::any_of(trajectory.bodies.begin(), trajectory.bodies.end(), [](BodyRecord const &body) {
        return std::max({body.color.x, body.color.y, body.color.z}) * body.emission > 1;
    });
    if (hdr_emission)
        json << "  \"extensionsUsed\": [\"KHR_materials_emissive_strength\"],\n";
    json << "  \"scenes\": [{\"nodes\": [";
    const std::size_t node_count = trajectory.bodies.size() + trajectory.bodies.size();
    for (std::size_t node = 0; node < node_count; ++node) {
        if (node > 0) {
            json << ", ";
        }
        json << node;
    }
    json << "]}],\n";

    json << "  \"nodes\": [\n";
    for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
        const Vec3 first = to_gltf(trajectory.samples.front().position[body]);
        const double radius = trajectory.bodies[body].display_radius;
        if (body > 0) {
            json << ",\n";
        }
        json << "    {\"name\": " << json_string(trajectory.bodies[body].name) << ", \"mesh\": " << body
             << ", \"translation\": [" << static_cast<float>(first.x) << ", " << static_cast<float>(first.y) << ", "
             << static_cast<float>(first.z) << "], \"scale\": [" << radius << ", " << radius << ", " << radius
             << "]}";
    }
    for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
        json << ",\n    {\"name\": " << json_string(trajectory.bodies[body].name + "_trail")
             << ", \"mesh\": " << (trajectory.bodies.size() + body) << "}";
    }
    json << "\n  ],\n";

    json << "  \"materials\": [\n";
    for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
        const Vec3 color = trajectory.bodies[body].color;
        const double emission = trajectory.bodies[body].emission;
        const double peak = std::max({color.x, color.y, color.z});
        const auto base = color / std::max(1.0, peak);
        const double strength = std::max(1.0, peak * emission);
        const auto emissive = color * (emission / strength);
        json << "    {\"pbrMetallicRoughness\": {\"baseColorFactor\": [" << base.x << ", " << base.y << ", "
             << base.z << ", 1], \"metallicFactor\": 0, \"roughnessFactor\": " << trajectory.bodies[body].roughness
             << "}, \"emissiveFactor\": [" << emissive.x << ", " << emissive.y << ", "
             << emissive.z << "], \"doubleSided\": true";
        if (strength > 1)
            json << ", \"extensions\": {\"KHR_materials_emissive_strength\": {\"emissiveStrength\": " << strength << "}}";
        json << "}";
        if (body + 1 != trajectory.bodies.size()) {
            json << ",";
        }
        json << "\n";
    }
    json << "  ],\n";

    json << "  \"meshes\": [\n";
    for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
        if (body > 0) {
            json << ",\n";
        }
        json << "    {\"primitives\": [{\"attributes\": {\"POSITION\": 0, \"NORMAL\": 1}, \"indices\": 2, "
                "\"material\": "
             << body << ", \"mode\": 4}]}";
    }
    for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
        const std::size_t accessor = 3 + body;
        json << ",\n    {\"primitives\": [{\"attributes\": {\"POSITION\": " << accessor << "}, \"mode\": 1}]}";
    }
    json << "\n  ],\n";

    // Accessors: 0 positions, 1 normals, 2 indices, then one trail, then times, then translations.
    json << "  \"accessors\": [\n";
    json << "    {\"bufferView\": 0, \"componentType\": 5126, \"count\": " << sphere_vertices
         << ", \"type\": \"VEC3\", \"min\": [" << min_position[0] << ", " << min_position[1] << ", " << min_position[2]
         << "], \"max\": [" << max_position[0] << ", " << max_position[1] << ", " << max_position[2] << "]},\n";
    json << "    {\"bufferView\": 1, \"componentType\": 5126, \"count\": " << sphere_vertices
         << ", \"type\": \"VEC3\"},\n";
    json << "    {\"bufferView\": 2, \"componentType\": 5125, \"count\": " << sphere.indices.size()
         << ", \"type\": \"SCALAR\"}";

    std::size_t buffer_view = 3;
    for (std::size_t body = 0; body < trails.size(); ++body) {
        json << ",\n    {\"bufferView\": " << buffer_view << ", \"componentType\": 5126, \"count\": "
             << (trails[body].count == 0 ? 0 : trails[body].count) << ", \"type\": \"VEC3\"";
        if (trails[body].count > 0) {
            json << ", \"min\": [" << trails[body].min_position[0] << ", " << trails[body].min_position[1] << ", "
                 << trails[body].min_position[2] << "], \"max\": [" << trails[body].max_position[0] << ", "
                 << trails[body].max_position[1] << ", " << trails[body].max_position[2] << "]";
        }
        json << "}";
        ++buffer_view;
    }
    const std::size_t time_accessor = 3 + trails.size();
    const std::size_t first_translation_accessor = time_accessor + (animate ? 1 : 0);
    if (animate) {
        json << ",\n    {\"bufferView\": " << buffer_view << ", \"componentType\": 5126, \"count\": " << times.size()
             << ", \"type\": \"SCALAR\", \"min\": [" << times.front() << "], \"max\": [" << times.back() << "]}";
        ++buffer_view;
        for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
            float min_translation[3] = {1e30f, 1e30f, 1e30f};
            float max_translation[3] = {-1e30f, -1e30f, -1e30f};
            auto const& packed = export_result.translations[body];
            for (std::size_t i = 0; i < packed.size(); i += 3) {
                for (int axis = 0; axis < 3; ++axis) {
                    min_translation[axis] = std::min(min_translation[axis], packed[i + static_cast<std::size_t>(axis)]);
                    max_translation[axis] = std::max(max_translation[axis], packed[i + static_cast<std::size_t>(axis)]);
                }
            }
            json << ",\n    {\"bufferView\": " << buffer_view << ", \"componentType\": 5126, \"count\": " << times.size()
                 << ", \"type\": \"VEC3\", \"min\": [" << min_translation[0] << ", " << min_translation[1] << ", "
                 << min_translation[2] << "], \"max\": [" << max_translation[0] << ", " << max_translation[1] << ", "
                 << max_translation[2] << "]}";
            ++buffer_view;
        }
    }
    json << "\n  ],\n";

    json << "  \"bufferViews\": [\n";
    json << "    {\"buffer\": 0, \"byteOffset\": " << position_offset << ", \"byteLength\": " << (sphere_vertices * 12)
         << "},\n";
    json << "    {\"buffer\": 0, \"byteOffset\": " << normal_offset << ", \"byteLength\": " << (sphere_vertices * 12)
         << "},\n";
    json << "    {\"buffer\": 0, \"byteOffset\": " << index_offset
         << ", \"byteLength\": " << (sphere.indices.size() * 4) << "}";
    for (TrailView const& trail : trails) {
        json << ",\n    {\"buffer\": 0, \"byteOffset\": " << trail.offset
             << ", \"byteLength\": " << (trail.count * 12) << "}";
    }
    if (animate) {
        json << ",\n    {\"buffer\": 0, \"byteOffset\": " << time_offset << ", \"byteLength\": " << (times.size() * 4)
             << "}";
        for (std::size_t offset : translation_offsets) {
            json << ",\n    {\"buffer\": 0, \"byteOffset\": " << offset
                 << ", \"byteLength\": " << (times.size() * 12) << "}";
        }
    }
    json << "\n  ],\n";
    json << "  \"buffers\": [{\"byteLength\": " << buffer.size() << ", \"uri\": \"data:application/octet-stream;base64,"
         << encoded << "\"}]";

    if (animate) {
        json << ",\n  \"animations\": [{\"name\": \"trajectories\", \"samplers\": [";
        for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
            if (body > 0) {
                json << ", ";
            }
            json << "{\"input\": " << time_accessor << ", \"output\": " << (first_translation_accessor + body)
                 << ", \"interpolation\": \"LINEAR\"}";
        }
        json << "], \"channels\": [";
        for (std::size_t body = 0; body < trajectory.bodies.size(); ++body) {
            if (body > 0) {
                json << ", ";
            }
            json << "{\"sampler\": " << body << ", \"target\": {\"node\": " << body
                 << ", \"path\": \"translation\"}}";
        }
        json << "]}]";
    }
    json << "\n}\n";
    export_result.json = json.str();
    return export_result;
}

void write_gltf(Trajectory const& trajectory, std::string const& path) {
    const GltfExport document = build_gltf(trajectory);
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("could not write glTF '" + path + "'");
    }
    output << document.json;
}

}  // namespace physics
