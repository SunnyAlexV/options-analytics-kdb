# Build for the C++ pricing library.
#
# Two ways to build:
#   1. Python extension (what the system uses):   pip install -e .
#      scikit-build-core runs this file and installs pricing/_core into the env.
#   2. C++ unit tests (GoogleTest):
#      cmake -S . -B build -DOAK_BUILD_TESTS=ON && cmake --build build -j && ctest --test-dir build
cmake_minimum_required(VERSION 3.20)
project(oak_pricing LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# The pricing maths as a plain C++ library, shared by the Python module and the tests.
add_library(oak_black76 STATIC cpp/src/black76.cpp)
target_include_directories(oak_black76 PUBLIC cpp/include)
set_target_properties(oak_black76 PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_compile_options(oak_black76 PRIVATE -Wall -Wextra -Wpedantic)

# Python module pricing._core (only when Python is available, e.g. under pip).
option(OAK_BUILD_PYTHON "Build the pybind11 Python module" ON)
if(OAK_BUILD_PYTHON)
  find_package(Python 3.10 COMPONENTS Interpreter Development.Module QUIET)
  if(Python_FOUND)
    if(NOT pybind11_DIR)
      execute_process(COMMAND "${Python_EXECUTABLE}" -m pybind11 --cmakedir
                      OUTPUT_VARIABLE pybind11_DIR OUTPUT_STRIP_TRAILING_WHITESPACE)
    endif()
    find_package(pybind11 CONFIG REQUIRED)
    pybind11_add_module(_core cpp/src/bindings.cpp)
    target_link_libraries(_core PRIVATE oak_black76)
    install(TARGETS _core DESTINATION pricing)
  endif()
endif()

# C++ unit tests with GoogleTest (downloaded at configure time).
option(OAK_BUILD_TESTS "Build the C++ unit tests" OFF)
if(OAK_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(googletest
    URL https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
  FetchContent_MakeAvailable(googletest)
  enable_testing()
  add_executable(test_black76 cpp/tests/test_black76.cpp)
  target_link_libraries(test_black76 PRIVATE oak_black76 GTest::gtest_main)
  include(GoogleTest)
  gtest_discover_tests(test_black76)
endif()
