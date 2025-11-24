from enum import IntEnum
import math
import time
from typing import Any, Dict, List, Optional
import uuid

from gnuradio import analog
from gnuradio import blocks
from gnuradio import filter as gr_filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window


class ChannelStatus(IntEnum):
    IDLE = 0
    ACTIVE = 1
    DWELL = 2

class ChannelMode(IntEnum):
    FM = 1
    NFM = 2


def dbToRatio(dB: float) -> float:
    return 10 ** (dB/20)


class ChannelConfig():
    def __init__(self, freq_hz: int, label: str, mode: ChannelMode=ChannelMode.FM, audioGain_dB: float=0, dwellTime_s: float=3.0, squelchThreshold:float=-55.0):

        self.id = uuid.uuid4()

        self.freq_hz = freq_hz
        self.label = label
        self.mode = mode

        self.dwellTime_s = dwellTime_s  # Time to wait after active before continuing scan
        self.audioGain_dB = audioGain_dB
        self.squelchThreshold = squelchThreshold

    @staticmethod
    def modeStrLookup(modeStr: str) -> Optional[ChannelMode]:
        return {
            "FM": ChannelMode.FM,
            "NFM": ChannelMode.NFM,
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
    def __init__(self, channelId, freq_hz: int, label: str, mode: ChannelMode, audioGain_dB: float, dwellTime_s: float, squelchThreshold:float, hardwareFreq_hz, audioSampleRate, rfSampleRate):

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
                audioSampleRate=audioSampleRate,
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
            audioSampleRate=swc.audioSampleRate,
            rfSampleRate=swc.rfSampleRate,
        )
        return channel

    def getStatus(self, statusPipe):
        return self.channelBlock.getStatus(statusPipe)


class ChannelBlock_FM(gr.hier_block2):
    def __init__(self, channelId, label: str, channelFreq_hz: int, hardwareFreq_hz: int, deviation_hz: int, audioGain_dB: float, squelchThreshold: float, dwellTime_s: float, audioSampleRate, rfSampleRate):
        gr.hier_block2.__init__(
            self, "FM_Channel",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_float*1),
        )

        self.channelId = channelId

        self._label = label
        self._deviation_hz = deviation_hz
        self._lastActive = 0
        self._active = False
        self._dwellTime = dwellTime_s
        self.audioGainFactor = dbToRatio(audioGain_dB)
        self.squelchThreshold = squelchThreshold

        self._lastStatusReport = None

        freqOffset_Hz = channelFreq_hz - hardwareFreq_hz

        ##################################################
        # Parameters
        ##################################################
        self.audioSampleRate = audioSampleRate
        self.rfSampleRate = rfSampleRate

        fmQuadRate = audioSampleRate * 4

        if self.rfSampleRate % fmQuadRate != 0:
            raise Exception(f"RF Sample Rate ({self.rfSampleRate}) is not a multiple of FM Quad Rate ({fmQuadRate})")

        inputDecimation = self.rfSampleRate // fmQuadRate

        def _filterDec(x):
            n = int(math.sqrt(x))
            while n > 1:
                if x % n == 0:
                    return n, x // n
                n -= 1
            return 1, x

        intermediateDecimation, xlatDecimation = _filterDec(inputDecimation)

        ##################################################
        # Blocks
        ##################################################

        ###
        # Input Channelization

        half_bandwidth = (self._deviation_hz + 3000)

        self.input_intermediate_filter = None
        if inputDecimation >= 8 and intermediateDecimation > 1:
            # Use an intermediate filter to spread out decimation, hopefully lowering CPU requirements

            self.freq_xlating_filter = gr_filter.freq_xlating_fft_filter_ccc(
                xlatDecimation,
                firdes.low_pass(1.0, self.rfSampleRate, self.rfSampleRate/(2*xlatDecimation), self.rfSampleRate/(4*xlatDecimation)),
                freqOffset_Hz,
                self.rfSampleRate
            )
            self.input_intermediate_filter = gr_filter.fft_filter_ccc(
                intermediateDecimation,
                firdes.low_pass(1, self.rfSampleRate/xlatDecimation, half_bandwidth, half_bandwidth/4),
                2
            )

        else:
            self.freq_xlating_filter = gr_filter.freq_xlating_fir_filter_ccc(
                int(self.rfSampleRate/fmQuadRate),
                firdes.low_pass(1.0, self.rfSampleRate, half_bandwidth, half_bandwidth/4),
                freqOffset_Hz,
                self.rfSampleRate
            )

        ###
        # Squelch and Demod

        self.analog_pwr_squelch_xx_0 = analog.pwr_squelch_cc(
            self.squelchThreshold,
            0.005,
            0,
            False
        )
        self.analog_nbfm_rx_0 = analog.nbfm_rx(
            audio_rate=audioSampleRate,
            quad_rate=fmQuadRate,
            tau=75e-6,
            max_dev=self._deviation_hz,
          )

        ###
        # Audio Filter

        self.audioFilter_0 = gr_filter.fft_filter_fff(
            1,
            firdes.band_pass(
                1,
                audioSampleRate,
                200,
                3500,
                100,
                window.WIN_HAMMING,
                6.76
            )
        )
        self.audioGain_0 = blocks.multiply_const_ff(self.audioGainFactor)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.audioGain_0, 0), (self, 0))
        self.connect((self.audioFilter_0, 0), (self.audioGain_0, 0))
        self.connect((self.analog_nbfm_rx_0, 0), (self.audioFilter_0, 0))
        self.connect((self.analog_pwr_squelch_xx_0, 0), (self.analog_nbfm_rx_0, 0))

        if self.input_intermediate_filter:
            self.connect((self.input_intermediate_filter, 0), (self.analog_pwr_squelch_xx_0, 0))
            self.connect((self.freq_xlating_filter, 0), (self.input_intermediate_filter, 0))
        else:
            self.connect((self.freq_xlating_filter, 0), (self.analog_pwr_squelch_xx_0, 0))

        self.connect((self, 0), (self.freq_xlating_filter, 0))

    def setAudioGain(self, dB: float):
        self.audioGainFactor = dbToRatio(dB)
        self.blocks_multiply_const_vxx_0.set_k(self.audioGainFactor)

    def setSquelchValue(self, squelchThreshold):
        self.squelchThreshold = squelchThreshold
        self.analog_pwr_squelch_xx_0.set_threshold(squelchThreshold)

    def getStatus(self, statusPipe):
        status = ChannelStatus.IDLE
        if self.analog_pwr_squelch_xx_0.unmuted():
            self._lastActive = time.time()
            status = ChannelStatus.ACTIVE
            if not self._active:
                self._active = True
#                print(f"\n{datetime.datetime.now().time().isoformat()} - {self._label}")
        else:
            self._active = False
            if time.time() - self._lastActive < self._dwellTime:
                status = ChannelStatus.DWELL

        if status != self._lastStatusReport:
            self._lastStatusReport = status
            if statusPipe:
                statusPipe.send([{
                    'type': 'channel_status',
                    'data': {
                        'id': self.channelId,
                        'status': status,
                        # 'rssi': <RSSI>
                    }
                }])

        return status



