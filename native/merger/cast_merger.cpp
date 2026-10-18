// CAst host for the PhysicsEngine galaxy merger (apps/viewer/main.cpp, physics_view).
// Simulation, rendering, recordings, looks and export are the viewer's own code
// (vendor/apps/viewer/viewer_core.hpp). Only the ImGui panel and GLFW window are
// replaced: CAst's Qt panel edits the same ViewState through this C API, and
// CAst's OpenGL window supplies the context and framebuffer.
#include "viewer_core.hpp"
#include "cast_sky.hpp"

#include <cstring>
#include <functional>
#include <unordered_map>

#define CAST_API extern "C" __declspec(dllexport)

GLFWglproc glfwGetProcAddress(char const* name) {
    auto proc = reinterpret_cast<GLFWglproc>(wglGetProcAddress(name));
    auto value = reinterpret_cast<std::intptr_t>(proc);
    if (value == 0 || value == 1 || value == 2 || value == 3 || value == -1) {
        static HMODULE library = LoadLibraryA("opengl32.dll");
        proc = library ? reinterpret_cast<GLFWglproc>(GetProcAddress(library, name)) : nullptr;
    }
    return proc;
}
double glfwGetTime() {
    static auto const start = std::chrono::steady_clock::now();
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

namespace {

thread_local std::string lastError;

int copyText(std::string const& text, char* buffer, int size) {
    if (buffer && size > 0) {
        const std::size_t count = std::min(text.size(), static_cast<std::size_t>(size - 1));
        std::memcpy(buffer, text.data(), count);
        buffer[count] = 0;
    }
    return static_cast<int>(text.size());
}

template <class Function> int guarded(Function&& function) {
    try {
        return function();
    } catch (std::exception const& e) {
        lastError = e.what();
        return 0;
    } catch (...) {
        lastError = "Unknown galaxy merger engine error.";
        return 0;
    }
}

struct Session {
    ViewState view;
    SceneGpu gpu;
    ExportTarget export_target;
    std::vector<unsigned char> export_pixels;
    std::vector<physics::Vec3> positions;
    physics::Diagnostics diagnostics;
    std::vector<GLuint> programs, arrays, buffers;
    cast_sky::Sky sky;

    // main(): programs, sphere mesh and vertex layouts, uniform locations.
    Session() {
        const GLuint lit_program = link_program(kLitVertex, kLitFragment);
        const GLuint color_program = link_program(kColorVertex, kColorFragment);
        const GLuint star_program = link_program(kStarVertex, kStarFragment);
        const GLuint field_program = link_program(kFieldVertex, kFieldFragment);
        const GLuint composite_program = link_program(kCompositeVertex, kCompositeFragment);
        const GLuint blur_program = link_program(kCompositeVertex, kBlurFragment);
        const GLuint volume_program = link_program(kVolumeVertex, kVolumeFragment);
        programs = {lit_program, color_program, star_program, field_program, composite_program, blur_program, volume_program};
        const physics::SphereMesh sphere = physics::make_uv_sphere(24, 36, physics::SphereUp::Z);

        GLuint sphere_vao = 0;
        GLuint sphere_buffers[3] = {};
        gl_gen_vertex_arrays(1, &sphere_vao);
        gl_bind_vertex_array(sphere_vao);
        gl_gen_buffers(3, sphere_buffers);
        gl_bind_buffer(GL_ARRAY_BUFFER, sphere_buffers[0]);
        gl_buffer_data(GL_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(sphere.positions.size() * sizeof(float)),
                       sphere.positions.data(), GL_STATIC_DRAW);
        gl_vertex_attrib_pointer(0, 3, GL_FLOAT, GL_FALSE, 0, nullptr);
        gl_enable_vertex_attrib_array(0);
        gl_bind_buffer(GL_ARRAY_BUFFER, sphere_buffers[1]);
        gl_buffer_data(GL_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(sphere.normals.size() * sizeof(float)),
                       sphere.normals.data(), GL_STATIC_DRAW);
        gl_vertex_attrib_pointer(1, 3, GL_FLOAT, GL_FALSE, 0, nullptr);
        gl_enable_vertex_attrib_array(1);
        gl_bind_buffer(GL_ELEMENT_ARRAY_BUFFER, sphere_buffers[2]);
        gl_buffer_data(GL_ELEMENT_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(sphere.indices.size() * sizeof(std::uint32_t)),
                       sphere.indices.data(), GL_STATIC_DRAW);

        GLuint color_vao = 0;
        GLuint color_buffer = 0;
        gl_gen_vertex_arrays(1, &color_vao);
        gl_bind_vertex_array(color_vao);
        gl_gen_buffers(1, &color_buffer);
        gl_bind_buffer(GL_ARRAY_BUFFER, color_buffer);
        gl_vertex_attrib_pointer(0, 3, GL_FLOAT, GL_FALSE, static_cast<GLsizei>(7 * sizeof(float)), nullptr);
        gl_enable_vertex_attrib_array(0);
        gl_vertex_attrib_pointer(1, 4, GL_FLOAT, GL_FALSE, static_cast<GLsizei>(7 * sizeof(float)),
                                 reinterpret_cast<void*>(3 * sizeof(float)));
        gl_enable_vertex_attrib_array(1);

        GLuint star_vao = 0;
        GLuint star_buffer = 0;
        gl_gen_vertex_arrays(1, &star_vao);
        gl_bind_vertex_array(star_vao);
        gl_gen_buffers(1, &star_buffer);
        gl_bind_buffer(GL_ARRAY_BUFFER, star_buffer);
        const GLsizei star_stride = static_cast<GLsizei>(8 * sizeof(float));
        gl_vertex_attrib_pointer(0, 3, GL_FLOAT, GL_FALSE, star_stride, nullptr);
        gl_enable_vertex_attrib_array(0);
        gl_vertex_attrib_pointer(1, 3, GL_FLOAT, GL_FALSE, star_stride, reinterpret_cast<void*>(3 * sizeof(float)));
        gl_enable_vertex_attrib_array(1);
        gl_vertex_attrib_pointer(2, 1, GL_FLOAT, GL_FALSE, star_stride, reinterpret_cast<void*>(6 * sizeof(float)));
        gl_enable_vertex_attrib_array(2);
        gl_vertex_attrib_pointer(3, 1, GL_FLOAT, GL_FALSE, star_stride, reinterpret_cast<void*>(7 * sizeof(float)));
        gl_enable_vertex_attrib_array(3);

        GLuint field_vao = 0;
        gl_gen_vertex_arrays(1, &field_vao);
        gpu.lit_program = lit_program;
        gpu.color_program = color_program;
        gpu.star_program = star_program;
        gpu.sphere_vao = sphere_vao;
        gpu.sphere_indices = static_cast<GLsizei>(sphere.indices.size());
        gpu.color_vao = color_vao;
        gpu.color_buffer = color_buffer;
        gpu.star_vao = star_vao;
        gpu.star_buffer = star_buffer;
        gpu.mvp_location = gl_get_uniform_location(lit_program, "u_mvp");
        gpu.model_location = gl_get_uniform_location(lit_program, "u_model");
        gpu.color_location = gl_get_uniform_location(lit_program, "u_color");
        gpu.roughness_location = gl_get_uniform_location(lit_program, "u_roughness");
        gpu.key_dir_location = gl_get_uniform_location(lit_program, "u_key_dir");
        gpu.fill_dir_location = gl_get_uniform_location(lit_program, "u_fill_dir");
        gpu.key_intensity_location = gl_get_uniform_location(lit_program, "u_key_intensity");
        gpu.fill_intensity_location = gl_get_uniform_location(lit_program, "u_fill_intensity");
        gpu.eye_location = gl_get_uniform_location(lit_program, "u_eye");
        gpu.view_projection_location = gl_get_uniform_location(color_program, "u_view_projection");
        gpu.star_view_location = gl_get_uniform_location(star_program, "u_view_projection");
        gpu.star_height_location = gl_get_uniform_location(star_program, "u_viewport_height");
        gpu.field_program = field_program;
        gpu.composite_program = composite_program;
        gpu.field_vao = field_vao;
        gpu.field_view_location = gl_get_uniform_location(field_program, "u_view_projection");
        gpu.field_height_location = gl_get_uniform_location(field_program, "u_viewport_height");
        gpu.field_gain_location = gl_get_uniform_location(field_program, "u_gain");
        gpu.field_max_point_location = gl_get_uniform_location(field_program, "u_max_point");
        gpu.field_profile_location = gl_get_uniform_location(field_program, "u_profile");
        gpu.composite_light_location = gl_get_uniform_location(composite_program, "u_light");
        gpu.composite_body_location = gl_get_uniform_location(composite_program, "u_body");
        gpu.composite_gas_location = gl_get_uniform_location(composite_program, "u_gas");
        gpu.composite_dust_location = gl_get_uniform_location(composite_program, "u_dust");
        gpu.composite_reddening_location = gl_get_uniform_location(composite_program, "u_reddening");
        gpu.composite_glow_location = gl_get_uniform_location(composite_program, "u_gas_glow");
        gpu.composite_soften_brightness_location = gl_get_uniform_location(composite_program, "u_soften_brightness");
        gpu.blur_program = blur_program;
        gpu.blur_image_location = gl_get_uniform_location(blur_program, "u_image");
        gpu.blur_texel_location = gl_get_uniform_location(blur_program, "u_texel");
        gpu.blur_span_location = gl_get_uniform_location(blur_program, "u_span");
        gpu.volume_program = volume_program;
        gpu.volume_field_location = gl_get_uniform_location(volume_program, "u_field");
        gpu.volume_eye_location = gl_get_uniform_location(volume_program, "u_eye");
        gpu.volume_forward_location = gl_get_uniform_location(volume_program, "u_forward");
        gpu.volume_right_location = gl_get_uniform_location(volume_program, "u_right");
        gpu.volume_up_location = gl_get_uniform_location(volume_program, "u_up");
        gpu.volume_scale_location = gl_get_uniform_location(volume_program, "u_scale");
        gpu.volume_origin_location = gl_get_uniform_location(volume_program, "u_origin");
        gpu.volume_extent_location = gl_get_uniform_location(volume_program, "u_extent");
        gpu.volume_mean_location = gl_get_uniform_location(volume_program, "u_mean");
        gpu.volume_floor_location = gl_get_uniform_location(volume_program, "u_floor");
        gpu.volume_gain_location = gl_get_uniform_location(volume_program, "u_gain");
        gpu.volume_contrast_location = gl_get_uniform_location(volume_program, "u_contrast");
        gpu.volume_heat_location = gl_get_uniform_location(volume_program, "u_heat");
        gpu.volume_extinction_location = gl_get_uniform_location(volume_program, "u_extinction");
        const float volume_triangle[] = {-1.0f, -1.0f, 3.0f, -1.0f, -1.0f, 3.0f};
        gl_gen_vertex_arrays(1, &gpu.volume_vao);
        gl_bind_vertex_array(gpu.volume_vao);
        GLuint volume_buffer = 0;
        gl_gen_buffers(1, &volume_buffer);
        gl_bind_buffer(GL_ARRAY_BUFFER, volume_buffer);
        gl_buffer_data(GL_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(sizeof(volume_triangle)), volume_triangle, GL_STATIC_DRAW);
        gl_vertex_attrib_pointer(0, 2, GL_FLOAT, GL_FALSE, 0, nullptr);
        gl_enable_vertex_attrib_array(0);
        gl_bind_vertex_array(0);
        arrays = {sphere_vao, color_vao, star_vao, field_vao, gpu.volume_vao};
        buffers = {sphere_buffers[0], sphere_buffers[1], sphere_buffers[2], color_buffer, star_buffer, volume_buffer};
        // The viewer opens on its first built-in simulation.
        load_builtin(view, 0);
    }

    Session(Session const&) = delete;
    Session& operator=(Session const&) = delete;

    ~Session() {
        if (view.control) view.control->cancel.store(true, std::memory_order_relaxed);
        if (view.pending.valid()) view.pending.wait();
        view.export_ffmpeg.finish();
        export_target.destroy();
        sky.destroy();
        destroy_field_targets(gpu);
        if (gpu.volume_texture) glDeleteTextures(1, &gpu.volume_texture);
        GLuint optical[] = {gpu.optical_program, gpu.optical_post};
        for (GLuint program : optical)
            if (program) gl_delete_program(program);
        if (gpu.optical_vao) gl_delete_vertex_arrays(1, &gpu.optical_vao);
        if (gpu.optical_buffer) gl_delete_buffers(1, &gpu.optical_buffer);
        for (GLuint program : programs) gl_delete_program(program);
        gl_delete_vertex_arrays(static_cast<GLsizei>(arrays.size()), arrays.data());
        gl_delete_buffers(static_cast<GLsizei>(buffers.size()), buffers.data());
    }

    // One pass of main()'s window loop, drawing into CAst's framebuffer.
    void frame(double dt, int width, int height, GLuint framebuffer) {
        const double frame_dt = std::min(std::max(dt, 0.0), 0.05);
        poll_simulation(view);
        const bool has_samples = !view.trajectory.samples.empty();
        if (has_samples && view.playing && !view.export_active && view.time_end > view.time_begin) {
            view.playhead += view.speed * frame_dt;
            if (view.playhead > view.time_end) {
                view.playhead = view.time_begin;
            }
        }
        glEnable(GL_DEPTH_TEST);
        glEnable(GL_PROGRAM_POINT_SIZE);
        glEnable(GL_CULL_FACE);
        glCullFace(GL_BACK);
        if (view.export_active) {
            pump_export(view, gpu, export_target, export_pixels);
        }
        diagnostics = {};
        if (has_samples) {
            sample_at(view.trajectory, view.playhead, positions, diagnostics);
        } else {
            positions.clear();
        }
        if (view.export_still && has_samples) {
            view.export_still = false;
            const std::string file = export_still_file(view.export_path);
            if (capture_rgb(gpu, view, export_target, positions, view.playhead, view.export_width, view.export_height,
                            export_pixels, view.export_error)) {
                if (write_png_rgb(file, view.export_width, view.export_height, export_pixels.data(), view.export_error)) {
                    view.export_status = "Wrote " + file;
                }
            }
        }
        draw_scene(gpu, view, positions, view.playhead, width, height, framebuffer);
        gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
    }
};

// What draw_parameters does after any edit.
void parameters_edited(ViewState& view) {
    view.quality_preset = -1;
    if (view.resumable) {
        view.resumable = false;
        view.status = "Parameters changed. Run starts over.";
    }
}

struct Field {
    std::function<double(Session&)> get;
    std::function<void(Session&, double)> set;  // Empty: read only.
};
using Fields = std::unordered_map<std::string, Field>;

template <class T> void appearance(Fields& fields, char const* name, T ViewState::*member) {
    fields[name] = {[member](Session& s) { return static_cast<double>(s.view.*member); },
                    [member](Session& s, double v) { s.view.*member = static_cast<T>(v); }};
}

void appearance_color(Fields& fields, std::string const& name, float (ViewState::*member)[3]) {
    for (int c = 0; c < 3; ++c) {
        fields[name + "." + "rgb"[c]] = {[member, c](Session& s) { return static_cast<double>((s.view.*member)[c]); },
                                       [member, c](Session& s, double v) { (s.view.*member)[c] = static_cast<float>(v); }};
    }
}

void exporting(Fields& fields, char const* name, int ViewState::*member) {
    fields[name] = {[member](Session& s) { return static_cast<double>(s.view.*member); },
                    [member](Session& s, double v) {
                        s.view.*member = static_cast<int>(std::lround(v));
                        invalidate_export_resume(s.view);
                    }};
}

// Scene parameters follow draw_parameters: locked while a run is busy, and any
// change clears the resolution preset and the Continue checkpoint.
void parameter(Fields& fields, std::string const& name, std::function<double(ViewState&)> get,
               std::function<void(ViewState&, double)> set) {
    fields[name] = {[get](Session& s) { return get(s.view); },
                    [set](Session& s, double v) {
                        if (s.view.busy || !s.view.editable) return;
                        set(s.view, v);
                        parameters_edited(s.view);
                    }};
}

physics::GalaxySpec* galaxy(ViewState& view, std::size_t index) {
    return index < view.scene.galaxies.size() ? &view.scene.galaxies[index] : nullptr;
}

physics::BodyDesc* authored(ViewState& view, std::size_t index) {
    if (view.scene.authored_bodies > 8 || index >= view.scene.authored_bodies || index >= view.scene.bodies.size())
        return nullptr;
    return &view.scene.bodies[index];
}

double& component(physics::Vec3& v, int axis) { return axis == 0 ? v.x : axis == 1 ? v.y : v.z; }

void galaxy_fields(Fields& fields, std::size_t index) {
    const std::string prefix = "g" + std::to_string(index) + ".";
    auto real = [&](char const* name, double physics::GalaxySpec::*member) {
        parameter(fields, prefix + name,
                  [index, member](ViewState& v) { auto* g = galaxy(v, index); return g ? g->*member : 0.0; },
                  [index, member](ViewState& v, double x) { if (auto* g = galaxy(v, index)) g->*member = x; });
    };
    auto count = [&](char const* name, std::size_t physics::GalaxySpec::*member, int minimum) {
        parameter(fields, prefix + name,
                  [index, member](ViewState& v) { auto* g = galaxy(v, index); return g ? static_cast<double>(g->*member) : 0.0; },
                  [index, member, minimum](ViewState& v, double x) {
                      if (auto* g = galaxy(v, index))
                          g->*member = static_cast<std::size_t>(std::max(static_cast<int>(std::lround(x)), minimum));
                  });
    };
    auto flag = [&](char const* name, bool physics::GalaxySpec::*member) {
        parameter(fields, prefix + name,
                  [index, member](ViewState& v) { auto* g = galaxy(v, index); return g && g->*member ? 1.0 : 0.0; },
                  [index, member](ViewState& v, double x) { if (auto* g = galaxy(v, index)) g->*member = x != 0.0; });
    };
    auto vector = [&](char const* name, physics::Vec3 physics::GalaxySpec::*member) {
        for (int axis = 0; axis < 3; ++axis) {
            parameter(fields, prefix + name + "." + "xyz"[axis],
                      [index, member, axis](ViewState& v) { auto* g = galaxy(v, index); return g ? component(g->*member, axis) : 0.0; },
                      [index, member, axis](ViewState& v, double x) { if (auto* g = galaxy(v, index)) component(g->*member, axis) = x; });
        }
    };
    using G = physics::GalaxySpec;
    count("particles", &G::particles, 4);
    real("haloMass", &G::halo_mass);
    real("haloScale", &G::halo_scale);
    real("haloCutoff", &G::halo_cutoff);
    real("diskMass", &G::disk_mass);
    real("diskScale", &G::disk_scale_radius);
    real("diskHeight", &G::disk_height);
    real("dispersionRatio", &G::dispersion_ratio);
    real("targetQ", &G::target_q);
    real("diskParticleFraction", &G::disk_particle_fraction);
    real("spiralStrength", &G::spiral_strength);
    parameter(fields, prefix + "spiralArms",
              [index](ViewState& v) { auto* g = galaxy(v, index); return g ? static_cast<double>(g->spiral_arms) : 2.0; },
              [index](ViewState& v, double x) { if (auto* g = galaxy(v, index)) g->spiral_arms = x >= 3.0 ? 4 : 2; });
    real("spiralPitch", &G::spiral_pitch_degrees);
    real("spiralPhase", &G::spiral_phase_degrees);
    flag("jeansVertical", &G::jeans_vertical);
    real("populationVariation", &G::population_variation);
    real("initialYoungFraction", &G::initial_young_fraction);
    real("initialYoungAge", &G::initial_young_age);
    real("youngArmContrast", &G::young_arm_contrast);
    real("bulgeMass", &G::bulge_mass);
    real("bulgeScale", &G::bulge_scale);
    real("gasMass", &G::gas_mass);
    count("gasParticles", &G::gas_particles, 0);
    real("gasHeight", &G::gas_height);
    real("hotGasMass", &G::hot_gas_mass);
    count("hotGasParticles", &G::hot_gas_particles, 0);
    real("hotEmission", &G::hot_emission);
    real("soundSpeed", &G::sound_speed);
    real("coolingTime", &G::cooling_time);
    real("starFormationTime", &G::star_formation_time);
    flag("continuousStarFormation", &G::continuous_star_formation);
    real("starFormationDensity", &G::star_formation_density);
    real("starFormationTemperature", &G::star_formation_temperature);
    flag("starFormationConverging", &G::star_formation_converging);
    real("feedbackEnergy", &G::feedback_energy);
    real("feedbackTime", &G::feedback_time);
    real("inclination", &G::inclination_degrees);
    real("node", &G::node_degrees);
    parameter(fields, prefix + "reverseSpin",
              [index](ViewState& v) { auto* g = galaxy(v, index); return g && g->spin < 0 ? 1.0 : 0.0; },
              [index](ViewState& v, double x) { if (auto* g = galaxy(v, index)) g->spin = x != 0.0 ? -1 : 1; });
    vector("center", &G::center);
    vector("velocity", &G::velocity);
    real("displayRadius", &G::display_radius);
    real("emission", &G::emission);
    real("haloEmission", &G::halo_emission);
    real("bulgeEmission", &G::bulge_emission);
    real("gasEmission", &G::gas_emission);
    real("youngStarEmission", &G::young_star_emission);
}

void body_fields(Fields& fields, std::size_t index) {
    const std::string prefix = "b" + std::to_string(index) + ".";
    auto real = [&](std::string const& name, std::function<double&(physics::BodyDesc&)> member) {
        parameter(fields, prefix + name,
                  [index, member](ViewState& v) { auto* b = authored(v, index); return b ? member(*b) : 0.0; },
                  [index, member](ViewState& v, double x) { if (auto* b = authored(v, index)) member(*b) = x; });
    };
    real("mass", [](physics::BodyDesc& b) -> double& { return b.particle.mass; });
    for (int axis = 0; axis < 3; ++axis) {
        real(std::string("position.") + "xyz"[axis], [axis](physics::BodyDesc& b) -> double& { return component(b.particle.position, axis); });
        real(std::string("velocity.") + "xyz"[axis], [axis](physics::BodyDesc& b) -> double& { return component(b.particle.velocity, axis); });
    }
    real("displayRadius", [](physics::BodyDesc& b) -> double& { return b.display_radius; });
    real("emission", [](physics::BodyDesc& b) -> double& { return b.emission; });
}

// CAst's sky (cast_sky.hpp); appearance only, so never locked by a run.
void sky_fields(Fields& fields) {
    using S = cast_sky::Sky;
    auto flag = [&](char const* name, bool S::*member) {
        fields[name] = {[member](Session& s) { return s.sky.*member ? 1.0 : 0.0; },
                        [member](Session& s, double v) { s.sky.*member = v != 0.0; }};
    };
    auto number = [&](char const* name, float S::*member) {
        fields[name] = {[member](Session& s) { return static_cast<double>(s.sky.*member); },
                        [member](Session& s, double v) { s.sky.*member = static_cast<float>(v); }};
    };
    flag("sky.milkyWay", &S::milky_way);
    flag("sky.stars", &S::stars);
    flag("sky.catalog", &S::catalog);
    number("sky.brightness", &S::brightness);
    number("sky.blackLevel", &S::black);
    number("sky.saturation", &S::saturation);
    number("sky.starBrightness", &S::star_brightness);
    number("sky.starSize", &S::star_size);
    number("sky.faintest", &S::faintest);
    number("sky.rotation", &S::rotation);
    number("sky.tilt", &S::tilt);
    fields["sky.hasMilkyWay"] = {[](Session& s) { return s.sky.texture ? 1.0 : 0.0; }, {}};
    fields["sky.catalogStars"] = {[](Session& s) { return static_cast<double>(s.sky.catalog_count); }, {}};
}

Fields const& fields() {
    static Fields const table = [] {
        Fields f;
        using V = ViewState;
        appearance(f, "picture", &V::picture_mode);
        appearance(f, "fieldResolution", &V::field_resolution);
        appearance(f, "fieldGain", &V::field_gain);
        appearance(f, "fieldContrast", &V::field_contrast);
        appearance(f, "fieldHeat", &V::field_heat);
        appearance(f, "exposure", &V::exposure);
        appearance(f, "bloom", &V::bloom);
        appearance(f, "opticalSmoothing", &V::optical_smoothing);
        appearance(f, "stellarDetail", &V::stellar_detail);
        appearance(f, "armDefinition", &V::arm_definition);
        appearance(f, "dustStructure", &V::dust_structure);
        appearance(f, "dust", &V::dust);
        appearance(f, "reddening", &V::reddening);
        appearance(f, "gasGlow", &V::gas_glow);
        appearance(f, "youngLifetime", &V::young_lifetime);
        appearance(f, "ionizingLifetime", &V::ionizing_lifetime);
        appearance(f, "oiiiLifetime", &V::oiii_lifetime);
        appearance(f, "youngBoost", &V::young_boost);
        appearance(f, "associationSize", &V::association_size);
        appearance(f, "nebulaRadius", &V::nebula_radius);
        appearance(f, "ionizingMassScale", &V::ionizing_mass_scale);
        appearance(f, "nebulaDensityScale", &V::nebula_density_scale);
        appearance(f, "halphaStrength", &V::halpha_strength);
        appearance(f, "oiiiStrength", &V::oiii_strength);
        appearance(f, "nebulaSize", &V::nebula_size);
        appearance_color(f, "halpha", &V::halpha_color);
        appearance_color(f, "oiii", &V::oiii_color);
        appearance(f, "stellarTexture", &V::stellar_texture);
        appearance(f, "showHalo", &V::show_halo);
        appearance(f, "showHotGas", &V::show_hot_gas);
        appearance(f, "showAxes", &V::show_axes);
        appearance(f, "pointScale", &V::point_scale);
        appearance(f, "lightSmoothing", &V::light_smoothing);
        appearance(f, "soften", &V::soften);
        appearance(f, "softenBrightness", &V::soften_brightness);
        appearance(f, "trail", &V::trail_fraction);
        appearance(f, "keyLight", &V::key_intensity);
        appearance(f, "fillLight", &V::fill_intensity);
        appearance(f, "playing", &V::playing);
        appearance(f, "playhead", &V::playhead);
        appearance(f, "speed", &V::speed);
        f["camera.distance"] = {[](Session& s) { return s.view.camera.distance; }, [](Session& s, double v) { s.view.camera.distance = v; }};
        f["camera.yaw"] = {[](Session& s) { return s.view.camera.yaw; }, [](Session& s, double v) { s.view.camera.yaw = v; }};
        f["camera.pitch"] = {[](Session& s) { return s.view.camera.pitch; }, [](Session& s, double v) { s.view.camera.pitch = v; }};

        parameter(f, "dt", [](V& v) { return v.scene.config.dt; }, [](V& v, double x) { v.scene.config.dt = x; });
        parameter(f, "steps", [](V& v) { return static_cast<double>(v.scene.config.steps); },
                  [](V& v, double x) { v.scene.config.steps = static_cast<std::size_t>(std::max(static_cast<long long>(std::llround(x)), 1LL)); });
        parameter(f, "sampleEvery", [](V& v) { return static_cast<double>(v.scene.config.sample_every); },
                  [](V& v, double x) { v.scene.config.sample_every = static_cast<std::size_t>(std::max(static_cast<long long>(std::llround(x)), 1LL)); });
        parameter(f, "G", [](V& v) { return v.scene.config.forces.gravitational_constant; },
                  [](V& v, double x) { v.scene.config.forces.gravitational_constant = x; });
        parameter(f, "softening", [](V& v) { return v.scene.config.forces.softening_length; },
                  [](V& v, double x) { v.scene.config.forces.softening_length = x; });
        for (std::size_t index = 0; index < 2; ++index) galaxy_fields(f, index);
        for (std::size_t index = 0; index < 8; ++index) body_fields(f, index);

        exporting(f, "export.width", &V::export_width);
        exporting(f, "export.height", &V::export_height);
        exporting(f, "export.fps", &V::export_fps);
        exporting(f, "export.format", &V::export_format);
        for (auto [name, member] : {std::pair{"export.start", &V::export_start}, std::pair{"export.end", &V::export_end}}) {
            f[name] = {[member](Session& s) { return s.view.*member; },
                       [member](Session& s, double v) {
                           s.view.*member = v;
                           s.view.export_range_set = true;
                           invalidate_export_resume(s.view);
                       }};
        }

        auto read = [&](char const* name, std::function<double(Session&)> get) { f[name] = {std::move(get), {}}; };
        read("gravity", [](Session& s) { return s.view.scene.config.forces.gravity ? 1.0 : 0.0; });
        read("energy", [](Session& s) { return s.diagnostics.energy; });
        read("momentum", [](Session& s) { return physics::norm(s.diagnostics.linear_momentum); });
        read("angularMomentum", [](Session& s) { return physics::norm(s.diagnostics.angular_momentum); });
        read("hasSamples", [](Session& s) { return s.view.trajectory.samples.empty() ? 0.0 : 1.0; });
        read("timeBegin", [](Session& s) { return s.view.time_begin; });
        read("timeEnd", [](Session& s) { return s.view.time_end; });
        read("busy", [](Session& s) { return s.view.busy ? 1.0 : 0.0; });
        read("resumable", [](Session& s) { return s.view.resumable ? 1.0 : 0.0; });
        read("editable", [](Session& s) { return s.view.editable ? 1.0 : 0.0; });
        read("step", [](Session& s) { return s.view.control ? static_cast<double>(s.view.control->step.load(std::memory_order_relaxed)) : 0.0; });
        read("preset", [](Session& s) { return static_cast<double>(s.view.preset_index); });
        read("quality", [](Session& s) { return static_cast<double>(s.view.quality_preset); });
        read("qualities", [](Session&) { return static_cast<double>(kQualityCount); });
        read("simulations", [](Session&) { return static_cast<double>(kSimulationCount); });
        read("galaxies", [](Session& s) { return static_cast<double>(s.view.scene.galaxies.size()); });
        read("bodies", [](Session& s) {
            return s.view.scene.authored_bodies <= 8 ? static_cast<double>(std::min(s.view.scene.authored_bodies, s.view.scene.bodies.size())) : 0.0;
        });
        read("refinedLight", [](Session& s) { return s.view.trajectory.tracer_softening > 0 ? 1.0 : 0.0; });
        read("particleScene", [](Session& s) {
            return !s.view.trajectory.bodies.empty() || !s.view.scene.bodies.empty() || !s.view.scene.galaxies.empty() ? 1.0 : 0.0;
        });
        read("colorGroups", [](Session& s) { return static_cast<double>(s.view.color_groups.size()); });
        read("export.active", [](Session& s) { return s.view.export_active ? 1.0 : 0.0; });
        read("export.resumable", [](Session& s) { return s.view.export_resumable ? 1.0 : 0.0; });
        read("export.frames", [](Session& s) {
            return static_cast<double>(export_frame_count(s.view.export_start, s.view.export_end, std::max(s.view.export_fps, 1)));
        });
        sky_fields(f);
        return f;
    }();
    return table;
}

std::string text(Session& s, std::string const& name) {
    ViewState& v = s.view;
    if (name == "status") return v.status;
    if (name == "error") return v.error;
    if (name == "name") return v.simulation_name;
    if (name == "exportStatus") return v.export_status;
    if (name == "exportError") return v.export_error;
    if (name == "exportPath") return v.export_path;
    if (name == "exportTarget") return v.export_format == 0 ? export_directory(v.export_path) : export_movie_file(v.export_path);
    if (name == "exportStill") return export_still_file(v.export_path);
    if (name == "qualityDetail") {
        return v.quality_preset >= 0 && v.quality_preset < kQualityCount
                   ? kQualityPresets[v.quality_preset].detail
                   : "Minimal is a short preview. Ultra thins the disk and raises the particle count.";
    }
    auto indexed = [&](char const* prefix, std::size_t& index) {
        const std::size_t length = std::strlen(prefix);
        if (name.rfind(prefix, 0) != 0 || name.size() == length) return false;
        try {
            index = static_cast<std::size_t>(std::stoul(name.substr(length)));
        } catch (...) {
            return false;
        }
        return true;
    };
    std::size_t index = 0;
    if (indexed("simulation", index) && index < static_cast<std::size_t>(kSimulationCount)) return kSimulations[index].name;
    if (indexed("quality", index) && index < static_cast<std::size_t>(kQualityCount)) return kQualityPresets[index].name;
    if (indexed("galaxy", index) && galaxy(v, index)) return galaxy(v, index)->name;
    if (indexed("seed", index) && galaxy(v, index)) return std::to_string(galaxy(v, index)->seed);
    if (indexed("body", index) && authored(v, index)) return authored(v, index)->name;
    if (indexed("colorGroup", index) && index < v.color_groups.size()) return v.color_groups[index].label;
    throw std::runtime_error("Unknown galaxy merger text: " + name);
}

// The session whose sky draw_scene adds, for the duration of one API call.
Session* drawing = nullptr;

struct Drawing {
    explicit Drawing(Session& session) { drawing = &session; }
    ~Drawing() { drawing = nullptr; }
};

}  // namespace

void draw_scene(SceneGpu& gpu, ViewState const& view, std::vector<physics::Vec3> const& positions, double shown_time,
                int width, int height, GLuint framebuffer) {
    lab_draw_scene(gpu, view, positions, shown_time, width, height, framebuffer);
    if (drawing) drawing->sky.draw(view, width, height, framebuffer);
}

CAST_API int mg_last_error(char* buffer, int size) { return copyText(lastError, buffer, size); }

CAST_API void mg_set_examples_directory(char const* path) { cast_examples_directory = path ? path : ""; }

CAST_API void mg_set_ffmpeg(char const* path) { ffmpeg_executable_override = path ? path : ""; }

CAST_API void* mg_create() {
    try {
        load_gl([](char const* name) -> void* { return reinterpret_cast<void*>(glfwGetProcAddress(name)); });
        return new Session();
    } catch (std::exception const& e) {
        lastError = e.what();
        return nullptr;
    }
}

CAST_API void mg_destroy(void* handle) {
    try {
        delete static_cast<Session*>(handle);
    } catch (...) {
    }
}

CAST_API int mg_frame(void* handle, double dt, int width, int height, unsigned framebuffer) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        Drawing scope(s);
        s.frame(dt, std::max(width, 1), std::max(height, 1), framebuffer);
        return 1;
    });
}

