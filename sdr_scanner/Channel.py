from enum import IntEnum
import math
import numpy as np
import time
from typing import cast, Any, Dict, List, Optional
import uuid

from gnuradio import analog
from gnuradio import blocks
from gnuradio import filter as gr_filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import logpwrfft, window

from .const import (
    NOISEFLOOR_LOWPASS_A,
    RSSI_LOWPASS_TC,
    RSSI_UPDATE_FREQ_HZ,
    STATUS_UPDATE_TIME_S,
    VOLUME_LOWPASS_ATTACK_TC,
    VOLUME_LOWPASS_DECAY_TC,
)

SQUELCH_TC = 0.0125


class ChannelStatus(IntEnum):
    IDLE = 0
    ACTIVE = 1
    DWELL = 2
    HOLD = 3
    FORCE_ACTIVE = 4


class ChannelMode(IntEnum):
    FM = 1
    NFM = 2
    AM = 3
    NOAA = 4
    BFM_EAS = 5
    USB = 6
    LSB = 7


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


class Mag2ToPower_EmbeddedPythonBlock(gr.sync_block):
    """
    Take the most recent input, convert to dBFS, and execute the provided callback.
    """

    def __init__(self, cb):
        gr.sync_block.__init__(
            self,
            name='Mag2ToPower Embedded Python Block',
            in_sig=[np.float32],
            out_sig=[]
        )
        self._cb = cb

    def work(self, input_items, output_items):
        val = input_items[0][-1]
        if val <= 0:
            dBFS = -150  # arbitrary lower bound
        else:
            dBFS = 10 * math.log10(input_items[0][-1])
        self._cb(dBFS)
        return len(input_items[0])


class MagToPowerLowPass_EmbeddedPythonBlock(gr.sync_block):
    """
    Calculate an averaged power for a signal stream, with separate alpha for
    attack and decay.

    Execute the callback with the latest value.
    """

    def __init__(self, cb, attackAlpha, decayAlpha):
        gr.sync_block.__init__(
            self,
            name='MagToPower Embedded Python Block',
            in_sig=[np.float32],
            out_sig=[]
        )
        self._cb = cb
        self._attackAlpha = attackAlpha
        self._decayAlpha = decayAlpha
        self._curMag2Avg = -150

    def work(self, input_items, output_items):

        for mag in input_items[0]:
            mag2 = mag ** 2
            if mag2 > self._curMag2Avg:
                self._curMag2Avg = (self._attackAlpha * mag2) + ((1 - self._attackAlpha) * self._curMag2Avg)
            else:
                self._curMag2Avg = (self._decayAlpha * mag2) + ((1 - self._decayAlpha) * self._curMag2Avg)

        if self._curMag2Avg <= 0:
            dBFS = -150  # arbitrary lower bound
        else:
            dBFS = 10 * math.log10(self._curMag2Avg)
        self._cb(dBFS)
        return len(input_items[0])


