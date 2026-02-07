# disable annoying INFO messages when connecting
import os
os.environ['SOAPY_SDR_LOG_LEVEL'] = 'WARNING'

import contextlib
from enum import IntEnum
from multiprocessing import shared_memory
import time
from typing import Any, Dict, List, Optional
import uuid

from gnuradio import audio
from gnuradio import gr
from gnuradio import soapy

from .const import AUDIO_SAMPLERATE, BFM_QUAD_RATE, FM_QUAD_RATE, MAX_RF_SAMPLERATE
from .AudioServer import AudioSender, AudioSender_grEmbeddedPythonBlock
from .Channel import Channel
from .ScanWindow import ScanWindow, ScanWindowConfig


class ReceiverType(IntEnum):
    UNKNOWN = 0
    RTL_SDR = 1
    SOAPY   = 2


class ReceiverStatus(IntEnum):
    IDLE = 0
    RUNNING_WINDOW = 1
    WINDOW_COMPLETE = 2
    FAILED = 3


class ReceiverBlock(gr.top_block):

    def __init__(self, receiverArgs) -> None:
        gr.top_block.__init__(self, "SDR Rx", catch_exceptions=True)

        self._receiverArgs = receiverArgs

        self.status: ReceiverStatus = ReceiverStatus.IDLE
        self._windowTimeout = 0.0
        self._scanWindow: Optional[ScanWindow] = None

        self._cachedSampleRates: Optional[List[int]] = None

    def setupWindow(self, scanWindow, audioSink) -> None:
        raise NotImplementedError()

    def teardownWindow(self, scanWindow, audioSink) -> None:
        raise NotImplementedError()

    def startWindow(self) -> None:
        if self._scanWindow is None:
            raise Exception("ScanWindow not configured")

        self.status = ReceiverStatus.RUNNING_WINDOW
        self.start()
        self._windowTimeout = time.time() + self._scanWindow.getMinimumScanTime()

    def stopWindow(self) -> None:
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

    def getSampleRates(self) -> List[int]:
        raise NotImplementedError()


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

    def __init__(self, receiverArgs):
        super().__init__(receiverArgs)

        self._receiverArgs = receiverArgs

        deviceArg = ''
        if 'deviceArg' in self._receiverArgs:
            deviceArg = self._receiverArgs['deviceArg']

        self.soapy_rtlsdr_source_0 = None
        dev = 'driver=rtlsdr'
        stream_args = ''
        tune_args = ['']
        settings = ['']

        self._gain = 20
        if 'gain' in self._receiverArgs:
            self._gain = self._receiverArgs['gain']

        self.soapy_rtlsdr_source_0 = soapy.source(dev, "fc32", 1, deviceArg, stream_args, tune_args, settings)
        self.soapy_rtlsdr_source_0.set_gain_mode(0, False)
        self.soapy_rtlsdr_source_0.set_frequency_correction(0, 0)
        self.soapy_rtlsdr_source_0.set_gain(0, 'TUNER', self._gain)

    def __str__(self):
        return f"RTL-SDR {self._receiverArgs}"

    def setupWindow(self, scanWindow, audioSink) -> None:

        self._scanWindow = scanWindow

        # tune radio to window center freq
        self.soapy_rtlsdr_source_0.set_sample_rate(0, scanWindow.rfSampleRate)
        self.soapy_rtlsdr_source_0.set_frequency(0, scanWindow.hardwareFreq_hz)

        # connect window to receiver and output audio
        self.connect( (scanWindow.scanWindowBlock, 0), (audioSink, 0) )
        self.connect( (self.soapy_rtlsdr_source_0, 0), (scanWindow.scanWindowBlock, 0) )

    def teardownWindow(self, scanWindow, audioSink) -> None:
        # disconnect
        self.disconnect( (self.soapy_rtlsdr_source_0, 0), (scanWindow.scanWindowBlock, 0) )
        self.disconnect( (scanWindow.scanWindowBlock, 0), (audioSink, 0) )

    def getSampleRates(self) -> List[int]:
        return self.SAMPLE_RATES


