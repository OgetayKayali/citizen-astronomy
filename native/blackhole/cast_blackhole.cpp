// CAst host for the PhysicsEngine black hole lab (apps/blackhole/main.cpp).
// Settings, Renderer, metadata, movie export and the interactive frame step are
// the lab's own code; only the ImGui panel and GLFW window are replaced, and the
// embedded ESO panorama is replaced by CAst's Milky Way texture.
#include "blackhole/geodesic.hpp"
#include "blackhole/kerr.hpp"
#include "gl_loader.hpp"
#include "frame_export.hpp"
#include "shaders.hpp"
#include "flow_renderer.hpp"
#include "camera_path.hpp"
#include "movie_export.hpp"
#include <GLFW/glfw3.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#define CAST_API extern "C" __declspec(dllexport)

GLFWglproc glfwGetProcAddress(char const* name) {
    auto proc=reinterpret_cast<GLFWglproc>(wglGetProcAddress(name));
    auto value=reinterpret_cast<std::intptr_t>(proc);
    if(value==0 || value==1 || value==2 || value==3 || value==-1) {
        static HMODULE library=LoadLibraryA("opengl32.dll");
        proc=library?reinterpret_cast<GLFWglproc>(GetProcAddress(library,name)):nullptr;
    }
    return proc;
}
double glfwGetTime() {
    static auto const start=std::chrono::steady_clock::now();
    return std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
}

namespace {
constexpr float pi=static_cast<float>(blackhole::pi);
char const* const skyCredit="Milky Way: ESA Gaia Sky 2020";
std::string skyCreditDetail="CAst Milky Way texture";
std::string starCatalogNote;
struct Settings {
    float distance=60, inclination=80, yaw=0, fov=45, outer=20;
    float pan=0,tilt=0,roll=0,cameraSmoothing=.18f,moveSpeed=15;
    float mass=100, speed=1200, temperature=6500, exposure=1.0f, bloom=.18f;
    float scale=.75f, orbitSpeed=3;
    float uiScale=1;
    float spin=.6f;
    int metric=1;
    int quality=1, samples=1, background=0;
    bool fluid=true;
    bool volume=true;
    float thickness=.006f,opacity=8.f;
    int flowGrid=128,flowView=0;
    int flowBackend=0;
    float viscosity=.08f,stirring=.4f;
    float detailContrast=2.f;
    float outerFade=.35f;
    bool milkyWay=true,cinematicGrade=true,particles=true;
    float skyBrightness=.10f,skyRotation=-65.f,particleStrength=1.f;
    float particleBrightness=1.f,particleSize=1.f,particleSizeVariety=1.f,particleLifetime=1.f,particleWhiteness=0.f;
    float skyContrast=1.f,skySaturation=1.f,skyBlackPoint=0.f,skyWarmth=0.f;
    bool stars=false;
    float starBrightness=1.f,starDensity=1.f,starSize=1.f,starColor=.7f,starFollow=.5f;
    bool starCatalog=false;
    float starMagnitudeLimit=8.f;
    float spaceBlend=1.f;
    bool physical=false, disk=true, delays=true, play=true, orbit=false;
};
blackhole_view::CameraPose cameraPose(Settings const& s) {return {s.distance,s.inclination,s.yaw,s.fov,s.pan,s.tilt,s.roll};}
float cameraMinimum(Settings const& s) {return blackhole_view::cameraMinimum(s.metric==1?s.spin:0);}
float cameraHorizon(Settings const& s) {return blackhole_view::cameraHorizon(s.metric==1?s.spin:0);}
void applyCamera(Settings& s,blackhole_view::CameraPose p) {
    s.distance=p.distance;s.inclination=p.inclination;s.yaw=p.yaw;s.fov=p.fov;s.pan=p.pan;s.tilt=p.tilt;s.roll=p.roll;
}
void resetScene(Settings& settings,double& time) {
    float uiScale=settings.uiScale;
    settings=Settings{};settings.uiScale=uiScale;time=0;
}
GLuint shader(GLenum type,char const* text) {
    GLuint s=gl_create_shader(type); gl_shader_source(s,1,&text,nullptr); gl_compile_shader(s);
    GLint ok=0; gl_get_shader_iv(s,GL_COMPILE_STATUS,&ok);
    if(!ok) { char log[8192]{}; gl_get_shader_info_log(s,sizeof(log),nullptr,log); gl_delete_shader(s); throw std::runtime_error(log); }
    return s;
}
GLuint program(char const* fragment) {
    GLuint v=shader(GL_VERTEX_SHADER,blackhole_view::vertex),f=shader(GL_FRAGMENT_SHADER,fragment),p=gl_create_program();
    gl_attach_shader(p,v); gl_attach_shader(p,f); gl_link_program(p); gl_delete_shader(v); gl_delete_shader(f);
    GLint ok=0; gl_get_program_iv(p,GL_LINK_STATUS,&ok);
    if(!ok) { char log[8192]{}; gl_get_program_info_log(p,sizeof(log),nullptr,log); gl_delete_program(p); throw std::runtime_error(log); }
    return p;
}
void uniform(GLuint p,char const* name,float x) { gl_uniform_1f(gl_get_uniform_location(p,name),x); }
void uniform(GLuint p,char const* name,int x) { gl_uniform_1i(gl_get_uniform_location(p,name),x); }
void vector(GLuint p,char const* name,blackhole::Vec3 v) {
    gl_uniform_3f(gl_get_uniform_location(p,name),static_cast<float>(v.x),static_cast<float>(v.y),static_cast<float>(v.z));
}
struct Target {
    GLuint fbo=0,texture=0; int width=0,height=0;
    Target() { gl_gen_framebuffers(1,&fbo); glGenTextures(1,&texture); }
    Target(Target const&)=delete; Target& operator=(Target const&)=delete;
    ~Target() { gl_delete_framebuffers(1,&fbo); glDeleteTextures(1,&texture); }
    void resize(int w,int h) {
        if(w==width && h==height) return;
        width=w; height=h; glBindTexture(GL_TEXTURE_2D,texture);
        glTexImage2D(GL_TEXTURE_2D,0,0x881A,w,h,0,GL_RGBA,GL_FLOAT,nullptr); // RGBA16F
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR); glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,0x812F); glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,0x812F);
        gl_bind_framebuffer(GL_FRAMEBUFFER,fbo); gl_framebuffer_texture_2d(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,texture,0);
        if(gl_check_framebuffer_status(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) throw std::runtime_error("Floating-point framebuffer unavailable.");
    }
    void bind() const { gl_bind_framebuffer(GL_FRAMEBUFFER,fbo); glViewport(0,0,width,height); }
};
void texture(GLuint id,int slot) { gl_active_texture(0x84C0+slot); glBindTexture(GL_TEXTURE_2D,id); }
// Replaces the lab's embedded-JPEG SkyTexture with CAst's equatorial Milky Way,
// uploaded with the same sRGB decode, wrapping and mipmapped filtering.
class CastSky {
    GLuint texture_=0; int width_=0,height_=0;
public:
    CastSky()=default;
    CastSky(CastSky const&)=delete;
    CastSky& operator=(CastSky const&)=delete;
    ~CastSky() { if(texture_) glDeleteTextures(1,&texture_); }
    bool ready() const {return texture_!=0;}
    float width() const {return static_cast<float>(width_);}
    void set(unsigned char const* rgb,int width,int height) {
        GLint limit=0;glGetIntegerv(GL_MAX_TEXTURE_SIZE,&limit);
        if(width>limit || height>limit) throw std::runtime_error("The Milky Way texture exceeds this GPU's texture size limit.");
        using GenerateMipmap=void(APIENTRY*)(GLenum);
        auto generate=reinterpret_cast<GenerateMipmap>(glfwGetProcAddress("glGenerateMipmap"));
        if(!generate) throw std::runtime_error("Texture mipmaps unavailable.");
        if(!texture_) glGenTextures(1,&texture_);
        gl_active_texture(0x84C5);glBindTexture(GL_TEXTURE_2D,texture_);
        glPixelStorei(GL_UNPACK_ALIGNMENT,1);
        glTexImage2D(GL_TEXTURE_2D,0,0x8C41,width,height,0,GL_RGB,GL_UNSIGNED_BYTE,rgb);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,0x812F);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR_MIPMAP_LINEAR);
        generate(GL_TEXTURE_2D);
        width_=width;height_=height;
        skyCreditDetail="CAst Milky Way texture | "+std::to_string(width)+"x"+std::to_string(height);
    }
    GLuint get() const {return texture_;}
};
// Real catalog stars bucketed on an equal-angle cube map (built in Python).
// cells: RG32F (offset, count) per cell, 6*grid by grid texels. stars: RGBA32F
// (equatorial unit vector, V magnitude) and RGBA8 sRGB color, in rows of 4096.
class StarCatalog {
    GLuint textures_[3]={0,0,0}; int grid_=0,count_=0;
    static void nearest() {
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,0x812F);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,0x812F);
    }
