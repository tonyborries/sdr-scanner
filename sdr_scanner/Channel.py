from enum import IntEnum
import math
import numpy as np
import time
from typing import Any, Dict, List, Optional
import uuid

from gnuradio import analog
from gnuradio import blocks
from gnuradio import filter as gr_filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window

from .const import (
    AUDIO_SAMPLERATE,
    FM_QUAD_RATE,
    NOISEFLOOR_LOWPASS_A,
    RSSI_LOWPASS_TC,
    RSSI_UPDATE_FREQ_HZ,
    STATUS_UPDATE_TIME_S
)


class ChannelStatus(IntEnum):
    IDLE = 0
    ACTIVE = 1
    DWELL = 2

class ChannelMode(IntEnum):
    FM = 1
    NFM = 2
    AM = 3


def dbToRatio(dB: float) -> float:
    return 10 ** (dB/20)

def _filterDec(x):
    """
    For a 2-stage decimation, find the closest factors.
    Return the smaller factor first.
    """
    n = int(math.sqrt(x))
    while n > 1:
        if x % n == 0:
            return n, x // n
        n -= 1
    return 1, x

class RSSI_EmbeddedPythonBlock(gr.sync_block):

    def __init__(self, rssiCb):
        gr.sync_block.__init__(
            self,
            name='RSSI Embedded Python Block',   # will show up in GRC
            in_sig=[np.float32],
            out_sig=[]
        )
        self.rssiCb = rssiCb

    def work(self, input_items, output_items):
        dBFS = 10 * math.log10(input_items[0][-1])
        self.rssiCb(dBFS)
        return len(input_items[0])


class ChannelConfig():
    def __init__(self, freq_hz: int, label: str, mode: ChannelMode=ChannelMode.FM, audioGain_dB: float=0, dwellTime_s: float=3.0, squelchThreshold:float=-55.0):

        self.id = uuid.uuid4()

        self.freq_hz = freq_hz
        self.label = label
        self.mode = mode

        self.dwellTime_s = dwellTime_s  # Time to wait after active before continuing scan
        self.audioGain_dB = audioGain_dB
        self.squelchThreshold = squelchThreshold

    def debugPrint(self):
        print(f"    {self.freq_hz / 1e6:6.3f} {self.mode.name} {self.label}")

    @staticmethod
    def modeStrLookup(modeStr: str) -> Optional[ChannelMode]:
        return {
            "FM": ChannelMode.FM,
            "NFM": ChannelMode.NFM,
            "AM": ChannelMode.AM,
        }.get(modeStr.upper())

    @classmethod
    def fromConfigDict(cls,
                       configDict: Dict[str, Any],
                       defaultChannelConfig: Optional["ChannelConfig"]=None
                       ) -> "ChannelConfig":
        """
        configDict is an entry from the Config file, build a ChannelConfig from it.
        defaultChannelConfig is a ChannelConfig used for default values.
        """

        kwargs = {}

        kwargs['freq_hz'] = configDict['freq'] * 1e6
        kwargs['label'] = configDict.get('label', configDict['freq'])

        if defaultChannelConfig:
            kwargs['mode'] = defaultChannelConfig.mode
            kwargs['audioGain_dB'] = defaultChannelConfig.audioGain_dB
            kwargs['squelchThreshold'] = defaultChannelConfig.squelchThreshold
            kwargs['dwellTime_s'] = defaultChannelConfig.dwellTime_s

        if 'mode' in configDict:
            kwargs['mode'] = cls.modeStrLookup(configDict['mode'])
        for k in ['audioGain_dB', 'squelchThreshold', 'dwellTime_s']:
            if k in configDict:
                kwargs[k] = configDict[k]

        cc = ChannelConfig(**kwargs)
        return cc


