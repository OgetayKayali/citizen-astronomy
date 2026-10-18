// CAst's optional sky behind the galaxy merger: the Milky Way texture Sky Atlas
// uses and a star field, random or from Sky Atlas's catalogs. This is not part of
// the PhysicsEngine viewer. It is drawn after the viewer's picture with a screen
// blend, so galaxy light stays on top and empty space shows the sky.
#pragma once

#include <random>

#ifndef GL_CLAMP_TO_EDGE
#define GL_CLAMP_TO_EDGE 0x812F
#endif
#ifndef GL_PROGRAM_POINT_SIZE
#define GL_PROGRAM_POINT_SIZE 0x8642
#endif

namespace cast_sky {

constexpr char const* kSkyVertex = R"(#version 330 core
out vec2 ndc;
void main() {
    vec2 corner = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    ndc = corner * 2.0 - 1.0;
    gl_Position = vec4(ndc, 0.0, 1.0);
}
)";

constexpr char const* kSkyFragment = R"(#version 330 core
in vec2 ndc;
out vec4 color;
uniform vec3 u_right;
uniform vec3 u_up;
uniform vec3 u_forward;
uniform vec2 u_tan;
uniform mat4 u_sky_from_scene;
uniform sampler2D u_milky_way;
uniform float u_brightness;
uniform float u_black;
uniform float u_saturation;
void main() {
    vec3 d = normalize(u_forward + ndc.x * u_tan.x * u_right + ndc.y * u_tan.y * u_up);
    vec3 e = mat3(u_sky_from_scene) * d;
    float u = 0.5 - atan(e.y, e.x) / 6.28318530718;
    vec2 uv = vec2(fract(u), 0.5 - asin(clamp(e.z, -1.0, 1.0)) / 3.14159265359);
    // Longitude wraps at the seam; take its derivatives from whichever copy is continuous there.
    float wrapped = fract(u + 0.5) - 0.5;
    vec2 dx = dFdx(uv), dy = dFdy(uv);
    if (abs(dFdx(wrapped)) < abs(dx.x)) dx.x = dFdx(wrapped);
    if (abs(dFdy(wrapped)) < abs(dy.x)) dy.x = dFdy(wrapped);
    vec3 c = textureGrad(u_milky_way, uv, dx, dy).rgb;
    c = max(c - u_black, 0.0) / max(1.0 - u_black, 1e-3);
    float luma = dot(c, vec3(0.2126, 0.7152, 0.0722));
    c = max(mix(vec3(luma), c, u_saturation), 0.0);
    color = vec4(min(c * u_brightness, vec3(1.0)), 1.0);
}
)";

// Each star is a Gaussian holding its flux; bright stars widen instead of clipping.
// Flux steps by 1.585x per magnitude (not 2.512x) so faint stars stay visible,
// matching the Black Hole view's catalog stars.
constexpr char const* kStarVertex = R"(#version 330 core
layout(location = 0) in vec4 a_star;
layout(location = 1) in vec4 a_color;
uniform vec3 u_right;
uniform vec3 u_up;
uniform vec3 u_forward;
uniform vec2 u_tan;
uniform mat4 u_scene_from_sky;
uniform float u_brightness;
uniform float u_size;
uniform float u_limit;
uniform float u_pixel;
out vec3 v_color;
out float v_sigma;
out float v_peak;
out float v_half;
void main() {
    vec3 d = mat3(u_scene_from_sky) * a_star.xyz;
    float z = dot(d, u_forward);
    float flux = u_brightness * 3.0 * pow(10.0, -0.2 * a_star.w) * u_pixel * u_pixel;
    v_color = a_color.rgb;
    v_sigma = 1.0;
    v_peak = 0.0;
    v_half = 0.5;
    if (z <= 1e-4 || a_star.w > u_limit || flux < 0.003) {
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
        gl_PointSize = 1.0;
        return;
    }
    gl_Position = vec4(dot(d, u_right) / (z * u_tan.x), dot(d, u_up) / (z * u_tan.y), 0.0, 1.0);
    float sigma = u_size * max(0.55 * u_pixel, sqrt(flux / 6.28318530718));
    float size = ceil(sigma * 6.0) + 1.0;
    v_sigma = sigma;
    v_peak = min(flux / (6.28318530718 * sigma * sigma), 1.0);
    v_half = size * 0.5;
    gl_PointSize = size;
}
)";