public:
    static constexpr int rowWidth=4096;
    StarCatalog()=default;
    StarCatalog(StarCatalog const&)=delete;
    StarCatalog& operator=(StarCatalog const&)=delete;
    ~StarCatalog() { clear(); }
    void clear() { if(textures_[0]) glDeleteTextures(3,textures_); textures_[0]=textures_[1]=textures_[2]=0; grid_=count_=0; }
    bool ready() const {return textures_[0]!=0;}
    int grid() const {return grid_;}
    int count() const {return count_;}
    void set(float const* cells,int grid,float const* stars,unsigned char const* colors,int count) {
        if(grid<=0 || count<=0) { clear(); return; }
        int rows=(count+rowWidth-1)/rowWidth;
        GLint limit=0;glGetIntegerv(GL_MAX_TEXTURE_SIZE,&limit);
        if(6*grid>limit || rows>limit) throw std::runtime_error("The star catalog exceeds this GPU's texture size limit.");
        std::vector<float> paddedStars(static_cast<size_t>(rows)*rowWidth*4,0.f);
        std::vector<unsigned char> paddedColors(static_cast<size_t>(rows)*rowWidth*4,0);
        std::copy(stars,stars+static_cast<size_t>(count)*4,paddedStars.begin());
        std::copy(colors,colors+static_cast<size_t>(count)*4,paddedColors.begin());
        if(!textures_[0]) glGenTextures(3,textures_);
        gl_active_texture(0x84C6);glPixelStorei(GL_UNPACK_ALIGNMENT,1);
        glBindTexture(GL_TEXTURE_2D,textures_[0]);
        glTexImage2D(GL_TEXTURE_2D,0,0x8230,6*grid,grid,0,0x8227,GL_FLOAT,cells); // RG32F, RG
        nearest();
        glBindTexture(GL_TEXTURE_2D,textures_[1]);
        glTexImage2D(GL_TEXTURE_2D,0,0x8814,rowWidth,rows,0,GL_RGBA,GL_FLOAT,paddedStars.data()); // RGBA32F
        nearest();
        glBindTexture(GL_TEXTURE_2D,textures_[2]);
        glTexImage2D(GL_TEXTURE_2D,0,0x8C43,rowWidth,rows,0,GL_RGBA,GL_UNSIGNED_BYTE,paddedColors.data()); // SRGB8_ALPHA8
        nearest();
        gl_active_texture(0x84C0);
        grid_=grid;count_=count;
    }
    void bind() const { for(int i=0;i<3;++i) texture(textures_[i],6+i); gl_active_texture(0x84C0); }
};
struct Renderer {
    GLuint ray=program(blackhole_view::rayFragment.c_str()),blur=program(blackhole_view::blurFragment),post=program(blackhole_view::postFragment),vao=0;
    Target scene,glowA,glowB;
    CastSky sky;
    StarCatalog stars;
    std::unique_ptr<blackhole_view::FlowTexture> flow;
    double lastFlowTime=-1;
    bool flowReady(double time) const {return flow && flow->ready(time);}
    void prepareFlow(Settings const& s,double time,bool synchronous) {
        if(!s.fluid || !s.disk) return;
        blackhole::FlowConfig config;
        config.radial=s.flowGrid;config.azimuthal=s.flowGrid*2;config.spin=s.metric==1?s.spin:0;
        config.inner=blackhole::kerrIsco(config.spin);config.outer=s.outer;config.alpha=std::min<double>(s.viscosity,.3);config.stirring=s.stirring;
        if(!flow) flow=std::make_unique<blackhole_view::FlowTexture>();
        flow->update(config,time,synchronous,time<lastFlowTime,s.flowBackend);lastFlowTime=time;
    }
    Renderer() { gl_gen_vertex_arrays(1,&vao); }
    ~Renderer() { gl_delete_vertex_arrays(1,&vao); gl_delete_program(ray); gl_delete_program(blur); gl_delete_program(post); }
    void draw(Settings const& s,double time,int width,int height,int x=0,int diagnostic=0,GLuint output=0) {
        int w=std::max(1,static_cast<int>(width*s.scale)),h=std::max(1,static_cast<int>(height*s.scale));
        gl_active_texture(0x84C0); scene.resize(w,h); glowA.resize(std::max(1,w/4),std::max(1,h/4)); glowB.resize(std::max(1,w/4),std::max(1,h/4));
        glDisable(GL_DEPTH_TEST); glDisable(GL_BLEND); glDisable(GL_SCISSOR_TEST); gl_bind_vertex_array(vao);
        scene.bind(); gl_use_program(ray);
        float yaw=s.yaw*pi/180,inc=s.inclination*pi/180;
        vector(ray,"outward",blackhole::cameraOutward(yaw,inc));
        vector(ray,"rightAxis",{std::cos(yaw),0,-std::sin(yaw)});
        vector(ray,"upAxis",{-std::cos(inc)*std::sin(yaw),std::sin(inc),-std::cos(inc)*std::cos(yaw)});
        auto view=blackhole_view::cameraFrame(cameraPose(s));
        vector(ray,"viewForward",{view.forward.x,view.forward.y,view.forward.z});
        vector(ray,"viewRight",{view.right.x,view.right.y,view.right.z});
        vector(ray,"viewUp",{view.up.x,view.up.y,view.up.z});
        gl_uniform_2f(gl_get_uniform_location(ray,"resolution"),static_cast<float>(w),static_cast<float>(h));
        uniform(ray,"distanceRg",s.distance); uniform(ray,"fov",s.fov*pi/180); uniform(ray,"outerRadius",s.outer);
        double a=s.metric==1?static_cast<double>(s.spin):0;
        uniform(ray,"metric",s.metric); uniform(ray,"spin",static_cast<float>(a));
        uniform(ray,"innerRadius",static_cast<float>(blackhole::kerrIsco(a)));
        uniform(ray,"cameraLapse",static_cast<float>(std::sqrt(std::max(1e-8,1-2*s.distance/(s.distance*s.distance+a*a*std::cos(inc)*std::cos(inc))))));
        uniform(ray,"timeRg",static_cast<float>(time)); uniform(ray,"temperature",s.temperature);
        uniform(ray,"stepSize",s.quality==0?.04f:s.quality==1?.02f:.01f);
        uniform(ray,"physical",static_cast<int>(s.physical)); uniform(ray,"backgroundMode",s.background);
        uniform(ray,"diskEnabled",static_cast<int>(s.disk)); uniform(ray,"delays",static_cast<int>(s.delays));
        uniform(ray,"samples",s.samples); uniform(ray,"diagnostic",diagnostic);
        uniform(ray,"fluidModel",s.fluid?1:0);uniform(ray,"fluidEnabled",0);
        uniform(ray,"radialProfile",4);uniform(ray,"fluidHistory",2);uniform(ray,"tracerHistory",3);
        uniform(ray,"detailContrast",s.detailContrast);
        uniform(ray,"outerFade",s.outerFade);
        bool useSky=s.milkyWay && s.background==0 && diagnostic==0 && sky.ready();
        uniform(ray,"milkyWayTexture",5);uniform(ray,"milkyWayEnabled",useSky?1:0);
        if(useSky) { gl_active_texture(0x84C5); texture(sky.get(),5); }
        uniform(ray,"skyBrightness",s.skyBrightness);uniform(ray,"skyRotation",s.skyRotation*pi/180);
        uniform(ray,"skyWidth",sky.width());
        uniform(ray,"skyContrast",s.skyContrast);uniform(ray,"skySaturation",s.skySaturation);
        uniform(ray,"skyBlackPoint",s.skyBlackPoint);uniform(ray,"skyWarmth",s.skyWarmth);
        uniform(ray,"starsEnabled",s.stars?1:0);uniform(ray,"starBrightness",s.starBrightness);
        uniform(ray,"starDensity",s.starDensity);uniform(ray,"starSize",s.starSize);
        uniform(ray,"starColor",s.starColor);uniform(ray,"starFollow",s.starFollow);
        bool catalog=useSky && s.stars && s.starCatalog && stars.ready();
        uniform(ray,"starCatalog",catalog?1:0);uniform(ray,"starGrid",stars.grid());
        uniform(ray,"starMagnitudeLimit",s.starMagnitudeLimit);
        uniform(ray,"starCells",6);uniform(ray,"starData",7);uniform(ray,"starColors",8);
        if(catalog) stars.bind();
        uniform(ray,"cinematicGrade",s.cinematicGrade?1:0);
        uniform(ray,"spaceBlend",s.spaceBlend);
        uniform(ray,"particlesEnabled",s.particles?1:0);uniform(ray,"particleStrength",s.particleStrength);
        uniform(ray,"particleBrightness",s.particleBrightness);uniform(ray,"particleSize",s.particleSize);
        uniform(ray,"particleSizeVariety",s.particleSizeVariety);uniform(ray,"particleLifetime",s.particleLifetime);
        uniform(ray,"particleWhiteness",s.particleWhiteness);
        uniform(ray,"volumeModel",s.volume && (!s.fluid || flowReady(time))?1:0);uniform(ray,"diskThickness",s.thickness);
        uniform(ray,"diskOpacity",s.opacity);uniform(ray,"flowRadialCells",static_cast<float>(s.flowGrid));
        if(flow && s.fluid) flow->bind(ray,s.flowView,time);
        glDrawArrays(GL_TRIANGLES,0,3);
        gl_use_program(blur); uniform(blur,"source",0);
        for(int pass=0;pass<6;++pass) {
            bool horizontal=pass%2==0;
            auto& dst=horizontal?glowA:glowB; dst.bind();
            texture(pass==0?scene.texture:(horizontal?glowB.texture:glowA.texture),0);
            uniform(blur,"extractBright",pass==0?1:0);
            gl_uniform_2f(gl_get_uniform_location(blur,"direction"),horizontal?1.5f/glowA.width:0,horizontal?0:1.5f/glowA.height);
            glDrawArrays(GL_TRIANGLES,0,3);
        }
        gl_bind_framebuffer(GL_FRAMEBUFFER,output); glViewport(x,0,width,height); gl_use_program(post);
        texture(scene.texture,0); texture(glowB.texture,1);
        uniform(post,"scene",0); uniform(post,"bloomTexture",1); uniform(post,"exposure",s.exposure); uniform(post,"bloom",s.bloom);
        uniform(post,"diagnostic",diagnostic); glDrawArrays(GL_TRIANGLES,0,3);
    }
};
std::vector<unsigned char> readPixels(int x,int width,int height,bool offscreen=false) {
    std::vector<unsigned char> pixels(static_cast<size_t>(width)*height*3);
    glPixelStorei(GL_PACK_ALIGNMENT,1); glReadBuffer(offscreen?GL_COLOR_ATTACHMENT0:GL_BACK); glReadPixels(x,0,width,height,GL_RGB,GL_UNSIGNED_BYTE,pixels.data());
    size_t stride=static_cast<size_t>(width)*3;
    for(int y=0;y<height/2;++y) for(size_t i=0;i<stride;++i)
        std::swap(pixels[static_cast<size_t>(y)*stride+i],pixels[static_cast<size_t>(height-1-y)*stride+i]);
    return pixels;
}
void metadata(std::string const& path,Settings const& s,double time,std::string const& backend) {
    std::ofstream out(path+".txt");
    if(!out) throw std::runtime_error("Could not save render metadata.");
    double a=s.metric==1?static_cast<double>(s.spin):0;
    out<<(s.metric==1?"Kerr":"Schwarzschild")<<" black hole; units GM/c^2 and GM/c^3; spin="<<a<<"\n"
        <<(s.fluid && s.volume?"Reconstructed gas layer with grey absorption":"Thin opaque prograde disk")
        <<", inner="<<blackhole::kerrIsco(a)<<", outer="<<s.outer<<"\nObserver: static above 6 rg; smooth transition to ZAMO below 3 rg in Kerr. No travel-velocity aberration.\n"
        <<"Mode="<<(s.physical?"Physical shifts + illustrative emission":"Cinematic")<<"\n"
        <<"Mass_solar="<<s.mass*1e6<<" Time_rg="<<time<<" Physical_seconds_per_playback_second="<<s.speed<<"\n"
        <<"Distance_rg="<<s.distance<<" Inclination_deg="<<s.inclination<<" Yaw_deg="<<s.yaw<<" Vertical_FOV_deg="<<s.fov<<"\n"
        <<"Pan_deg="<<s.pan<<" Tilt_deg="<<s.tilt<<" Roll_deg="<<s.roll<<" CameraSmoothing_seconds="<<s.cameraSmoothing<<"\n"
        <<"Temperature_K="<<s.temperature<<" Exposure="<<s.exposure<<" Bloom="<<s.bloom<<"\n"
        <<"Quality="<<s.quality<<" Samples="<<s.samples<<" Scale="<<s.scale<<" Background="<<s.background<<" Disk="<<s.disk<<" Delays="<<s.delays<<"\n";
    out<<"DiskModel="<<(s.fluid?"effective-potential fluid":"procedural reference")<<" Grid="<<s.flowGrid<<"x"<<s.flowGrid*2
        <<" Backend="<<backend<<" BackendRequested="<<s.flowBackend<<" Alpha="<<s.viscosity<<" Stirring="<<s.stirring<<" FlowView="<<s.flowView<<" History=512tg at 4tg spacing; seed=7\n";
    out<<"MilkyWay="<<(s.milkyWay && s.background==0)<<" SkyBrightness="<<s.skyBrightness<<" SkyRotation_deg="<<s.skyRotation
        <<" SkyContrast="<<s.skyContrast<<" SkySaturation="<<s.skySaturation<<" SkyBlackLevel="<<s.skyBlackPoint<<" SkyWarmth="<<s.skyWarmth
        <<" StarOverlay="<<s.stars<<" StarBrightness="<<s.starBrightness<<" StarDensity="<<s.starDensity<<" StarSize="<<s.starSize
        <<" StarColor="<<s.starColor<<" StarFollowMilkyWay="<<s.starFollow
        <<" StarCatalog="<<(s.starCatalog && !starCatalogNote.empty())<<" StarMagnitudeLimit="<<s.starMagnitudeLimit<<"\n"
        <<" SpaceBlend="<<s.spaceBlend<<" CinematicGrade="<<s.cinematicGrade<<" FineParticles="<<s.particles<<" ParticleAmount="<<s.particleStrength
        <<" ParticleBrightness="<<s.particleBrightness<<" ParticleSize="<<s.particleSize<<" ParticleSizeVariety="<<s.particleSizeVariety
        <<" ParticleLifetime="<<s.particleLifetime<<" ParticleWhiteness="<<s.particleWhiteness<<"\n";
    out<<"Decorative disk effects apply only to cinematic emission. Particles are orbiting emissivity details, not simulated matter.\n";
    if(s.milkyWay && s.background==0 && s.stars) out<<(s.starCatalog && !starCatalogNote.empty()
        ?"Stars: "+starCatalogNote+"; drawn as lensed points with brightness compressed for display (x1.585 per magnitude instead of x2.512).\n"
        :std::string("Stars: decorative random overlay, not a catalog.\n"));
    if(s.milkyWay && s.background==0) out<<skyCredit<<" | "<<skyCreditDetail<<"\n"
        <<"Earth's sky in the galactic orientation of the lab's original panorama, not the sky around this black hole.\n"
        <<"Sky adapted by rotation, brightness, filtering, tone mapping and gravitational lensing.\n";
    out<<"Volume="<<(s.fluid && s.volume)<<" Thickness_H_over_r="<<s.thickness<<" Grey_opacity="<<s.opacity
        <<" DetailContrast="<<s.detailContrast<<" OuterFade="<<s.outerFade<<" HeightTaper=sqrt-envelope ParticleModel=flaring-glints HistoryInterpolation=velocity-reconstructed\nVertical structure is reconstructed from 2D gas; not evolved in 3D.\n";
}
// Sky credit badge, rendered by CAst's UI at the requested size and alpha
// blended into exported frames where the lab drew its ImGui credit.
struct Credit {
    std::vector<unsigned char> rgba; int width=0,height=0;
    void composite(std::vector<unsigned char>& rgb,int frameWidth,int frameHeight) const {
        if(rgba.empty()) return;
        int left=frameWidth-width-7,top=frameHeight-height-7;
        for(int y=0;y<height;++y) {
            int fy=top+y; if(fy<0 || fy>=frameHeight) continue;
            for(int x=0;x<width;++x) {
                int fx=left+x; if(fx<0 || fx>=frameWidth) continue;
                unsigned char const* src=&rgba[(static_cast<size_t>(y)*width+x)*4];
                unsigned char* dst=&rgb[(static_cast<size_t>(fy)*frameWidth+fx)*3];
                int alpha=src[3];
                for(int c=0;c<3;++c) dst[c]=static_cast<unsigned char>((src[c]*alpha+dst[c]*(255-alpha)+127)/255);
            }
        }
    }
};
// MovieControls from the lab without its ImGui panel.
struct MovieControls {
    blackhole_view::CameraPath path;
    blackhole_view::CameraPose customStart;
    blackhole_view::CameraPose captureCamera;
    int resolution=1,rate=1,format=0;
    float duration=10,previewPosition=0;
    bool freeze=false,preview=false,previewPlay=false,request=false;
    std::string destination;
    std::string status;
    std::filesystem::path defaultDirectory="exports";
    std::unique_ptr<blackhole_view::MovieWriter> writer;
    Target output;
    Settings scene;
    blackhole_view::CameraPath shot;
    double startTime=0,started=0;
    int width=1920,height=1080,fps=30,total=300,submitted=0;
    bool png=false,hold=false,hasFrame=false;
    std::string file,backend,renderError;
    Credit credit;
    bool active() const {return bool(writer);}
    void manifest(std::string const& state,int count) {
        auto name=png?(std::filesystem::path(file)/"movie").string():file;
        metadata(name,scene,startTime,backend);
        std::ofstream out(name+".txt",std::ios::app);
        out<<"ExportStatus="<<state<<" Frames="<<count<<" PlannedFrames="<<total<<" FPS="<<fps<<" Width="<<width<<" Height="<<height
           <<" Duration_seconds="<<double(count)/fps<<"\nCameraPath="<<blackhole_view::shotIds[static_cast<int>(shot.shot)]
           <<" Sweep="<<shot.sweep<<" Travel="<<shot.travel<<" Ease="<<shot.ease<<" FreezeDisk="<<hold
           <<" Altitude="<<shot.altitude<<" Impact="<<shot.impact<<" Bank="<<shot.bank<<" Framing="<<shot.framing<<" Arc="<<shot.arc<<" PassLeft="<<shot.left
           <<"\nEndDistance="<<shot.end.distance<<" EndInclination="<<shot.end.inclination<<" EndYaw="<<shot.end.yaw<<" EndFov="<<shot.end.fov
           <<" EndPan="<<shot.end.pan<<" EndTilt="<<shot.end.tilt<<" EndRoll="<<shot.end.roll
           <<"\nCamera progress = frame / (planned frames - 1); gas time = "<<(hold?"start (frozen).":"start + frame / fps * playback rate.")<<"\n";
        if(!out) throw std::runtime_error("Could not save movie metadata.");
    }
    void start(Settings const& current,double time,std::string const& name,bool sequence,int w,int h,int frameRate,int frames,
               blackhole_view::CameraPath const& pathValue,bool frozen,bool useCustomStart=true) {
        if(active()) return;
        scene=current;scene.scale=1;scene.orbit=false;
        shot=pathValue;if(useCustomStart && shot.shot==blackhole_view::Shot::custom)applyCamera(scene,customStart);
        startTime=time;file=name;png=sequence;width=w;height=h;fps=frameRate;total=frames;hold=frozen;
        submitted=0;hasFrame=false;started=glfwGetTime();backend="pending";renderError.clear();
        writer=std::make_unique<blackhole_view::MovieWriter>(file,png,width,height,fps);
        status="Preparing export...";preview=false;previewPlay=false;
    }
    void startFromUi(Settings const& s,double time) {
        static constexpr int widths[]={1280,1920,2560,3840},heights[]={720,1080,1440,2160},rates[]={24,30,60};
        std::string name=destination;
        if(destination.empty()) {
            auto stamp=std::chrono::system_clock::now().time_since_epoch().count();
            name=(defaultDirectory/("blackhole-"+std::to_string(stamp)+(format==0?".mp4":""))).string();
        }
        if(format==0 && std::filesystem::path(name).extension()!=".mp4")name+=".mp4";
        Settings startScene=s;
        if(path.shot==blackhole_view::Shot::still)applyCamera(startScene,captureCamera);
        try {start(startScene,time,name,format==1,widths[resolution],heights[resolution],rates[rate],int(std::lround(duration*rates[rate])),path,freeze);}
        catch(std::exception const& e) {status=e.what();}
    }
    void tick(Renderer& renderer) {
        try {
        if(!writer)return;
        if(writer->done) {
            std::string error=renderError.empty()?writer->error():renderError;int count=writer->written;
            bool cancel=writer->cancelled;
            backend=renderer.flow&&scene.fluid?renderer.flow->backendName():"procedural";
            try {manifest(error.empty()?(cancel?"cancelled":"complete"):"failed",count);}
            catch(std::exception const& e) {if(error.empty())error=e.what();}
            status=error.empty()?(cancel?"Stopped: ":"Saved: ")+file+" ("+std::to_string(count)+" frames)":"Export failed: "+error;
            writer.reset();return;
        }
        if(writer->cancelled || submitted>=total || !writer->available())return;
        double time=startTime+(hold?0.:double(submitted)/fps*scene.speed/blackhole::gravitationalSeconds(scene.mass*1e6));
        renderer.prepareFlow(scene,time,false);
        if(scene.fluid && scene.disk && !renderer.flowReady(time)) {status="Preparing gas for frame "+std::to_string(submitted+1);return;}
        Settings frame=scene;applyCamera(frame,blackhole_view::cameraAt(cameraPose(scene),shot,total>1?double(submitted)/(total-1):0,cameraMinimum(scene)));
        gl_active_texture(0x84C0);output.resize(width,height);
        renderer.draw(frame,time,width,height,0,0,output.fbo);
        auto pixels=readPixels(0,width,height,true);
        if(scene.milkyWay && scene.background==0) credit.composite(pixels,width,height);
        if(writer->submit(pixels)) {++submitted;hasFrame=true;}
        if(submitted==total)writer->finish();
        status="Rendering / encoding";
        } catch(std::exception const& e) {
            renderError=e.what();status="Export failed: "+renderError;
            if(writer)writer->cancel();
        }
    }
};
// Viewport overlays drawn where the lab drew ImGui windows: 0 = sky credit
// (bottom right), 1 = "Preparing disk" box (centered).
constexpr char overlayVertex[]=R"GLSL(#version 330 core
uniform vec4 rect;
out vec2 uv;
void main() {
    vec2 p=vec2(gl_VertexID&1,(gl_VertexID>>1)&1);
    uv=vec2(p.x,1.0-p.y); gl_Position=vec4(mix(rect.xy,rect.zw,p),0,1);
}
)GLSL";
constexpr char overlayFragment[]=R"GLSL(#version 330 core
in vec2 uv;
out vec4 color;
uniform sampler2D image;
void main() { color=texture(image,uv); }
)GLSL";
struct Overlay {
    GLuint texture=0; int width=0,height=0;
    ~Overlay() {if(texture) glDeleteTextures(1,&texture);}
};
struct Session {
    Renderer renderer;
    Settings s;
    MovieControls movie;
    blackhole_view::CameraPose smoothedCamera;
    Settings displayed;
    double time=0,statusUntil=0;
    std::string status,flowStatus;
    bool buffering=false,preparing=false,exporting=false;
    Credit viewCredit;
    Overlay overlays[2];
    GLuint overlayProgram=0;
    Session() {
        movie.customStart=cameraPose(s);
        smoothedCamera=cameraPose(s);movie.captureCamera=smoothedCamera;displayed=s;
    }
    ~Session() {if(overlayProgram) gl_delete_program(overlayProgram);}
    std::string backend() const {return renderer.flow&&s.fluid?renderer.flow->backendName():"procedural";}
    void drawOverlay(int slot,int w,int h) {
        Overlay const& overlay=overlays[slot];
        if(!overlay.texture || overlay.width<1 || overlay.height<1) return;
        if(!overlayProgram) {
            GLuint v=shader(GL_VERTEX_SHADER,overlayVertex),f=shader(GL_FRAGMENT_SHADER,overlayFragment),p=gl_create_program();
            gl_attach_shader(p,v);gl_attach_shader(p,f);gl_link_program(p);gl_delete_shader(v);gl_delete_shader(f);
            GLint ok=0;gl_get_program_iv(p,GL_LINK_STATUS,&ok);
            if(!ok) {gl_delete_program(p);throw std::runtime_error("Overlay shader failed to link.");}
            overlayProgram=p;
        }
        int left=slot==0?std::max(8,w-overlay.width-7):(w-overlay.width)/2;
        int bottom=slot==0?7:(h-overlay.height)/2;
        auto ndcX=[&](int x){return 2.f*x/w-1;};auto ndcY=[&](int y){return 2.f*y/h-1;};
        glViewport(0,0,w,h);gl_bind_vertex_array(renderer.vao);gl_use_program(overlayProgram);
        gl_uniform_1i(gl_get_uniform_location(overlayProgram,"image"),0);
        GLint rect=gl_get_uniform_location(overlayProgram,"rect");
        using Uniform4f=void(APIENTRY*)(GLint,GLfloat,GLfloat,GLfloat,GLfloat);
        static auto uniform4f=reinterpret_cast<Uniform4f>(glfwGetProcAddress("glUniform4f"));
        if(uniform4f) uniform4f(rect,ndcX(left),ndcY(bottom),ndcX(left+overlay.width),ndcY(bottom+overlay.height));
        texture(overlay.texture,0);
        glEnable(GL_BLEND);glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA);glColorMask(1,1,1,0);
        glDrawArrays(GL_TRIANGLE_STRIP,0,4);
        glColorMask(1,1,1,1);glDisable(GL_BLEND);
    }
};
thread_local std::string lastError;
int copyText(std::string const& text,char* buffer,int size) {
    if(buffer && size>0) {
        size_t count=std::min(text.size(),static_cast<size_t>(size-1));
        std::memcpy(buffer,text.data(),count);buffer[count]=0;
    }
    return static_cast<int>(text.size());
}
struct Value {
    void* pointer=nullptr; int type=0; // 0 float, 1 int, 2 bool, 3 Shot
};
Value cameraField(blackhole_view::CameraPose& p,std::string const& key) {
    if(key=="distance") return {&p.distance,0};
    if(key=="inclination") return {&p.inclination,0};
    if(key=="yaw") return {&p.yaw,0};
    if(key=="fov") return {&p.fov,0};
    if(key=="pan") return {&p.pan,0};
    if(key=="tilt") return {&p.tilt,0};
    if(key=="roll") return {&p.roll,0};
    return {};
}
Value field(Session& session,std::string const& name) {
    auto& s=session.s;
#define CAST_FLOAT(n) if(name==#n) return {&s.n,0};
#define CAST_INT(n) if(name==#n) return {&s.n,1};
#define CAST_BOOL(n) if(name==#n) return {&s.n,2};
    CAST_FLOAT(distance) CAST_FLOAT(inclination) CAST_FLOAT(yaw) CAST_FLOAT(fov) CAST_FLOAT(outer)
    CAST_FLOAT(pan) CAST_FLOAT(tilt) CAST_FLOAT(roll) CAST_FLOAT(cameraSmoothing) CAST_FLOAT(moveSpeed)
    CAST_FLOAT(mass) CAST_FLOAT(speed) CAST_FLOAT(temperature) CAST_FLOAT(exposure) CAST_FLOAT(bloom)
    CAST_FLOAT(scale) CAST_FLOAT(orbitSpeed) CAST_FLOAT(uiScale) CAST_FLOAT(spin) CAST_INT(metric)
    CAST_INT(quality) CAST_INT(samples) CAST_INT(background) CAST_BOOL(fluid) CAST_BOOL(volume)
    CAST_FLOAT(thickness) CAST_FLOAT(opacity) CAST_INT(flowGrid) CAST_INT(flowView) CAST_INT(flowBackend)
    CAST_FLOAT(viscosity) CAST_FLOAT(stirring) CAST_FLOAT(detailContrast) CAST_FLOAT(outerFade)
    CAST_BOOL(milkyWay) CAST_BOOL(cinematicGrade) CAST_BOOL(particles)
    CAST_FLOAT(skyBrightness) CAST_FLOAT(skyRotation) CAST_FLOAT(particleStrength) CAST_FLOAT(spaceBlend)
    CAST_FLOAT(particleBrightness) CAST_FLOAT(particleSize) CAST_FLOAT(particleSizeVariety)
    CAST_FLOAT(particleLifetime) CAST_FLOAT(particleWhiteness)
    CAST_FLOAT(skyContrast) CAST_FLOAT(skySaturation) CAST_FLOAT(skyBlackPoint) CAST_FLOAT(skyWarmth)
    CAST_BOOL(stars) CAST_FLOAT(starBrightness) CAST_FLOAT(starDensity) CAST_FLOAT(starSize)
    CAST_FLOAT(starColor) CAST_FLOAT(starFollow)
    CAST_BOOL(starCatalog) CAST_FLOAT(starMagnitudeLimit)
    CAST_BOOL(physical) CAST_BOOL(disk) CAST_BOOL(delays) CAST_BOOL(play) CAST_BOOL(orbit)
#undef CAST_FLOAT
#undef CAST_INT
#undef CAST_BOOL
    auto& m=session.movie;auto& p=m.path;
    if(name=="movie.shot") return {&p.shot,3};
    if(name=="movie.sweep") return {&p.sweep,0};
    if(name=="movie.travel") return {&p.travel,0};
    if(name=="movie.altitude") return {&p.altitude,0};
    if(name=="movie.impact") return {&p.impact,0};
    if(name=="movie.bank") return {&p.bank,0};
    if(name=="movie.framing") return {&p.framing,0};
    if(name=="movie.arc") return {&p.arc,0};
    if(name=="movie.left") return {&p.left,2};
    if(name=="movie.ease") return {&p.ease,2};
    if(name=="movie.resolution") return {&m.resolution,1};
    if(name=="movie.rate") return {&m.rate,1};
    if(name=="movie.format") return {&m.format,1};
    if(name=="movie.duration") return {&m.duration,0};
    if(name=="movie.previewPosition") return {&m.previewPosition,0};
    if(name=="movie.freeze") return {&m.freeze,2};
    if(name=="movie.preview") return {&m.preview,2};
    if(name=="movie.previewPlay") return {&m.previewPlay,2};
    if(name.rfind("movie.end.",0)==0) return cameraField(p.end,name.substr(10));
    if(name.rfind("movie.start.",0)==0) return cameraField(m.customStart,name.substr(12));
    if(name.rfind("movie.capture.",0)==0) return cameraField(m.captureCamera,name.substr(14));
    return {};
}
template<class Function> int guarded(Function&& function) {
    try {return function();}
    catch(std::exception const& e) {lastError=e.what();return 0;}
    catch(...) {lastError="Unknown black hole engine error.";return 0;}
}
}

