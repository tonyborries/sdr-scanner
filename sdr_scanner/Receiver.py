# disable annoying INFO messages when connecting
import os
os.environ['SOAPY_SDR_LOG_LEVEL'] = 'WARNING'

import contextlib
from enum import IntEnum
import time
from typing import Any, Dict, List, Optional
import uuid

from gnuradio import audio
from gnuradio import gr
from gnuradio import soapy

from .const import AUDIO_SAMPLERATE
from .Channel import Channel
from .ScanWindow import ScanWindow, ScanWindowConfig


class ReceiverType(IntEnum):
    UNKNOWN = 0
    RTL_SDR = 1


class ReceiverStatus(IntEnum):
    IDLE = 0
    RUNNING_WINDOW = 1
    WINDOW_COMPLETE = 2
    FAILED = 3


class ReceiverBlock(gr.top_block):

    def __init__(self, deviceArg=''):
        gr.top_block.__init__(self, "RTL-SDR Rx", catch_exceptions=True)

        self.deviceArg = deviceArg

        self.status: ReceiverStatus = ReceiverStatus.IDLE
        self._windowTimeout = 0.0
        self._scanWindow: Optional[ScanWindow] = None

    def setupWindow(self, scanWindow, audioSink):
        raise NotImplementedError()

    def teardownWindow(self, scanWindow, audioSink):
        raise NotImplementedError()

    def startWindow(self):
        if self._scanWindow is None:
            raise Exception("ScanWindow not configured")

        self.status = ReceiverStatus.RUNNING_WINDOW
        self.start()
        self._windowTimeout = time.time() + self._scanWindow.getMinimumScanTime()

    def stopWindow(self):
        self.stop()
        self.wait()
        self.status = ReceiverStatus.IDLE

    def checkWindow(self, statusPipe) -> bool:
        """
        return True if the Window is active, False if it is done and stopped
        """
        if not self._scanWindow:
            return False
        if not self._scanWindow.isActive(statusPipe) and time.time() > self._windowTimeout:
            self.stopWindow()
            self.status = ReceiverStatus.WINDOW_COMPLETE
            return False
        return True


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
        super().__init__(deviceArg='')

        self.soapy_rtlsdr_source_0 = None
        dev = 'driver=rtlsdr'
        stream_args = ''
        tune_args = ['']
        settings = ['']

        self.soapy_rtlsdr_source_0 = soapy.source(dev, "fc32", 1, self.deviceArg, stream_args, tune_args, settings)
        self.soapy_rtlsdr_source_0.set_gain_mode(0, False)
        self.soapy_rtlsdr_source_0.set_frequency_correction(0, 0)
        self.soapy_rtlsdr_source_0.set_gain(0, 'TUNER', 20)

    def __str__(self):
        return f"RTL-SDR {self.deviceArg}"

    def setupWindow(self, scanWindow, audioSink):

        self._scanWindow = scanWindow

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

def lookupRxBlockCls(rxType: ReceiverType) -> type["ReceiverBlock"]:
    return {
        ReceiverType.RTL_SDR: Receiver_RTLSDR
    }[rxType]


class Receiver():
    def __init__(self, rxId, rxType: ReceiverType, receiverArgs: Dict[str, Any]):
        self.id = rxId
        self.rxType = rxType
        self.receiverArgs = receiverArgs

        self._scanWindowsById: Dict[Any, ScanWindow] = {}

        self._receiverBlock = lookupRxBlockCls(self.rxType)(**self.receiverArgs)

    def __str__(self):
        return f"Receiver: {str(self._receiverBlock)}"

    def getReceiverBlock(self):
        return self._receiverBlock

    def applyConfigDict(self, configDict):
        self._scanWindowsById = {}

        for swc in configDict['scanWindows']:
            self._scanWindowsById[swc.id] = ScanWindow.fromConfig(swc)

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

        self.scanWindowConfigs: List[ScanWindowConfig] = []
        self._scanWindowsById: Dict[Any, ScanWindow] = {}

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

    runningWindow = None
    while True:

        ###
        # Check for commands

        if pipe.poll():
            packet = pipe.recv()
            for item in packet:
                if item['type'] == 'config':
                    if rxBlock.status == ReceiverStatus.RUNNING_WINDOW:
                        rxBlock.stopWindow()
                        rxBlock.teardownWindow(runningWindow, audio_sink_0)
                    rx.applyConfigDict(item['data'])
                elif item['type'] == 'kill':
                    if rxBlock.status == ReceiverStatus.RUNNING_WINDOW:
                        rxBlock.stopWindow()
                    return
                elif item['type'] == 'scan_window':
                    windowId = item['data']
                    scanWindow = rx.getScanWindow(windowId)
                    runningWindow = scanWindow
                    if rxBlock.status != ReceiverStatus.IDLE:
                        raise Exception(f"Received new Scan Window {windowId} while not IDLE")
                    #print(f"Scanning window {windowId} on {str(rxBlock)}")
                    rxBlock.setupWindow(scanWindow, audio_sink_0)
                    rxBlock.startWindow()
                elif item['type'] == "ChannelMute":
                    ccId = item['data']['id']
                    mute = item['data']['mute']
                    for sw in rx._scanWindowsById.values():
                        for c in sw.channels:
                            if c.id == ccId:
                                c.setMute(mute)
                elif item['type'] == "ChannelSolo":
                    ccId = item['data']['id']
                    solo = item['data']['solo']
                    for sw in rx._scanWindowsById.values():
                        for c in sw.channels:
                            if c.id == ccId:
                                c.setSolo(solo)
                elif item['type'] == "ChannelHold":
                    ccId = item['data']['id']
                    hold = item['data']['hold']
                    for sw in rx._scanWindowsById.values():
                        for c in sw.channels:
                            if c.id == ccId:
                                c.setHold(hold)
                elif item['type'] == "ChannelForceActive":
                    ccId = item['data']['id']
                    forceActive = item['data']['forceActive']
                    for sw in rx._scanWindowsById.values():
                        for c in sw.channels:
                            if c.id == ccId:
                                c.setForceActive(forceActive)

        ###
        # Check Running Window

        if rxBlock.status == ReceiverStatus.RUNNING_WINDOW:
            rxBlock.checkWindow(pipe)

        # Cleanup from finished Window

        if rxBlock.status == ReceiverStatus.WINDOW_COMPLETE:
            if runningWindow is not None:
                pipe.send([{'type': 'window_done', 'data': runningWindow.id}])
                runningWindow = None
            rxBlock.teardownWindow(scanWindow, audio_sink_0)
            rxBlock.status = ReceiverStatus.IDLE

        time.sleep(0.001)

