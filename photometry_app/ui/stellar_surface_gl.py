"""Depth-tested Library surfaces in the same HDR/bloom pipeline as detached stars."""
import ctypes

VERTEX = '''#version 330 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec4 properties;
uniform float extent, aspect;
out vec3 n;
out vec4 prop;
void main(){
    gl_Position=vec4(position.x/(extent*aspect),position.y/extent,-position.z/(4.*extent),1.);
    n=normal;prop=properties;
}
'''


class StellarSurface:
    def __init__(self, original):
        from OpenGL import GL
        from OpenGL.GL.shaders import compileProgram, compileShader
        helpers=original[original.index('float hash('):original.index('vec3 corona(')]
        fragment='''#version 330 core
in vec3 n;
in vec4 prop;
layout(location=0) out vec4 outputColor;
layout(location=1) out vec4 stellarLight;
uniform float time,detail;
uniform vec3 tint0,tint1;
uniform int customTint;
'''+helpers+'''
void main(){
    vec3 normal=normalize(n);
    if(normal.z<=0.)discard;
    vec3 tint=customTint==1?(prop.w<.5?tint0:tint1):color(prop.x);
    // Surface brightness follows the model, gently compressed for a readable display.
    float brightness=clamp(pow(max(prop.z,.000001),.35),.015,12.);
    vec3 light=surface(normal,5800.,prop.y,tint,1.+4.*prop.w)*brightness;
    outputColor=vec4(light,1.);stellarLight=vec4(light,1.);
}
'''
        self.program=compileProgram(compileShader(VERTEX,GL.GL_VERTEX_SHADER),compileShader(fragment,GL.GL_FRAGMENT_SHADER))
        self.vao=GL.glGenVertexArrays(1);self.buffer=GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao);GL.glBindBuffer(GL.GL_ARRAY_BUFFER,self.buffer)
        for location,size,offset in ((0,3,0),(1,3,12),(2,4,24)):
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(location,size,GL.GL_FLOAT,False,40,ctypes.c_void_p(offset))
        GL.glBindVertexArray(0)
        self.key=None

    def draw(self,model,extent,aspect,look,tints,custom):
        from OpenGL import GL
        key=(model.family,model.values.tobytes(),model.extras.tobytes(),model.cycles)
        GL.glUseProgram(self.program);GL.glBindVertexArray(self.vao)
        if key!=self.key:
            mesh=model.mesh(model.cycles)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER,self.buffer)
            GL.glBufferData(GL.GL_ARRAY_BUFFER,mesh.nbytes,mesh,GL.GL_DYNAMIC_DRAW)
            self.count=len(mesh);self.key=key
        for name,value in (('extent',extent),('aspect',aspect),('time',model.cycles*8),('detail',look['detail'])):
            GL.glUniform1f(GL.glGetUniformLocation(self.program,name),value)
        for j,tint in enumerate(tints):GL.glUniform3f(GL.glGetUniformLocation(self.program,f'tint{j}'),*tint)
        GL.glUniform1i(GL.glGetUniformLocation(self.program,'customTint'),int(custom))
        GL.glEnable(GL.GL_DEPTH_TEST);GL.glDepthFunc(GL.GL_LESS)
        GL.glDrawArrays(GL.GL_TRIANGLES,0,self.count)
        GL.glDisable(GL.GL_DEPTH_TEST)

    def close(self):
        from OpenGL import GL
        GL.glDeleteBuffers(1,[self.buffer]);GL.glDeleteVertexArrays(1,[self.vao]);GL.glDeleteProgram(self.program)