class Channel():
    def __init__(self, channelId, freq_hz: int, label: str, mode: ChannelMode, audioGain_dB: float, dwellTime_s: float, squelchThreshold:float, hardwareFreq_hz, rfSampleRate):

        self.id = channelId

        self.freq_hz = freq_hz
        self.label = label
        self.mode = mode

        self.dwellTime_s = dwellTime_s  # Time to wait after active before continuing scan
        self.audioGain_dB = audioGain_dB
        self.squelchThreshold = squelchThreshold

        self.hardwareFreq_hz = hardwareFreq_hz

        ###
        # Build Channel Block based on Mode

        self.channelBlock = None

        if mode in [ChannelMode.FM, ChannelMode.NFM]:
            deviation = 5000
            if mode == ChannelMode.NFM:
                deviation = 2500

            self.channelBlock = ChannelBlock_FM(
                self.id,
                self.label,
                self.freq_hz,
                self.hardwareFreq_hz,
                deviation,
                self.audioGain_dB,
                self.squelchThreshold,
                self.dwellTime_s,
                rfSampleRate=rfSampleRate
            )
        elif mode == ChannelMode.AM:
            self.channelBlock = ChannelBlock_AM(
                self.id,
                self.label,
                self.freq_hz,
                self.hardwareFreq_hz,
                self.audioGain_dB,
                self.squelchThreshold,
                self.dwellTime_s,
                rfSampleRate=rfSampleRate
            )

        if not self.channelBlock:
            raise Exception("Channel Block not Initialized - Check Mode setting")


    @classmethod
    def fromConfig(cls, cc: ChannelConfig, swc: "ScanWindowConfig"):
        channel = Channel(
            channelId=cc.id,
            freq_hz=cc.freq_hz,
            label=cc.label,
            mode=cc.mode,
            audioGain_dB=cc.audioGain_dB,
            dwellTime_s=cc.dwellTime_s,
            squelchThreshold=cc.squelchThreshold,
            hardwareFreq_hz=swc.hardwareFreq_hz,
            rfSampleRate=swc.rfSampleRate,
        )
        return channel

    def getStatus(self, statusPipe):
        return self.channelBlock.getStatus(statusPipe)

class ChannelBlock_Base(gr.hier_block2):

    def __init__(self):
        gr.hier_block2.__init__(
            self, "_Channel",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_float*1),
        )

        self._active = False
        self._lastActive = 0
        self._lastStatusReport = None
        self._lastStatusTime = 0.0

        self._rssi = None
        self._noiseFloor_dBFS = None

    def updateRSSI(self, rssi: float):
        """
        rssi - dbFS
        """
        self._rssi = rssi
        if not self._active:
            if self._noiseFloor_dBFS is None:
                self._noiseFloor_dBFS = rssi
            else:
                self._noiseFloor_dBFS = (NOISEFLOOR_LOWPASS_A * rssi) + ((1 - NOISEFLOOR_LOWPASS_A) * self._noiseFloor_dBFS)


