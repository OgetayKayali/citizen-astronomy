#include "frame_export.hpp"

#include <algorithm>
#include <filesystem>
#include <iomanip>
#include <sstream>
#include <vector>

#if defined(_MSC_VER)
#pragma warning(push)
#pragma warning(disable : 4996 4244 4267)
#endif
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"
#if defined(_MSC_VER)
#pragma warning(pop)
#endif

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shobjidl.h>
#else
#include <cstdio>
#include <sys/wait.h>
#endif

std::string ffmpeg_executable_override;

bool write_png_rgb(std::string const& path, int width, int height, unsigned char const* rgb_top_down, std::string& error) {
    std::filesystem::path file(path);
    if (file.has_parent_path()) {
        std::error_code code;
        std::filesystem::create_directories(file.parent_path(), code);
        if (code) {
            error = "could not create '" + file.parent_path().string() + "'";
            return false;
        }
    }
    if (stbi_write_png(path.c_str(), width, height, 3, rgb_top_down, width * 3) == 0) {
        error = "could not write '" + path + "'";
        return false;
    }
    return true;
}

std::string frame_png_path(std::string const& directory, int frame) {
    std::ostringstream name;
    name << "frame_" << std::setw(6) << std::setfill('0') << frame << ".png";
    return (std::filesystem::path(directory) / name.str()).string();
}

#if defined(_WIN32)

std::wstring widen(std::string const& text) {
    if (text.empty()) {
        return {};
    }
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.c_str(), -1, nullptr, 0);
    unsigned int code_page = CP_UTF8;
    if (count <= 0) {
        code_page = CP_ACP;
        count = MultiByteToWideChar(code_page, 0, text.c_str(), -1, nullptr, 0);
    }
    if (count <= 0) {
        return {};
    }
    std::wstring wide(static_cast<std::size_t>(count), L'\0');
    MultiByteToWideChar(code_page, 0, text.c_str(), -1, wide.data(), count);
    if (!wide.empty() && wide.back() == L'\0') {
        wide.pop_back();
    }
    return wide;
}

bool browse_file(bool save, bool directory, wchar_t const* title, COMDLG_FILTERSPEC const* filters, unsigned filter_count,
                 std::string const& suggested, std::string& chosen) {
    HRESULT started = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    const bool uninit = SUCCEEDED(started);
    IFileDialog* dialog = nullptr;
    const CLSID kind = save ? CLSID_FileSaveDialog : CLSID_FileOpenDialog;
    HRESULT created = CoCreateInstance(kind, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dialog));
    if (FAILED(created) || dialog == nullptr) {
        if (uninit) {
            CoUninitialize();
        }
        return false;
    }
    DWORD options = 0;
    dialog->GetOptions(&options);
    options |= FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR | FOS_FILEMUSTEXIST;
    if (save) {
        options |= FOS_OVERWRITEPROMPT;
        options &= ~static_cast<DWORD>(FOS_FILEMUSTEXIST);
    }
    if (directory) {
        options |= FOS_PICKFOLDERS;
    }
    dialog->SetOptions(options);
    dialog->SetTitle(title);
    if (filters != nullptr && filter_count > 0) {
        dialog->SetFileTypes(filter_count, filters);
    }
    if (!suggested.empty()) {
        const std::wstring wide = widen(suggested);
        if (!wide.empty()) {
            dialog->SetFileName(wide.c_str());
        }
    }
    const HRESULT shown = dialog->Show(nullptr);
    bool accepted = false;
    if (SUCCEEDED(shown)) {
        IShellItem* item = nullptr;
        if (SUCCEEDED(dialog->GetResult(&item)) && item != nullptr) {
            PWSTR text = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &text)) && text != nullptr) {
                chosen = std::filesystem::path(text).string();
                accepted = true;
                CoTaskMemFree(text);
            }
            item->Release();
        }
    }
    dialog->Release();
    if (uninit) {
        CoUninitialize();
    }
    return accepted;
}