// CAst's Milky Way: tightly packed RGB rows, top row first, equirectangular in RA/Dec.
CAST_API int mg_set_milky_way(void* handle, unsigned char const* rgb, int width, int height) {
    return guarded([&] {
        static_cast<Session*>(handle)->sky.upload_milky_way(rgb, width, height);
        return 1;
    });
}

// Catalog stars: per star an equatorial unit vector and V magnitude, and sRGBA bytes.
// A count of zero returns to the random stars.
CAST_API int mg_set_star_catalog(void* handle, float const* stars, unsigned char const* colors, int count) {
    return guarded([&] {
        static_cast<Session*>(handle)->sky.upload_catalog(stars, colors, count);
        return 1;
    });
}

CAST_API int mg_get(void* handle, char const* name, double* value) {
    return guarded([&] {
        auto found = fields().find(name);
        if (found == fields().end()) return 0;
        *value = found->second.get(*static_cast<Session*>(handle));
        return 1;
    });
}

CAST_API int mg_set(void* handle, char const* name, double value) {
    return guarded([&] {
        auto found = fields().find(name);
        if (found == fields().end() || !found->second.set) return 0;
        found->second.set(*static_cast<Session*>(handle), value);
        return 1;
    });
}

CAST_API int mg_text(void* handle, char const* name, char* buffer, int size) {
    try {
        return copyText(text(*static_cast<Session*>(handle), name), buffer, size);
    } catch (std::exception const& e) {
        lastError = e.what();
        return copyText("", buffer, size);
    }
}

