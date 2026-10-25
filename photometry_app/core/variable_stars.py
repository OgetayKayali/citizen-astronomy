"""Runnable educational Library models; the original EA solver remains unchanged."""
from __future__ import annotations

import ctypes as ct
import json
import math
from pathlib import Path
import weakref

import numpy as np

from .binary_light import BinaryLightModel, PARAMETERS, pointer, _DP

# Native array ABI. Keep order in sync with variable.hpp.
EXTRAS = ('timescale', 'radius_amp', 'temp_amp', 'lag', 'harmonic', 'frequency_ratio',
          'secondary_amp', 'alternation', 'fill_a', 'fill_b', 'fillout', 'gravity_beta',
          'albedo', 'spot_size', 'spot_temp', 'spot_latitude', 'event_start', 'rise',
          'decay', 'event_amp', 'recurrence', 'disk_radius', 'accretion', 'molecular')
# label, minimum, maximum, decimals, logarithmic
CONTROLS = {
    'radius_amp': ('Radius amplitude / fraction', 0, .6, 3, False),
    'temp_amp': ('Temperature amplitude / fraction', 0, .6, 3, False),
    'lag': ('Radius lag / cycles', -.5, .5, 3, False),
    'harmonic': ('Second harmonic strength', 0, .8, 3, False),
    'frequency_ratio': ('Secondary / primary frequency', .1, 5, 4, False),
    'secondary_amp': ('Secondary mode strength', 0, 1, 3, False),
    'alternation': ('Alternating cycle strength', 0, .8, 3, False),
    'fill_a': ('A polar radius / Roche pole', .3, 1, 3, False),
    'fill_b': ('B polar radius / Roche pole', .3, 1, 3, False),
    'fillout': ('Common-envelope fill-out', .01, .6, 3, False),
    'gravity_beta': ('Gravity-darkening exponent', 0, .25, 3, False),
    'albedo': ('Irradiation efficiency', 0, 1, 3, False),
    'spot_size': ('Active-region radius / deg', 2, 65, 1, False),
    'spot_temp': ('Spot / photosphere temperature', .3, 1, 3, False),
    'spot_latitude': ('Spot latitude / deg', -90, 90, 1, False),
    'event_start': ('Event onset / days', 0, 5000, 5, False),
    'rise': ('Rise timescale / days', .00001, 1000, 5, True),
    'decay': ('Decay timescale / days', .00001, 5000, 5, True),
    'event_amp': ('Peak excess / baseline driver', .01, 1e6, 3, True),
    'recurrence': ('Recurrence / days', .01, 5000, 3, True),
    'disk_radius': ('Disk outer radius / R☉', .02, 10, 3, True),
    'accretion': ('Accretion / M☉ per year', 1e-12, 1e-6, 12, True),
    'molecular': ('Visual molecular-opacity proxy', 0, 2, 3, False),
}
RADIAL = ('radius_amp', 'temp_amp', 'lag', 'harmonic')
MODES = ('radius_amp', 'temp_amp', 'frequency_ratio', 'secondary_amp')
EVENT = ('event_start', 'rise', 'decay', 'event_amp')
COMMON = 'Educational forward model, not a fit to a named star. Blackbody energy bands and user-supplied limb darkening; no stellar-interior evolution.'
# name, description, limitations, exposed controls, periodic
FAMILIES = {
    1: ('EB · Beta Lyrae', 'Two Roche equipotential surfaces with gravity darkening, eclipses and irradiation.', 'Circular synchronous orbit; one-pass irradiation. No gas stream, disk or mass-transfer evolution.', ('fill_a','fill_b','gravity_beta','albedo'), True),
    2: ('EW · W Ursae Majoris', 'A shared Roche envelope produces contact eclipses and continuous ellipsoidal variation.', 'Circular synchronous orbit; prescribed component temperatures, no energy transport through the neck.', ('fillout','gravity_beta'), True),
    3: ('Classical Cepheid', 'An asymmetric radial radius/temperature cycle with a phase lag.', 'Prescribed waveform; no hydrodynamic pulsation or period–luminosity calibration.', RADIAL, True),
    4: ('Type II Cepheid', 'A lower-mass radial pulsator with independent radius and temperature variations.', 'Representative radial model; subclass resonance and shocks are omitted.', RADIAL, True),
    5: ('RR Lyrae · RRab', 'A short-period asymmetric radial pulsation.', 'RRab example; not an RRc/RRd or Blazhko modulation model.', RADIAL, True),
    6: ('Mira', 'Large radial pulsation plus a temperature-dependent visual-opacity proxy.', 'Opacity is illustrative, not a molecular spectrum; no dust, wind or shocks.', RADIAL+('molecular',), True),
    7: ('Delta Scuti', 'Two radial oscillations produce beating in radius and temperature.', 'Prescribed frequencies; no mode excitation or stellar seismology fit.', ('lag',)+MODES, False),
    8: ('SX Phoenicis', 'A compact, short-period two-mode radial pulsator.', 'Metallicity is not used in the blackbody spectrum; modes are illustrative.', ('lag',)+MODES, False),
    9: ('Beta Cephei', 'Two quadrupole surface modes on a hot star; inclination changes cancellation.', 'Small-perturbation surface normals; no eigenfunction or opacity-driving calculation.', MODES, False),
    10: ('Slowly pulsating B star', 'Longer-period quadrupole temperature and radius patterns.', 'Illustrative g-mode geometry; no interior propagation or rotation splitting.', MODES, False),
    11: ('RV Tauri', 'Two pulsations per formal period with alternating deep and shallow minima.', 'Prescribed alternation; no shock dynamics or long secondary variation.', ('radius_amp','temp_amp','lag','alternation'), True),
    12: ('Semiregular giant', 'Two incommensurate radial modes produce a changing envelope.', 'Deterministic beating; no stochastic convection or dust extinction.', ('lag',)+MODES, False),
    13: ('Rotating spotted star', 'A cool surface cap rotates into and out of view with limb darkening.', 'One fixed spot; no differential rotation, spot growth or faculae.', ('spot_size','spot_temp','spot_latitude'), True),
    14: ('Dwarf nova', 'An accretion-rate outburst heats an optically thick disk around a white dwarf.', 'Prescribed recurrence and rate response; steady thin-disk annuli, no disk-instability PDE or gas stream.', EVENT+('recurrence','disk_radius','accretion'), False),
    15: ('Nova', 'A single luminosity eruption expands and cools an effective photosphere.', 'Prescribed pseudophotosphere; no nuclear burning, ejecta lines or hydrodynamics.', EVENT, False),
    16: ('Flare star', 'A localized hot cap brightens rapidly and decays on a cool star.', 'One observer-facing active region; prescribed thermal event, no magnetic reconnection or nonthermal spectrum.', EVENT+('spot_size',), False),
}