class ChannelBlock_FM(ChannelBlock_Base):
    def __init__(self, channelId, label: str, channelFreq_hz: int, hardwareFreq_hz: int, deviation_hz: int, audioGain_dB: float, squelchThreshold: float, dwellTime_s: float, rfSampleRate):
        super().__init__()

        self.channelId = channelId

        self._label = label
        self._deviation_hz = deviation_hz
        self._dwellTime = dwellTime_s
        self.audioGainFactor = dbToRatio(audioGain_dB)
        self.squelchThreshold = squelchThreshold

        freqOffset_Hz = channelFreq_hz - hardwareFreq_hz

        ##################################################
        # Parameters
        ##################################################
        self.rfSampleRate = rfSampleRate


        if self.rfSampleRate % FM_QUAD_RATE != 0:
            raise Exception(f"RF Sample Rate ({self.rfSampleRate}) is not a multiple of FM Quad Rate ({FM_QUAD_RATE})")

        inputDecimation = self.rfSampleRate // FM_QUAD_RATE

        intermediateDecimation, xlatDecimation = _filterDec(inputDecimation)

        ##################################################
        # Blocks
        ##################################################

        ###
        # Input Channelization

        half_bandwidth = (self._deviation_hz + 3000)

        self.blockInputIntermediateFilter = None
        if inputDecimation >= 8 and intermediateDecimation > 1:
            # Use an intermediate filter to spread out decimation, hopefully lowering CPU requirements

            self.blockFreqXlatingFilter = gr_filter.freq_xlating_fft_filter_ccc(
                xlatDecimation,
                firdes.low_pass(1.0, self.rfSampleRate, self.rfSampleRate/(2*xlatDecimation), self.rfSampleRate/(4*xlatDecimation)),
                freqOffset_Hz,
                self.rfSampleRate
            )
            self.blockInputIntermediateFilter = gr_filter.fft_filter_ccc(
                intermediateDecimation,
                firdes.low_pass(1, self.rfSampleRate/xlatDecimation, half_bandwidth, half_bandwidth/4),
                2
            )

        else:
            self.blockFreqXlatingFilter = gr_filter.freq_xlating_fir_filter_ccc(
                self.rfSampleRate // FM_QUAD_RATE,
                firdes.low_pass(1.0, self.rfSampleRate, half_bandwidth, half_bandwidth/4),
                freqOffset_Hz,
                self.rfSampleRate
            )

        ###
        # Squelch and Demod

        self.blockAnalogPowerSquelch = analog.pwr_squelch_cc(
            self.squelchThreshold,
            0.005,
            0,
            False
        )
        self.blockAnalogNbfmRx = analog.nbfm_rx(
            audio_rate=AUDIO_SAMPLERATE,
            quad_rate=FM_QUAD_RATE,
            tau=75e-6,
            max_dev=self._deviation_hz,
          )

        ###
        # Audio Filter

        self.blockAudioFilter = gr_filter.fft_filter_fff(
            1,
            firdes.band_pass(
                1,
                AUDIO_SAMPLERATE,
                200,
                3500,
                100,
                window.WIN_HAMMING,
                6.76
            )
        )
        self.blockAudioGain = blocks.multiply_const_ff(self.audioGainFactor)

        ###
        # RSSI

        self.blockRssiComplexToMag2 = blocks.complex_to_mag_squared(1)
        self.blockRssiLowPassFilter = gr_filter.single_pole_iir_filter_ff( (1 / (FM_QUAD_RATE * RSSI_LOWPASS_TC)), 1)
        self.blockRssiDecimate = blocks.keep_one_in_n(gr.sizeof_float*1, (FM_QUAD_RATE // RSSI_UPDATE_FREQ_HZ) )
        self.blockRssi = RSSI_EmbeddedPythonBlock(self.updateRSSI)


        ##################################################
        # Connections
        ##################################################

        ###
        # RF Chain

        self.connect((self.blockAudioGain, 0), (self, 0))
        self.connect((self.blockAudioFilter, 0), (self.blockAudioGain, 0))
        self.connect((self.blockAnalogNbfmRx, 0), (self.blockAudioFilter, 0))
        self.connect((self.blockAnalogPowerSquelch, 0), (self.blockAnalogNbfmRx, 0))

        if self.blockInputIntermediateFilter:
            self.connect((self.blockInputIntermediateFilter, 0), (self.blockAnalogPowerSquelch, 0))
            self.connect((self.blockFreqXlatingFilter, 0), (self.blockInputIntermediateFilter, 0))
        else:
            self.connect((self.blockFreqXlatingFilter, 0), (self.blockAnalogPowerSquelch, 0))

        self.connect((self, 0), (self.blockFreqXlatingFilter, 0))

        ###
        # RSSI Chain

        self.connect((self.blockRssiDecimate, 0), (self.blockRssi, 0))
        self.connect((self.blockRssiLowPassFilter, 0), (self.blockRssiDecimate, 0))
        self.connect((self.blockRssiComplexToMag2, 0), (self.blockRssiLowPassFilter, 0))
        if self.blockInputIntermediateFilter:
            self.connect((self.blockInputIntermediateFilter, 0), (self.blockRssiComplexToMag2, 0))
        else:
            self.connect((self.blockFreqXlatingFilter, 0), (self.blockRssiComplexToMag2, 0))


    def setAudioGain(self, dB: float):
        self.audioGainFactor = dbToRatio(dB)
        self.blockAudioGain.set_k(self.audioGainFactor)

    def setSquelchValue(self, squelchThreshold):
        self.squelchThreshold = squelchThreshold
        self.blockAnalogPowerSquelch.set_threshold(squelchThreshold)

    def getStatus(self, statusPipe):
        status = ChannelStatus.IDLE
        if self.blockAnalogPowerSquelch.unmuted():
            self._active = True
            self._lastActive = time.time()
            status = ChannelStatus.ACTIVE
        else:
            self._active = False
            if time.time() - self._lastActive < self._dwellTime:
                status = ChannelStatus.DWELL

        if status != self._lastStatusReport or (status != ChannelStatus.IDLE and (time.time() - self._lastStatusTime) > STATUS_UPDATE_TIME_S):
            self._lastStatusTime = time.time()
            self._lastStatusReport = status
            if statusPipe:
                statusPipe.send([{
                    'type': 'channel_status',
                    'data': {
                        'id': self.channelId,
                        'status': status,
                         'rssi': self._rssi,
                         'noiseFloor': self._noiseFloor_dBFS,
                    }
                }])

        return status


class ChannelBlock_AM(ChannelBlock_Base):

    FIXED_AUDIO_GAIN_FACTOR = 3

    def __init__(self, channelId, label: str, channelFreq_hz: int, hardwareFreq_hz: int, audioGain_dB: float, squelchThreshold: float, dwellTime_s: float, rfSampleRate):
        super().__init__()

        self.channelId = channelId

        self._label = label
        self._dwellTime = dwellTime_s
        self.audioGainFactor = dbToRatio(audioGain_dB) * self.FIXED_AUDIO_GAIN_FACTOR
        self.squelchThreshold = squelchThreshold

        freqOffset_Hz = channelFreq_hz - hardwareFreq_hz

        ##################################################
        # Parameters
        ##################################################
        self.rfSampleRate = rfSampleRate

        inputDecimation = self.rfSampleRate // AUDIO_SAMPLERATE

        intermediateDecimation, xlatDecimation = _filterDec(inputDecimation)

        ##################################################
        # Blocks
        ##################################################

        ###
        # Input Channelization

        self.blockInputIntermediateFilter = None
        if inputDecimation >= 8 and intermediateDecimation > 1:
            # Use an intermediate filter to spread out decimation, hopefully lowering CPU requirements

            self.blockFreqXlatingFilter = gr_filter.freq_xlating_fft_filter_ccc(
                xlatDecimation,
                firdes.low_pass(1.0, self.rfSampleRate, self.rfSampleRate/(2*xlatDecimation), self.rfSampleRate/(4*xlatDecimation)),
                freqOffset_Hz,
                self.rfSampleRate
            )
            self.blockInputIntermediateFilter = gr_filter.fft_filter_ccc(
                intermediateDecimation,
                firdes.low_pass(1, self.rfSampleRate/xlatDecimation, 4000, 2000),
                2
            )

        else:
            self.blockFreqXlatingFilter = gr_filter.freq_xlating_fir_filter_ccc(
                int(self.rfSampleRate/AUDIO_SAMPLERATE),
                firdes.low_pass(1.0, self.rfSampleRate, 4000, 2000),
                freqOffset_Hz,
                self.rfSampleRate
            )

        ###
        # Squelch and Demod

        self.blockAnalogPowerSquelch = analog.pwr_squelch_cc(
            self.squelchThreshold,
            0.005,
            0,
            False
        )

        self.blockAnalogAgc = analog.feedforward_agc_cc(int(AUDIO_SAMPLERATE * 0.2), 0.5)

        self.blockAnalogAMDemod = blocks.complex_to_mag(1)

        ###
        # Audio

        self.blockAudioFilter = gr_filter.fft_filter_fff(
            1,
            firdes.band_pass(
                1,
                AUDIO_SAMPLERATE,
                200,
                3500,
                100,
                window.WIN_HAMMING,
                6.76
            )
        )
        self.blockAudioGain = blocks.multiply_const_ff(self.audioGainFactor)

        ###
        # RSSI

        self.blockRssiComplexToMag2 = blocks.complex_to_mag_squared(1)
        self.blockRssiLowPassFilter = gr_filter.single_pole_iir_filter_ff( (1 / (AUDIO_SAMPLERATE * RSSI_LOWPASS_TC)), 1)
        self.blockRssiDecimate = blocks.keep_one_in_n(gr.sizeof_float*1, (AUDIO_SAMPLERATE // RSSI_UPDATE_FREQ_HZ) )
        self.blockRssi = RSSI_EmbeddedPythonBlock(self.updateRSSI)


        ##################################################
        # Connections
        ##################################################

        ###
        # RF Chain

        self.connect((self.blockAudioGain, 0), (self, 0))
        self.connect((self.blockAudioFilter, 0), (self.blockAudioGain, 0))
        self.connect((self.blockAnalogAMDemod, 0), (self.blockAudioFilter, 0))
        self.connect((self.blockAnalogAgc, 0), (self.blockAnalogAMDemod, 0))
        
        self.connect((self.blockAnalogPowerSquelch, 0), (self.blockAnalogAgc, 0))

        if self.blockInputIntermediateFilter:
            self.connect((self.blockInputIntermediateFilter, 0), (self.blockAnalogPowerSquelch, 0))
            self.connect((self.blockFreqXlatingFilter, 0), (self.blockInputIntermediateFilter, 0))
        else:
            self.connect((self.blockFreqXlatingFilter, 0), (self.blockAnalogPowerSquelch, 0))

        self.connect((self, 0), (self.blockFreqXlatingFilter, 0))

        ###
        # RSSI Chain

        self.connect((self.blockRssiDecimate, 0), (self.blockRssi, 0))
        self.connect((self.blockRssiLowPassFilter, 0), (self.blockRssiDecimate, 0))
        self.connect((self.blockRssiComplexToMag2, 0), (self.blockRssiLowPassFilter, 0))
        if self.blockInputIntermediateFilter:
            self.connect((self.blockInputIntermediateFilter, 0), (self.blockRssiComplexToMag2, 0))
        else:
            self.connect((self.blockFreqXlatingFilter, 0), (self.blockRssiComplexToMag2, 0))


    def setAudioGain(self, dB: float):
        self.audioGainFactor = dbToRatio(dB)
        self.blockAudioGain.set_k(self.audioGainFactor * self.FIXED_AUDIO_GAIN_FACTOR)

    def setSquelchValue(self, squelchThreshold):
        self.squelchThreshold = squelchThreshold
        self.blockAnalogPowerSquelch.set_threshold(squelchThreshold)

    def getStatus(self, statusPipe):
        status = ChannelStatus.IDLE
        if self.blockAnalogPowerSquelch.unmuted():
            self._active = True
            self._lastActive = time.time()
            status = ChannelStatus.ACTIVE
        else:
            self._active = False
            if time.time() - self._lastActive < self._dwellTime:
                status = ChannelStatus.DWELL

        if status != self._lastStatusReport or (status != ChannelStatus.IDLE and (time.time() - self._lastStatusTime) > STATUS_UPDATE_TIME_S):
            self._lastStatusTime = time.time()
            self._lastStatusReport = status
            if statusPipe:
                statusPipe.send([{
                    'type': 'channel_status',
                    'data': {
                        'id': self.channelId,
                        'status': status,
                        'rssi': self._rssi,
                         'noiseFloor': self._noiseFloor_dBFS,
                    }
                }])

        return status



