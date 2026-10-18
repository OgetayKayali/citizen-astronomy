cmake_minimum_required(VERSION 3.20)
project(cast_cluster_flow CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# vendor/ is produced by scripts/vendor_cluster_flow.py from PhysicsEngine:
# the cosmic_flow library unchanged, and apps/cosmic/main.cpp as cosmic_core.hpp
# without its command line, ImGui panel, GLFW window loop and main().
set(VENDOR ${CMAKE_CURRENT_SOURCE_DIR}/vendor)
add_library(cast_cluster_flow SHARED
    cast_cluster_flow.cpp
    ${VENDOR}/src/cosmic/flow.cpp
    ${VENDOR}/src/cosmic/cosmology.cpp
    ${VENDOR}/src/cosmic/recording.cpp
    ${VENDOR}/apps/viewer/gl_loader.cpp
    ${VENDOR}/apps/viewer/frame_export.cpp)
target_include_directories(cast_cluster_flow PRIVATE
    ${VENDOR}/src
    ${VENDOR}/apps/cosmic
    ${VENDOR}/apps/viewer
    ${VENDOR}/third_party)
target_link_libraries(cast_cluster_flow PRIVATE opengl32 ole32 shell32)
find_package(OpenMP REQUIRED)
target_link_libraries(cast_cluster_flow PRIVATE OpenMP::OpenMP_CXX)
if(MSVC)
    target_compile_options(cast_cluster_flow PRIVATE /W3 /permissive- /utf-8 /bigobj)
endif()
