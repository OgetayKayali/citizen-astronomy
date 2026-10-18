#pragma once
// Stand-in for the two GLFW calls the vendored lab headers make. CAst owns the
// window and OpenGL context (Qt), so entry points come from the current WGL context.
#include "gl_loader.hpp"

using GLFWglproc = void (*)();
GLFWglproc glfwGetProcAddress(char const* name);
double glfwGetTime();
