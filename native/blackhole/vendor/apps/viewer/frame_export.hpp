#pragma once

#include <string>

// Writes a top-down RGB image. The caller flips OpenGL's bottom-up rows first.
bool write_png_rgb(std::string const& path, int width, int height, unsigned char const* rgb_top_down, std::string& error);

std::string frame_png_path(std::string const& directory, int frame);

// Folder when `directory` is true, otherwise a save-file path. Returns false if the user cancels.
bool browse_export_path(bool directory, std::string const& suggested, std::string& chosen);
bool browse_open_scene(std::string& chosen);
bool browse_open_recording(std::string& chosen);
bool browse_save_recording(std::string const& suggested, std::string& chosen);
bool browse_cosmic_cache(bool save,std::string& chosen);
bool browse_mp4(std::string& chosen);

// CAst: when non-empty, used instead of searching PATH for ffmpeg.exe.
extern std::string ffmpeg_executable_override;

// Feeds raw RGB frames to ffmpeg and closes the file when finished or destroyed.
class FfmpegPipe {
public:
    FfmpegPipe() = default;
    FfmpegPipe(FfmpegPipe const&) = delete;
    FfmpegPipe& operator=(FfmpegPipe const&) = delete;
    ~FfmpegPipe();

    bool start(int width, int height, int fps, std::string const& output_path, std::string& error);
    bool write_frame(unsigned char const* rgb_top_down, int width, int height, std::string& error);
    void finish();
    bool finish_checked(std::string& error);
    [[nodiscard]] bool open() const { return open_; }

private:
    bool open_ = false;
#if defined(_WIN32)
    void* process_ = nullptr;
    void* thread_ = nullptr;
    void* write_handle_ = nullptr;
#else
    struct FILE* file_ = nullptr;
#endif
};