class ChannelConfig():
    def __init__(self, freq_hz: int, label: str, mode: ChannelMode=ChannelMode.FM, audioGain_dB: float=0, dwellTime_s: float=3.0, squelchThreshold:float=-55.0, mute:bool=False, solo:Optional[bool]=None, hold:bool=False):

        self.id = uuid.uuid4()

        self.freq_hz = freq_hz
        self.label = label
        self.mode = mode

        self.dwellTime_s = dwellTime_s  # Time to wait after active before continuing scan
        self.audioGain_dB = audioGain_dB
        self.squelchThreshold = squelchThreshold

        self._enabled = True
        self._disableUntil: Optional[float] = None
        self.mute = mute
        self.solo = solo
        self.hold = hold
        self.forceActive = False

    def enable(self, enable: bool=True):
        self._disableUntil = None
        self._enabled = enable

    def disableUntil(self, disableUntilTime: float):
        self._disableUntil = disableUntilTime
        self._enabled = False

    def isEnabled(self) -> bool:
        if self._disableUntil is not None:
            if time.time() > self._disableUntil:
                self._disableUntil = None
            return False
        return self._enabled

    def setSolo(self, solo: Optional[bool]):
        """
        Solo mode - overrides Mute
        Note: This is a tri-state setting

        True - This Channel is unmuted
        False - This Channel is muted
        None - Solo inactive - use Mute setting
        """
        self.solo = solo
        
    def setHold(self, hold):
        self.hold = hold

    def setForceActive(self, forceActive):
        self.forceActive = forceActive

    def debugPrint(self):
        print(f"    {self.freq_hz / 1e6:6.3f} {self.mode.name} {self.label}")

    @staticmethod
    def modeStrLookup(modeStr: str) -> Optional[ChannelMode]:
        return {
            "FM": ChannelMode.FM,
            "NFM": ChannelMode.NFM,
            "AM": ChannelMode.AM,
            "NOAA": ChannelMode.NOAA,
            "BFM_EAS": ChannelMode.BFM_EAS,
            'USB': ChannelMode.USB,
            'LSB': ChannelMode.LSB,
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
        kwargs['label'] = str(configDict.get('label', configDict['freq']))

        if defaultChannelConfig:
            kwargs['mode'] = defaultChannelConfig.mode
            kwargs['audioGain_dB'] = defaultChannelConfig.audioGain_dB
            kwargs['squelchThreshold'] = defaultChannelConfig.squelchThreshold
            kwargs['dwellTime_s'] = defaultChannelConfig.dwellTime_s

        if 'mode' in configDict:
            mode = cls.modeStrLookup(configDict['mode'])
            if mode is None:
                raise Exception(f"Unknown Mode \"{configDict['mode']}\"")
            kwargs['mode'] = mode
        for k in ['audioGain_dB', 'squelchThreshold', 'dwellTime_s']:
            if k in configDict:
                kwargs[k] = configDict[k]

        cc = ChannelConfig(**kwargs)
        return cc

    def getJson(self):
        return {
            'id': self.id,
            'freq_hz': self.freq_hz,
            'label': self.label,
            'mode': self.mode.name,
            'dwellTime_s': self.dwellTime_s,
            'audioGain_dB': self.audioGain_dB,
            'squelchThreshold': self.squelchThreshold,
            'enabled': self._enabled,
            'disableUntil': self._disableUntil,
            'mute': self.mute,
            'solo': self.solo,
            'hold': self.hold,
            'forceActive': self.forceActive,
        }


class Channel():
    def __init__(
            self,
            channelId,
            freq_hz: int,
            label: str,
            mode: ChannelMode,
            audioGain_dB: float,
            dwellTime_s: float,
            squelchThreshold:float,
            hardwareFreq_hz,
            rfSampleRate,
            audioSampleRate,
            mute,
            solo,
            hold,
            forceActive=False
        ):

        self.id = channelId

        self.freq_hz = freq_hz
        self.label = label
        self.mode = mode

        self.dwellTime_s = dwellTime_s  # Time to wait after active before continuing scan
        self.audioGain_dB = audioGain_dB
        self.squelchThreshold = squelchThreshold
        self._mute = mute
        self._solo = solo
        self._hold = hold
        self._forceActive = forceActive

        self.hardwareFreq_hz = hardwareFreq_hz

        ###
        # Build Channel Block based on Mode

        self.channelBlock = cast(ChannelBlock_Base, None)

        chArgs = [
            self.id,
            self.label,
            self._mute,
            self._solo,
            self._hold,
            self.squelchThreshold,
            self.audioGain_dB,
            self.dwellTime_s,
            self.freq_hz,
            self.hardwareFreq_hz,
            rfSampleRate,
            audioSampleRate
        ]

        if mode in [ChannelMode.FM, ChannelMode.NFM]:
            deviation = 5000
            if mode == ChannelMode.NFM:
                deviation = 2500

            self.channelBlock = ChannelBlock_FM(
                *chArgs,
                deviation,
            )

        elif mode == ChannelMode.AM:
            self.channelBlock = ChannelBlock_AM(
                *chArgs,
            )
        elif mode == ChannelMode.NOAA:
            self.channelBlock = ChannelBlock_EAS(
                *chArgs,
                deviation_hz=5000,
                alertTones=[1050],
            )
        elif mode == ChannelMode.BFM_EAS:
            self.channelBlock = ChannelBlock_EAS(
                *chArgs,
                deviation_hz=75000,
                alertTones=[853, 960],
            )
        elif mode == ChannelMode.USB:
            self.channelBlock = ChannelBlock_SSB(
                *chArgs,
                upperNotLowerSideband=True,
            )
        elif mode == ChannelMode.LSB:
            self.channelBlock = ChannelBlock_SSB(
                *chArgs,
                upperNotLowerSideband=False,
            )

        if self.channelBlock is None:
            raise Exception("Channel Block not Initialized - Check Mode setting")

        self.setForceActive(self._forceActive)

    @classmethod
    def fromJson(cls, data: Dict[str, Any], scanWindowHardwareFreq_hz: int, scanWindowRFSampleRate: int, scanWindowAudioSampleRate: int) -> "Channel":
        mode = ChannelConfig.modeStrLookup(data['mode'])
        if mode is None:
            raise Exception(f"Unknown Channel Mode Type: ({data['mode']})")

        channel = cls(
            channelId=data['id'],
            freq_hz=data['freq_hz'],
            label=data['label'],
            mode=mode,
            audioGain_dB=data['audioGain_dB'],
            dwellTime_s=data['dwellTime_s'],
            squelchThreshold=data['squelchThreshold'],
            hardwareFreq_hz=scanWindowHardwareFreq_hz,
            rfSampleRate=scanWindowRFSampleRate,
            audioSampleRate=scanWindowAudioSampleRate,
            mute=data['mute'],
            solo=data['solo'],
            hold=data['hold'],
            forceActive=data['forceActive'],
        )
        return channel

    def getStatus(self, statusPipe):
        return self.channelBlock.getStatus(statusPipe)

    def getMinimumScanTime(self):
        return self.channelBlock.getMinimumScanTime()

    def setMute(self, mute):
        self._mute = mute
        self.channelBlock.setMute(mute)

    def setSolo(self, solo: Optional[bool]):
        self._solo = solo
        self.channelBlock.setSolo(solo)

    def setHold(self, hold):
        self._hold = hold
        self.channelBlock.setHold(hold)

    def setForceActive(self, forceActive):
        self._forceActive = forceActive
        self.channelBlock.setForceActive(forceActive)


class ChannelBlock_Base(gr.hier_block2):

    # Used to normalize volume from different modes
    FIXED_AUDIO_GAIN_FACTOR = 1

    def __init__(
            self, 
            channelId,
            label: str,
            mute: bool,
            solo: Optional[bool],
            hold: bool,
            squelchThreshold: float,
            audioGain_dB: float,
            dwellTime_s: float,
            audioSampleRate: int,
            ):
        gr.hier_block2.__init__(
            self, "_Channel",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_float*1),
        )

        self.channelId = channelId
        self._label = label
        self._mute = mute
        self._solo = solo
        self._hold = hold
        self._forceActive = False
        self.squelchThreshold = squelchThreshold
        self.audioGainFactor = dbToRatio(audioGain_dB) * self.FIXED_AUDIO_GAIN_FACTOR
        self._dwellTime_s = dwellTime_s
        self._audioSampleRate = audioSampleRate

        self._active = False
        self._lastActive = 0.0
        self._lastStatusReport: Optional[ChannelStatus] = None
        self._lastStatusTime = 0.0

        self._rssi: Optional[float] = None
        self._noiseFloor_dBFS: Optional[float] = None
        self._volume_dBFS: Optional[float] = None

        ###
        # Output Mute

        # Audio out from the implementation specific blocks must connect to this.

        self.blockAudioMute = blocks.mute_ff(False)
        self.connect((self.blockAudioMute, 0), (self, 0))
        
        ###
        # Volume Blocks

        volumeLowpassAttackA = (1 / (self._audioSampleRate * VOLUME_LOWPASS_ATTACK_TC))
        volumeLowpassDecayA = (1 / (self._audioSampleRate * VOLUME_LOWPASS_DECAY_TC))
        self.blockVolume = MagToPowerLowPass_EmbeddedPythonBlock(self.updateVolume, volumeLowpassAttackA, volumeLowpassDecayA)

    def _connectVolume(self, sourceBlock, sourceBlockPort):
        """
        sourceBlock
            The output audio block
        sourceBlockPort
            The sourceBlock port to connect from
        """
        self.connect((sourceBlock, sourceBlockPort), (self.blockVolume, 0))

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

    def updateVolume(self, volume_dBFS: float):
        self._volume_dBFS = volume_dBFS

    def getMinimumScanTime(self):
        return 0.1

    def setMute(self, mute: bool=True):
        self._mute = mute

        finalMute = self._mute
        if self._solo is not None:
            if not self._solo:
                finalMute = True

        self.blockAudioMute.set_mute(finalMute)

    def setSolo(self, solo: Optional[bool]):
        self._solo = solo
        self.setMute(self._mute)

    def setHold(self, hold: bool):
        self._hold = hold

    def setForceActive(self, forceActive):
        raise NotImplementedError()


