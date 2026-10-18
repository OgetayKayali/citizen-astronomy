// CAst host for the PhysicsEngine Cosmic Flow lab (apps/cosmic/main.cpp, cosmic_view).
// The gas solver, cosmology, worker thread, baking, cache replay, renderer and MP4
// export are the lab's own code (vendor/apps/cosmic/cosmic_core.hpp). Session::frame
// is one pass of run()'s window loop without ImGui: CAst's Qt panel edits the same
// state through this C API, and CAst's OpenGL window supplies the context and framebuffer.
#include "cosmic_core.hpp"

#include <cstring>
#include <unordered_map>

#define CAST_API extern "C" __declspec(dllexport)

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
        lastError = "Unknown Cluster Flow engine error.";
        return 0;
    }
}

void* glProc(char const* name) {
    auto proc = reinterpret_cast<void*>(wglGetProcAddress(name));
    auto value = reinterpret_cast<std::intptr_t>(proc);
    if (value == 0 || value == 1 || value == 2 || value == 3 || value == -1) {
        static HMODULE library = LoadLibraryA("opengl32.dll");
        proc = library ? reinterpret_cast<void*>(GetProcAddress(library, name)) : nullptr;
    }
    return proc;
}

constexpr GLenum texture3D = 0x806F, rgba32f = 0x8814, clampEdge = 0x812F, wrapR = 0x8072;

float defaultDistance(bool cosmology, float clusterBox) { return !cosmology && clusterBox > 2 ? 3.8f : 3.f; }

// run()'s locals, in its order, for cosmic_view launched without arguments.
struct Session {
    Options options;
    GLuint prog = 0, vao = 0, texture = 0, tracerProg = 0, tracerVao = 0, tracerBuffer = 0;
    std::unique_ptr<Worker> worker;
    Snapshot snapshot;
    unsigned uploaded = 0;
    Controls controls = initialControls(options);
    std::unique_ptr<cosmic::CacheReader> replay;
    bool playing = false, loopPlayback = true, exporting = false, startMovie = false, cancelMovie = false;
    int replayFrame = -1, movieFrame = 0, movieWidth = 1280, movieHeight = 720, movieFps = options.fps, movieQuality = 0;
    float playHead = 0, playbackFps = static_cast<float>(options.fps);
    std::string cachePath, moviePath, handledCache;
    FfmpegPipe movie;
    GLuint movieFbo = 0, movieTexture = 0;
    BakeRequest bakeSettings;
    BakeStatus bakeStatus;
    float distance = options.cameraDistance, exposure = 2.6f, slice = 1.f, slicePosition = 0, tracerOpacity = .07f;
    cosmic_view::CameraMotion camera = options.camera, movieCamera = options.camera;
    Vector cameraTarget = options.focus;
    bool showTracers = true;
    int mode = options.mode, samples = 192, frame = 0, accuracy = 1;
    bool pendingScene = false;
    std::string status;
    Clock::time_point lastPlayback = Clock::now();
    // CAst: live evolution paused while the mode is hidden; Save image path from CAst's dialog.
    bool suspended = false;
    std::string savePath;

