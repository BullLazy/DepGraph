cmake_minimum_required(VERSION 3.22)
project(audio_pipeline LANGUAGES CXX)

find_package(OpenCV REQUIRED)
find_package(Boost COMPONENTS filesystem REQUIRED)
FetchContent_Declare(googletest GIT_REPOSITORY https://github.com/google/googletest.git GIT_TAG v1.14.0)