class ChannelBlock_FM(ChannelBlock_Base):
    def __init__(
            self,
            channelId,
            label: str,
            mute: bool,
            solo: Optional[bool],
            hold: bool,
            squelchThreshold: float,
            audioGain_dB: float,
            dwellTime_s: float,
            channelFreq_hz: int,
            hardwareFreq_hz: int,
            rfSampleRate: int,
            audioSampleRate: int,
            deviation_hz: int,
        ):
        super().__init__(
            channelId,
            label,
            mute,
            solo,
            hold,
            squelchThreshold,
            audioGain_dB,
            dwellTime_s,
            audioSampleRate,
        )

        self._deviation_hz = deviation_hz
        self.rfSampleRate = rfSampleRate

        ###
        # Find an FM Quad rate that we can divide down to

        self.fmQuadRate = self._audioSampleRate
        if self._deviation_hz > self._audioSampleRate:
            # BFM - need much wider bandwidth
            fmQuadMultiple = None
            n = math.ceil(200_000 / self._audioSampleRate)
            while fmQuadMultiple is None:
                if self.rfSampleRate % (self._audioSampleRate * n) == 0:
                    fmQuadMultiple = n
                if self.rfSampleRate < self._audioSampleRate * n:
                    raise Exception("Unable to find FM Quad Rate for BFM")
                n += 1
            self.fmQuadRate = self._audioSampleRate * fmQuadMultiple

        freqOffset_Hz = channelFreq_hz - hardwareFreq_hz

        if self.rfSampleRate % self.fmQuadRate != 0:
            raise Exception(f"RF Sample Rate ({self.rfSampleRate}) is not a multiple of FM Quad Rate ({self.fmQuadRate})")

        inputDecimation = self.rfSampleRate // self.fmQuadRate
        intermediateDecimation, xlatDecimation = _filterDec(inputDecimation)


        print("------")
        print(f"intermediateDecimation {intermediateDecimation}, xlatDecimation: {xlatDecimation}")
        print(f"FM Channel: rfSampleRate: {rfSampleRate} fmQuadRate: {self.fmQuadRate} _audioSampleRate: {self._audioSampleRate}")

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
                self.rfSampleRate // self.fmQuadRate,
                firdes.low_pass(1.0, self.rfSampleRate, half_bandwidth, half_bandwidth/4),
                freqOffset_Hz,
                self.rfSampleRate
            )

        ###
        # Squelch and Demod

        self.blockAnalogPowerSquelch = analog.pwr_squelch_cc(
            self.squelchThreshold,
            1 / (self.fmQuadRate * SQUELCH_TC),
            0,
            False
        )
        self.blockAnalogNbfmRx = analog.nbfm_rx(
            audio_rate=self._audioSampleRate,
            quad_rate=self.fmQuadRate,
            tau=75e-6,
            max_dev=self._deviation_hz,
          )

        ###
        # Audio Filter

        self.blockAudioFilter = gr_filter.fft_filter_fff(
            1,
            firdes.band_pass(
                1,
                self._audioSampleRate,
                200,
                3500,
                100,
                window.WIN_HAMMING,
                6.76
            )
        )
        self.blockAudioGain = blocks.multiply_const_ff(self.audioGainFactor)

        ###
        # RSSI Blocks

        self.blockRssiComplexToMag2 = blocks.complex_to_mag_squared(1)
        self.blockRssiLowPassFilter = gr_filter.single_pole_iir_filter_ff( (1 / (self.fmQuadRate * RSSI_LOWPASS_TC)), 1)
        self.blockRssiDecimate = blocks.keep_one_in_n(gr.sizeof_float*1, (self.fmQuadRate // RSSI_UPDATE_FREQ_HZ) )
        self.blockRssi = Mag2ToPower_EmbeddedPythonBlock(self.updateRSSI)

        ##################################################
        # Connections
        ##################################################

        ###
        # RF Chain

        self.connect((self.blockAudioGain, 0), (self.blockAudioMute, 0))
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

        # Volume
        self._connectVolume(self.blockAudioGain, 0)

    def setAudioGain(self, dB: float):
        self.audioGainFactor = dbToRatio(dB) * self.FIXED_AUDIO_GAIN_FACTOR
        self.blockAudioGain.set_k(self.audioGainFactor)

    def setSquelchValue(self, squelchThreshold):
        self.squelchThreshold = squelchThreshold
        self.blockAnalogPowerSquelch.set_threshold(squelchThreshold)

    def getStatus(self, statusPipe):
        status = ChannelStatus.HOLD if self._hold else ChannelStatus.IDLE
        if self.blockAnalogPowerSquelch.unmuted():
            self._active = True
            self._lastActive = time.time()
            if self._forceActive:
                status = ChannelStatus.FORCE_ACTIVE
            else:
                status = ChannelStatus.ACTIVE
        else:
            self._active = False
            if time.time() - self._lastActive < self._dwellTime_s:
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
                         'volume': self._volume_dBFS,
                    }
                }])

        return status

    def setForceActive(self, forceActive):
        self._forceActive = forceActive
        if forceActive:
            # Open Squelch
            self.blockAnalogPowerSquelch.set_threshold(-150.0)
        else:
            # Reset Squelch
            self.setSquelchValue(self.squelchThreshold)