struct BhInput {
    float mouseDx,mouseDy,wheel;
    int dragLeft,dragRight,dragMiddle;
    int keyW,keyA,keyS,keyD,keyQ,keyE,shift;
    int pressSpace,pressR;
    int mouse,keyboard;
};
struct BhState {
    int preparing,exporting,buffering,movieActive;
    int written,total,submitted,flowReady;
    double time,exportElapsed;
};

CAST_API int bh_last_error(char* buffer,int size) {return copyText(lastError,buffer,size);}

CAST_API void* bh_create() {
    try {
        load_gl([](char const* name)->void* {return reinterpret_cast<void*>(glfwGetProcAddress(name));});
        return new Session();
    } catch(std::exception const& e) {lastError=e.what();return nullptr;}
}

CAST_API void bh_destroy(void* handle) {
    auto* session=static_cast<Session*>(handle);
    if(!session) return;
    try {
        auto& movie=session->movie;
        if(movie.active()) {
            movie.writer->cancel();
            while(!movie.writer->done)std::this_thread::sleep_for(std::chrono::milliseconds(5));
            movie.tick(session->renderer);
        }
    } catch(...) {}
    delete session;
}

CAST_API int bh_set_sky(void* handle,unsigned char const* rgb,int width,int height) {
    return guarded([&] {static_cast<Session*>(handle)->renderer.sky.set(rgb,width,height);return 1;});
}