bool browse_export_path(bool directory, std::string const& suggested, std::string& chosen) {
    COMDLG_FILTERSPEC filters[] = {{L"PNG image", L"*.png"}, {L"MP4 movie", L"*.mp4"}, {L"All files", L"*.*"}};
    return browse_file(!directory, directory, directory ? L"Export frames" : L"Export movie", directory ? nullptr : filters,
                       directory ? 0 : 3, suggested, chosen);
}

bool browse_open_scene(std::string& chosen) {
    COMDLG_FILTERSPEC filters[] = {{L"Scene", L"*.scene"}, {L"All files", L"*.*"}};
    return browse_file(false, false, L"Load simulation", filters, 2, {}, chosen);
}

bool browse_open_recording(std::string& chosen) {
    COMDLG_FILTERSPEC filters[] = {{L"Recording", L"*.traj"}, {L"All files", L"*.*"}};
    return browse_file(false, false, L"Load recording", filters, 2, {}, chosen);
}

bool browse_save_recording(std::string const& suggested, std::string& chosen) {
    COMDLG_FILTERSPEC filters[] = {{L"Recording", L"*.traj"}};
    return browse_file(true, false, L"Save recording", filters, 1, suggested, chosen);
}
bool browse_cosmic_cache(bool save,std::string& chosen) {
    COMDLG_FILTERSPEC filters[]={{L"Cosmic Flow cache",L"*.cflow"}};
    return browse_file(save,false,save?L"Bake simulation to cache":L"Open simulation cache",filters,1,save?"cosmic-flow.cflow":"",chosen);
}
bool browse_mp4(std::string& chosen) {
    COMDLG_FILTERSPEC filters[]={{L"MP4 movie",L"*.mp4"}};
    return browse_file(true,false,L"Export MP4 movie",filters,1,"cosmic-flow.mp4",chosen);
}

struct WinPipe {
    HANDLE process = nullptr;
    HANDLE thread = nullptr;
    HANDLE write = nullptr;
};

FfmpegPipe::~FfmpegPipe() { finish(); }

