cmake_minimum_required(VERSION 3.20)
project(cast_blackhole CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# vendor/ is copied from PhysicsEngine (src/blackhole, apps/blackhole, apps/viewer,
# third_party/stb_image_write.h). Local changes are marked "CAst:".
set(VENDOR ${CMAKE_CURRENT_SOURCE_DIR}/vendor)
add_library(cast_blackhole SHARED
    cast_blackhole.cpp
    ${VENDOR}/src/blackhole/geodesic.cpp
    ${VENDOR}/src/blackhole/kerr.cpp
    ${VENDOR}/src/blackhole/flow.cpp
    ${VENDOR}/apps/viewer/gl_loader.cpp
    ${VENDOR}/apps/viewer/frame_export.cpp)
target_include_directories(cast_blackhole PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/shim
    ${VENDOR}/src
    ${VENDOR}/apps/blackhole
    ${VENDOR}/apps/viewer
    ${VENDOR}/third_party)
target_link_libraries(cast_blackhole PRIVATE opengl32 ole32 shell32)
if(MSVC)
    target_compile_options(cast_blackhole PRIVATE /W3 /permissive- /utf-8 /O2)
endif()
