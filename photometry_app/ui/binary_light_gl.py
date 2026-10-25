"""Linear-light stellar bloom for Binary Light Lab's OpenGL 3.3 scene.

SpaceEngine's documented separation of HDR bloom and procedural corona inspired
this treatment (https://spaceengine.org/news/blog190611/). This is CAst's own
implementation, not SpaceEngine code. Vendored surface shading is retained;
the radial wave corona is removed. Only visible stellar pixels seed bloom, so
an eclipsed star cannot leave an independent, full-brightness halo behind.
"""
from __future__ import annotations


def hdr_scene_shader(original: str) -> str:
    """Adapt the original shader without modifying the provenance-checked source."""
    source = original.replace("\r\n", "\n")
    source = source.replace("out vec4 outputColor;", "layout(location=0) out vec4 outputColor;\nlayout(location=1) out vec4 stellarLight;")
    start = source.index("vec3 corona(")
    end = source.index("void main(){", start)
    source = source[:start] + source[end:]
    old = """    }else{
        col+=corona(p,center0,radius0,ca)+corona(p,center1,radius1,cb);
    }"""
    if source.count(old) != 1:
        raise RuntimeError("Binary Light Lab corona shader changed; review the HDR adapter.")
    source = source.replace(old, "    }")
    start = source.index("    // Mild filmic rolloff")
    # Separate emission before texture filtering, preventing sky-color leakage at
    # an occultor's limb. Sky pixels remain sharp and do not seed bloom.
    return source[:start] + """    float stellarMask=(za>-1e19||zb>-1e19)?1.:0.;
    outputColor=vec4(col,1.);
    stellarLight=vec4(col*stellarMask,1.);
}
"""


BLUR_FRAGMENT = """#version 330 core
in vec2 uv;
out vec4 outputColor;
uniform sampler2D source;
uniform vec2 direction;
vec3 sampleLight(vec2 at) {
    return texture(source,at).rgb;
}
void main() {
    // Dense symmetric taps avoid displaced copies of a sharp stellar limb.
    vec3 light=vec3(0.);
    float total=0.;
    for(int i=-6;i<=6;++i){
        float weight=exp(-float(i*i)/18.);
        light+=sampleLight(uv+direction*float(i))*weight;
        total+=weight;
    }
    outputColor=vec4(light/total,1.);
}
"""


COMPOSITE_FRAGMENT = """#version 330 core
in vec2 uv;
out vec4 outputColor;
uniform sampler2D scene, bloom0, bloom1, bloom2, bloom3;
uniform float glow, exposure;
void main() {
    // A compact bright shoulder and much weaker broad wings, without periodic rays.
    vec3 halo=texture(bloom0,uv).rgb*.45+texture(bloom1,uv).rgb*.30
             +texture(bloom2,uv).rgb*.17+texture(bloom3,uv).rgb*.08;
    vec3 linearLight=texture(scene,uv).rgb+halo*(.32*glow);
    // Combine emission and camera scatter before exposure and the display transfer.
    vec3 mapped=vec3(1.)-exp(-linearLight*exposure);
    outputColor=vec4(pow(max(mapped,vec3(0.)),vec3(1./2.2)),1.);
}
"""