class ChannelBlock_AM(ChannelBlock_Base):

    FIXED_AUDIO_GAIN_FACTOR = 3

    def __init__(
            self,
            channelId,
            label: str,
            mute: bool,
            solo: Optional[bool],
            hold: bool,
            squelchThreshold: float,
            audioGain_dB: float,
            dwellTime_s: float,
            channelFreq_hz: int,
            hardwareFreq_hz: int,
            rfSampleRate: int,
            audioSampleRate: int,
        ):
        super().__init__(
            channelId,
            label,
            mute,
            solo,
            hold,
            squelchThreshold,
            audioGain_dB,
            dwellTime_s,
            audioSampleRate,
        )

        freqOffset_Hz = channelFreq_hz - hardwareFreq_hz
        self.rfSampleRate = rfSampleRate

        if self.rfSampleRate % self._audioSampleRate != 0:
            raise Exception(f"RF Sample Rate ({self.rfSampleRate}) is not a multiple of Audio Sample Rate ({self._audioSampleRate})")

        inputDecimation = self.rfSampleRate // self._audioSampleRate
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
                int(self.rfSampleRate/self._audioSampleRate),
                firdes.low_pass(1.0, self.rfSampleRate, 4000, 2000),
                freqOffset_Hz,
                self.rfSampleRate
            )

        ###
        # Squelch and Demod

        self.blockAnalogPowerSquelch = analog.pwr_squelch_cc(
            self.squelchThreshold,
            1 / (self._audioSampleRate * SQUELCH_TC),
            0,
            False
        )

        self.blockAnalogAgc = analog.feedforward_agc_cc(int(self._audioSampleRate * 0.2), 0.5)

        self.blockAnalogAMDemod = blocks.complex_to_mag(1)

        ###
        # Audio

        self.blockAudioFilter = gr_filter.fft_filter_fff(
            1,
            firdes.band_pass(
                1,
                self._audioSampleRate,
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
        self.blockRssiLowPassFilter = gr_filter.single_pole_iir_filter_ff( (1 / (self._audioSampleRate * RSSI_LOWPASS_TC)), 1)
        self.blockRssiDecimate = blocks.keep_one_in_n(gr.sizeof_float*1, (self._audioSampleRate // RSSI_UPDATE_FREQ_HZ) )
        self.blockRssi = Mag2ToPower_EmbeddedPythonBlock(self.updateRSSI)


        ##################################################
        # Connections
        ##################################################

        ###
        # RF Chain

        self.connect((self.blockAudioGain, 0), (self.blockAudioMute, 0))
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

        # Volume
        self._connectVolume(self.blockAudioGain, 0)

    def setAudioGain(self, dB: float):
        self.audioGainFactor = dbToRatio(dB) * self.FIXED_AUDIO_GAIN_FACTOR
        self.blockAudioGain.set_k(self.audioGainFactor)

    def setSquelchValue(self, squelchThreshold):
        self.squelchThreshold = squelchThreshold
        self.blockAnalogPowerSquelch.set_threshold(squelchThreshold)

    def getStatus(self, statusPipe):
        status = ChannelStatus.HOLD if self._hold else ChannelStatus.IDLE
        if self.blockAnalogPowerSquelch.unmuted():
            self._active = True
            self._lastActive = time.time()
            if self._forceActive:
                status = ChannelStatus.FORCE_ACTIVE
            else:
                status = ChannelStatus.ACTIVE

        else:
            self._active = False
            if time.time() - self._lastActive < self._dwellTime_s:
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
                        'volume': self._volume_dBFS,
                    }
                }])

        return status

    def setForceActive(self, forceActive):
        self._forceActive = forceActive
        if forceActive:
            # Open Squelch
            self.blockAnalogPowerSquelch.set_threshold(-150.0)
        else:
            # Reset Squelch
            self.setSquelchValue(self.squelchThreshold)


