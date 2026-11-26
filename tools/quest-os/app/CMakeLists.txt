cmake_minimum_required(VERSION 3.22)
project(anyps5_os C CXX)
set(CMAKE_CXX_STANDARD 20)
set(OPENXR_SDK "" CACHE PATH "OpenXR-SDK source")
set(CONSOLE_SRC "" CACHE PATH "console-native/src")
set(DEPS "" CACHE PATH "static deps prefix")
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_API_LAYERS OFF CACHE BOOL "" FORCE)
set(DYNAMIC_LOADER ON CACHE BOOL "" FORCE)
add_subdirectory(${OPENXR_SDK} openxr EXCLUDE_FROM_ALL)
add_library(native_app_glue STATIC ${ANDROID_NDK}/sources/android/native_app_glue/android_native_app_glue.c)
target_include_directories(native_app_glue PUBLIC ${ANDROID_NDK}/sources/android/native_app_glue)
file(GLOB_RECURSE CONSOLE_SOURCES CONFIGURE_DEPENDS
  ${CONSOLE_SRC}/core/*.cpp ${CONSOLE_SRC}/host/*.cpp ${CONSOLE_SRC}/widgets/*.cpp ${CONSOLE_SRC}/screens/*.cpp
  )
file(GLOB PLATFORM_SOURCES CONFIGURE_DEPENDS ${CONSOLE_SRC}/platform/*.cpp ${CONSOLE_SRC}/platform/posix/*.cpp)
add_library(anyps5os SHARED xr_main.cpp ${CONSOLE_SOURCES} ${PLATFORM_SOURCES})
target_compile_definitions(anyps5os PRIVATE ANYPS5_SOURCE_ASSETS="/data/local/tmp/anyps5-assets")
target_include_directories(anyps5os PRIVATE ${CONSOLE_SRC} ${DEPS}/include)
target_link_libraries(anyps5os native_app_glue openxr_loader
  ${DEPS}/lib/libSDL3_ttf.a ${DEPS}/lib/libSDL3_image.a ${DEPS}/lib/libSDL3.a
  vulkan android log dl m GLESv2 EGL OpenSLES)
set_target_properties(anyps5os PROPERTIES LINK_FLAGS "-u ANativeActivity_onCreate")
