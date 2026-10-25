"""OpenGL 3.3 magnetosphere: static VBOs, analytic deformation in vertex shaders."""
from __future__ import annotations

import ctypes
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QMatrix4x4, QSurfaceFormat, QVector3D
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from photometry_app.core.magnetosphere import dipole_lines, tail_angle, topology_seeds

VERTEX = """#version 330 core
layout(location=0) in vec3 point;
uniform mat4 mvp;
uniform int mode;
uniform float r0, alpha, coupling, tilt, time, travel, bow, thetaMax, bz, pointSize;
uniform float arrowSign, bowNose, frontX, frontWidth, frontOpacity, daysideStrength, obstacleNose;
uniform sampler1D windProfile;
out vec3 world;
out float fade;
vec3 field(vec3 p){
    float c=cos(tilt), s=sin(tilt);
    p=vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);
    float r=length(p);
    float night=smoothstep(0.,1.,-p.x/max(r,1.));
    p.x-=night*(r-1.)*(2.4+3.*coupling);
    if(p.x < -1.) p.x=-1.-54.*tanh((-p.x-1.)/54.);
    r=length(p);
    float boundary=r0*pow(2./(1.+clamp(p.x/r,-.9999,1.)),alpha)*.93;
    float mapped=1.+(r-1.)/pow(1.+pow((r-1.)/(boundary-1.),4.),.25);
    return p*mapped/r;
}
void main(){
    vec3 p=point; fade=1.;
    if(mode==1) p=field(p);
    if(mode==2){
        float theta=p.x*thetaMax, phi=p.y;
        float r=r0*pow(2./(1.+cos(theta)),alpha);
        p=r*vec3(cos(theta),sin(theta)*cos(phi),sin(theta)*sin(phi));
        if(bow>1.){
            // Jelínek generalized parabola, limited to x >= 0 (dayside/flanks).
            float x=bowNose*(1.-point.x);
            float rho=sqrt(max(0.,4.*bowNose*(bowNose-x)))/1.17;
            p=vec3(x,rho*cos(phi),rho*sin(phi));
        }
        fade=1.-smoothstep(48.,65.,-p.x);
    }
    if(mode==4) p*=1.035;
    if(mode==5 || mode==6){
        p.x=40.-mod(point.x+travel,124.);
        if(mode==6) p.x+=.45*sin(point.z*.45);
        float rho=length(p.yz);
        float bs=obstacleNose;
        float avoid=sqrt(max(0.,4.*bs*(bs-p.x)))/1.17;
        float radius=pow(pow(max(rho,.1),6.)+pow(avoid,6.),1./6.);
        p.yz*=radius/max(rho,.1);
        fade=smoothstep(-84.,-74.,p.x)*(1.-smoothstep(37.,40.,p.x));
        vec4 wind=texture(windProfile,((p.x+84.)/124.*255.+.5)/256.);
        if(mode==6 && arrowSign!=0.) fade*=clamp(arrowSign*wind.g/5.,0.,1.);
        if(mode==5) fade*=clamp(sqrt(wind.r)*.5,.15,1.);
    }
    if(mode==7){
        p.x=frontX+point.x*frontWidth/3.;
        fade=frontOpacity;
    }
    if(mode==8){p=vec3(r0*.94+point.x,point.y,point.z); fade=daysideStrength;}
    if(mode==9){p=vec3(-22.+point.x*(1.+coupling),point.y,point.z); fade=coupling;}
    if(mode==11){
        float u=point.x, phi=point.y, h=point.z;
        vec3 a=vec3(.28*cos(phi),.28*sin(phi),h*sqrt(1.-.28*.28));
        a=vec3(cos(tilt)*a.x+sin(tilt)*a.z,a.y,-sin(tilt)*a.x+cos(tilt)*a.z);
        vec3 b=vec3(r0*.3,2.*sin(phi),h*(5.+r0*.25));
        vec3 c=vec3(-22.,6.*sin(phi),h*(9.+3.*coupling));
        vec3 d=vec3(-60.,8.*sin(phi),h*(10.+4.*coupling));
        p=pow(1.-u,3.)*a+3.*pow(1.-u,2.)*u*b+3.*(1.-u)*u*u*c+u*u*u*d;
    }
    world=p;
    gl_Position=mvp*vec4(p,1.);
    gl_PointSize=pointSize;
}
"""