// Requires the GL context. count<=0 removes the catalog.
CAST_API int bh_set_star_catalog(void* handle,float const* cells,int grid,float const* stars,unsigned char const* colors,int count,char const* note) {
    return guarded([&] {
        static_cast<Session*>(handle)->renderer.stars.set(cells,grid,stars,colors,count);
        starCatalogNote=count>0 && note?note:"";
        return 1;
    });
}

CAST_API void bh_set_ffmpeg(char const* path) {ffmpeg_executable_override=path?path:"";}

CAST_API void bh_set_export_directory(void* handle,char const* path) {
    static_cast<Session*>(handle)->movie.defaultDirectory=path?std::filesystem::path(path):std::filesystem::path("exports");
}

// which: 0 = viewport PNG, 1 = movie frames.
CAST_API void bh_set_credit(void* handle,int which,unsigned char const* rgba,int width,int height) {
    auto* session=static_cast<Session*>(handle);
    Credit& credit=which==1?session->movie.credit:session->viewCredit;
    credit.width=width;credit.height=height;
    if(rgba && width>0 && height>0) credit.rgba.assign(rgba,rgba+static_cast<size_t>(width)*height*4);
    else credit.rgba.clear();
}

// Requires the session's GL context to be current. Null pixels hide the overlay.
CAST_API void bh_set_overlay(void* handle,int slot,unsigned char const* rgba,int width,int height) {
    auto* session=static_cast<Session*>(handle);
    if(slot<0 || slot>1) return;
    Overlay& overlay=session->overlays[slot];
    if(!rgba || width<1 || height<1) {overlay.width=overlay.height=0;return;}
    if(!overlay.texture) glGenTextures(1,&overlay.texture);
    gl_active_texture(0x84C0);glBindTexture(GL_TEXTURE_2D,overlay.texture);
    glPixelStorei(GL_UNPACK_ALIGNMENT,1);
    glTexImage2D(GL_TEXTURE_2D,0,0x8058,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,rgba); // RGBA8
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,0x812F);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,0x812F);
    overlay.width=width;overlay.height=height;
}

