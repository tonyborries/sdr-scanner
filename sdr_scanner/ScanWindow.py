from typing import Any, Dict, List, Optional
import uuid

from gnuradio import blocks
from gnuradio import gr
import gnuradio.filter as gr_filter

from .const import AUDIO_SAMPLERATE, BFM_QUAD_RATE
from .Channel import ChannelConfig, ChannelStatus, Channel


class ScanWindowConfig():
    def __init__(self, hardwareFreq_hz: int, rfBandwidth: int, channelConfigs: List[ChannelConfig]):
        """
        rfBandwidth
            The width of the ScanWindow in Hz. Note that receivers need to select a samplerate that is
            sufficient to cover this when instantiating their ScanWindow Objects.
        """
        self.id = str(uuid.uuid4())

        self.hardwareFreq_hz = hardwareFreq_hz
        self.rfBandwidth = rfBandwidth
        self.channelConfigs = channelConfigs

    def debugPrint(self):
        print(f"== ScanWindow: {self.hardwareFreq_hz / 1e6:6.3f} {self.rfBandwidth} {self.id} ==")
        for cc in sorted(self.channelConfigs, key=lambda x: x.freq_hz):
            cc.debugPrint()

    def getJson(self):
        return {
            "id": self.id,
            "hardwareFreq_hz": self.hardwareFreq_hz,
            "rfBandwidth": self.rfBandwidth,
            "channels": [ cc.getJson() for cc in self.channelConfigs ]
        }

class ScanWindow():
    """
    This object runs on the Receiver, built from the Config object
    """

    def __init__(self, swId, hardwareFreq_hz: int, rfSampleRate: int, audioSampleRate: int, channels: List[Channel]):

        self.id = swId

        self.hardwareFreq_hz = hardwareFreq_hz
        self.rfSampleRate = rfSampleRate
        self.channels = channels

        self._audioSampleRate = audioSampleRate

        # ScanWindowBlock that plugs into the radios
        self.scanWindowBlock = ScanWindowBlock(
            channels,
            self._audioSampleRate
        )

        self._minimumScanTime = None

    @classmethod
    def fromJson(cls, data: Dict[str, Any], rfSampleRates: List[int]) -> "ScanWindow":
        """
        rfSampleRates
            List of SampleRates available on the receiver instantiating this ScanWindow
        """
        hardwareFreq_hz = data['hardwareFreq_hz']

        # Select a samplerate sufficient for the ScanWindow
        rfBandwidth = data['rfBandwidth']
        rfSampleRate = min( [rate for rate in rfSampleRates if rate >= rfBandwidth] )

        ###
        # Determine what audioRate this window will operate at.

        audioSampleRate = None
        if rfSampleRate % AUDIO_SAMPLERATE == 0:
            # Ideal case, we can decimate direct down to audio
            audioSampleRate = AUDIO_SAMPLERATE
        else:
            # We'll have to resample the audio
            n = rfSampleRate // AUDIO_SAMPLERATE
            while n > 0:
                if rfSampleRate % n == 0:
                    audioSampleRate = rfSampleRate // n
                    break
                n -= 1
        if audioSampleRate is None:
            raise Exception(f"ScanWindow could not find suitable audio sample rate: ({rfSampleRate})")


        ###
        # Build the Channels

        channels = [ Channel.fromJson(cData, hardwareFreq_hz, rfSampleRate, audioSampleRate) for cData in data['channels'] ]
        sw = cls(
            data['id'],
            hardwareFreq_hz,
            rfSampleRate,
            audioSampleRate,
            channels,
        )
        return sw

    def isActive(self, statusPipe):
        active = False
        for channel in self.channels:
            if channel.getStatus(statusPipe) != ChannelStatus.IDLE:
                active = True
        return active

    def getMinimumScanTime(self):
        """
        return the minimum time this window needs to scan.
        e.g., EAS modes need enough samples to build up the FFT
        """
        if self._minimumScanTime is None:
            self._minimumScanTime = max( [c.getMinimumScanTime() for c in self.channels] )
        return self._minimumScanTime


class ScanWindowBlock(gr.hier_block2):
    """
    GNU Radio Block that gets 'plugged in' for scanning
    """
    def __init__(self, channels: List[Channel], audioSampleRate):
        gr.hier_block2.__init__(
            self, "ScanWindow",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_float*1),
        )

        # output mixer block
        self.mixerAddBlock = blocks.add_vff(1)

        channelIdx = 0
        for channel in channels:
            channelBlock = channel.channelBlock
            self.connect( (self, 0), (channelBlock, 0) )
            self.connect((channelBlock, 0), (self.mixerAddBlock, channelIdx))
            channelIdx += 1

        # Audio Output and resmapling

        self._audioSampleRate = audioSampleRate

        self.blockResampler = None

        if self._audioSampleRate == AUDIO_SAMPLERATE:
            # direct output
            self.connect( (self.mixerAddBlock, 0), (self, 0))
        else:
            # need to resample the audio stream to our global setting
            i = AUDIO_SAMPLERATE
            d = self._audioSampleRate
            n = 2
            while n < i:
                if i % n == 0 and d % n == 0:
                    i = i // n
                    d = d // n
                else:
                    n += 1
            print(f"ScanWindow resampling: int: {i} dec: {d}")

            self.blockResampler = gr_filter.rational_resampler_fff(
                interpolation=i,
                decimation=d,
                taps=gr_filter.firdes.low_pass(
                    1.0,
                    i, # Filter is designed relative to the *highest* sample rate it operates on
                    0.5 * min(1.0, float(i) / d),
                    0.05
                )
            )
            self.connect( (self.mixerAddBlock, 0), (self.blockResampler, 0))
            self.connect( (self.blockResampler, 0), (self, 0))
            