FRAGMENT = """#version 330 core
in vec3 world;
in float fade;
out vec4 outputColor;
uniform vec4 color;
uniform int mode;
uniform float time, bz;
uniform vec3 eye;
uniform sampler2D earth;
void main(){
    vec4 c=color;
    if(mode==3){
        vec3 n=normalize(world);
        vec2 uv=vec2(atan(n.y,n.x)/6.2831853+.5-time/1440.,acos(clamp(n.z,-1.,1.))/3.14159265);
        vec3 surface=texture(earth,uv).rgb;
        float day=smoothstep(-.08,.28,n.x);
        float light=.09+1.15*max(0.,n.x); // modest display fill, not night-side emission
        vec3 v=normalize(eye-world);
        float rim=pow(1.-max(0.,dot(n,v)),3.);
        c=vec4(surface*light+vec3(.08,.3,.65)*rim*(.15+.65*day),1.);
    }
    if(mode==4){
        vec3 n=normalize(world), v=normalize(eye-world);
        float rim=pow(1.-abs(dot(n,v)),3.);
        c=vec4(.12,.45,.95,rim*.28*(.3+.7*max(0.,n.x)));
    }
    if(mode==5 || mode==10){
        float d=length(gl_PointCoord-.5)*2.;
        if(d>1.) discard;
        c.a*=exp(-3.*d*d);
    }
    c.a*=fade;
    outputColor=c;
}
"""

FIELD_GEOMETRY = """#version 330 core
layout(lines) in;
layout(triangle_strip, max_vertices=4) out;
uniform vec2 viewport;
out float across;
void main(){
    vec4 a=gl_in[0].gl_Position, b=gl_in[1].gl_Position;
    if(a.w<=0. || b.w<=0.) return;
    vec2 delta=(b.xy/b.w-a.xy/a.w)*viewport;
    if(length(delta)<.00001) return;
    vec2 side=normalize(vec2(-delta.y,delta.x))*3.2/viewport;
    across=-1.; gl_Position=a+vec4(-side*a.w,0.,0.); EmitVertex();
    across=1.; gl_Position=a+vec4(side*a.w,0.,0.); EmitVertex();
    across=-1.; gl_Position=b+vec4(-side*b.w,0.,0.); EmitVertex();
    across=1.; gl_Position=b+vec4(side*b.w,0.,0.); EmitVertex();
    EndPrimitive();
}
"""

FIELD_FRAGMENT = """#version 330 core
in float across;
uniform vec4 color;
out vec4 outputColor;
void main(){
    float core=exp(-18.*across*across), glow=.24*exp(-3.*across*across);
    outputColor=vec4(color.rgb*(1.+.18*core),color.a*(core+glow));
}
"""


def sphere(rows=48, cols=96):
    theta = np.linspace(0, np.pi, rows+1)
    phi = np.linspace(0, 2*np.pi, cols+1)
    t, p = np.meshgrid(theta, phi, indexing="ij")
    vertices = np.stack((np.sin(t)*np.cos(p), np.sin(t)*np.sin(p), np.cos(t)), -1)
    return quads(vertices)


def quads(v):
    return np.stack((v[:-1, :-1], v[1:, :-1], v[1:, 1:],
                     v[:-1, :-1], v[1:, 1:], v[:-1, 1:]), axis=-2).reshape(-1, 3)


def segments(curves):
    return np.stack((curves[:, :-1], curves[:, 1:]), axis=-2).reshape(-1, 3)