    Session() {
        prog = program();
        gl_gen_vertex_arrays(1, &vao);
        gl_bind_vertex_array(vao);
        glGenTextures(1, &texture);
        tracerProg = program(tracerVertex, tracerFragment);
        gl_gen_vertex_arrays(1, &tracerVao);
        gl_bind_vertex_array(tracerVao);
        gl_gen_buffers(1, &tracerBuffer);
        gl_bind_buffer(GL_ARRAY_BUFFER, tracerBuffer);
        gl_vertex_attrib_pointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(cosmic::Tracer), nullptr);
        gl_vertex_attrib_pointer(1, 3, GL_FLOAT, GL_FALSE, sizeof(cosmic::Tracer), reinterpret_cast<void*>(offsetof(cosmic::Tracer, density)));
        gl_vertex_attrib_pointer(2, 3, GL_FLOAT, GL_FALSE, sizeof(cosmic::Tracer), reinterpret_cast<void*>(offsetof(cosmic::Tracer, velocity)));
        gl_enable_vertex_attrib_array(0);
        gl_enable_vertex_attrib_array(1);
        gl_enable_vertex_attrib_array(2);
        gl_bind_vertex_array(0);
        glBindTexture(texture3D, texture);
        glTexParameteri(texture3D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(texture3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(texture3D, GL_TEXTURE_WRAP_S, clampEdge);
        glTexParameteri(texture3D, GL_TEXTURE_WRAP_T, clampEdge);
        glTexParameteri(texture3D, wrapR, clampEdge);
        bakeSettings.duration = options.duration;
        bakeSettings.grid = options.cacheGrid;
        bakeSettings.tracers = options.cacheTracers;
        worker = std::make_unique<Worker>(options);
        if (distance == 0) distance = defaultDistance(options.cosmology.enabled, options.clusterBox);
    }

    Session(Session const&) = delete;
    Session& operator=(Session const&) = delete;

    ~Session() {
        worker.reset();
        movie.finish();
        if (movieFbo) gl_delete_framebuffers(1, &movieFbo);
        if (movieTexture) glDeleteTextures(1, &movieTexture);
        glDeleteTextures(1, &texture);
        gl_delete_vertex_arrays(1, &vao);
        gl_delete_program(prog);
        gl_delete_buffers(1, &tracerBuffer);
        gl_delete_vertex_arrays(1, &tracerVao);
        gl_delete_program(tracerProg);
    }

    void loadCache(std::string const& path) {
        auto loaded = std::make_unique<cosmic::CacheReader>(path);
        replay = std::move(loaded);
        cachePath = path;
        playing = false;
        playHead = 0;
        replayFrame = -1;
        controls.run = false;
        uploaded = ~0u;
    }

    // Keep pending commands until the worker acknowledges them.
    void pushControls() {
        if (!worker) return;
        std::lock_guard lock(worker->mutex);
        bool reset = worker->controls.reset, burst = worker->controls.burst;
        worker->controls = controls;
        worker->controls.reset |= reset;
        worker->controls.burst |= burst;
        controls.reset = false;
        controls.burst = false;
    }

    bool sceneLocked() const { return exporting || bakeStatus.busy || replay != nullptr; }

    void drawScene(int renderWidth, int renderHeight, int offset) {
        int width = renderWidth, height = renderHeight, left = offset, viewWidth = std::max(1, renderWidth - offset);
        auto uniform = [&](char const* name) { return gl_get_uniform_location(prog, name); };
        glViewport(0, 0, width, height);
        glClearColor(.015f, .022f, .035f, 1);
        glClear(GL_COLOR_BUFFER_BIT);
        glViewport(left, 0, viewWidth, height);
        glDisable(GL_BLEND);
        glDisable(GL_DEPTH_TEST);
        gl_use_program(prog);
        gl_bind_vertex_array(vao);
        gl_active_texture(0x84C0);
        glBindTexture(texture3D, texture);
        Vector orbitOffset = camera.outward() * distance;
        Vector eye = cameraTarget + orbitOffset;
        Vector forward = camera.outward() * (-1.f), right = camera.right(), up = camera.up();
        gl_uniform_3f(uniform("eye"), eye.x, eye.y, eye.z);
        gl_uniform_3f(uniform("forward"), forward.x, forward.y, forward.z);
        gl_uniform_3f(uniform("right"), right.x, right.y, right.z);
        gl_uniform_3f(uniform("up"), up.x, up.y, up.z);
        gl_uniform_2f(uniform("viewport"), static_cast<float>(viewWidth), static_cast<float>(height));
        gl_uniform_1i(uniform("field"), 0);
        gl_uniform_1i(uniform("mode"), mode);
        gl_uniform_1i(uniform("samples"), samples);
        gl_uniform_1f(uniform("exposure"), exposure);
        gl_uniform_1f(uniform("sliceWidth"), slice);
        gl_uniform_1f(uniform("slicePosition"), slicePosition);
        gl_uniform_1f(uniform("backgroundDensity"), snapshot.cosmology.enabled ? .065f : .004f * snapshot.gasDensity);
        gl_uniform_1f(uniform("boxScale"), snapshot.cosmology.enabled ? 1.f : snapshot.clusterBox * .5f);
        if (uploaded) glDrawArrays(GL_TRIANGLES, 0, 3);
        if (uploaded && showTracers && mode != 3) {
            gl_use_program(tracerProg);
            gl_bind_vertex_array(tracerVao);
            auto u = [&](char const* name) { return gl_get_uniform_location(tracerProg, name); };
            gl_uniform_3f(u("eye"), eye.x, eye.y, eye.z);
            gl_uniform_3f(u("forward"), forward.x, forward.y, forward.z);
            gl_uniform_3f(u("right"), right.x, right.y, right.z);
            gl_uniform_3f(u("up"), up.x, up.y, up.z);
            gl_uniform_2f(u("viewport"), static_cast<float>(viewWidth), static_cast<float>(height));
            gl_uniform_1f(u("sliceWidth"), slice);
            gl_uniform_1f(u("slicePosition"), slicePosition);
            float tracerNormalization = 180000.f / static_cast<float>(std::max<std::size_t>(1, snapshot.tracers.size()));
            gl_uniform_1f(u("tracerOpacity"), tracerOpacity * exposure / 2.6f * tracerNormalization);
            gl_uniform_1i(u("mode"), mode);
            glEnable(0x8642);
            glEnable(GL_BLEND);
            glBlendFunc(GL_ONE, GL_ONE);
            glDrawArrays(GL_POINTS, 0, static_cast<GLsizei>(snapshot.tracers.size()));
            glDisable(GL_BLEND);
        }
        gl_bind_vertex_array(0);
    }

    // One pass of run()'s window loop, drawing into CAst's framebuffer.
    void loop_pass(int width, int height, GLuint framebuffer) {
        while (glGetError() != GL_NO_ERROR) {
        }
        if (startMovie) {
            startMovie = false;
            try {
                if (!replay) throw std::runtime_error("Open or bake a cache before exporting.");
                if (!movieFbo) {
                    gl_gen_framebuffers(1, &movieFbo);
                    glGenTextures(1, &movieTexture);
                }
                glBindTexture(GL_TEXTURE_2D, movieTexture);
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, movieWidth, movieHeight, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
                gl_bind_framebuffer(GL_FRAMEBUFFER, movieFbo);
                gl_framebuffer_texture_2d(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, movieTexture, 0);
                if (gl_check_framebuffer_status(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
                    throw std::runtime_error("Could not create the movie render target.");
                gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
                std::string error;
                if (!movie.start(movieWidth, movieHeight, movieFps, moviePath, error)) throw std::runtime_error(error);
                exporting = true;
                playing = false;
                movieFrame = 0;
                cancelMovie = false;
                movieCamera = camera;
            } catch (std::exception const& e) {
                gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
                status = e.what();
            }
        }
        if (worker) {
            std::lock_guard lock(worker->mutex);
            if (!replay && worker->latest.generation != uploaded) snapshot = worker->latest;
            if (!worker->latest.error.empty()) status = worker->latest.error;
            bakeStatus = worker->bakeStatus;
        }
        if (!bakeStatus.busy && !bakeStatus.readyPath.empty() && bakeStatus.readyPath != handledCache) {
            handledCache = bakeStatus.readyPath;
            try {
                loadCache(handledCache);
                status = bakeStatus.completed < bakeStatus.total ? "Bake canceled. Completed frames are ready to replay or export."
                                                                 : "Bake finished. Ready to replay or export.";
            } catch (std::exception const& e) {
                status = e.what();
            }
        }
        if (!bakeStatus.error.empty()) status = bakeStatus.error;
        auto playbackNow = Clock::now();
        double wallDelta = std::chrono::duration<double>(playbackNow - lastPlayback).count();
        lastPlayback = playbackNow;
        // Movie motion follows output time, independent of rendering speed.
        if (exporting) camera = movieCamera.atTime(static_cast<double>(movieFrame) / movieFps);
        else camera = camera.atTime(wallDelta);
        if (replay) {
            if (playing && !exporting) {
                playHead += static_cast<float>(wallDelta) * playbackFps;
                if (playHead >= replay->info().frames) {
                    if (loopPlayback) playHead = std::fmod(playHead, static_cast<float>(replay->info().frames));
                    else {
                        playHead = static_cast<float>(replay->info().frames - 1);
                        playing = false;
                    }
                }
            }
            int wanted = exporting ? movieFrame : std::clamp(static_cast<int>(playHead), 0, replay->info().frames - 1);
            if (wanted != replayFrame) {
                static_cast<cosmic::Frame&>(snapshot) = replay->read(wanted);
                ++snapshot.generation;
                uploaded = ~0u;
                replayFrame = wanted;
            }
        }
        if (!snapshot.volume.empty() && snapshot.generation != uploaded) {
            glBindTexture(texture3D, texture);
            gl_tex_image_3d(texture3D, 0, rgba32f, snapshot.grid, snapshot.grid, snapshot.grid, 0, GL_RGBA, GL_FLOAT, snapshot.volume.data());
            gl_bind_buffer(GL_ARRAY_BUFFER, tracerBuffer);
            gl_buffer_data(GL_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(snapshot.tracers.size() * sizeof(cosmic::Tracer)),
                           snapshot.tracers.data(), GL_DYNAMIC_DRAW);
            uploaded = snapshot.generation;
        }
        pushControls();
        gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
        drawScene(width, height, 0);
        if (exporting) {
            try {
                gl_bind_framebuffer(GL_FRAMEBUFFER, movieFbo);
                drawScene(movieWidth, movieHeight, 0);
                auto pixels = readFrame(movieWidth, movieHeight);
                gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
                std::string error;
                if (!movie.write_frame(pixels.data(), movieWidth, movieHeight, error)) throw std::runtime_error(error);
                ++movieFrame;
                playHead = static_cast<float>(movieFrame - 1);
                if (movieFrame >= replay->info().frames || cancelMovie) {
                    if (!movie.finish_checked(error)) throw std::runtime_error(error);
                    exporting = false;
                    status = (cancelMovie ? "Partial movie saved: " : "MP4 saved: ") + moviePath;
                }
            } catch (std::exception const& e) {
                gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
                movie.finish();
                exporting = false;
                status = e.what();
            }
        }
        if (!savePath.empty()) {
            try {
                saveFrame(savePath, width, height, 0);
                status = "Saved " + savePath;
            } catch (std::exception const& e) {
                status = e.what();
            }
            savePath.clear();
        }
        gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
        ++frame;
    }
};

struct Field {
    std::function<double(Session&)> get;
    std::function<void(Session&, double)> set;  // Empty: read only.
};
using Fields = std::unordered_map<std::string, Field>;

int whole(double v) { return static_cast<int>(std::lround(v)); }

// The panel's tab locks: View while exporting; Physics and Scene also while baking or replaying.
enum class Lock { None, View, Scene, Bake, Replay };

bool locked(Session const& s, Lock lock) {
    switch (lock) {
        case Lock::View: return s.exporting;
        case Lock::Scene: return s.sceneLocked();
        case Lock::Bake: return s.exporting || s.bakeStatus.busy;
        case Lock::Replay: return s.exporting || !s.replay;
        default: return false;
    }
}

void add(Fields& f, char const* name, Lock lock, std::function<double(Session&)> get, std::function<void(Session&, double)> set) {
    f[name] = {std::move(get), [lock, set = std::move(set)](Session& s, double v) {
                   if (!locked(s, lock)) set(s, v);
               }};
}

void real(Fields& f, char const* name, Lock lock, float Session::*member) {
    add(f, name, lock, [member](Session& s) { return static_cast<double>(s.*member); },
        [member](Session& s, double v) { s.*member = static_cast<float>(v); });
}

void control(Fields& f, char const* name, float Controls::*member, bool clusterOnly = false) {
    add(f, name, Lock::Scene, [member](Session& s) { return static_cast<double>(s.controls.*member); },
        [member, clusterOnly](Session& s, double v) {
            if (!(clusterOnly && s.controls.initial.cosmology.enabled)) s.controls.*member = static_cast<float>(v);
        });
}

// Scene settings stay pending until Apply scene & restart.
void scene(Fields& f, char const* name, std::function<double(Controls&)> get, std::function<void(Controls&, double)> set,
           int only = 0 /* 1 cluster, 2 cosmology */) {
    add(f, name, Lock::Scene, [get](Session& s) { return get(s.controls); },
        [set, only](Session& s, double v) {
            const bool cosmology = s.controls.initial.cosmology.enabled;
            if ((only == 1 && cosmology) || (only == 2 && !cosmology)) return;
            set(s.controls, v);
            s.pendingScene = true;
        });
}

void cosmology(Fields& f, char const* name, float cosmic::Cosmology::*member) {
    scene(f, name, [member](Controls& c) { return static_cast<double>(c.initial.cosmology.*member); },
          [member](Controls& c, double v) { c.initial.cosmology.*member = static_cast<float>(v); }, 2);
}

void initial(Fields& f, char const* name, float cosmic::InitialConditions::*member, int only) {
    scene(f, name, [member](Controls& c) { return static_cast<double>(c.initial.*member); },
          [member](Controls& c, double v) { c.initial.*member = static_cast<float>(v); }, only);
}

Fields const& fields() {
    static Fields const table = [] {
        Fields f;
        using S = Session;
        // View
        add(f, "camera.yaw", Lock::View, [](S& s) { return double(s.camera.yaw); },
            [](S& s, double v) { s.camera.yaw = float(v); s.camera.rotateYaw = false; });
        add(f, "camera.pitch", Lock::View, [](S& s) { return double(s.camera.pitch); },
            [](S& s, double v) { s.camera.pitch = float(v); s.camera.rotatePitch = false; });
        add(f, "camera.yawSpeed", Lock::View, [](S& s) { return double(s.camera.yawSpeed); }, [](S& s, double v) { s.camera.yawSpeed = float(v); });
        add(f, "camera.pitchSpeed", Lock::View, [](S& s) { return double(s.camera.pitchSpeed); }, [](S& s, double v) { s.camera.pitchSpeed = float(v); });
        add(f, "camera.rotateYaw", Lock::View, [](S& s) { return s.camera.rotateYaw ? 1.0 : 0.0; }, [](S& s, double v) { s.camera.rotateYaw = v != 0; });
        add(f, "camera.rotatePitch", Lock::View, [](S& s) { return s.camera.rotatePitch ? 1.0 : 0.0; }, [](S& s, double v) { s.camera.rotatePitch = v != 0; });
        add(f, "mode", Lock::View, [](S& s) { return double(s.mode); }, [](S& s, double v) { s.mode = std::clamp(whole(v), 0, 3); });
        real(f, "exposure", Lock::View, &S::exposure);
        add(f, "showTracers", Lock::View, [](S& s) { return s.showTracers ? 1.0 : 0.0; }, [](S& s, double v) { s.showTracers = v != 0; });
        real(f, "tracerOpacity", Lock::View, &S::tracerOpacity);
        real(f, "slice", Lock::View, &S::slice);
        real(f, "slicePosition", Lock::View, &S::slicePosition);
        add(f, "samples", Lock::View, [](S& s) { return double(s.samples); }, [](S& s, double v) { s.samples = std::clamp(whole(v), 96, 512); });
        real(f, "distance", Lock::View, &S::distance);
        add(f, "focus.x", Lock::View, [](S& s) { return double(s.cameraTarget.x); }, [](S& s, double v) { s.cameraTarget.x = float(v); });
        add(f, "focus.y", Lock::View, [](S& s) { return double(s.cameraTarget.y); }, [](S& s, double v) { s.cameraTarget.y = float(v); });
        add(f, "focus.z", Lock::View, [](S& s) { return double(s.cameraTarget.z); }, [](S& s, double v) { s.cameraTarget.z = float(v); });

        // Physics: live. Halo gravity, cooling and outbursts belong to Cluster flow.
        control(f, "speed", &Controls::speed);
        control(f, "gravity", &Controls::gravity, true);
        control(f, "cooling", &Controls::cooling, true);
        control(f, "coolingFloor", &Controls::coolingFloor, true);
        add(f, "automatic", Lock::Scene, [](S& s) { return s.controls.automatic ? 1.0 : 0.0; },
            [](S& s, double v) { if (!s.controls.initial.cosmology.enabled) s.controls.automatic = v != 0; });
        control(f, "feedback", &Controls::feedback, true);
        control(f, "burstInterval", &Controls::burstInterval, true);
        control(f, "burstRadius", &Controls::burstRadius, true);
        control(f, "burstHeat", &Controls::burstHeat, true);
        control(f, "burstKick", &Controls::burstKick, true);
        control(f, "collimation", &Controls::collimation, true);
        add(f, "accuracy", Lock::Scene, [](S& s) { return double(s.accuracy); }, [](S& s, double v) {
            s.accuracy = std::clamp(whole(v), 0, 4);
            if (s.accuracy != 4) {
                const int a = s.accuracy;
                s.controls.secondOrder = a != 0;
                s.controls.courant = a <= 1 ? .3f : a == 2 ? .15f : .075f;
                s.controls.maxDt = a == 0 ? .04f : a == 1 ? .025f : a == 2 ? .0125f : .00625f;
            }
        });
        add(f, "secondOrder", Lock::Scene, [](S& s) { return s.controls.secondOrder ? 1.0 : 0.0; },
            [](S& s, double v) { s.controls.secondOrder = v != 0; s.accuracy = 4; });
        add(f, "courant", Lock::Scene, [](S& s) { return double(s.controls.courant); },
            [](S& s, double v) { s.controls.courant = float(v); s.accuracy = 4; });
        add(f, "maxDt", Lock::Scene, [](S& s) { return double(s.controls.maxDt); },
            [](S& s, double v) { s.controls.maxDt = float(v); s.accuracy = 4; });

        // Scene: pending until applied.
        scene(f, "scene.model", [](Controls& c) { return c.initial.cosmology.enabled ? 1.0 : 0.0; },
              [](Controls& c, double v) { c.initial.cosmology.enabled = whole(v) == 1; });
        initial(f, "scene.clusterBox", &cosmic::InitialConditions::clusterBox, 1);
        cosmology(f, "scene.boxMpc", &cosmic::Cosmology::boxMpc);
        cosmology(f, "scene.hubble", &cosmic::Cosmology::hubble);
        cosmology(f, "scene.omegaDM", &cosmic::Cosmology::omegaDM);
        cosmology(f, "scene.omegaB", &cosmic::Cosmology::omegaB);
        cosmology(f, "scene.omegaDE", &cosmic::Cosmology::omegaDE);
        cosmology(f, "scene.w", &cosmic::Cosmology::w);
        cosmology(f, "scene.startRedshift", &cosmic::Cosmology::startRedshift);
        cosmology(f, "scene.sigma8", &cosmic::Cosmology::sigma8);
        cosmology(f, "scene.spectralIndex", &cosmic::Cosmology::spectralIndex);
        scene(f, "scene.mesh", [](Controls& c) { return double(c.initial.cosmology.mesh); },
              [](Controls& c, double v) { c.initial.cosmology.mesh = whole(v); }, 2);
        scene(f, "scene.grid", [](Controls& c) { return double(c.grid); }, [](Controls& c, double v) { c.grid = whole(v); });
        scene(f, "scene.tracers", [](Controls& c) { return double(c.tracers); },
              [](Controls& c, double v) { c.tracers = std::clamp(whole(v), 0, 1000000); });
        scene(f, "scene.seed", [](Controls& c) { return double(c.seed); }, [](Controls& c, double v) { c.seed = std::max(0, whole(v)); });
        initial(f, "scene.gasDensity", &cosmic::InitialConditions::gasDensity, 1);
        initial(f, "scene.temperature", &cosmic::InitialConditions::temperature, 0);
        initial(f, "scene.inflow", &cosmic::InitialConditions::inflow, 1);
        initial(f, "scene.turbulence", &cosmic::InitialConditions::turbulence, 1);
        initial(f, "scene.filamentWidth", &cosmic::InitialConditions::filamentWidth, 1);
        initial(f, "scene.haloSoftening", &cosmic::InitialConditions::haloSoftening, 1);

        // Bake and replay
        add(f, "bake.duration", Lock::Bake, [](S& s) { return double(s.bakeSettings.duration); }, [](S& s, double v) { s.bakeSettings.duration = float(v); });
        add(f, "bake.frames", Lock::Bake, [](S& s) { return double(s.bakeSettings.frames); }, [](S& s, double v) { s.bakeSettings.frames = std::clamp(whole(v), 2, 1200); });
        add(f, "bake.grid", Lock::Bake, [](S& s) { return double(s.bakeSettings.grid); }, [](S& s, double v) { s.bakeSettings.grid = std::clamp(whole(v), 16, 160); });
        add(f, "bake.tracers", Lock::Bake, [](S& s) { return double(s.bakeSettings.tracers); }, [](S& s, double v) { s.bakeSettings.tracers = std::clamp(whole(v), 0, 1000000); });
        add(f, "playHead", Lock::Replay, [](S& s) { return double(static_cast<int>(s.playHead)); }, [](S& s, double v) {
            s.playHead = static_cast<float>(std::clamp(whole(v), 0, s.replay->info().frames - 1));
            s.playing = false;
        });
        add(f, "playbackFps", Lock::Replay, [](S& s) { return double(s.playbackFps); }, [](S& s, double v) { s.playbackFps = float(v); });
        add(f, "loop", Lock::Replay, [](S& s) { return s.loopPlayback ? 1.0 : 0.0; }, [](S& s, double v) { s.loopPlayback = v != 0; });
        add(f, "movieQuality", Lock::Replay, [](S& s) { return double(s.movieQuality); }, [](S& s, double v) { s.movieQuality = std::clamp(whole(v), 0, 2); });
        add(f, "movieFps", Lock::Replay, [](S& s) { return double(s.movieFps); }, [](S& s, double v) { s.movieFps = std::clamp(whole(v), 1, 60); });

        auto read = [&](char const* name, std::function<double(Session&)> get) { f[name] = {std::move(get), {}}; };
        read("run", [](S& s) { return s.controls.run ? 1.0 : 0.0; });
        read("playing", [](S& s) { return s.playing ? 1.0 : 0.0; });
        read("suspended", [](S& s) { return s.suspended ? 1.0 : 0.0; });
        read("pendingScene", [](S& s) { return s.pendingScene ? 1.0 : 0.0; });
        read("uploaded", [](S& s) { return s.uploaded ? 1.0 : 0.0; });
        read("replay", [](S& s) { return s.replay ? 1.0 : 0.0; });
        read("replay.frames", [](S& s) { return s.replay ? double(s.replay->info().frames) : 0.0; });
        read("exporting", [](S& s) { return s.exporting ? 1.0 : 0.0; });
        read("movieFrame", [](S& s) { return double(s.movieFrame); });
        read("bake.busy", [](S& s) { return s.bakeStatus.busy ? 1.0 : 0.0; });
        read("bake.completed", [](S& s) { return double(s.bakeStatus.completed); });
        read("bake.total", [](S& s) { return double(s.bakeStatus.total); });
        read("scene.curvature", [](S& s) { return s.controls.initial.cosmology.curvature(); });
        read("scene.clumps", [](S& s) { return double(cosmic::Flow::clusterClumpCount(s.controls.initial.clusterBox)); });
        read("shown.time", [](S& s) { return s.snapshot.time; });
        read("shown.grid", [](S& s) { return double(s.snapshot.grid); });
        read("shown.clusterBox", [](S& s) { return double(s.snapshot.clusterBox); });
        read("shown.cosmology", [](S& s) { return s.snapshot.cosmology.enabled ? 1.0 : 0.0; });
        read("shown.boxMpc", [](S& s) { return double(s.snapshot.cosmology.boxMpc); });
        read("shown.scaleFactor", [](S& s) { return s.snapshot.scaleFactor; });
        read("shown.lastDt", [](S& s) { return double(s.snapshot.lastDt); });
        read("shown.stepMs", [](S& s) { return double(s.snapshot.stepMs); });
        read("shown.tracers", [](S& s) { return double(s.snapshot.tracers.size()); });
        return f;
    }();
    return table;
}

}  // namespace

CAST_API int cf_last_error(char* buffer, int size) { return copyText(lastError, buffer, size); }

CAST_API void cf_set_ffmpeg(char const* path) { ffmpeg_executable_override = path ? path : ""; }

// The lab's tooltip for a control label (control_help.hpp), or "".
CAST_API int cf_help(char const* label, char* buffer, int size) {
    char const* text = cosmic_ui::help(label ? label : "");
    return copyText(text ? text : "", buffer, size);
}

// cosmic_view --verify-worker: the lab's own worker, bake and cancellation check. No OpenGL.
CAST_API int cf_verify_worker() {
    return guarded([] { return verifyWorker() == 0 ? 1 : 0; });
}

CAST_API void* cf_create() {
    try {
        load_gl(glProc);
        return new Session();
    } catch (std::exception const& e) {
        lastError = e.what();
        return nullptr;
    }
}

CAST_API void cf_destroy(void* handle) {
    try {
        delete static_cast<Session*>(handle);
    } catch (...) {
    }
}

CAST_API int cf_frame(void* handle, int width, int height, unsigned framebuffer) {
    return guarded([&] {
        static_cast<Session*>(handle)->loop_pass(std::max(width, 1), std::max(height, 1), framebuffer);
        return 1;
    });
}

CAST_API int cf_get(void* handle, char const* name, double* value) {
    return guarded([&] {
        auto found = fields().find(name);
        if (found == fields().end()) return 0;
        *value = found->second.get(*static_cast<Session*>(handle));
        return 1;
    });
}

CAST_API int cf_set(void* handle, char const* name, double value) {
    return guarded([&] {
        auto found = fields().find(name);
        if (found == fields().end() || !found->second.set) return 0;
        found->second.set(*static_cast<Session*>(handle), value);
        return 1;
    });
}

CAST_API int cf_text(void* handle, char const* name, char* buffer, int size) {
    Session& s = *static_cast<Session*>(handle);
    const std::string key = name ? name : "";
    std::string text;
    if (key == "status") text = s.status;
    else if (key == "cacheName") text = std::filesystem::path(s.cachePath).filename().string();
    else if (key == "description") text = s.replay ? s.replay->info().description : "";
    else if (key == "moviePath") text = s.moviePath;
    else {
        lastError = "Unknown Cluster Flow text: " + key;
        return copyText("", buffer, size);
    }
    return copyText(text, buffer, size);
}

// Buttons of the lab's panel, by name.
CAST_API int cf_action(void* handle, char const* name) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        Controls& controls = s.controls;
        const std::string action = name ? name : "";
        const bool transport = !s.exporting && !s.bakeStatus.busy;
        if (action == "playPause") {
            if (transport) {
                if (s.replay) s.playing = !s.playing;
                else controls.run = !controls.run;
            }
        } else if (action == "restart") {
            if (transport) {
                if (s.replay) {
                    s.playHead = 0;
                    s.playing = false;
                } else {
                    try {
                        if (controls.initial.cosmology.enabled) controls.initial.cosmology.validate();
                        controls.reset = true;
                        s.pendingScene = false;
                    } catch (std::exception const& e) {
                        s.status = e.what();
                    }
                }
            }
        } else if (action == "apply") {
            if (!s.sceneLocked()) {
                try {
                    if (controls.initial.cosmology.enabled) controls.initial.cosmology.validate();
                    controls.reset = true;
                    s.pendingScene = false;
                } catch (std::exception const& e) {
                    s.status = e.what();
                }
            }
        } else if (action == "burst") {
            if (!s.sceneLocked() && !controls.initial.cosmology.enabled) controls.burst = true;
        } else if (action == "stopCamera") {
            if (!s.exporting) s.camera.stop();
        } else if (action == "resetCamera") {
            if (!s.exporting) {
                s.camera = {};
                s.distance = defaultDistance(s.snapshot.cosmology.enabled, s.snapshot.clusterBox);
                s.cameraTarget = {};
            }
        } else if (action == "flatGeometry") {
            auto& p = controls.initial.cosmology;
            if (!s.sceneLocked() && p.enabled && p.omegaDM + p.omegaB <= 1) {
                p.omegaDE = std::max(0.f, 1 - p.omegaDM - p.omegaB);
                s.pendingScene = true;
            }
        } else if (action == "tngPreset") {
            auto& p = controls.initial.cosmology;
            if (!s.sceneLocked() && p.enabled) {
                auto box = p.boxMpc;
                p = cosmic::Cosmology{};
                p.enabled = true;
                p.boxMpc = box;
                s.pendingScene = true;
            }
        } else if (action == "bake.cancel") {
            if (s.bakeStatus.busy && s.worker) {
                std::lock_guard lock(s.worker->mutex);
                s.worker->cancelBake = true;
            }
        } else if (action == "returnLive") {
            if (s.replay && !s.exporting) {
                s.replay.reset();
                s.playing = false;
                if (!s.worker) s.worker = std::make_unique<Worker>(s.options);
                s.uploaded = ~0u;
            }
        } else if (action == "export.stop") {
            s.cancelMovie = true;
        } else if (action == "suspend") {
            // CAst: hiding the mode pauses live evolution; showing it again resumes.
            if (!s.replay && controls.run) {
                controls.run = false;
                s.suspended = true;
                s.pushControls();
            }
        } else if (action == "resume") {
            if (s.suspended) {
                s.suspended = false;
                if (!s.replay && !s.bakeStatus.busy) controls.run = true;
                s.pushControls();
            }
        } else {
            return 0;
        }
        return 1;
    });
}