class StellarBloom:
    """Own a floating-point scene and four separable Gaussian blur levels."""

    def __init__(self, vertex_shader: str):
        from OpenGL import GL
        from OpenGL.GL.shaders import compileProgram, compileShader
        self.blur = self.composite = None
        self.targets = []
        self.depth = None
        self.size = None
        try:
            self.blur = compileProgram(compileShader(vertex_shader, GL.GL_VERTEX_SHADER),
                                       compileShader(BLUR_FRAGMENT, GL.GL_FRAGMENT_SHADER))
            self.composite = compileProgram(compileShader(vertex_shader, GL.GL_VERTEX_SHADER),
                                            compileShader(COMPOSITE_FRAGMENT, GL.GL_FRAGMENT_SHADER))
        except Exception:
            self.close()
            raise

    def _release_targets(self):
        from OpenGL import GL
        for framebuffer, texture, _, _ in self.targets:
            GL.glDeleteFramebuffers(1, [framebuffer])
            GL.glDeleteTextures(1, [texture])
        self.targets.clear()
        if self.depth:
            GL.glDeleteRenderbuffers(1, [self.depth])
            self.depth = None
        self.size = None

    def close(self):
        from OpenGL import GL
        self._release_targets()
        for name in ("blur", "composite"):
            program = getattr(self, name)
            if program:
                GL.glDeleteProgram(program)
                setattr(self, name, None)

    def _target(self, width, height):
        from OpenGL import GL
        texture = int(GL.glGenTextures(1))
        framebuffer = int(GL.glGenFramebuffers(1))
        # Register resources immediately so even an allocation failure is cleaned up.
        self.targets.append((framebuffer, texture, width, height))
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA16F, width, height, 0, GL.GL_RGBA, GL.GL_FLOAT, None)
        for axis in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, axis, GL.GL_CLAMP_TO_BORDER)
        GL.glTexParameterfv(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_BORDER_COLOR, [0., 0., 0., 0.])
        for parameter in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, parameter, GL.GL_LINEAR)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, texture, 0)
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("Could not create the stellar HDR framebuffer.")

    def begin(self, width, height):
        from OpenGL import GL
        GL.glActiveTexture(GL.GL_TEXTURE0)
        if self.size != (width, height):
            self._release_targets()
            try:
                self._target(width, height)
                self._target(width, height)
                self.depth = GL.glGenRenderbuffers(1)
                GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.depth)
                GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, width, height)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.targets[0][0])
                GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, self.depth)
                for level in range(4):
                    w, h = max(1, width//(2 << level)), max(1, height//(2 << level))
                    self._target(w, h)
                    self._target(w, h)
                self.size = (width, height)
            except Exception:
                self._release_targets()
                raise
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.targets[0][0])
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT1, GL.GL_TEXTURE_2D, self.targets[1][1], 0)
        GL.glDrawBuffers(2, [GL.GL_COLOR_ATTACHMENT0, GL.GL_COLOR_ATTACHMENT1])
        GL.glViewport(0, 0, width, height)
        GL.glDepthMask(True)
        GL.glClear(GL.GL_DEPTH_BUFFER_BIT)

    def finish(self, framebuffer, glow, exposure, pixel_ratio):
        from OpenGL import GL
        width, height = self.size
        GL.glActiveTexture(GL.GL_TEXTURE0)
        if glow > 0:
            GL.glUseProgram(self.blur)
            GL.glUniform1i(GL.glGetUniformLocation(self.blur, "source"), 0)
            source = self.targets[1][1]
            for level in range(4):
                for axis in range(2):
                    fbo, texture, w, h = self.targets[2+level*2+axis]
                    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                    GL.glViewport(0, 0, w, h)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, source)
                    # Constant logical-pixel width across display scaling and resizing.
                    GL.glUniform2f(GL.glGetUniformLocation(self.blur, "direction"),
                                   pixel_ratio/w if axis == 0 else 0.,
                                   pixel_ratio/h if axis == 1 else 0.)
                    GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
                    source = texture
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
        GL.glViewport(0, 0, width, height)
        GL.glUseProgram(self.composite)
        for unit, name in enumerate(("scene", "bloom0", "bloom1", "bloom2", "bloom3")):
            GL.glActiveTexture(GL.GL_TEXTURE0+unit)
            # With bloom off, sample the initialized scene instead of undefined targets.
            index = unit*2+1 if unit and glow > 0 else 0
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.targets[index][1])
            GL.glUniform1i(GL.glGetUniformLocation(self.composite, name), unit)
        GL.glUniform1f(GL.glGetUniformLocation(self.composite, "glow"), glow)
        GL.glUniform1f(GL.glGetUniformLocation(self.composite, "exposure"), exposure)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        for unit in range(4, -1, -1):
            GL.glActiveTexture(GL.GL_TEXTURE0+unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