CAST_API int mg_set_export_path(void* handle, char const* path) {
    return guarded([&] {
        ViewState& v = static_cast<Session*>(handle)->view;
        std::snprintf(v.export_path, sizeof(v.export_path), "%s", path ? path : "");
        invalidate_export_resume(v);
        return 1;
    });
}

// Galaxy seeds are full 64-bit integers, so they travel as text.
CAST_API int mg_set_seed(void* handle, int index, char const* digits) {
    return guarded([&] {
        ViewState& v = static_cast<Session*>(handle)->view;
        physics::GalaxySpec* g = index >= 0 ? galaxy(v, static_cast<std::size_t>(index)) : nullptr;
        if (!g || v.busy || !v.editable) return 0;
        g->seed = std::stoull(digits ? digits : "");
        parameters_edited(v);
        return 1;
    });
}

// Buttons of the viewer's panel, by name.
CAST_API int mg_action(void* handle, char const* name) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        ViewState& v = s.view;
        const std::string action = name ? name : "";
        if (action == "run") {
            if (v.editable && !v.busy) launch_simulation(v, false);
        } else if (action == "continue") {
            if (v.editable && !v.busy) launch_simulation(v, true);
        } else if (action == "stop") {
            if (v.control) v.control->cancel.store(true, std::memory_order_relaxed);
        } else if (action == "playPause") {
            v.playing = !v.playing;
        } else if (action == "frame") {
            frame_camera(v);
        } else if (action.rfind("compose.", 0) == 0) {
            compose_stellar_view(v, action.substr(8));
        } else if (action == "look.broadband") {
            v.halpha_strength = 8; v.oiii_strength = 2; v.gas_glow = .45f; v.young_boost = 1; v.ionizing_mass_scale = .0002f;
        } else if (action == "look.emission") {
            v.halpha_strength = 24; v.oiii_strength = 6; v.gas_glow = 1; v.young_boost = 2; v.ionizing_mass_scale = .00002f;
        } else if (action.rfind("quality.", 0) == 0) {
            if (!v.busy && v.editable) apply_quality_preset(v, std::stoi(action.substr(8)));
        } else if (action == "export.start") {
            start_export(v, false);
        } else if (action == "export.continue") {
            start_export(v, true);
        } else if (action == "export.stop") {
            v.export_stop = true;
        } else if (action == "export.still") {
            v.export_still = true;
            v.export_error.clear();
        } else {
            return 0;
        }
        return 1;
    });
}