// Bake from start..., with CAst's file dialog in place of the lab's.
CAST_API int cf_bake(void* handle, char const* chosen) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        if (s.exporting || s.bakeStatus.busy) return 0;
        Controls& controls = s.controls;
        std::string path = chosen ? chosen : "";
        try {
            if (controls.initial.cosmology.enabled) controls.initial.cosmology.validate();
            if (std::filesystem::path(path).extension().empty()) path += ".cflow";
            if (!s.worker) s.worker = std::make_unique<Worker>(s.options);
            s.replay.reset();
            s.handledCache.clear();
            controls.run = false;
            controls.reset = false;
            s.suspended = false;
            s.bakeSettings.path = path;
            std::lock_guard lock(s.worker->mutex);
            s.worker->controls = controls;
            s.worker->bakeRequest = s.bakeSettings;
            s.worker->bakeQueued = true;
            s.worker->bakeStatus = {true, 0, s.bakeSettings.frames, {}, {}};
            s.bakeStatus = s.worker->bakeStatus;
            s.pendingScene = false;
        } catch (std::exception const& e) {
            s.status = e.what();
        }
        return 1;
    });
}

CAST_API int cf_open_cache(void* handle, char const* path) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        if (s.exporting || s.bakeStatus.busy) return 0;
        try {
            s.loadCache(path ? path : "");
            s.suspended = false;
        } catch (std::exception const& e) {
            s.status = e.what();
        }
        return 1;
    });
}