class ToneDetect_EmbeddedPythonBlock(gr.sync_block):
    """
    Check for the existence of specific tones in the stream. If the Tone(s) are detected,
    execute the activeCb Callback.

    testIndexes
        The FFT Indexes of expected Tones
    refLowIndex / refHighIndex
        FFT Indexes of a reference passband to compare the Tone frequecies against.
    """

    def __init__(self, activeCb, testIndexes: List[int], refLowIndex: int, refHighIndex: int, fftSize: int):
        gr.sync_block.__init__(
            self,
            name='NOAA EAS Embedded Python Block',
            in_sig=[(np.float32, fftSize)],
            out_sig=[]
        )
        self.activeCb = activeCb
        self.testIndexes = testIndexes
        self.refLowIndex = refLowIndex
        self.refHighIndex = refHighIndex
        self.fftSize = fftSize

    def work(self, input_items, output_items):

        THRESHOLD = 20.0

        for inVec in input_items[0]:

            # Compute reference band power
            refPwr = max(inVec[self.refLowIndex: self.refHighIndex + 1])

            # Ensure each tone freq is above the threshold
            active = True
            for i in self.testIndexes:
#                print(f"{inVec[i-1]} {inVec[i]} {inVec[i+1]} {refPwr}")
                if inVec[i] - refPwr < THRESHOLD or inVec[i] < inVec[i-1] or inVec[i] < inVec[i+1]:
                    active = False
                    break
            self.activeCb(active)

        return len(input_items[0])