CAST_API int bh_get(void* handle,char const* name,double* out) {
    auto value=field(*static_cast<Session*>(handle),name);
    if(!value.pointer || !out) return 0;
    switch(value.type) {
    case 0: *out=*static_cast<float*>(value.pointer);break;
    case 1: *out=*static_cast<int*>(value.pointer);break;
    case 2: *out=*static_cast<bool*>(value.pointer)?1:0;break;
    default: *out=static_cast<int>(*static_cast<blackhole_view::Shot*>(value.pointer));break;
    }
    return 1;
}

CAST_API int bh_set(void* handle,char const* name,double input) {
    auto value=field(*static_cast<Session*>(handle),name);
    if(!value.pointer || !std::isfinite(input)) return 0;
    switch(value.type) {
    case 0: *static_cast<float*>(value.pointer)=static_cast<float>(input);break;
    case 1: *static_cast<int*>(value.pointer)=static_cast<int>(std::lround(input));break;
    case 2: *static_cast<bool*>(value.pointer)=input!=0;break;
    default: *static_cast<blackhole_view::Shot*>(value.pointer)=static_cast<blackhole_view::Shot>(std::clamp(static_cast<int>(std::lround(input)),0,7));break;
    }
    return 1;
}

CAST_API void bh_reset_scene(void* handle) {
    auto* session=static_cast<Session*>(handle);
    resetScene(session->s,session->time);
}

