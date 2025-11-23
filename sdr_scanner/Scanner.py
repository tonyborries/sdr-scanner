from multiprocessing import Pipe, Process
import time
from typing import List
import yaml

from sdr_scanner.Channel import ChannelConfig
from sdr_scanner.Receiver import ReceiverConfig, runAsProcess
from sdr_scanner.ScanWindow import ScanWindowConfig


AUDIO_SAMPLERATE = 16000


class Scanner():

    def __init__(self):

        self.channelConfigs: List[ChannelConfig] = []
        self._channelConfigByIdCache = {}

        self.scanWindowConfigs: List[ScanWindowConfig] = []

        self.receiverConfigs: List[ReceiverConfig] = []
        self._receiverProcesses = []  # tuples of (receiverConfig, receiver, pipe, process)

        self._defaultChannelConfig = ChannelConfig(0, 'DEFAULT')

        self._receiverCurrentScanWindow = {}
        self._windowLastScan = {}  # {windowId: time.time()}

        self._channelStatusCallbacks = []
        self._scanWindowStartCallbacks = []
        self._scanWindowDoneCallbacks = []

        self._stopFlag = False

    @classmethod
    def fromConfigFile(cls, configFilePath: str) -> "Scanner":
        with open(configFilePath, 'r') as F_CONFIG:
            configDict = yaml.safe_load(F_CONFIG)

            scanner = cls()

            ###
            # Receiver

            for rx in configDict['receivers']:
                rxTypeStr = rx['type']
                del rx['type']
                rxConfig = ReceiverConfig(rxTypeStr, rx, AUDIO_SAMPLERATE)
                scanner.receiverConfigs.append(rxConfig)

            ###
            # Channels

            if 'channel_defaults' in configDict:
                configDict['channel_defaults']['freq'] = 0
                scanner._defaultChannelConfig = ChannelConfig.fromConfigDict(configDict['channel_defaults'])

            for c in configDict['channels']:
#                freq_hz = c['freq'] * 1e6
#                kwargs = {k: v for k, v in c.items()}
#                if 'label' not in kwargs:
#                    kwargs['label'] = c['freq']
#                del kwargs['freq']
#                kwargs['freq_hz'] = freq_hz
#
#                if 'mode' in kwargs:
#                    kwargs['mode'] = ChannelConfig.modeStrLookup(kwargs['mode'])
#
#                cc = ChannelConfig(**kwargs)
                cc = ChannelConfig.fromConfigDict(c, scanner._defaultChannelConfig)

                scanner.channelConfigs.append(cc)
            scanner.buildWindows()

        return scanner

    def stop(self):
        self._stopFlag = True

    def addChannelStatusCb(self, fn):
        """
        fn
            callable that takes one argument, the 'channel_status' data dict
        """
        self._channelStatusCallbacks.append(fn)

    def addScanWindowStartCb(self, fn):
        self._scanWindowStartCallbacks.append(fn)

    def addScanWindowDoneCb(self, fn):
        self._scanWindowDoneCallbacks.append(fn)

    def getChannelById(self, channelId):
        if channelId in self._channelConfigByIdCache:
            return self._channelConfigByIdCache[channelId]
        for cc in self.channelConfigs:
            if cc.id == channelId:
                self._channelConfigByIdCache[channelId] = cc
                return cc
        return None

    def buildWindows(self):
        if not self.receiverConfigs:
            raise Exception("No Receivers Configured")
        bandwidth = min( [ max(r.getSampleRates()) for r in self.receiverConfigs] )

        BAND_EDGE_MARGIN = 200_000

        self.scanWindowConfigs = []

        freqsToAllocate = set([cc.freq_hz for cc in self.channelConfigs])
        while freqsToAllocate:
            lowFreq = sorted(freqsToAllocate)[0]
            hardwareFreq = lowFreq + bandwidth / 2 - BAND_EDGE_MARGIN
            highFreq = 2*hardwareFreq - lowFreq

            ccs = [cc for cc in self.channelConfigs if cc.freq_hz >= lowFreq and cc.freq_hz <= highFreq]
            for cc in ccs:
                freqsToAllocate.remove(cc.freq_hz)
            swc = ScanWindowConfig(hardwareFreq, bandwidth, AUDIO_SAMPLERATE, ccs)
            self.scanWindowConfigs.append(swc)

    def processReceiverMsg(self, receiverId, msg):
        for item in msg:
            if item['type'] == 'window_done':
                windowId = item['data']
                self._windowLastScan[windowId] = time.time()
                self._receiverCurrentScanWindow[receiverId] = None
                for cb in self._scanWindowDoneCallbacks:
                    cb(windowId)
            elif item['type'] == 'channel_status':
                for cb in self._channelStatusCallbacks:
                    cb(item['data'])

    def syncToReceivers(self):
        for receiver, pipe, process in self._receiverProcesses:
            pipe.send([
                {
                    'type': 'config',
                    'data': {
                        'scanWindows': self.scanWindowConfigs,
                    },
                }
            ])

    def getNextScanWindow(self):
        targetId = None
        targetTime = None
        runningWindows = self._receiverCurrentScanWindow.values()
        for sw in self.scanWindowConfigs:
            if sw.id not in runningWindows:
                if targetId is None:
                    targetId = sw.id
                    targetTime = self._windowLastScan.get(targetId, 0)
                else:
                    compTime = self._windowLastScan.get(sw.id, 0)
                    if compTime < targetTime:
                        targetId = sw.id
                        targetTime = compTime
        return targetId

    def runReceiverProcesses(self):

        ###
        # Init Receiver Processes

        for rxConfig in self.receiverConfigs:

            receiverPipe, remotePipe = Pipe()

            p = Process(target=runAsProcess, args=(remotePipe, rxConfig ))
            self._receiverProcesses.append( (rxConfig, receiverPipe, p) )
            p.start()
            self._receiverCurrentScanWindow[rxConfig.id] = None

        ###
        # Sync Config

        self.syncToReceivers()

        ###
        # Loop

        while True:

            for rxConfig, pipe, process in self._receiverProcesses:

                ###
                # Check if there are any messages in the Pipes

                if pipe.poll():
                    msg = pipe.recv()
                    self.processReceiverMsg(rxConfig.id, msg)

                ###
                # Assign ScanWindows

                if not self._receiverCurrentScanWindow[rxConfig.id]:
                    # Assign new window

                    nextWindowId = self.getNextScanWindow()
                    self._receiverCurrentScanWindow[rxConfig.id] = nextWindowId
                    pipe.send([{'type': 'scan_window', 'data': nextWindowId}])
                    for cb in self._scanWindowStartCallbacks:
                        cb(nextWindowId, rxConfig.id)

            time.sleep(0.001)

            if self._stopFlag:
                for rxConfig, pipe, process in self._receiverProcesses:
                    pipe.send([{'type': 'kill'}])
                    process.join()
                return


###
# Message Types

# Direction        'type'           data
# Scanner <-> RX
#     -->          scan_window       <WINDOW_ID>
#     <--          window_done       <WINDOW_ID>

#     <--          channel_status    {'id':<CHANNEL_ID>, status: <ChannelStatus Enum>, ['rssi': <RSSI>] }