// Export MP4..., with CAst's file dialog; the export starts on the next frame.
CAST_API int cf_export_mp4(void* handle, char const* path) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        if (!s.replay || s.exporting) return 0;
        s.moviePath = path ? path : "";
        if (std::filesystem::path(s.moviePath).extension().empty()) s.moviePath += ".mp4";
        s.movieWidth = s.movieQuality == 0 ? 1280 : s.movieQuality == 1 ? 1920 : 3840;
        s.movieHeight = s.movieQuality == 0 ? 720 : s.movieQuality == 1 ? 1080 : 2160;
        s.startMovie = true;
        return 1;
    });
}

// Save image...: the next frame is written at the view's size.
CAST_API int cf_save_image(void* handle, char const* path) {
    return guarded([&] {
        static_cast<Session*>(handle)->savePath = path ? path : "";
        return 1;
    });
}

// The lab's mouse handling. Pixels in window points; height is the view's height in points.
CAST_API void cf_drag(void* handle, double dx, double dy, int pan, double height) {
    Session& s = *static_cast<Session*>(handle);
    if (s.exporting) return;
    if (pan) {
        Vector right = s.camera.right(), up = s.camera.up();
        s.cameraTarget = s.cameraTarget + (right * static_cast<float>(-dx) + up * static_cast<float>(dy)) *
                                              (.8f * s.distance / std::max(1.f, static_cast<float>(height)));
        s.cameraTarget.x = std::clamp(s.cameraTarget.x, -1.f, 1.f);
        s.cameraTarget.y = std::clamp(s.cameraTarget.y, -1.f, 1.f);
        s.cameraTarget.z = std::clamp(s.cameraTarget.z, -1.f, 1.f);
    } else {
        s.camera.stop();
        s.camera.yaw = cosmic_view::CameraMotion::wrap(s.camera.yaw - dx * .006 / cosmic_view::CameraMotion::radians);
        s.camera.pitch = cosmic_view::CameraMotion::wrap(s.camera.pitch + dy * .006 / cosmic_view::CameraMotion::radians);
    }
}