CAST_API void bh_movie_set_a(void* handle) {auto* s=static_cast<Session*>(handle);s->movie.customStart=s->movie.captureCamera;}
CAST_API void bh_movie_set_b(void* handle) {auto* s=static_cast<Session*>(handle);s->movie.path.end=s->movie.captureCamera;}
CAST_API void bh_movie_request(void* handle) {static_cast<Session*>(handle)->movie.request=true;}
CAST_API void bh_movie_stop(void* handle) {auto* s=static_cast<Session*>(handle);if(s->movie.writer)s->movie.writer->cancel();}
CAST_API void bh_movie_set_destination(void* handle,char const* path) {static_cast<Session*>(handle)->movie.destination=path?path:"";}
CAST_API int bh_movie_status(void* handle,char* buffer,int size) {return copyText(static_cast<Session*>(handle)->movie.status,buffer,size);}

CAST_API int bh_status(void* handle,char* buffer,int size) {
    auto* s=static_cast<Session*>(handle);
    return copyText(s->status.empty()?s->flowStatus:s->status,buffer,size);
}
CAST_API int bh_flow_status(void* handle,char* buffer,int size) {return copyText(static_cast<Session*>(handle)->flowStatus,buffer,size);}

CAST_API void bh_state(void* handle,BhState* out) {
    auto* s=static_cast<Session*>(handle);
    auto& movie=s->movie;
    out->preparing=s->preparing;out->exporting=s->exporting;out->buffering=s->buffering;out->movieActive=movie.active();
    out->written=movie.writer?int(movie.writer->written):0;out->total=movie.total;out->submitted=movie.submitted;
    out->flowReady=s->renderer.flowReady(s->time);
    out->time=s->time;out->exportElapsed=movie.active()?glfwGetTime()-movie.started:0;
}