class StellarLabModel(BinaryLightModel):
    def __init__(self):
        self.family = 0
        self.extras = np.array([5,.08,.1,.35,.25,1.413,.3,.25,.9,1,.15,.08,.5,30,.7,15,1,.2,2,20,20,.5,1e-10,.7], dtype=float)
        self.quality = 192
        self._handle = None
        self._native_cleanup = None
        self._key = None
        self._last_observation = None
        super().__init__()
        signatures = {
            'create': ([ct.c_int,_DP,_DP,ct.c_int],ct.c_void_p), 'delete': ([ct.c_void_p],None),
            'error': ([],ct.c_char_p), 'info': ([ct.c_void_p,_DP],None),
            'observe': ([ct.c_void_p,ct.c_double,_DP],ct.c_int),
            'curve': ([ct.c_void_p,ct.c_double,ct.c_double,ct.c_int,_DP],ct.c_int),
            'mesh': ([ct.c_void_p,ct.c_double,ct.POINTER(ct.c_float),ct.c_int],ct.c_int),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.lib,'vl_'+name);fn.argtypes,fn.restype=args,result

    @property
    def periodic(self):
        return not self.family or FAMILIES[self.family][4]

    @property
    def binary(self):
        return self.family in (0,1,2,14)

    @property
    def name(self):
        return FAMILIES[self.family][0] if self.family else 'EA · detached binary'

    @property
    def scope(self):
        return FAMILIES[self.family][2]+' '+COMMON if self.family else ''

    def get(self,name):
        return float(self.extras[EXTRAS.index(name)]) if name in EXTRAS else super().get(name)

    def set(self,name,value):
        if name in EXTRAS:self.extras[EXTRAS.index(name)]=value
        else:super().set(name,value)

    def set_preset(self,index):
        self.family=0
        super().set_preset(index)

    def select_family(self,family):
        if family not in FAMILIES:raise ValueError('Unknown Library model')
        self.family=family
        self.values[:]=[1,1,5800,.6, .7,.7,4200,.7, 6,0,75,0,0, 100,0,0,2]
        self.extras[:]=[5,.08,.1,.35,.25,1.413,.3,.25,.9,1,.15,.08,.5,30,.7,15,1,.2,2,20,20,.5,1e-10,.7]
        # mass, radius, temperature, variability period/window in days
        physical={1:(3,2,10000,2),2:(1.2,1.2,6200,.36),3:(5,40,5900,5.4),4:(.6,15,5500,12),
                  5:(.65,5,6500,.57),6:(1,240,3000,330),7:(1.8,2,7500,.12),8:(1.1,1.4,7600,.055),
                  9:(12,6,24000,.19),10:(5,3.5,16000,1.8),11:(.6,55,5000,60),12:(1.5,100,3500,100),
                  13:(.8,.85,4800,3.2),14:(.8,.012,18000,40),15:(1,.012,100000,100),16:(.3,.35,3300,.08)}
        mass,radius,temp,period=physical[family]
        self.values[:3]=mass,radius,temp
        self.set('timescale',period)
        if family<=2:
            self.values[4:8]=[1.8,1.8,6200,.6] if family==1 else [.72,.8,5900,.6]
            super().set_period(period)
            self.set('inclination',82)
            self.set('gravity_beta',.25 if family==1 else .08)
        if family==6:
            self.set('radius_amp',.22);self.set('temp_amp',.22)
        if family in (7,8,9,10):
            self.set('radius_amp',.025 if family<9 else .012);self.set('temp_amp',.025)
        if family==5:self.set('harmonic',.55)
        if family==12:self.set('radius_amp',.16);self.set('temp_amp',.08);self.set('frequency_ratio',.681)
        if family==14:
            self.values[4:8]=[.4,.42,3400,.7];self.set('separation',1.5);self.set('inclination',50)
            self.set('event_start',3);self.set('rise',.25);self.set('decay',2);self.set('event_amp',100)
        if family==15:
            self.set('event_start',5);self.set('rise',1);self.set('decay',18);self.set('event_amp',10000)
        if family==16:
            self.set('event_start',.012);self.set('rise',.0003);self.set('decay',.008);self.set('event_amp',80);self.set('spot_size',16)
        self.restart()

    @property
    def period(self):
        return super().period if self.family in (0,1,2) else self.get('timescale')

    def set_period(self,days):
        if self.family in (0,1,2):super().set_period(days)
        else:self.set('timescale',days)

    @property
    def invalid(self):
        if not self.family:return super().invalid
        if self.family not in FAMILIES:return 'Unknown model family.'
        if not np.isfinite(self.values).all() or not np.isfinite(self.extras).all():return 'All parameters must be finite.'
        limits={'mass_a':(.01,100),'mass_b':(.01,100),'radius_a':(.001,1500),'radius_b':(.001,1500),
                'temperature_a':(1500,200000),'temperature_b':(1500,200000),'limb_a':(0,1),'limb_b':(0,1),
                'separation':(.1,2000),'inclination':(0,90),'distance':(1,100000),'third_light':(0,.95),
                'exposure':(0,self.period*86400*.05),'timescale':(.001,5000),'omega':(-180,180),'node':(-180,180)}
        for key,(_,low,high,_,_) in CONTROLS.items():limits[key]=(low,high)
        for key,(low,high) in limits.items():
            if not low<=self.get(key)<=high:return f'{key.replace("_"," ")} must be between {low:g} and {high:g}.'
        if self.get('band') not in range(4):return 'Choose a valid observing band.'
        if self.get('eccentricity')!=0:return 'This surface model assumes a circular orbit.'
        if self.family==14 and not self.get('radius_a')<self.get('disk_radius')<self.get('separation')-self.get('radius_b'):
            return 'The disk must fit between the white dwarf and its companion.'
        if self.family==14 and self.get('disk_radius')>.95*self.get('separation')*.49*(self.get('mass_a')/self.get('mass_b'))**(2/3)/(.6*(self.get('mass_a')/self.get('mass_b'))**(2/3)+math.log1p((self.get('mass_a')/self.get('mass_b'))**(1/3))):
            return 'Disk radius exceeds the primary Roche lobe.'
        return ''

    def _native(self):
        if self.invalid:raise ValueError(self.invalid)
        self.set('timescale',self.period)
        key=(self.family,self.quality,self.values.tobytes(),self.extras.tobytes())
        if key!=self._key:
            h=self.lib.vl_create(self.family,pointer(self.values),pointer(self.extras),self.quality)
            if not h:raise ValueError(self.lib.vl_error().decode())
            if self._native_cleanup:self._native_cleanup()
            self._handle=h;self._key=key;self._last_observation=None
            self._native_cleanup=weakref.finalize(self,self.lib.vl_delete,h)
        return self._handle

    def observe(self,phase):
        if not self.family:return super().observe(phase)
        h=self._native()
        if self._last_observation is not None and self._last_observation[0]==phase:return self._last_observation[1].copy()
        result=np.empty(5)
        if not self.lib.vl_observe(h,phase,pointer(result)):raise ValueError(self.lib.vl_error().decode())
        self._last_observation=(phase,result)
        return result.copy()

    def curve(self,count=601,start=0,stop=1):
        if not self.family and start==0 and stop==1:return super().curve(count)
        if count<2:raise ValueError('At least two samples are required')
        if not self.family:return np.array([self.observe(t) for t in np.linspace(start,stop,count)])
        result=np.empty((count,5))
        if not self.lib.vl_curve(self._native(),start,stop,count,pointer(result)):raise ValueError(self.lib.vl_error().decode())
        return result

    def mesh(self,cycles):
        h=self._native()
        result=np.empty((70000,10),dtype=np.float32)
        count=self.lib.vl_mesh(h,cycles,result.ctypes.data_as(ct.POINTER(ct.c_float)),len(result))
        if count<0 or count>len(result):raise ValueError('Could not construct stellar surface')
        return result[:count]

    def info(self):
        out=np.empty(5);self.lib.vl_info(self._native(),pointer(out));return out

    def snapshot(self):
        model=StellarLabModel();model.family=self.family;model.values[:]=self.values;model.extras[:]=self.extras;model.quality=self.quality
        return model

    def advance(self,dt,speed):
        if not self.family:return super().advance(dt,speed)
        if self.invalid:return
        self.cycles+=min(.1,max(0.,dt))*speed
        while self.next_sample<=self.cycles+1e-10:
            ideal=self.observe(self.next_sample)[0]
            measured=max(1e-8,ideal+self.lib.bl_noise(self.rng,self.noise_ppm*1e-6)) if self.noise_ppm else ideal
            self.samples.append((self.next_sample,measured,ideal));self.next_sample+=1/120
        del self.samples[:-6000]

    def save(self,path):
        if self.invalid:raise ValueError(self.invalid)
        if not self.family and str(path).lower().endswith('.binary'):return super().save(path)
        Path(path).write_text(json.dumps(dict(format='CAst Stellar Lab',version=1,family=self.family,
            parameters=dict(zip(PARAMETERS,self.values)),variability=dict(zip(EXTRAS,self.extras)),
            noise_ppm=self.noise_ppm,comparison=self.comparison),indent=2)+'\n',encoding='utf-8')

    def load(self,path):
        text=Path(path).read_text(encoding='utf-8')
        candidate=StellarLabModel()
        if text.startswith('BINARY_LIGHT_LAB'):
            BinaryLightModel.load(candidate,path)
        else:
            try:
                data=json.loads(text)
                if data['format']!='CAst Stellar Lab' or data['version']!=1:raise ValueError('Unsupported model file')
                candidate.family=data['family']
                if type(candidate.family) is not int or candidate.family not in (0,*FAMILIES):raise ValueError('Unknown model family')
                candidate.values[:]=[data['parameters'][k] for k in PARAMETERS]
                candidate.extras[:]=[data['variability'][k] for k in EXTRAS]
                candidate.noise_ppm=float(data['noise_ppm']);candidate.comparison=float(data['comparison'])
                if not 0<=candidate.noise_ppm<=10000 or not .1<=candidate.comparison<=10:raise ValueError('Invalid observing settings')
                if candidate.invalid:raise ValueError(candidate.invalid)
                if candidate.family:candidate._native()
            except (KeyError,TypeError,OverflowError) as exc:raise ValueError('Incomplete or invalid stellar model file') from exc
        self.family=candidate.family;self.values[:]=candidate.values;self.extras[:]=candidate.extras
        self.noise_ppm=candidate.noise_ppm;self.comparison=candidate.comparison;self.restart()

    def export(self,path,observed=False):
        if not self.family:return super().export(path,observed)
        stop=1 if self.periodic else max(1,self.cycles)
        phases=np.linspace(0,stop,1001);curve=self.curve(1001,0,stop)
        metadata=dict(model=self.name,scope=self.scope,period_or_window_days=self.period,
            integration_pixels=self.quality,parameters=dict(zip(PARAMETERS,self.values)),variability=dict(zip(EXTRAS,self.extras)))
        if self.family in (1,2):
            info=self.info()
            metadata['roche_surface']=dict(l1_separation_units=info[1],common_potential=info[2] if self.family==2 else None,
                volume_radius_a_Rsun=info[3],volume_radius_b_Rsun=info[4],normalization='phase-zero projected flux')
        path=Path(path)
        with path.open('w',encoding='utf-8') as f:
            f.write('# '+json.dumps(metadata,ensure_ascii=False)+'\n')
            f.write('cycles,time_days,relative_flux,delta_magnitude,irradiance_W_m2,primary_contribution,secondary_contribution\n')
            np.savetxt(f,np.column_stack((phases,phases*self.period,curve)),delimiter=',',fmt='%.12g')
        if not observed:return path
        output=path.with_name(path.stem+'-observed.csv')
        with output.open('w',encoding='utf-8') as f:
            f.write(f'# {self.name}; seed=2026; noise_ppm={self.noise_ppm}; comparison={self.comparison}; '+self.scope+'\n')
            f.write('cycles,time_days,ideal_flux,measured_flux,differential_magnitude\n')
            for t,measured,ideal in self.samples:
                f.write(','.join(format(v,'.12g') for v in (t,t*self.period,ideal,measured,-2.5*math.log10(measured/self.comparison)))+'\n')
        return output
