#pragma once

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include <GL/gl.h>

#include <cstddef>
#include <stdexcept>
#include <string>

#ifndef GL_ARRAY_BUFFER
#define GL_ARRAY_BUFFER 0x8892
#endif
#ifndef GL_ELEMENT_ARRAY_BUFFER
#define GL_ELEMENT_ARRAY_BUFFER 0x8893
#endif
#ifndef GL_STATIC_DRAW
#define GL_STATIC_DRAW 0x88E4
#endif
#ifndef GL_DYNAMIC_DRAW
#define GL_DYNAMIC_DRAW 0x88E8
#endif
#ifndef GL_VERTEX_SHADER
#define GL_VERTEX_SHADER 0x8B31
#endif
#ifndef GL_FRAGMENT_SHADER
#define GL_FRAGMENT_SHADER 0x8B30
#endif
#ifndef GL_COMPILE_STATUS
#define GL_COMPILE_STATUS 0x8B81
#endif
#ifndef GL_LINK_STATUS
#define GL_LINK_STATUS 0x8B82
#endif
#ifndef GL_INFO_LOG_LENGTH
#define GL_INFO_LOG_LENGTH 0x8B84
#endif

using GlCreateShader = GLuint(APIENTRY*)(GLenum);
using GlShaderSource = void(APIENTRY*)(GLuint, GLsizei, char const* const*, GLint const*);
using GlCompileShader = void(APIENTRY*)(GLuint);
using GlGetShaderiv = void(APIENTRY*)(GLuint, GLenum, GLint*);
using GlGetShaderInfoLog = void(APIENTRY*)(GLuint, GLsizei, GLsizei*, char*);
using GlDeleteShader = void(APIENTRY*)(GLuint);
using GlCreateProgram = GLuint(APIENTRY*)();
using GlAttachShader = void(APIENTRY*)(GLuint, GLuint);
using GlLinkProgram = void(APIENTRY*)(GLuint);
using GlGetProgramiv = void(APIENTRY*)(GLuint, GLenum, GLint*);
using GlGetProgramInfoLog = void(APIENTRY*)(GLuint, GLsizei, GLsizei*, char*);
using GlUseProgram = void(APIENTRY*)(GLuint);
using GlDeleteProgram = void(APIENTRY*)(GLuint);
using GlGenVertexArrays = void(APIENTRY*)(GLsizei, GLuint*);
using GlBindVertexArray = void(APIENTRY*)(GLuint);
using GlDeleteVertexArrays = void(APIENTRY*)(GLsizei, GLuint const*);
using GlGenBuffers = void(APIENTRY*)(GLsizei, GLuint*);
using GlBindBuffer = void(APIENTRY*)(GLenum, GLuint);
using GlBufferData = void(APIENTRY*)(GLenum, std::ptrdiff_t, void const*, GLenum);
using GlDeleteBuffers = void(APIENTRY*)(GLsizei, GLuint const*);
using GlVertexAttribPointer = void(APIENTRY*)(GLuint, GLint, GLenum, GLboolean, GLsizei, void const*);
using GlEnableVertexAttribArray = void(APIENTRY*)(GLuint);
using GlGetUniformLocation = GLint(APIENTRY*)(GLuint, char const*);
using GlUniform1f = void(APIENTRY*)(GLint, GLfloat);
using GlUniform1i = void(APIENTRY*)(GLint, GLint);
using GlUniform2f = void(APIENTRY*)(GLint, GLfloat, GLfloat);
using GlUniform3f = void(APIENTRY*)(GLint, GLfloat, GLfloat, GLfloat);
using GlActiveTexture = void(APIENTRY*)(GLenum);
using GlUniformMatrix4fv = void(APIENTRY*)(GLint, GLsizei, GLboolean, GLfloat const*);
using GlGenFramebuffers = void(APIENTRY*)(GLsizei, GLuint*);
using GlBindFramebuffer = void(APIENTRY*)(GLenum, GLuint);
using GlDeleteFramebuffers = void(APIENTRY*)(GLsizei, GLuint const*);
using GlGenRenderbuffers = void(APIENTRY*)(GLsizei, GLuint*);
using GlBindRenderbuffer = void(APIENTRY*)(GLenum, GLuint);
using GlDeleteRenderbuffers = void(APIENTRY*)(GLsizei, GLuint const*);
using GlRenderbufferStorage = void(APIENTRY*)(GLenum, GLenum, GLsizei, GLsizei);
using GlFramebufferTexture2D = void(APIENTRY*)(GLenum, GLenum, GLenum, GLuint, GLint);
using GlFramebufferRenderbuffer = void(APIENTRY*)(GLenum, GLenum, GLenum, GLuint);
using GlCheckFramebufferStatus = GLenum(APIENTRY*)(GLenum);
using GlDrawBuffers = void(APIENTRY*)(GLsizei, GLenum const*);
using GlTexImage3D = void(APIENTRY*)(GLenum, GLint, GLint, GLsizei, GLsizei, GLsizei, GLint, GLenum, GLenum, void const*);