constexpr char const* kStarFragment = R"(#version 330 core
in vec3 v_color;
in float v_sigma;
in float v_peak;
in float v_half;
out vec4 color;
void main() {
    vec2 r = (gl_PointCoord - 0.5) * 2.0 * v_half;
    color = vec4(v_color * v_peak * exp(-dot(r, r) / (2.0 * v_sigma * v_sigma)), 1.0);
}
)";

// Rows are the galactic axes in equatorial coordinates (Sky Atlas's frame).
constexpr double kGalacticFromEquatorial[3][3] = {
    {-0.0548755604, -0.8734370902, -0.4838350155},
    {0.4941094279, -0.4448296300, 0.7469822445},
    {-0.8676661490, -0.1980763734, 0.4559837762},
};

// Approximate star color for a B-V index, as sRGB in 0..255.
inline void bv_color(double bv, unsigned char* rgb) {
    const double t = 4600.0 * (1.0 / (0.92 * bv + 1.7) + 1.0 / (0.92 * bv + 0.62)) / 100.0;
    double r = t <= 66 ? 255 : 329.698727446 * std::pow(t - 60, -0.1332047592);
    double g = t <= 66 ? 99.4708025861 * std::log(t) - 161.1195681661 : 288.1221695283 * std::pow(t - 60, -0.0755148492);
    double b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * std::log(t - 10) - 305.0447927307;
    rgb[0] = static_cast<unsigned char>(std::clamp(r, 0.0, 255.0));
    rgb[1] = static_cast<unsigned char>(std::clamp(g, 0.0, 255.0));
    rgb[2] = static_cast<unsigned char>(std::clamp(b, 0.0, 255.0));
    rgb[3] = 255;
}

struct Sky {
    bool milky_way = false;
    bool stars = false;
    bool catalog = true;
    float brightness = 0.6f;
    float black = 0.06f;
    float saturation = 1.0f;
    float star_brightness = 4.0f;
    float star_size = 1.0f;
    float faintest = 8.0f;
    // Tilt (degrees about the scene's x axis) stands the galactic plane up; rotation then
    // turns the galactic center to that azimuth about the scene's z axis. The defaults put
    // the plane through the viewer's opening view direction (yaw 1.1 looks toward azimuth
    // 243 degrees), so the southern Milky Way around Crux and Centaurus is behind the galaxies.
    float rotation = -117.0f;
    float tilt = 90.0f;

    GLuint sky_program = 0, star_program = 0, empty_vao = 0, texture = 0;
    GLuint random_vao = 0, random_buffers[2] = {}, catalog_vao = 0, catalog_buffers[2] = {};
    GLsizei random_count = 0, catalog_count = 0;