CAST_API int mg_load_builtin(void* handle, int index) {
    return guarded([&] {
        load_builtin(static_cast<Session*>(handle)->view, index);
        return 1;
    });
}

CAST_API int mg_load_file(void* handle, char const* path) {
    return guarded([&] {
        load_user_file(static_cast<Session*>(handle)->view, path ? path : "");
        return 1;
    });
}

// save_recording with CAst's file dialog in place of the viewer's.
CAST_API int mg_save_recording(void* handle, char const* path) {
    return guarded([&] {
        ViewState& view = static_cast<Session*>(handle)->view;
        if (view.trajectory.samples.empty()) {
            view.error = "Run a simulation before saving a recording.";
            return 1;
        }
        std::string chosen = path ? path : "";
        if (std::filesystem::path(chosen).extension().empty()) {
            chosen += ".traj";
        }
        try {
            physics::write_trajectory(view.trajectory, chosen);
            view.error.clear();
            view.status = "Saved " + chosen;
        } catch (std::exception const& error) {
            view.error = error.what();
        }
        return 1;
    });
}

CAST_API int mg_save_look(void* handle, char const* path) {
    return guarded([&] {
        ViewState& view = static_cast<Session*>(handle)->view;
        try { save_look(view, path ? path : ""); view.status = "Saved optical look."; view.error.clear(); }
        catch (std::exception const& e) { view.error = e.what(); }
        return 1;
    });
}

