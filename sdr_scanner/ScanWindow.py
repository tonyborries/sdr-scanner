from typing import List
import uuid

from gnuradio import blocks
from gnuradio import gr

from .Channel import ChannelConfig, ChannelStatus, Channel


class ScanWindowConfig():
    def __init__(self, hardwareFreq_hz: int, rfSampleRate: int, channelConfigs: List[ChannelConfig]):

        self.id = uuid.uuid4()

        self.hardwareFreq_hz = hardwareFreq_hz
        self.rfSampleRate = rfSampleRate
        self.channelConfigs = channelConfigs

    def debugPrint(self):
        print(f"== ScanWindow: {self.hardwareFreq_hz / 1e6:6.3f} {self.rfSampleRate} {self.id} ==")
        for cc in sorted(self.channelConfigs, key=lambda x: x.freq_hz):
            cc.debugPrint()


class ScanWindow():
    """
    This object runs on the Receiver, built from the Config object
    """

    def __init__(self, swId, hardwareFreq_hz: int, rfSampleRate: int, channels: List[Channel]):

        self.id = swId

        self.hardwareFreq_hz = hardwareFreq_hz
        self.rfSampleRate = rfSampleRate
        self.channels = channels

        # ScanWindowBlock that plugs into the radios
        self.scanWindowBlock = ScanWindowBlock(
            channels,
        )

        self._minimumScanTime = None

    @classmethod
    def fromConfig(cls, swc: ScanWindowConfig):

        channels = []
        for cc in swc.channelConfigs:
            channels.append( Channel.fromConfig(cc, swc) )

        sw = cls(
            swc.id,
            hardwareFreq_hz=swc.hardwareFreq_hz,
            rfSampleRate=swc.rfSampleRate,
            channels=channels
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
    def __init__(self, channels: List[Channel]):
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

        self.connect( (self.mixerAddBlock, 0), (self, 0))