class ChannelBlock_EAS(ChannelBlock_Base):

    def __init__(
            self,
            channelId,
            label: str,
            mute: bool,
            solo: Optional[bool],
            hold: bool,
            squelchThreshold: float,
            audioGain_dB: float,
            dwellTime_s: float,
            channelFreq_hz: int,
            hardwareFreq_hz: int,
            rfSampleRate: int,
            audioSampleRate: int,
            deviation_hz: int,
            alertTones: List[int],
        ):
        super().__init__(
            channelId,
            label,
            mute,
            solo,
            hold,
            squelchThreshold,
            audioGain_dB,
            dwellTime_s,
            audioSampleRate,
        )

        self._triggerCount = 0
        self._alertTones = alertTones
        self._timeoutTime = 0.0

        ##################################################
        # Blocks
        ##################################################

        ###
        # FM Demodulator

        self.blockFM = ChannelBlock_FM(
            channelId,
            label,
            mute,
            solo,
            hold,
            squelchThreshold,
            audioGain_dB,
            dwellTime_s,
            channelFreq_hz,
            hardwareFreq_hz,
            rfSampleRate,
            audioSampleRate,
            deviation_hz,
        )

        ###
        # EAS Attention Tone Squelch

        FFT_SIZE = 1024

        self.blockLogPowerFFT = logpwrfft.logpwrfft_f(
            sample_rate=audioSampleRate,
            fft_size=FFT_SIZE,
            ref_scale=1,
            frame_rate=30,
            avg_alpha=1.0,
            average=False,
            shift=False
        )

        def _binNum(freq):
            return round(freq * FFT_SIZE / audioSampleRate)

        self.blockToneDetect = ToneDetect_EmbeddedPythonBlock(
            activeCb=self.activeCb,
            testIndexes=[_binNum(t) for t in self._alertTones],
            refLowIndex=_binNum(1100),
            refHighIndex=_binNum(1200),
            fftSize=FFT_SIZE
        )

        self.blockEASAudioMute = blocks.mute_ff(True)

        ##################################################
        # Connections
        ##################################################

        self.connect((self.blockLogPowerFFT, 0), (self.blockToneDetect, 0))
        self.connect((self.blockFM, 0), (self.blockLogPowerFFT, 0))

        self.connect((self.blockEASAudioMute, 0), (self.blockAudioMute, 0))
        self.connect((self.blockFM, 0), (self.blockEASAudioMute, 0))
        self.connect((self, 0), (self.blockFM, 0))
        
    def activeCb(self, isActive: bool):
        """
        Continually called by the Embedded Python Block to indicate if there is activity on the channel
        """

        if isActive:
            # Require 3 triggers in a row to activate - helps avoid false positives
            self._triggerCount += 1
            print(f"** EAS Trigger Count: {self._triggerCount}")
            if self._triggerCount >= 3:
                self.blockEASAudioMute.set_mute(False)
                self._active = True
                self._lastActive = time.time()
                self._timeoutTime = self._lastActive + self._dwellTime_s
        else:
            self._triggerCount = 0

    def getStatus(self, statusPipe):
        status = ChannelStatus.HOLD if self._hold else ChannelStatus.IDLE
        if self._active or self._forceActive:
            self._active = True
            if self._forceActive:
                status = ChannelStatus.FORCE_ACTIVE
            else:
                status = ChannelStatus.ACTIVE
                if time.time() > self._timeoutTime:
                    self._active = False
                    self.blockEASAudioMute.set_mute(True)
        elif self._triggerCount > 0:
            # in a pre-trigger state - keep the window active
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
                        'rssi': self.blockFM._rssi,
                        'noiseFloor': self.blockFM._noiseFloor_dBFS,
                        'volume': self.blockFM._volume_dBFS,
                    }
                }])

        return status

    def getMinimumScanTime(self):
        return 0.2

