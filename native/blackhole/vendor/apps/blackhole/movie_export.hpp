#pragma once
#include "frame_export.hpp"
#include <atomic>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <mutex>
#include <thread>
#include <vector>
#include <stdexcept>

namespace blackhole_view {
// Encoding and disk writes run off the GL/UI thread. The bounded queue prevents
// a slow encoder from consuming unbounded memory at 4K.
class MovieWriter {
    std::mutex mutex_;
    std::condition_variable wake_;
    std::deque<std::vector<unsigned char>> queue_;
    std::thread worker_;
    bool finish_=false;
    std::string error_;
public:
    std::atomic<bool> cancelled=false,done=false;
    std::atomic<int> written=0;
    MovieWriter(std::string output,bool png,int width,int height,int fps) {
        if(output.empty()) throw std::runtime_error("Choose an output path.");
        auto file=std::filesystem::path(output);
        if(std::filesystem::exists(file)) throw std::runtime_error("Output already exists. Choose a new file or folder.");
        if(!png && std::filesystem::exists(output+".txt")) throw std::runtime_error("Export metadata already exists. Choose a new filename.");
        std::string staging=png?output:output+".partial.mp4";
        if(std::filesystem::exists(staging)) throw std::runtime_error("A partial export already exists at this path.");
        if(!png && (width%2 || height%2)) throw std::runtime_error("MP4 dimensions must be even.");
        if(png) std::filesystem::create_directories(file);
        else if(file.has_parent_path()) std::filesystem::create_directories(file.parent_path());
        worker_=std::thread([this,output,staging,png,width,height,fps] {
            try {
                std::string error;FfmpegPipe encoder;
                if(!png && !encoder.start(width,height,fps,staging,error)) throw std::runtime_error(error);
                for(;;) {
                    std::vector<unsigned char> pixels;
                    {
                        std::unique_lock lock(mutex_);
                        wake_.wait(lock,[&]{return cancelled || finish_ || !queue_.empty();});
                        if(cancelled || (finish_ && queue_.empty())) break;
                        pixels=std::move(queue_.front());queue_.pop_front();
                    }
                    bool ok=png?write_png_rgb(frame_png_path(output,written),width,height,pixels.data(),error):encoder.write_frame(pixels.data(),width,height,error);
                    if(!ok) throw std::runtime_error(error);
                    ++written;
                }
                if(!png) {
                    bool finalized=encoder.finish_checked(error);
                    if(!finalized && !(cancelled && written==0)) throw std::runtime_error(error);
                    if(written>0) std::filesystem::rename(staging,output);
                    else {std::error_code ec;std::filesystem::remove(staging,ec);}
                }
            } catch(std::exception const& e) {std::lock_guard lock(mutex_);error_=e.what();}
            done=true;
        });
    }
    ~MovieWriter() {cancel();if(worker_.joinable())worker_.join();}
    bool available() {std::lock_guard lock(mutex_);return !done && !cancelled && !finish_ && queue_.size()<2;}
    bool submit(std::vector<unsigned char>& pixels) {
        std::lock_guard lock(mutex_);
        if(done || cancelled || finish_ || queue_.size()>=2) return false;
        queue_.push_back(std::move(pixels));wake_.notify_one();return true;
    }
    void finish() {std::lock_guard lock(mutex_);finish_=true;wake_.notify_one();}
    void cancel() {std::lock_guard lock(mutex_);cancelled=true;wake_.notify_one();}
    std::string error() {std::lock_guard lock(mutex_);return error_;}
};
}