    void upload_milky_way(unsigned char const* rgb, int width, int height) {
        GLint largest = 0;
        glGetIntegerv(GL_MAX_TEXTURE_SIZE, &largest);
        if (!rgb || width <= 0 || height <= 0 || width > largest || height > largest)
            throw std::runtime_error("The Milky Way texture is larger than this GPU supports.");
        using GenerateMipmap = void(APIENTRY*)(GLenum);
        static auto const generate_mipmap = reinterpret_cast<GenerateMipmap>(glfwGetProcAddress("glGenerateMipmap"));
        if (!texture) glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, width, height, 0, GL_RGB, GL_UNSIGNED_BYTE, rgb);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        if (generate_mipmap) generate_mipmap(GL_TEXTURE_2D);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, generate_mipmap ? GL_LINEAR_MIPMAP_LINEAR : GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glBindTexture(GL_TEXTURE_2D, 0);
    }

    // stars: equatorial unit vector and V magnitude per star; colors: sRGB bytes with alpha.
    void upload_catalog(float const* stars_xyzm, unsigned char const* colors, int count) {
        catalog_count = 0;
        if (!stars_xyzm || !colors || count <= 0) return;
        upload(catalog_vao, catalog_buffers, stars_xyzm, colors, count);
        catalog_count = count;
    }

    void draw(ViewState const& view, int width, int height, GLuint framebuffer) {
        const bool show_milky_way = milky_way && texture;
        if (!show_milky_way && !stars) return;
        prepare();
        gl_bind_framebuffer(GL_FRAMEBUFFER, framebuffer);
        glViewport(0, 0, width, height);
        const GLboolean depth = glIsEnabled(GL_DEPTH_TEST), cull = glIsEnabled(GL_CULL_FACE), blend = glIsEnabled(GL_BLEND);
        GLboolean depth_mask = GL_TRUE;
        glGetBooleanv(GL_DEPTH_WRITEMASK, &depth_mask);
        GLint source = GL_ONE, destination = GL_ZERO;
        glGetIntegerv(GL_BLEND_SRC, &source);
        glGetIntegerv(GL_BLEND_DST, &destination);
        glDisable(GL_DEPTH_TEST);
        glDisable(GL_CULL_FACE);
        glDepthMask(GL_FALSE);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE_MINUS_DST_COLOR, GL_ONE);

        const physics::Vec3 eye = view.camera.eye();
        const physics::Vec3 forward = physics::normalized(view.camera.target - eye);
        const physics::Vec3 right = physics::normalized(physics::cross(forward, physics::Vec3{0, 0, 1}));
        const physics::Vec3 up = physics::cross(right, forward);
        const float tan_half = static_cast<float>(std::tan(22.5 * kPi / 180.0));
        const float aspect = static_cast<float>(width) / static_cast<float>(height);
        Mat4 sky_from_scene, scene_from_sky;
        orientation(sky_from_scene, scene_from_sky);
        auto camera = [&](GLuint program) {
            gl_uniform_3f(gl_get_uniform_location(program, "u_right"), float(right.x), float(right.y), float(right.z));
            gl_uniform_3f(gl_get_uniform_location(program, "u_up"), float(up.x), float(up.y), float(up.z));
            gl_uniform_3f(gl_get_uniform_location(program, "u_forward"), float(forward.x), float(forward.y), float(forward.z));
            gl_uniform_2f(gl_get_uniform_location(program, "u_tan"), aspect * tan_half, tan_half);
        };

        if (show_milky_way) {
            gl_use_program(sky_program);
            camera(sky_program);
            gl_uniform_matrix4_fv(gl_get_uniform_location(sky_program, "u_sky_from_scene"), 1, GL_FALSE, sky_from_scene.m);
            gl_uniform_1i(gl_get_uniform_location(sky_program, "u_milky_way"), 0);
            gl_uniform_1f(gl_get_uniform_location(sky_program, "u_brightness"), brightness);
            gl_uniform_1f(gl_get_uniform_location(sky_program, "u_black"), black);
            gl_uniform_1f(gl_get_uniform_location(sky_program, "u_saturation"), saturation);
            gl_active_texture(GL_TEXTURE0);
            glBindTexture(GL_TEXTURE_2D, texture);
            gl_bind_vertex_array(empty_vao);
            glDrawArrays(GL_TRIANGLES, 0, 3);
            glBindTexture(GL_TEXTURE_2D, 0);
        }
        if (stars) {
            const bool real = catalog && catalog_count > 0;
            gl_use_program(star_program);
            camera(star_program);
            gl_uniform_matrix4_fv(gl_get_uniform_location(star_program, "u_scene_from_sky"), 1, GL_FALSE, scene_from_sky.m);
            gl_uniform_1f(gl_get_uniform_location(star_program, "u_brightness"), star_brightness);
            gl_uniform_1f(gl_get_uniform_location(star_program, "u_size"), star_size);
            gl_uniform_1f(gl_get_uniform_location(star_program, "u_limit"), faintest);
            // Star sizes follow output height so exports match the viewport.
            gl_uniform_1f(gl_get_uniform_location(star_program, "u_pixel"), std::max(height / 1080.0f, 0.75f));
            glEnable(GL_PROGRAM_POINT_SIZE);
            gl_bind_vertex_array(real ? catalog_vao : random_vao);
            glDrawArrays(GL_POINTS, 0, real ? catalog_count : random_count);
        }

        gl_bind_vertex_array(0);
        gl_use_program(0);
        glBlendFunc(static_cast<GLenum>(source), static_cast<GLenum>(destination));
        if (!blend) glDisable(GL_BLEND);
        if (depth) glEnable(GL_DEPTH_TEST);
        if (cull) glEnable(GL_CULL_FACE);
        glDepthMask(depth_mask);
    }

    void destroy() {
        if (sky_program) gl_delete_program(sky_program);
        if (star_program) gl_delete_program(star_program);
        if (texture) glDeleteTextures(1, &texture);
        GLuint arrays[] = {empty_vao, random_vao, catalog_vao};
        for (GLuint array : arrays)
            if (array) gl_delete_vertex_arrays(1, &array);
        for (GLuint const* buffers : {random_buffers, catalog_buffers})
            if (buffers[0]) gl_delete_buffers(2, buffers);
        *this = Sky{};
    }