CAST_API void cf_scroll(void* handle, double notches) {
    Session& s = *static_cast<Session*>(handle);
    if (s.exporting) return;
    s.distance = std::clamp(s.distance * std::exp(-static_cast<float>(notches) * .09f), .06f, 7.f);
}

// The current view at any size, as the lab's --render does, into a PNG.
CAST_API int cf_render_png(void* handle, char const* path, int width, int height) {
    return guarded([&] {
        Session& s = *static_cast<Session*>(handle);
        GLint previous = 0;
        glGetIntegerv(0x8CA6 /* GL_FRAMEBUFFER_BINDING */, &previous);
        GLuint fbo = 0, color = 0;
        gl_gen_framebuffers(1, &fbo);
        glGenTextures(1, &color);
        glBindTexture(GL_TEXTURE_2D, color);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, width, height, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
        gl_bind_framebuffer(GL_FRAMEBUFFER, fbo);
        gl_framebuffer_texture_2d(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
        std::string error;
        try {
            while (glGetError() != GL_NO_ERROR) {
            }
            s.drawScene(width, height, 0);
            saveFrame(path ? path : "", width, height, 0);
        } catch (std::exception const& e) {
            error = e.what();
        }
        gl_bind_framebuffer(GL_FRAMEBUFFER, static_cast<GLuint>(previous));
        gl_delete_framebuffers(1, &fbo);
        glDeleteTextures(1, &color);
        if (!error.empty()) throw std::runtime_error(error);
        return 1;
    });
}