extern GlCreateShader gl_create_shader;
extern GlShaderSource gl_shader_source;
extern GlCompileShader gl_compile_shader;
extern GlGetShaderiv gl_get_shader_iv;
extern GlGetShaderInfoLog gl_get_shader_info_log;
extern GlDeleteShader gl_delete_shader;
extern GlCreateProgram gl_create_program;
extern GlAttachShader gl_attach_shader;
extern GlLinkProgram gl_link_program;
extern GlGetProgramiv gl_get_program_iv;
extern GlGetProgramInfoLog gl_get_program_info_log;
extern GlUseProgram gl_use_program;
extern GlDeleteProgram gl_delete_program;
extern GlGenVertexArrays gl_gen_vertex_arrays;
extern GlBindVertexArray gl_bind_vertex_array;
extern GlDeleteVertexArrays gl_delete_vertex_arrays;
extern GlGenBuffers gl_gen_buffers;
extern GlBindBuffer gl_bind_buffer;
extern GlBufferData gl_buffer_data;
extern GlDeleteBuffers gl_delete_buffers;
extern GlVertexAttribPointer gl_vertex_attrib_pointer;
extern GlEnableVertexAttribArray gl_enable_vertex_attrib_array;
extern GlGetUniformLocation gl_get_uniform_location;
extern GlUniform1f gl_uniform_1f;
extern GlUniform1i gl_uniform_1i;
extern GlUniform2f gl_uniform_2f;
extern GlUniform3f gl_uniform_3f;
extern GlActiveTexture gl_active_texture;
extern GlUniformMatrix4fv gl_uniform_matrix4_fv;
extern GlGenFramebuffers gl_gen_framebuffers;
extern GlBindFramebuffer gl_bind_framebuffer;
extern GlDeleteFramebuffers gl_delete_framebuffers;
extern GlGenRenderbuffers gl_gen_renderbuffers;
extern GlBindRenderbuffer gl_bind_renderbuffer;
extern GlDeleteRenderbuffers gl_delete_renderbuffers;
extern GlRenderbufferStorage gl_renderbuffer_storage;
extern GlFramebufferTexture2D gl_framebuffer_texture_2d;
extern GlFramebufferRenderbuffer gl_framebuffer_renderbuffer;
extern GlCheckFramebufferStatus gl_check_framebuffer_status;
extern GlDrawBuffers gl_draw_buffers;
extern GlTexImage3D gl_tex_image_3d;

#ifndef GL_FRAMEBUFFER
#define GL_FRAMEBUFFER 0x8D40
#endif
#ifndef GL_RENDERBUFFER
#define GL_RENDERBUFFER 0x8D41
#endif
#ifndef GL_COLOR_ATTACHMENT0
#define GL_COLOR_ATTACHMENT0 0x8CE0
#endif
#ifndef GL_DEPTH_ATTACHMENT
#define GL_DEPTH_ATTACHMENT 0x8D00
#endif
#ifndef GL_FRAMEBUFFER_COMPLETE
#define GL_FRAMEBUFFER_COMPLETE 0x8CD5
#endif
#ifndef GL_RGB8
#define GL_RGB8 0x8051
#endif
#ifndef GL_DEPTH_COMPONENT24
#define GL_DEPTH_COMPONENT24 0x81A6
#endif

using GlLoader = void* (*)(char const*);
void load_gl(GlLoader loader);
