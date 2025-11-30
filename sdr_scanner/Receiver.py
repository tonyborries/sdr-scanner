from enum import IntEnum
import sys
import signal
import time
from typing import Any, Dict, List
import uuid

from gnuradio import audio
from gnuradio import gr
from gnuradio import soapy

from .const import AUDIO_SAMPLERATE
from .Channel import Channel
from .ScanWindow import ScanWindow


class ReceiverType(IntEnum):
    UNKNOWN = 0
    RTL_SDR = 1


class ReceiverBlock(gr.top_block):

    def __init__(self):
        gr.top_block.__init__(self, "RTL-SDR Rx", catch_exceptions=True)

    def setupWindow(self, scanWindow, audioSink):
        raise NotImplementedError()

    def teardownWindow(self, scanWindow, audioSink):
        raise NotImplementedError()

    def runWindow(self, scanWindow, audioSink, statusPipe):
        # radio specific setup
        self.setupWindow(scanWindow, audioSink)

        # run for specified time
        def sig_handler(sig=None, frame=None):
            self.stop()
            self.wait()

            sys.exit(0)

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        self.timeoutflag = False

        self.start()

        windowTimeout = time.time() + scanWindow.getMinimumScanTime()

        try:
            #input('Press Enter to quit: ')
            while not self.timeoutflag:
                time.sleep(0.001)
                if not scanWindow.isActive(statusPipe) and time.time() > windowTimeout:
                    self.timeoutflag = True
        except EOFError:
            pass

        self.stop()
        self.wait()

        # radio specific teardown
        self.teardownWindow(scanWindow, audioSink)


class Receiver_RTLSDR(ReceiverBlock):

    SAMPLE_RATES = [
        # Make sure they decimate down evenly
        1_024_000,
        1_536_000,
        1_792_000,
        1_920_000,
        2_048_000,
#        2_560_000,  temp disable to force sticking with 2048
    ]

    def __init__(self, deviceArg=''):
        super().__init__()

        self.soapy_rtlsdr_source_0 = None
        dev = 'driver=rtlsdr'
        stream_args = ''
        tune_args = ['']
        settings = ['']

        self.deviceArg = deviceArg

        self.soapy_rtlsdr_source_0 = soapy.source(dev, "fc32", 1, self.deviceArg, stream_args, tune_args, settings)
        self.soapy_rtlsdr_source_0.set_gain_mode(0, False)
        self.soapy_rtlsdr_source_0.set_frequency_correction(0, 0)
        self.soapy_rtlsdr_source_0.set_gain(0, 'TUNER', 20)

    def __str__(self):
        return f"RTL-SDR {self.deviceArg}"

    def setupWindow(self, scanWindow, audioSink):

        # tune radio to window center freq
        self.soapy_rtlsdr_source_0.set_sample_rate(0, scanWindow.rfSampleRate)
        self.soapy_rtlsdr_source_0.set_frequency(0, scanWindow.hardwareFreq_hz)

        # connect window to receiver and output audio
        self.connect( (scanWindow.scanWindowBlock, 0), (audioSink, 0) )
        self.connect( (self.soapy_rtlsdr_source_0, 0), (scanWindow.scanWindowBlock, 0) )

    def teardownWindow(self, scanWindow, audioSink):
        # disconnect
        self.disconnect( (self.soapy_rtlsdr_source_0, 0), (scanWindow.scanWindowBlock, 0) )
        self.disconnect( (scanWindow.scanWindowBlock, 0), (audioSink, 0) )


def lookupRxType(rxTypeStr) -> ReceiverType:
    return {
        'RTL-SDR': ReceiverType.RTL_SDR
    }.get(rxTypeStr, ReceiverType.UNKNOWN)

def lookupRxBlockCls(rxType: ReceiverType) -> "ReceiverBlock":
    return {
        ReceiverType.RTL_SDR: Receiver_RTLSDR
    }.get(rxType)


class Receiver():
    def __init__(self, rxId, rxType: ReceiverType, receiverArgs: Dict[str, Any]):
        self.id = rxId
        self.rxType = rxType
        self.receiverArgs = receiverArgs

        self._scanWindowsById = {}

        self._receiverBlock = lookupRxBlockCls(self.rxType)(**self.receiverArgs)

    def __str__(self):
        return f"Receiver: {str(self._receiverBlock)}"

    def getReceiverBlock(self):
        return self._receiverBlock

    def applyConfigDict(self, configDict):
        self._scanWindowsById = {}

        for swc in configDict['scanWindows']:

            channels = []
            for cc in swc.channelConfigs:
                channels.append( Channel.fromConfig(cc, swc) )

            self._scanWindowsById[swc.id] = ScanWindow.fromConfig(swc)

        print("Config'd")

    def getScanWindow(self, swId):
        return self._scanWindowsById[swId]




class ReceiverConfig():

    def __init__(self, rxTypeStr: str, receiverArgs: Dict[str, Any]):
        self.id = uuid.uuid4()

        self.receiverArgs = receiverArgs
        if self.receiverArgs is None:
            self.receiverArgs = {}

        self.rxType = lookupRxType(rxTypeStr)
        if self.rxType == ReceiverType.UNKNOWN:
            raise Exception(f"Unknown Receiver Type: '{rxTypeStr}'")
        self._rxBlockCls = lookupRxBlockCls(self.rxType)
        if not self._rxBlockCls:
            raise Exception(f"Block not found for Receiver Type: '{rxTypeStr}'")

        self.scanWindowConfigs = []

        self._scanWindowsById = {}


    def getSampleRates(self):
        return Receiver_RTLSDR.SAMPLE_RATES



def runAsProcess(pipe, receiverConfig: ReceiverConfig):

#    with contextlib.redirect_stderr(None):
#        with contextlib.redirect_stdout(None):
            _runAsProcess(pipe, receiverConfig)

def _runAsProcess(pipe, receiverConfig: ReceiverConfig):


    rx = Receiver(receiverConfig.id, receiverConfig.rxType, receiverConfig.receiverArgs)

    rxBlock = rx.getReceiverBlock()

    audio_sink_0 = audio.sink(AUDIO_SAMPLERATE, '', True)

    while True:

        ###
        # Check for commands

        if pipe.poll():
            packet = pipe.recv()
            for item in packet:
                if item['type'] == 'config':
                    rx.applyConfigDict(item['data'])
                elif item['type'] == 'kill':
                    return
                elif item['type'] == 'scan_window':
                    windowId = item['data']
                    #print(f"Scanning window {windowId} on {str(rxBlock)}")
                    rxBlock.runWindow(rx.getScanWindow(windowId), audio_sink_0, pipe)
                    pipe.send([{'type': 'window_done', 'data': windowId}])


