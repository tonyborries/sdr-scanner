from typing import List
import uuid

from gnuradio import blocks
from gnuradio import gr

from .Channel import ChannelConfig, ChannelStatus, Channel


class ScanWindowConfig():
    def __init__(self, hardwareFreq_hz: int, rfSampleRate: int, audioSampleRate: int, channelConfigs: List[ChannelConfig]):

        self.id = uuid.uuid4()

        self.hardwareFreq_hz = hardwareFreq_hz
        self.rfSampleRate = rfSampleRate
        self.audioSampleRate = audioSampleRate
        self.channelConfigs = channelConfigs


class ScanWindow():
    """
    This object runs on the Receiver, built from the Config object
    """

    def __init__(self, swId, hardwareFreq_hz: int, rfSampleRate: int, audioSampleRate: int, channels: List[Channel]):

        self.id = swId

        self.hardwareFreq_hz = hardwareFreq_hz
        self.rfSampleRate = rfSampleRate
        self.channels = channels
        self.audioSampleRate = audioSampleRate


        # ScanWindowBlock that plugs into the radios
        self.scanWindowBlock = ScanWindowBlock(
            channels,
        )

    @classmethod
    def fromConfig(cls, swc: ScanWindowConfig):

        channels = []
        for cc in swc.channelConfigs:
            channels.append( Channel.fromConfig(cc, swc) )

        sw = cls(
            swc.id,
            hardwareFreq_hz=swc.hardwareFreq_hz,
            rfSampleRate=swc.rfSampleRate,
            audioSampleRate=swc.audioSampleRate,
            channels=channels
        )
        return sw

    def isActive(self, statusPipe):
        active = False
        for channel in self.channels:
            if channel.getStatus(statusPipe) != ChannelStatus.IDLE:
                active = True
        return active


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