CAST_API int mg_load_look(void* handle, char const* path) {
    return guarded([&] {
        ViewState& view = static_cast<Session*>(handle)->view;
        try { load_look(view, path ? path : ""); view.status = "Loaded optical look."; view.error.clear(); }
        catch (std::exception const& e) { view.error = e.what(); }
        return 1;
    });
}

CAST_API int mg_color_group(void* handle, int index, float* rgb) {
    return guarded([&] {
        ViewState& view = static_cast<Session*>(handle)->view;
        if (index < 0 || static_cast<std::size_t>(index) >= view.color_groups.size()) return 0;
        std::copy(std::begin(view.color_groups[index].rgb), std::end(view.color_groups[index].rgb), rgb);
        return 1;
    });
}

CAST_API int mg_set_color_group(void* handle, int index, float r, float g, float b) {
    return guarded([&] {
        ViewState& view = static_cast<Session*>(handle)->view;
        if (index < 0 || static_cast<std::size_t>(index) >= view.color_groups.size()) return 0;
        ColorGroup& group = view.color_groups[index];
        group.rgb[0] = r;
        group.rgb[1] = g;
        group.rgb[2] = b;
        paint_group(view, group);
        return 1;
    });
}

// The viewer's middle-mouse drag: orbit, or pan with Shift. Pixels.
CAST_API void mg_drag(void* handle, double dx, double dy, int pan) {
    Camera& camera = static_cast<Session*>(handle)->view.camera;
    if (pan) {
        const double scale = camera.distance * 0.0016;
        camera.target += camera.right() * (-dx * scale) + camera.up() * (dy * scale);
    } else {
        camera.yaw -= dx * 0.005;
        camera.pitch = std::clamp(camera.pitch + dy * 0.005, -1.45, 1.45);
    }
}

// The viewer's scroll callback. Wheel notches.
CAST_API void mg_scroll(void* handle, double notches) {
    ViewState& state = static_cast<Session*>(handle)->view;
    state.camera.distance *= std::pow(0.9, notches);
    state.camera.distance = std::clamp(state.camera.distance, state.scene_radius * 0.02, state.scene_radius * 80.0);
}

// Batch render (physics_view --render): runs a built scene to its end if needed,
// then writes one PNG of the current view and appearance.
CAST_API int mg_render_png(void* handle, char const* path, int width, int height) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        if (s.view.trajectory.samples.empty()) throw std::runtime_error("Nothing to render yet.");
        Drawing scope(s);
        std::vector<physics::Vec3> positions;
        physics::Diagnostics diagnostics;
        sample_at(s.view.trajectory, s.view.playhead, positions, diagnostics);
        std::string error;
        if (!capture_rgb(s.gpu, s.view, s.export_target, positions, s.view.playhead, width, height, s.export_pixels, error) ||
            !write_png_rgb(path ? path : "", width, height, s.export_pixels.data(), error))
            throw std::runtime_error(error);
        return 1;
    });
}