# TODO set Volume / Squelch

    def setForceActive(self, forceActive):
        self._forceActive = forceActive
        if forceActive:
            # Open Squelch
            self.blockFM.blockAnalogPowerSquelch.set_threshold(-150.0)
            self.blockEASAudioMute.set_mute(False)
            self._active = True
        else:
            # Reset Squelch
            self.blockFM.setSquelchValue(self.blockFM.squelchThreshold)
            self._timeoutTime = 0.0


class ChannelBlock_SSB(ChannelBlock_Base):

    FIXED_AUDIO_GAIN_FACTOR = 50

    def __init__(
            self,
            channelId,
            label: str,
            mute: bool,
            solo: Optional[bool],
            hold: bool,
            squelchThreshold: float,
            audioGain_dB: float,
            dwellTime_s: float,
            channelFreq_hz: int,
            hardwareFreq_hz: int,
            rfSampleRate: int,
            audioSampleRate: int,
            upperNotLowerSideband: bool,
        ):
        super().__init__(
            channelId,
            label,
            mute,
            solo,
            hold,
            squelchThreshold,
            audioGain_dB,
            dwellTime_s,
            audioSampleRate,
        )

        self.upperNotLowerSideband = upperNotLowerSideband

        self.rfSampleRate = rfSampleRate



        ###
        # Find an IF Freq that we can divide down to, and a suitable ifSampling Rate 

        ifMultiple = None
        ifSamplingRateMultiple = None

        n = math.ceil(20_000 / self._audioSampleRate)
        while ifMultiple is None:
            if self.rfSampleRate % (self._audioSampleRate * n) == 0:
                # Can we find an ifSamplingRate
                for nn in [3, 4, 5]:
                    if self.rfSampleRate % (self._audioSampleRate * n * nn) == 0:
                        ifMultiple = n
                        ifSamplingRateMultiple = n * nn
            if self.rfSampleRate < self._audioSampleRate * n:
                break
            n += 1
        if ifMultiple is None or ifSamplingRateMultiple is None:
            raise Exception("Unable to find Suitable IF Frequency")
        
        ifFreq = self._audioSampleRate * ifMultiple
        ifSampleRate = self._audioSampleRate * ifSamplingRateMultiple

        print(f"ifFreq: {ifFreq}  ifSampleRate: {ifSampleRate}")

        freqOffset_Hz = channelFreq_hz - hardwareFreq_hz - ifFreq

        if self.upperNotLowerSideband:
            ifPassbandLow = ifFreq
            ifPassbandHigh = ifFreq + 3000
        else:
            ifPassbandLow = ifFreq - 3000
            ifPassbandHigh = ifFreq

        if self.rfSampleRate % ifSampleRate != 0:
            raise Exception(f"RF Sample Rate ({self.rfSampleRate}) is not a multiple of IF Sample Rate ({ifSampleRate})")

        inputDecimation = self.rfSampleRate // ifSampleRate

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
                firdes.band_pass(1, self.rfSampleRate/xlatDecimation, ifPassbandLow, ifPassbandHigh, 1000),
                2
            )

        else:
            self.blockFreqXlatingFilter = gr_filter.freq_xlating_fir_filter_ccc(
                inputDecimation,
                firdes.band_pass(1.0, self.rfSampleRate, ifPassbandLow, ifPassbandHigh, 1000),
                freqOffset_Hz,
                self.rfSampleRate
            )

        ###
        # Squelch and Demod

        self.blockAnalogPowerSquelch = analog.pwr_squelch_cc(
            self.squelchThreshold,
            1 / (ifSampleRate * SQUELCH_TC),
            0,
            False
        )

        self.blockAnalogAgc = analog.agc2_cc(
            0.1,    # attack
            0.0001, # decay
            0.05,    # ref
            1.0,    # init gain
            
        )
        self.blockAnalogAgc.set_max_gain(3.0)

        self.blockComplexToReal = blocks.complex_to_real(1)

        # BFO and mixer
        self.blockIfOsc = analog.sig_source_f(ifSampleRate, analog.GR_COS_WAVE, ifFreq, 1, 0, 0)
        self.blockIfMultiply = blocks.multiply_vff(1)

        ###
        # Audio

        self.blockAudioFilter = gr_filter.fft_filter_fff(
            int(ifSampleRate / self._audioSampleRate),
            firdes.low_pass(
                1,
                ifSampleRate,
                3000,
                500,
                window.WIN_HAMMING,
                6.76
            )
        )
        self.blockAudioGain = blocks.multiply_const_ff(self.audioGainFactor)

        ###
        # RSSI

        self.blockRssiComplexToMag2 = blocks.complex_to_mag_squared(1)
        self.blockRssiLowPassFilter = gr_filter.single_pole_iir_filter_ff( (1 / (ifSampleRate * RSSI_LOWPASS_TC)), 1)
        self.blockRssiDecimate = blocks.keep_one_in_n(gr.sizeof_float*1, (ifSampleRate // RSSI_UPDATE_FREQ_HZ) )
        self.blockRssi = Mag2ToPower_EmbeddedPythonBlock(self.updateRSSI)


        ##################################################
        # Connections
        ##################################################

        ###
        # RF Chain

        self.connect((self.blockAudioGain, 0), (self.blockAudioMute, 0))
        self.connect((self.blockAudioFilter, 0), (self.blockAudioGain, 0))
        self.connect((self.blockIfMultiply, 0), (self.blockAudioFilter, 0))
        self.connect((self.blockIfOsc, 0), (self.blockIfMultiply, 1))
        self.connect((self.blockComplexToReal, 0), (self.blockIfMultiply, 0))
        self.connect((self.blockAnalogAgc, 0), (self.blockComplexToReal, 0))
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

        # Volume
        self._connectVolume(self.blockAudioGain, 0)

    def setAudioGain(self, dB: float):
        self.audioGainFactor = dbToRatio(dB) * self.FIXED_AUDIO_GAIN_FACTOR
        self.blockAudioGain.set_k(self.audioGainFactor)

    def setSquelchValue(self, squelchThreshold):
        self.squelchThreshold = squelchThreshold
        self.blockAnalogPowerSquelch.set_threshold(squelchThreshold)

    def getStatus(self, statusPipe):
        status = ChannelStatus.HOLD if self._hold else ChannelStatus.IDLE
        if self.blockAnalogPowerSquelch.unmuted():
            self._active = True
            self._lastActive = time.time()
            if self._forceActive:
                status = ChannelStatus.FORCE_ACTIVE
            else:
                status = ChannelStatus.ACTIVE
        else:
            self._active = False
            if time.time() - self._lastActive < self._dwellTime_s:
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
                        'volume': self._volume_dBFS,
                    }
                }])

        return status

    def setForceActive(self, forceActive):
        self._forceActive = forceActive
        if forceActive:
            # Open Squelch
            self.blockAnalogPowerSquelch.set_threshold(-150.0)
        else:
            # Reset Squelch
            self.setSquelchValue(self.squelchThreshold)