CAST_API double bh_kerr_horizon(double spin) {return blackhole::kerrHorizon(spin);}
CAST_API double bh_kerr_isco(double spin) {return blackhole::kerrIsco(spin);}
CAST_API double bh_gravitational_seconds(double solarMasses) {return blackhole::gravitationalSeconds(solarMasses);}
CAST_API double bh_camera_minimum(void* handle) {return cameraMinimum(static_cast<Session*>(handle)->s);}
CAST_API double bh_camera_horizon(void* handle) {return cameraHorizon(static_cast<Session*>(handle)->s);}

// One iteration of the lab's interactive loop, rendering into framebuffer `fbo`.
CAST_API int bh_frame(void* handle,BhInput const* input,double elapsed,int w,int h,unsigned fbo) {
    auto* session=static_cast<Session*>(handle);
    return guarded([&] {
        auto& s=session->s;auto& movie=session->movie;auto& renderer=session->renderer;auto& time=session->time;
        BhInput const in=*input;
        double dt=std::clamp(elapsed,0.,.1);
        if(glfwGetTime()>session->statusUntil) session->status.clear();
        if(!movie.active()) {
            if(in.keyboard) {
                if(in.pressSpace) s.play=!s.play;
                if(in.pressR) resetScene(s,time);
                if(!movie.preview) {
                    double right=double(in.keyD!=0)-double(in.keyA!=0);
                    double forward=double(in.keyW!=0)-double(in.keyS!=0);
                    double up=double(in.keyE!=0)-double(in.keyQ!=0);
                    double length=std::sqrt(right*right+forward*forward+up*up);
                    if(length>0) {
                        double gap=std::max(.02f,s.distance-cameraHorizon(s));
                        double step=std::min(dt*s.moveSpeed*(in.shift?3:1)*std::min(1.,gap/5.),gap*.2)/length;
                        applyCamera(s,blackhole_view::moveCamera(cameraPose(s),right*step,up*step,forward*step,cameraMinimum(s)));s.orbit=false;
                    }
                }
            }
            if(in.mouse && !movie.preview) {
                if(in.dragLeft) {
                    s.orbit=false;
                    s.yaw+=in.mouseDx*.25f;
                    s.inclination=std::clamp(s.inclination+in.mouseDy*.25f,1.f,179.f);
                }
                if(in.dragRight) {
                    s.pan=std::remainder(s.pan+in.mouseDx*.18f,360.f);
                    s.tilt=std::clamp(s.tilt-in.mouseDy*.18f,-85.f,85.f);
                }
                if(in.dragMiddle) {
                    double step=std::min(s.distance,3*std::max(.02f,s.distance-cameraHorizon(s)))*.0015;
                    applyCamera(s,blackhole_view::moveCamera(cameraPose(s),-in.mouseDx*step,in.mouseDy*step,0,cameraMinimum(s)));s.orbit=false;
                }
                s.distance=std::clamp(cameraHorizon(s)+(s.distance-cameraHorizon(s))*std::exp(-in.wheel*.08f),cameraMinimum(s),200.f);
            }
        }
        s.yaw=std::remainder(s.yaw,360.f);
        s.distance=std::max(s.distance,cameraMinimum(s));
        if(movie.request) {movie.request=false;movie.startFromUi(s,time);}
        bool exporting=movie.active();
        if(exporting)movie.tick(renderer);
        else renderer.prepareFlow(s,time,false);
        bool buffering=false;
        if(!exporting && s.play && !(movie.preview && movie.freeze)) {
            double step=dt*s.speed/blackhole::gravitationalSeconds(s.mass*1e6);
            if(s.fluid && s.disk) {
                buffering=!renderer.flow->advance(time,step);
                buffering=buffering || step>blackhole_view::historySpacing;
            } else time+=step;
            if(s.orbit && !movie.preview) s.yaw=std::remainder(s.yaw+static_cast<float>(dt)*s.orbitSpeed,360.f);
        }
        auto& flowStatus=session->flowStatus;
        flowStatus.clear();
        if(s.fluid && s.disk && renderer.flow) {
            if(!renderer.flowReady(time)) flowStatus="Preparing fluid history... "+std::to_string(static_cast<int>(std::clamp((renderer.flow->newest()+512)/(time+516)*100,0.,100.)))+"%";
            else flowStatus=buffering?"Buffering fluid / playback limited":"Fluid ready";
            flowStatus+=" | "+renderer.flow->backendName();
        }
        session->buffering=buffering;session->exporting=exporting;
        session->preparing=!exporting && s.fluid && s.disk && !renderer.flowReady(time);
        glDisable(0x8DB9); // GL_FRAMEBUFFER_SRGB: the post pass writes display values.
        gl_bind_framebuffer(GL_FRAMEBUFFER,fbo); glViewport(0,0,w,h); glClearColor(.035f,.043f,.058f,1); glClear(GL_COLOR_BUFFER_BIT);
        Settings displayed=s;
        if(exporting) {
            if(movie.hasFrame) {
                using Blit=void(APIENTRY*)(GLint,GLint,GLint,GLint,GLint,GLint,GLint,GLint,GLbitfield,GLenum);
                static auto blit=reinterpret_cast<Blit>(glfwGetProcAddress("glBlitFramebuffer"));
                float fit=std::min(float(w)/movie.width,float(h)/movie.height);
                int fw=int(movie.width*fit),fh=int(movie.height*fit),x=(w-fw)/2,y=(h-fh)/2;
                if(blit) {
                    gl_bind_framebuffer(0x8CA8,movie.output.fbo);gl_bind_framebuffer(0x8CA9,fbo);
                    blit(0,0,movie.width,movie.height,x,y,x+fw,y+fh,GL_COLOR_BUFFER_BIT,GL_LINEAR);
                    gl_bind_framebuffer(GL_FRAMEBUFFER,fbo);
                }
            }
        } else {
            session->smoothedCamera=blackhole_view::smoothCamera(session->smoothedCamera,cameraPose(s),dt,s.cameraSmoothing);
            session->smoothedCamera.distance=std::max(session->smoothedCamera.distance,cameraMinimum(s));
            applyCamera(displayed,session->smoothedCamera);
            if(movie.preview) {
                if(movie.previewPlay) {
                    movie.previewPosition=std::min(1.f,movie.previewPosition+static_cast<float>(dt)/movie.duration);
                    if(movie.previewPosition>=1)movie.previewPlay=false;
                }
                auto base=movie.path.shot==blackhole_view::Shot::custom?movie.customStart:cameraPose(s);
                applyCamera(displayed,blackhole_view::cameraAt(base,movie.path,movie.previewPosition,cameraMinimum(s)));
            }
            movie.captureCamera=cameraPose(displayed);
            renderer.draw(displayed,time,w,h,0,0,fbo);
            session->displayed=displayed;
        }
        gl_bind_framebuffer(GL_FRAMEBUFFER,fbo);
        if(!exporting && s.milkyWay && s.background==0) session->drawOverlay(0,w,h);
        if(session->preparing) session->drawOverlay(1,w,h);
        return 1;
    });
}

// "Save PNG...": the current viewport scene at viewport size, plus its sidecar.
CAST_API int bh_save_png(void* handle,char const* path,int w,int h) {
    auto* session=static_cast<Session*>(handle);
    int ok=guarded([&] {
        auto& renderer=session->renderer;auto const& displayed=session->displayed;
        Target target;
        gl_active_texture(0x84C0);target.resize(w,h);
        renderer.draw(displayed,session->time,w,h,0,0,target.fbo);
        auto pixels=readPixels(0,w,h,true);
        if(displayed.milkyWay && displayed.background==0) session->viewCredit.composite(pixels,w,h);
        std::string error;
        if(!write_png_rgb(path,w,h,pixels.data(),error)) throw std::runtime_error(error);
        metadata(path,displayed,session->time,session->backend());
        session->status=std::string("Saved ")+path;
        return 1;
    });
    if(!ok) session->status=lastError;
    session->statusUntil=glfwGetTime()+5;
    return ok;
}