bool FfmpegPipe::start(int width, int height, int fps, std::string const& output_path, std::string& error) {
    finish();
    char found[MAX_PATH] = {};
    DWORD foundLength=0;
    if (!ffmpeg_executable_override.empty() && ffmpeg_executable_override.size() < MAX_PATH) {
        std::copy(ffmpeg_executable_override.begin(), ffmpeg_executable_override.end(), found);
        foundLength = static_cast<DWORD>(ffmpeg_executable_override.size());
    } else {
        foundLength=SearchPathA(nullptr, "ffmpeg.exe", nullptr, MAX_PATH, found, nullptr);
    }
    if (foundLength == 0 || foundLength >= MAX_PATH) {
        error = "ffmpeg was not found on PATH. A PNG sequence does not need it.";
        return false;
    }
    std::filesystem::path file(output_path);
    if (file.has_parent_path()) {
        std::error_code code;
        std::filesystem::create_directories(file.parent_path(), code);
    }

    SECURITY_ATTRIBUTES inherit{};
    inherit.nLength = sizeof(inherit);
    inherit.bInheritHandle = TRUE;
    HANDLE read_end = nullptr;
    HANDLE write_end = nullptr;
    if (CreatePipe(&read_end, &write_end, &inherit, 0) == 0) {
        error = "could not open a pipe to ffmpeg";
        return false;
    }
    SetHandleInformation(write_end, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileA("NUL", GENERIC_WRITE, FILE_SHARE_WRITE, &inherit, OPEN_EXISTING, 0, nullptr);

    std::string command = "ffmpeg -y -loglevel error -f rawvideo -pix_fmt rgb24 -s " + std::to_string(width) + "x" +
                          std::to_string(height) + " -r " + std::to_string(fps) +
                          " -i - -an -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart \"" + output_path + "\"";
    std::vector<char> mutable_command(command.begin(), command.end());
    mutable_command.push_back('\0');

    STARTUPINFOA startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = read_end;
    startup.hStdOutput = nul;
    startup.hStdError = nul;
    PROCESS_INFORMATION process{};
    const BOOL created = CreateProcessA(found, mutable_command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                                        nullptr, &startup, &process);
    CloseHandle(read_end);
    if (nul != INVALID_HANDLE_VALUE) {
        CloseHandle(nul);
    }
    if (created == 0) {
        CloseHandle(write_end);
        error = "could not start ffmpeg";
        return false;
    }
    process_ = process.hProcess;
    thread_ = process.hThread;
    write_handle_ = write_end;
    open_ = true;
    return true;
}

bool FfmpegPipe::write_frame(unsigned char const* rgb_top_down, int width, int height, std::string& error) {
    if (!open_ || write_handle_ == nullptr) {
        error = "ffmpeg is not running";
        return false;
    }
    const std::size_t bytes = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3;
    std::size_t written_total = 0;
    auto* handle = static_cast<HANDLE>(write_handle_);
    while (written_total < bytes) {
        DWORD chunk = 0;
        const DWORD ask = static_cast<DWORD>(std::min<std::size_t>(bytes - written_total, 1 << 20));
        if (WriteFile(handle, rgb_top_down + written_total, ask, &chunk, nullptr) == 0 || chunk == 0) {
            error = "ffmpeg stopped accepting frames";
            finish();
            return false;
        }
        written_total += chunk;
    }
    return true;
}

void FfmpegPipe::finish() {
    std::string error; finish_checked(error);
}
bool FfmpegPipe::finish_checked(std::string& error) {
    bool ok=true;
    if (write_handle_ != nullptr) {
        CloseHandle(static_cast<HANDLE>(write_handle_));
        write_handle_ = nullptr;
    }
    if (process_ != nullptr) {
        DWORD wait=WaitForSingleObject(static_cast<HANDLE>(process_),30000),code=1;
        if(wait!=WAIT_OBJECT_0 || !GetExitCodeProcess(static_cast<HANDLE>(process_),&code) || code!=0) { error="ffmpeg did not finalize the MP4 successfully."; ok=false; }
        CloseHandle(static_cast<HANDLE>(process_));
        process_ = nullptr;
    }
    if (thread_ != nullptr) {
        CloseHandle(static_cast<HANDLE>(thread_));
        thread_ = nullptr;
    }
    open_ = false;
    return ok;
}

#else

bool browse_export_path(bool, std::string const&, std::string&) { return false; }
bool browse_open_scene(std::string&) { return false; }
bool browse_open_recording(std::string&) { return false; }
bool browse_save_recording(std::string const&, std::string&) { return false; }
bool browse_cosmic_cache(bool,std::string&) { return false; }
bool browse_mp4(std::string&) { return false; }

FfmpegPipe::~FfmpegPipe() { finish(); }

bool FfmpegPipe::start(int width, int height, int fps, std::string const& output_path, std::string& error) {
    finish();
    std::filesystem::path file(output_path);
    if (file.has_parent_path()) {
        std::error_code code;
        std::filesystem::create_directories(file.parent_path(), code);
    }
    std::string command = "ffmpeg -y -loglevel error -f rawvideo -pix_fmt rgb24 -s " + std::to_string(width) + "x" +
                          std::to_string(height) + " -r " + std::to_string(fps) +
                          " -i - -an -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart \"" + output_path + "\"";
    file_ = popen(command.c_str(), "w");
    if (file_ == nullptr) {
        error = "could not start ffmpeg";
        return false;
    }
    open_ = true;
    return true;
}

bool FfmpegPipe::write_frame(unsigned char const* rgb_top_down, int width, int height, std::string& error) {
    if (!open_ || file_ == nullptr) {
        error = "ffmpeg is not running";
        return false;
    }
    const std::size_t bytes = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3;
    if (std::fwrite(rgb_top_down, 1, bytes, file_) != bytes) {
        error = "ffmpeg stopped accepting frames";
        finish();
        return false;
    }
    return true;
}

void FfmpegPipe::finish() {
    std::string error; finish_checked(error);
}
bool FfmpegPipe::finish_checked(std::string& error) {
    bool ok=true;
    if (file_ != nullptr) {
        if(pclose(file_)!=0) { error="ffmpeg did not finalize the MP4 successfully."; ok=false; }
        file_ = nullptr;
    }
    open_ = false;
    return ok;
}

#endif
