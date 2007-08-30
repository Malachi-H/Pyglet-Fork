#!/usr/bin/env python

'''
'''

__docformat__ = 'restructuredtext'
__version__ = '$Id$'

import ctypes

from pyglet.media import BasePlayer, ManagedSoundPlayerMixIn, Listener
from pyglet.media import MediaException

from pyglet.media.drivers.alsa import asound

alsa_debug = 'alsa.log'

class ALSAException(MediaException):
    pass

def check(err):
    if err < 0:
        raise ALSAException(asound.snd_strerror(err))
    return err

class Device(object):
    def __init__(self, name):
        self.name = name
        self.pcm = ctypes.POINTER(asound.snd_pcm_t)()
        self.hwparams = ctypes.POINTER(asound.snd_pcm_hw_params_t)()
        self.swparams = ctypes.POINTER(asound.snd_pcm_sw_params_t)() 

        check(asound.snd_pcm_open(ctypes.byref(self.pcm),
                                  name,
                                  asound.SND_PCM_STREAM_PLAYBACK,
                                  asound.SND_PCM_NONBLOCK))
        check(asound.snd_pcm_hw_params_malloc(ctypes.byref(self.hwparams)))
        check(asound.snd_pcm_sw_params_malloc(ctypes.byref(self.swparams)))
        check(asound.snd_pcm_hw_params_any(self.pcm, self.hwparams))

        if alsa_debug:
            asound.snd_output_printf(debug_output, 'New device: %s\n' % name)
            check(asound.snd_pcm_dump(self.pcm, debug_output))
            check(asound.snd_pcm_dump_setup(self.pcm, debug_output))
            asound.snd_output_printf(debug_output, 'hwparams:\n')
            check(asound.snd_pcm_hw_params_dump(self.hwparams, debug_output))
            asound.snd_output_printf(debug_output, 'swparams:\n')
            check(asound.snd_pcm_sw_params_dump(self.swparams, debug_output))
            asound.snd_output_printf(debug_output, '---------\n')

    def __del__(self):
        try:
            check(asound.snd_pcm_close(self.pcm))
            print 'closed'
        except (NameError, AttributeError):
            pass

    def prepare(self, source):
        # TODO avoid creating in this case.
        if not source.audio_format:
            return

        format = {
            8:  asound.SND_PCM_FORMAT_U8,
            16: asound.SND_PCM_FORMAT_S16,
            24: asound.SND_PCM_FORMAT_S24,  # probably won't work
            32: asound.SND_PCM_FORMAT_S32
        }.get(source.audio_format.sample_size)
        if format is None:
            raise ALSAException('Unsupported audio format.')

        check(asound.snd_pcm_set_params(self.pcm,
            format, 
            asound.SND_PCM_ACCESS_RW_INTERLEAVED,
            source.audio_format.channels,
            source.audio_format.sample_rate,
            1,
            0))
        

class ALSAPlayer(BasePlayer):
    _min_buffer_time = 0.3
    _max_buffer_size = 65536

    def __init__(self):
        super(ALSAPlayer, self).__init__()

        self._sources = []
        self._playing = False
        self._device = None

        self._buffer_time = 0.
        self._start_time = None

    def queue(self, source):
        source = source._get_queue_source()

        if not self._sources:
            source._init_texture(self)
        self._sources.append(source)

    def next(self):
        if self._sources:
            old_source = self._sources.pop(0)
            old_source._release_texture(self)
            old_source._stop()

        if self._sources:
            self._sources[0]._init_texture(self)

    def dispatch_events(self):
        if not self._sources:
            return

        if not self._device:
            self._device = Device('plug:front')
            self._device.prepare(self._sources[0])

        self_time = self.time

        if self._texture:
            self._sources[0]._update_texture(self, self_time)

        source = self._sources[0]
        while source and self._buffer_time - self_time < self._min_buffer_time:
            max_bytes = int(
                self._min_buffer_time * source.audio_format.bytes_per_second)
            max_bytes = min(max_bytes, self._max_buffer_size)
            audio_data = source._get_audio_data(max_bytes)

            if audio_data:
                self._buffer_time = audio_data.timestamp + audio_data.duration
                samples = \
                    audio_data.length // source.audio_format.bytes_per_sample
                if self._start_time is None:
                    self._start_time = self._get_asound_time()
                samples_out = check(asound.snd_pcm_writei(self._device.pcm, 
                                                          audio_data.data,
                                                          samples))
                if samples_out < samples:
                    # TODO keep going until it's all written.
                    pass

                # TODO xrun recovery
            else:
                # EOS (in buffer)
                source = None

    def _get_time(self):
        if self._start_time is None:
            return 0.
        return self._get_asound_time() - self._start_time

    def _get_asound_time(self):
        status = ctypes.POINTER(asound.snd_pcm_status_t)()
        timestamp = asound.snd_timestamp_t()

        check(asound.snd_pcm_status_malloc(ctypes.byref(status)))
        check(asound.snd_pcm_status(self._device.pcm, status))
        asound.snd_pcm_status_get_tstamp(status, ctypes.byref(timestamp))
        asound.snd_pcm_status_free(status)
        return timestamp.tv_sec + timestamp.tv_usec * 0.000001

    def play(self):
        if self._playing:
            return

        self._playing = True

        if not self._sources:
            return

    def pause(self):
        self._playing = False

        if not self._sources:
            return

    def seek(self, timestamp):
        if self._sources:
            self._sources[0]._seek(timestamp)
            self._timestamp = timestamp
            self._timestamp_time = time.time()

    def _get_source(self):
        if self._sources:
            return self._sources[0]
        return None

    def _stop(self):
        raise RuntimeError('Invalid eos_action for this player.')

    def _set_volume(self, volume):
        self._volume = volume
        # TODO apply to device

    # All other properties are silently ignored.

    def _set_min_gain(self, min_gain):
        self._min_gain = min_gain

    def _set_max_gain(self, max_gain):
        self._max_gain = max_gain

    def _set_position(self, position):
        self._position = position

    def _set_velocity(self, velocity):
        self._velocity = velocity

    def _set_pitch(self, pitch):
        self._pitch = pitch

    def _set_cone_orientation(self, cone_orientation):
        self._cone_orientation = cone_orientation

    def _set_cone_inner_angle(self, cone_inner_angle):
        self._cone_inner_angle = cone_inner_angle

    def _set_cone_outer_gain(self, cone_outer_gain):
        self._cone_outer_gain = cone_outer_gain

class ALSAManagedSoundPlayer(ALSAPlayer, ManagedSoundPlayerMixIn):
    pass

class ALSAListener(Listener):
    def set_volume(self, volume):
        # TODO master volume
        self._volume = volume

    # All other properties are silently ignored.

    def set_position(self, position):
        self._position = position

    def set_velocity(self, velocity):
        self._velocity = velocity

    def set_forward_orientation(self, orientation):
        self._forward_orientation = orientation

    def set_up_orientation(self, orientation):
        self._up_orientation = orientation

    def set_doppler_factor(self, factor):
        self._doppler_factor = factor

    def set_speed_of_sound(self, speed_of_sound):
        self._speed_of_sound = speed_of_sound

def driver_init():
    global debug_output
    print asound.snd_asoundlib_version()
    debug_output = ctypes.POINTER(asound.snd_output_t)()
    if alsa_debug:
        check(asound.snd_output_stdio_open(ctypes.byref(debug_output),
                                           alsa_debug,
                                           'w'))

driver_listener = ALSAListener()
DriverPlayer = ALSAPlayer
DriverManagedSoundPlayer = ALSAManagedSoundPlayer