class Receiver_SOAPY(ReceiverBlock):

    def __init__(self, receiverArgs):
        super().__init__(receiverArgs)

        self._rxChannel = 0

        self._receiverArgs = receiverArgs

        self._deviceArg = ''
        if 'deviceArg' in self._receiverArgs:
            self._deviceArg = self._receiverArgs['deviceArg']

        self.blockSoapySource = None
        self._dev = f"driver={self._receiverArgs['driver']}"
        self._stream_args = ''
        self._tune_args = ['']
        self._settings = ['']

        self.blockSoapySource = None
        self._buildSourceBlock()

        self._rxGain = 20
        if 'gain' in receiverArgs:
            self._rxGain = receiverArgs['gain']

        self._rxGains = {}
        if 'gains' in receiverArgs:
            self._rxGains = receiverArgs['gains']

    def _buildSourceBlock(self) -> None:
        if self.blockSoapySource is None:
            self.blockSoapySource = soapy.source(self._dev, "fc32", 1, self._deviceArg, self._stream_args, self._tune_args, self._settings)
            if self.blockSoapySource is None:
                raise Exception("Failed Opening Receiver")

    def __str__(self):
        return f"SOAPY-SDR {self._receiverArgs}"

    def setupWindow(self, scanWindow, audioSink) -> None:

        self._buildSourceBlock()

        self._scanWindow = scanWindow

        # tune radio to window center freq
        self.blockSoapySource.set_sample_rate(0, scanWindow.rfSampleRate)
        self.blockSoapySource.set_frequency(0, scanWindow.hardwareFreq_hz)

        self.blockSoapySource.set_gain_mode(0, False)
        if self._rxGains:
            for name, gain in self._rxGains.items():
                self.blockSoapySource.set_gain(self._rxChannel, name, gain)
        else:
            self.blockSoapySource.set_gain(self._rxChannel, self._rxGain)

        self.blockSoapySource.set_frequency_correction(0, 0)

        # connect window to receiver and output audio
        self.connect( (scanWindow.scanWindowBlock, 0), (audioSink, 0) )
        self.connect( (self.blockSoapySource, 0), (scanWindow.scanWindowBlock, 0) )

    def teardownWindow(self, scanWindow, audioSink) -> None:
        # disconnect
        self.disconnect( (self.blockSoapySource, 0), (scanWindow.scanWindowBlock, 0) )
        self.disconnect( (scanWindow.scanWindowBlock, 0), (audioSink, 0) )
        self.blockSoapySource = None

    def getSampleRates(self) -> List[int]:
        
        if self._cachedSampleRates is not None:
            return self._cachedSampleRates

        rates = set()
        for rateRange in self.blockSoapySource.get_sample_rate_range(self._rxChannel):
            rates.add(rateRange.minimum())
            rates.add(rateRange.maximum())
        rates = {int(x) for x in rates}
        
        def _factors(i: int):
            """
            Returns a list of prime factors
            """
            if i <= 0:
                raise Exception("Can't handle <= 0")

            factors = []
            n = 2
            while n ** 2 <= i:
                while i % n == 0:
                    factors.append(n)
                    i //= n
                n += 1
        
            return factors

        # prefer rates we can divide down in to AUDIO_SAMPLERATE in multiple steps
        preferredRates = set()
        for rate in rates:
            if rate < MAX_RF_SAMPLERATE and len(_factors(rate)) >= 4 and rate % (AUDIO_SAMPLERATE) == 0:
                preferredRates.add(rate)
        if preferredRates:
            print(f"Receiver using preferred rates: {preferredRates}")
            rates = preferredRates

        self._cachedSampleRates = list(rates)
        return self._cachedSampleRates


