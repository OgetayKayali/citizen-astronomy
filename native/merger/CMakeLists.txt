cmake_minimum_required(VERSION 3.20)
project(cast_merger CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# vendor/ is produced by scripts/vendor_galaxy_merger.py from PhysicsEngine:
# the physics library unchanged, and apps/viewer/main.cpp as viewer_core.hpp
# without its ImGui panel, GLFW window and main().
set(VENDOR ${CMAKE_CURRENT_SOURCE_DIR}/vendor)
add_library(cast_merger SHARED
    cast_merger.cpp
    ${VENDOR}/src/core/state.cpp
    ${VENDOR}/src/visualization/optical.cpp
    ${VENDOR}/src/visualization/tracers.cpp
    ${VENDOR}/src/forces/forces.cpp
    ${VENDOR}/src/forces/hydro.cpp
    ${VENDOR}/src/integrators/integrators.cpp
    ${VENDOR}/src/simulation/scene.cpp
    ${VENDOR}/src/simulation/galaxy.cpp
    ${VENDOR}/src/simulation/validation.cpp
    ${VENDOR}/src/io/sphere.cpp
    ${VENDOR}/src/io/trajectory.cpp
    ${VENDOR}/src/io/gltf.cpp
    ${VENDOR}/apps/viewer/gl_loader.cpp
    ${VENDOR}/apps/viewer/frame_export.cpp)
target_include_directories(cast_merger PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/shim
    ${VENDOR}/src
    ${VENDOR}/apps/viewer
    ${VENDOR}/third_party)
target_link_libraries(cast_merger PRIVATE opengl32 ole32 shell32)
find_package(OpenMP REQUIRED)
target_link_libraries(cast_merger PRIVATE OpenMP::OpenMP_CXX)
if(MSVC)
    target_compile_options(cast_merger PRIVATE /W3 /permissive- /utf-8 /bigobj)
endif()
