cmake_minimum_required(VERSION 3.13)
project(runner LANGUAGES CXX)

# Define the application target. To change its name, change BINARY_NAME in the
# top-level CMakeLists.txt, not the value here, or `flutter run` will no longer
# work.
#
# Any new source files that you add to the application should be added here.
add_executable(${BINARY_NAME}
  "main.cc"
  "my_application.cc"
  "${FLUTTER_MANAGED_DIR}/generated_plugin_registrant.cc"
)

# Apply the standard set of build settings. This can be removed for applications
# that need different build settings.
apply_standard_settings(${BINARY_NAME})

# Add preprocessor definitions for the application ID.
add_definitions(-DAPPLICATION_ID="${APPLICATION_ID}")

# Add dependency libraries. Add any application-specific dependencies here.
target_link_libraries(${BINARY_NAME} PRIVATE flutter)
target_link_libraries(${BINARY_NAME} PRIVATE PkgConfig::GTK)

# Impeller's software fallback has to be detected before the view is created.
# Epoxy is a hard dependency of libgtk-3-dev (gtkglarea.h includes it), so it needs
# no new package; EGL itself comes from the same mesa runtime Flutter requires.
# Without epoxy the probe is skipped and Impeller stays on, as before.
pkg_check_modules(ODESSY_EPOXY IMPORTED_TARGET epoxy)
if(ODESSY_EPOXY_FOUND)
  target_link_libraries(${BINARY_NAME} PRIVATE PkgConfig::ODESSY_EPOXY)
  target_compile_definitions(${BINARY_NAME} PRIVATE ODESSY_HAVE_EPOXY=1)
else()
  message(WARNING "epoxy not found: software-renderer detection is disabled")
endif()

target_include_directories(${BINARY_NAME} PRIVATE "${CMAKE_SOURCE_DIR}")