def lookupRxType(rxTypeStr) -> ReceiverType:
    return {
        'RTL-SDR': ReceiverType.RTL_SDR,
        'SOAPY': ReceiverType.SOAPY,
    }.get(rxTypeStr.upper(), ReceiverType.UNKNOWN)

def lookupRxBlockCls(rxType: ReceiverType) -> type["ReceiverBlock"]:
    return {
        ReceiverType.RTL_SDR: Receiver_RTLSDR,
        ReceiverType.SOAPY: Receiver_SOAPY,
    }[rxType]


class Receiver():
    def __init__(self, rxId, rxType: ReceiverType, receiverArgs: Dict[str, Any]):
        self.id = rxId
        self.rxType = rxType
        self.receiverArgs = receiverArgs

        self._scanWindowsById: Dict[Any, ScanWindow] = {}

        self._receiverBlock = lookupRxBlockCls(self.rxType)(self.receiverArgs)

    def __str__(self):
        return f"Receiver: {str(self._receiverBlock)}"

    def getReceiverBlock(self) -> ReceiverBlock:
        return self._receiverBlock

    def applyConfigDict(self, configDict) -> None:
        self._scanWindowsById = {}

        for swData in configDict['scanWindows']:
            sw = ScanWindow.fromJson(swData, self._receiverBlock.getSampleRates())
            self._scanWindowsById[sw.id] = sw

    def getScanWindow(self, swId) -> ScanWindow:
        return self._scanWindowsById[swId]


class ReceiverConfig():

    def __init__(self, rxTypeStr: str, receiverArgs: Dict[str, Any]):
        self.id = str(uuid.uuid4())

        self.receiverArgs = receiverArgs
        if self.receiverArgs is None:
            self.receiverArgs = {}

        # Soapy Params
        self._rxChannel = 0

        self.rxType = lookupRxType(rxTypeStr)
        if self.rxType == ReceiverType.UNKNOWN:
            raise Exception(f"Unknown Receiver Type: '{rxTypeStr}'")
        self._rxBlockCls = lookupRxBlockCls(self.rxType)
        if not self._rxBlockCls:
            raise Exception(f"Block not found for Receiver Type: '{rxTypeStr}'")

        if self.rxType == Receiver_SOAPY:
            if 'driver' not in self.receiverArgs:
                raise Exception("Must provide 'driver' setting for SOAPY receiver type")

        self.scanWindowConfigs: List[ScanWindowConfig] = []
        self._scanWindowsById: Dict[Any, ScanWindow] = {}


def runAsProcess(pipe, receiverConfig: ReceiverConfig, audioShmBuffer: shared_memory.SharedMemory, headIdx: Any, tailIdx: Any):

#    with contextlib.redirect_stderr(None):
#        with contextlib.redirect_stdout(None):
            _runAsProcess(pipe, receiverConfig, audioShmBuffer, headIdx, tailIdx)

def _runAsProcess(pipe, receiverConfig: ReceiverConfig, audioShmBuffer: shared_memory.SharedMemory, headIdx: Any, tailIdx: Any):

    rx = Receiver(receiverConfig.id, receiverConfig.rxType, receiverConfig.receiverArgs)
    rxBlock = rx.getReceiverBlock()

    # On startup, send back our Receiver SampleRates
    pipe.send([{'type': 'sample_rates', 'data': rxBlock.getSampleRates()}])


    # blockAudioSink = audio.sink(AUDIO_SAMPLERATE, '', True)
    audioSender = AudioSender(audioShmBuffer, headIdx, tailIdx)
    blockAudioSink = AudioSender_grEmbeddedPythonBlock(audioSender)

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
                        rxBlock.teardownWindow(runningWindow, blockAudioSink)
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
                    rxBlock.setupWindow(scanWindow, blockAudioSink)
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
            rxBlock.teardownWindow(scanWindow, blockAudioSink)
            rxBlock.status = ReceiverStatus.IDLE

        time.sleep(0.001)