class MagnetosphereScene(QOpenGLWidget):
    failed = Signal(str)
    frame_rendered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMinimumSize(360, 300)
        self.state = None
        self.tilt = 11
        self.quiet = False
        self.line_count = 48
        self.particle_count = 1600
        self.visible_layers = dict(field=True, magnetopause=True, bow=True, particles=True, imf=True, regions=True, topology=False)
        self.program = self.field_program = self.texture = None
        self.profile_texture = None
        self.wind_profile = np.tile(np.array([1., 3., 400., 0.], dtype=np.float32), (256, 1))
        self.profile_dirty = True
        self.buffers = {}
        self.uniforms = {}
        self.error = ""
        self._last = None
        self.camera_preset("Overview")
        self.setToolTip("Drag to orbit · Right-drag or Shift-drag to pan · Wheel to zoom")

    def camera_preset(self, name):
        self.yaw, self.pitch = -75., 19.
        self.target = np.array([-14., 0., 0.])
        self.distance = 84.
        if name == "Dayside":
            self.target, self.distance, self.pitch = np.array([4., 0., 0.]), 29., 12.
        elif name == "Magnetotail":
            self.target, self.distance = np.array([-26., 0., 0.]), 62.
        elif name == "Earth":
            self.target, self.distance, self.yaw = np.zeros(3), 6., -38.
        elif name == "Pressure comparison":
            self.target, self.distance, self.yaw, self.pitch = np.array([5., 0., 0.]), 38., -90., 0.
        elif name == "Topology · schematic":
            self.target, self.distance, self.yaw, self.pitch = np.array([-22., 0., 0.]), 82., -90., 8.
        elif name == "Polar view":
            self.target, self.distance, self.pitch = np.array([-5., 0., 0.]), 46., 80.
        self.update()

    def _buffer(self, name, data):
        from OpenGL import GL
        data = np.ascontiguousarray(data, dtype=np.float32).reshape(-1, 3)
        vao, vbo = GL.glGenVertexArrays(1), GL.glGenBuffers(1)
        self.buffers[name] = (vao, vbo, len(data))
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 12, ctypes.c_void_p(0))

    def initializeGL(self):
        from OpenGL import GL
        from OpenGL.GL.shaders import compileProgram, compileShader
        self.context().aboutToBeDestroyed.connect(self.cleanup)
        self.error = ""
        try:
            # Different sampler types must be assigned distinct units before validation/draw.
            self.program = compileProgram(compileShader(VERTEX, GL.GL_VERTEX_SHADER), compileShader(FRAGMENT, GL.GL_FRAGMENT_SHADER), validate=False)
            GL.glUseProgram(self.program)
            GL.glUniform1i(GL.glGetUniformLocation(self.program, "windProfile"), 1)
            GL.glUniform1i(GL.glGetUniformLocation(self.program, "earth"), 0)
            self.field_program = compileProgram(compileShader(VERTEX, GL.GL_VERTEX_SHADER),
                compileShader(FIELD_GEOMETRY, GL.GL_GEOMETRY_SHADER), compileShader(FIELD_FRAGMENT, GL.GL_FRAGMENT_SHADER))
            for name in ("mvp", "mode", "r0", "alpha", "coupling", "tilt", "time", "travel", "bow", "thetaMax", "bz", "color", "eye", "earth", "pointSize", "arrowSign", "bowNose", "frontX", "frontWidth", "frontOpacity", "daysideStrength", "obstacleNose"):
                self.uniforms[name] = GL.glGetUniformLocation(self.program, name)
            self.field_uniforms = {name: GL.glGetUniformLocation(self.field_program, name) for name in self.uniforms}
            self.field_uniforms["viewport"] = GL.glGetUniformLocation(self.field_program, "viewport")
            self._buffer("earth", sphere())
            self._buffer("field", segments(dipole_lines(144)))
            self._buffer("open", segments(topology_seeds()))
            a, b = np.meshgrid(np.linspace(0, 1, 90), np.linspace(0, 2*np.pi, 73), indexing="ij")
            grid = np.stack((a, b, np.zeros_like(a)), -1)
            self._buffer("surface", quads(grid))
            self._buffer("wire", segments(grid[:, ::9].transpose(1, 0, 2)))
            rng = np.random.default_rng(4188)
            self._buffer("particles", np.column_stack((rng.uniform(0, 124, 5000), rng.uniform(-24, 24, (5000, 2)))))
            stars = rng.normal(size=(700, 3))
            stars = stars/np.linalg.norm(stars, axis=1)[:, None]*240
            self._buffer("stars", stars)
            imf = []
            arrows = []
            for phase in np.linspace(0, 124, 10, endpoint=False):
                for y in (-11, 11):
                    z = np.linspace(-18, 18, 80)
                    imf.append(np.column_stack((np.full_like(z, phase), np.full_like(z, y), z)))
                    # Paired arrowheads are drawn separately according to sign of Bz.
                    arrows.extend(((phase, y-.65, 2), (phase, y, 4), (phase, y+.65, 2), (phase, y, 4)))
            self._buffer("imf", segments(np.array(imf)))
            self._buffer("arrows_n", arrows)
            south = np.array(arrows); south[:, 2] *= -1
            self._buffer("arrows_s", south)
            front = []
            for x in (0., 1., 2., 3.):
                for y in np.linspace(-25, 25, 18):
                    front.extend(((x, y, -25), (x, y, 25)))
            self._buffer("front", front)
            phi = np.linspace(0, 2*np.pi, 120)
            ring = np.column_stack((np.zeros_like(phi), 2*np.cos(phi), 2*np.sin(phi)))
            self._buffer("region", segments(ring[None, ...]))
            tail = np.column_stack((10*np.cos(phi), np.zeros_like(phi), 2*np.sin(phi)))
            self._buffer("tail_region", segments(tail[None, ...]))
            self._buffer("axis", [[0, 0, 0], [18, 0, 0], [18, 0, 0], [16.5, .6, 0], [18, 0, 0], [16.5, -.6, 0]])
            self._buffer("dipole", [[0, 0, -1.5], [0, 0, 1.5]])
            image = QImage(str(Path(__file__).resolve().parents[1]/"data"/"earth_blue_marble.jpg"))
            if image.isNull():
                raise RuntimeError("Bundled NASA Earth texture is missing")
            image = image.scaledToWidth(2048).convertToFormat(QImage.Format.Format_RGBA8888)
            self.texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, image.width(), image.height(), 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, bytes(image.constBits()))
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            self.profile_texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_1D, self.profile_texture)
            GL.glTexImage1D(GL.GL_TEXTURE_1D, 0, GL.GL_RGBA32F, 256, 0, GL.GL_RGBA, GL.GL_FLOAT, self.wind_profile)
            GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_1D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        except Exception as exc:
            self.error = f"OpenGL 3.3 renderer unavailable: {exc}"
            self.failed.emit(self.error)

    def cleanup(self):
        from OpenGL import GL
        self.makeCurrent()
        for vao, vbo, _ in self.buffers.values():
            GL.glDeleteBuffers(1, [vbo]); GL.glDeleteVertexArrays(1, [vao])
        self.buffers.clear()
        if self.texture:
            GL.glDeleteTextures(1, [self.texture]); self.texture = None
        if self.profile_texture:
            GL.glDeleteTextures(1, [self.profile_texture]); self.profile_texture = None
        if self.program:
            GL.glDeleteProgram(self.program); self.program = None
        if self.field_program:
            GL.glDeleteProgram(self.field_program); self.field_program = None
        self.doneCurrent()

    def matrices(self):
        yaw, pitch = np.deg2rad([self.yaw, self.pitch])
        self.eye = self.target+self.distance*np.array([np.cos(pitch)*np.cos(yaw), np.cos(pitch)*np.sin(yaw), np.sin(pitch)])
        view = QMatrix4x4()
        view.lookAt(QVector3D(*self.eye), QVector3D(*self.target), QVector3D(0, 0, 1))
        projection = QMatrix4x4()
        projection.perspective(42., self.width()/max(1, self.height()), .05, 600.)
        return projection*view

    def paintGL(self):
        from OpenGL import GL
        GL.glClearColor(.008, .014, .027, 1)
        GL.glDepthMask(True)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if not self.program or self.error or self.state is None:
            return
        s = self.state
        GL.glUseProgram(self.program)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        mvp = self.matrices()
        values = dict(r0=s.r0, alpha=s.alpha, coupling=s.coupling, tilt=math.radians(self.tilt), time=s.time,
                      travel=s.travel, bow=1., thetaMax=tail_angle(s), bz=s.bz, pointSize=2*self.devicePixelRatioF(),
                      arrowSign=0., bowNose=s.bow_r0, frontX=s.front_x, frontWidth=s.front_width, frontOpacity=s.front_opacity,
                      daysideStrength=s.newell/(1+s.newell), obstacleNose=s.bow_r0 if s.bow_applicable else s.r0)
        def configure(program, uniforms):
            GL.glUseProgram(program)
            GL.glUniformMatrix4fv(uniforms["mvp"], 1, False, np.array(mvp.data(), np.float32))
            GL.glUniform3f(uniforms["eye"], *self.eye)
            for name, value in values.items():
                GL.glUniform1f(uniforms[name], value)
        configure(self.program, self.uniforms)
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_1D, self.profile_texture)
        if self.profile_dirty:
            GL.glTexSubImage1D(GL.GL_TEXTURE_1D, 0, 0, 256, GL.GL_RGBA, GL.GL_FLOAT, self.wind_profile)
            self.profile_dirty = False
        GL.glUniform1i(GL.glGetUniformLocation(self.program, "windProfile"), 1)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
        GL.glUniform1i(self.uniforms["earth"], 0)

        def draw(name, mode, color, primitive=GL.GL_LINES, count=None):
            vao, _, size = self.buffers[name]
            GL.glBindVertexArray(vao)
            GL.glUniform1i(self.uniforms["mode"], mode)
            GL.glUniform4f(self.uniforms["color"], *color)
            GL.glDrawArrays(primitive, 0, size if count is None else min(count, size))

        draw("earth", 3, (1, 1, 1, 1), GL.GL_TRIANGLES)
        # Transparent layers read opaque Earth depth but never occlude each other.
        GL.glDepthMask(False)
        draw("stars", 10, (.6, .7, .85, .4), GL.GL_POINTS)
        draw("earth", 4, (1, 1, 1, 1), GL.GL_TRIANGLES)
        draw("axis", 0, (.8, .76, .46, .35))
        draw("dipole", 1, (.9, .95, 1., .9))
        if self.visible_layers["magnetopause"]:
            draw("surface", 2, (.15, .7, .75, .035), GL.GL_TRIANGLES)
            draw("wire", 2, (.24, .8, .84, .3))
        if self.visible_layers["bow"] and s.bow_applicable:
            GL.glUniform1f(self.uniforms["bow"], 2.)
            draw("surface", 2, (.64, .56, .95, .018), GL.GL_TRIANGLES)
            draw("wire", 2, (.68, .58, .96, .22))
            GL.glUniform1f(self.uniforms["bow"], 1.)
        if self.visible_layers["field"] or self.visible_layers["topology"]:
            # Stable selection of complete meridional families preserves spatial coverage.
            vao, _, _ = self.buffers["field"]
            configure(self.field_program, self.field_uniforms)
            ratio = self.devicePixelRatioF()
            GL.glUniform2f(self.field_uniforms["viewport"], self.width()*ratio, self.height()*ratio)
            GL.glBindVertexArray(vao)
            GL.glUniform1i(self.field_uniforms["mode"], 1)
            for j in range(self.line_count if self.visible_layers["field"] else 0):
                i = (j//6)*(144//self.line_count)*6 + j % 6
                mix = (i % 6)/5
                GL.glUniform4f(self.field_uniforms["color"], .2+.4*mix, .8, 1., .7 if self.visible_layers["topology"] else .9)
                GL.glDrawArrays(GL.GL_LINES, i*318, 318)
            if self.visible_layers["topology"]:
                GL.glUniform1i(self.field_uniforms["mode"], 11)
                GL.glUniform4f(self.field_uniforms["color"], .95, .4, .72, .8)
                GL.glBindVertexArray(self.buffers["open"][0])
                GL.glDrawArrays(GL.GL_LINES, 0, self.buffers["open"][2])
            configure(self.program, self.uniforms)
        if self.visible_layers["particles"]:
            GL.glUniform1f(self.uniforms["pointSize"], 2.6*self.devicePixelRatioF())
            draw("particles", 5, (1., .72, .3, .24), GL.GL_POINTS, self.particle_count)
        if self.visible_layers["imf"]:
            draw("imf", 6, (.98, .66, .24, .16))
            GL.glUniform1f(self.uniforms["arrowSign"], 1.)
            draw("arrows_n", 6, (1., .72, .3, .8))
            GL.glUniform1f(self.uniforms["arrowSign"], -1.)
            draw("arrows_s", 6, (1., .72, .3, .8))
            GL.glUniform1f(self.uniforms["arrowSign"], 0.)
        if not self.quiet:
            draw("front", 7, (1., .62, .27, .12))
        if self.visible_layers["regions"]:
            draw("region", 8, (1., .45, .64, .9))
            draw("tail_region", 9, (1., .45, .64, .7))
        GL.glDepthMask(True)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        self.frame_rendered.emit()

    def mousePressEvent(self, event):
        self._last = event.position()

    def mouseMoveEvent(self, event):
        if self._last is None:
            return
        delta = event.position()-self._last
        self._last = event.position()
        if event.buttons() & Qt.MouseButton.RightButton or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            yaw, pitch = np.deg2rad([self.yaw, self.pitch])
            right = np.array([-np.sin(yaw), np.cos(yaw), 0.])
            up = np.array([-np.sin(pitch)*np.cos(yaw), -np.sin(pitch)*np.sin(yaw), np.cos(pitch)])
            self.target += (-right*delta.x()+up*delta.y())*self.distance*.0015
        elif event.buttons() & Qt.MouseButton.LeftButton:
            self.yaw -= delta.x()*.35
            self.pitch = float(np.clip(self.pitch+delta.y()*.3, -89, 89))
        self.update()

    def wheelEvent(self, event):
        self.distance = float(np.clip(self.distance*math.exp(-event.angleDelta().y()*.001), 2.5, 220))
        self.update()