private:
    void prepare() {
        if (sky_program) return;
        sky_program = link_program(kSkyVertex, kSkyFragment);
        star_program = link_program(kStarVertex, kStarFragment);
        gl_gen_vertex_arrays(1, &empty_vao);
        make_random_stars();
    }

    static void upload(GLuint& vao, GLuint (&buffers)[2], float const* stars_xyzm, unsigned char const* colors, int count) {
        if (!vao) {
            gl_gen_vertex_arrays(1, &vao);
            gl_gen_buffers(2, buffers);
        }
        gl_bind_vertex_array(vao);
        gl_bind_buffer(GL_ARRAY_BUFFER, buffers[0]);
        gl_buffer_data(GL_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(count) * 4 * sizeof(float), stars_xyzm, GL_STATIC_DRAW);
        gl_vertex_attrib_pointer(0, 4, GL_FLOAT, GL_FALSE, 0, nullptr);
        gl_enable_vertex_attrib_array(0);
        gl_bind_buffer(GL_ARRAY_BUFFER, buffers[1]);
        gl_buffer_data(GL_ARRAY_BUFFER, static_cast<std::ptrdiff_t>(count) * 4, colors, GL_STATIC_DRAW);
        gl_vertex_attrib_pointer(1, 4, GL_UNSIGNED_BYTE, GL_TRUE, 0, nullptr);
        gl_enable_vertex_attrib_array(1);
        gl_bind_vertex_array(0);
    }

    // Decorative stars: about as many as the catalog holds to V 9.5, a third of them
    // crowded toward the galactic plane so they follow the Milky Way.
    void make_random_stars() {
        constexpr int count = 90000;
        std::mt19937 random(20261017u);
        std::uniform_real_distribution<double> unit(0.0, 1.0);
        std::normal_distribution<double> color_index(0.65, 0.42);
        std::vector<float> stars(count * 4);
        std::vector<unsigned char> colors(count * 4);
        const double slope = 0.45, faint = 9.5, bright = -1.0;
        const double low = std::pow(10.0, slope * bright), high = std::pow(10.0, slope * faint);
        for (int i = 0; i < count; ++i) {
            const double longitude = 2 * kPi * unit(random);
            double latitude;
            if (unit(random) < 0.35) {
                const double spread = -0.15 * std::log(std::max(unit(random), 1e-12));
                latitude = unit(random) < 0.5 ? spread : -spread;
                latitude = std::clamp(latitude, -kPi / 2, kPi / 2);
            } else {
                latitude = std::asin(2 * unit(random) - 1);
            }
            const double g[3] = {std::cos(latitude) * std::cos(longitude), std::cos(latitude) * std::sin(longitude),
                                 std::sin(latitude)};
            for (int axis = 0; axis < 3; ++axis) {
                stars[i * 4 + axis] = static_cast<float>(kGalacticFromEquatorial[0][axis] * g[0] +
                                                         kGalacticFromEquatorial[1][axis] * g[1] +
                                                         kGalacticFromEquatorial[2][axis] * g[2]);
            }
            stars[i * 4 + 3] = static_cast<float>(std::log10(low + unit(random) * (high - low)) / slope);
            bv_color(std::clamp(color_index(random), -0.3, 1.9), &colors[i * 4]);
        }
        upload(random_vao, random_buffers, stars.data(), colors.data(), count);
        random_count = count;
    }

    // At zero rotation and tilt the scene's axes are galactic axes, and the Milky Way
    // band lies in the merger's orbital (xy) plane.
    void orientation(Mat4& sky_from_scene, Mat4& scene_from_sky) const {
        const double r = rotation * kPi / 180.0, t = tilt * kPi / 180.0;
        const double rz[3][3] = {{std::cos(r), -std::sin(r), 0}, {std::sin(r), std::cos(r), 0}, {0, 0, 1}};
        const double rx[3][3] = {{1, 0, 0}, {0, std::cos(t), -std::sin(t)}, {0, std::sin(t), std::cos(t)}};
        double scene[3][3] = {};  // scene from galactic: tilt about x, then turn about z
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j)
                for (int k = 0; k < 3; ++k) scene[i][j] += rz[i][k] * rx[k][j];
        double m[3][3] = {};  // equatorial from scene = transpose(galactic from equatorial) * transpose(scene)
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j)
                for (int k = 0; k < 3; ++k) m[i][j] += kGalacticFromEquatorial[k][i] * scene[j][k];
        sky_from_scene = Mat4{};
        scene_from_sky = Mat4{};
        for (int row = 0; row < 3; ++row)
            for (int column = 0; column < 3; ++column) {
                sky_from_scene.m[column * 4 + row] = static_cast<float>(m[row][column]);
                scene_from_sky.m[row * 4 + column] = static_cast<float>(m[row][column]);
            }
    }
};

}  // namespace cast_sky
