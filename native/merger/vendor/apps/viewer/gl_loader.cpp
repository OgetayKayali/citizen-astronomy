#include "gl_loader.hpp"

GlCreateShader gl_create_shader = nullptr;
GlShaderSource gl_shader_source = nullptr;
GlCompileShader gl_compile_shader = nullptr;
GlGetShaderiv gl_get_shader_iv = nullptr;
GlGetShaderInfoLog gl_get_shader_info_log = nullptr;
GlDeleteShader gl_delete_shader = nullptr;
GlCreateProgram gl_create_program = nullptr;
GlAttachShader gl_attach_shader = nullptr;
GlLinkProgram gl_link_program = nullptr;
GlGetProgramiv gl_get_program_iv = nullptr;
GlGetProgramInfoLog gl_get_program_info_log = nullptr;
GlUseProgram gl_use_program = nullptr;
GlDeleteProgram gl_delete_program = nullptr;
GlGenVertexArrays gl_gen_vertex_arrays = nullptr;
GlBindVertexArray gl_bind_vertex_array = nullptr;
GlDeleteVertexArrays gl_delete_vertex_arrays = nullptr;
GlGenBuffers gl_gen_buffers = nullptr;
GlBindBuffer gl_bind_buffer = nullptr;
GlBufferData gl_buffer_data = nullptr;
GlDeleteBuffers gl_delete_buffers = nullptr;
GlVertexAttribPointer gl_vertex_attrib_pointer = nullptr;
GlEnableVertexAttribArray gl_enable_vertex_attrib_array = nullptr;
GlGetUniformLocation gl_get_uniform_location = nullptr;
GlUniform1f gl_uniform_1f = nullptr;
GlUniform1i gl_uniform_1i = nullptr;
GlUniform2f gl_uniform_2f = nullptr;
GlUniform3f gl_uniform_3f = nullptr;
GlActiveTexture gl_active_texture = nullptr;
GlUniformMatrix4fv gl_uniform_matrix4_fv = nullptr;
GlGenFramebuffers gl_gen_framebuffers = nullptr;
GlBindFramebuffer gl_bind_framebuffer = nullptr;
GlDeleteFramebuffers gl_delete_framebuffers = nullptr;
GlGenRenderbuffers gl_gen_renderbuffers = nullptr;
GlBindRenderbuffer gl_bind_renderbuffer = nullptr;
GlDeleteRenderbuffers gl_delete_renderbuffers = nullptr;
GlRenderbufferStorage gl_renderbuffer_storage = nullptr;
GlFramebufferTexture2D gl_framebuffer_texture_2d = nullptr;
GlFramebufferRenderbuffer gl_framebuffer_renderbuffer = nullptr;
GlCheckFramebufferStatus gl_check_framebuffer_status = nullptr;
GlDrawBuffers gl_draw_buffers = nullptr;
GlTexImage3D gl_tex_image_3d = nullptr;

namespace {

template <typename Function>
void load_one(GlLoader loader, char const* name, Function& function) {
    function = reinterpret_cast<Function>(loader(name));
    if (function == nullptr) {
        throw std::runtime_error(std::string("OpenGL entry point is missing: ") + name);
    }
}

}  // namespace

void load_gl(GlLoader loader) {
    load_one(loader, "glCreateShader", gl_create_shader);
    load_one(loader, "glShaderSource", gl_shader_source);
    load_one(loader, "glCompileShader", gl_compile_shader);
    load_one(loader, "glGetShaderiv", gl_get_shader_iv);
    load_one(loader, "glGetShaderInfoLog", gl_get_shader_info_log);
    load_one(loader, "glDeleteShader", gl_delete_shader);
    load_one(loader, "glCreateProgram", gl_create_program);
    load_one(loader, "glAttachShader", gl_attach_shader);
    load_one(loader, "glLinkProgram", gl_link_program);
    load_one(loader, "glGetProgramiv", gl_get_program_iv);
    load_one(loader, "glGetProgramInfoLog", gl_get_program_info_log);
    load_one(loader, "glUseProgram", gl_use_program);
    load_one(loader, "glDeleteProgram", gl_delete_program);
    load_one(loader, "glGenVertexArrays", gl_gen_vertex_arrays);
    load_one(loader, "glBindVertexArray", gl_bind_vertex_array);
    load_one(loader, "glDeleteVertexArrays", gl_delete_vertex_arrays);
    load_one(loader, "glGenBuffers", gl_gen_buffers);
    load_one(loader, "glBindBuffer", gl_bind_buffer);
    load_one(loader, "glBufferData", gl_buffer_data);
    load_one(loader, "glDeleteBuffers", gl_delete_buffers);
    load_one(loader, "glVertexAttribPointer", gl_vertex_attrib_pointer);
    load_one(loader, "glEnableVertexAttribArray", gl_enable_vertex_attrib_array);
    load_one(loader, "glGetUniformLocation", gl_get_uniform_location);
    load_one(loader, "glUniform1f", gl_uniform_1f);
    load_one(loader, "glUniform1i", gl_uniform_1i);
    load_one(loader, "glUniform2f", gl_uniform_2f);
    load_one(loader, "glUniform3f", gl_uniform_3f);
    load_one(loader, "glActiveTexture", gl_active_texture);
    load_one(loader, "glUniformMatrix4fv", gl_uniform_matrix4_fv);
    load_one(loader, "glGenFramebuffers", gl_gen_framebuffers);
    load_one(loader, "glBindFramebuffer", gl_bind_framebuffer);
    load_one(loader, "glDeleteFramebuffers", gl_delete_framebuffers);
    load_one(loader, "glGenRenderbuffers", gl_gen_renderbuffers);
    load_one(loader, "glBindRenderbuffer", gl_bind_renderbuffer);
    load_one(loader, "glDeleteRenderbuffers", gl_delete_renderbuffers);
    load_one(loader, "glRenderbufferStorage", gl_renderbuffer_storage);
    load_one(loader, "glFramebufferTexture2D", gl_framebuffer_texture_2d);
    load_one(loader, "glFramebufferRenderbuffer", gl_framebuffer_renderbuffer);
    load_one(loader, "glCheckFramebufferStatus", gl_check_framebuffer_status);
    load_one(loader, "glDrawBuffers", gl_draw_buffers);
    load_one(loader, "glTexImage3D", gl_tex_image_3d);
}
