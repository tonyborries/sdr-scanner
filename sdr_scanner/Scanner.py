from multiprocessing import Pipe, Process
import queue
import sys
import time
from typing import List, Optional
import yaml

from .Channel import ChannelConfig
from .Receiver import ReceiverConfig, runAsProcess
from .ScanWindow import ScanWindowConfig


class Scanner():
    MAINTENANCE_LOOP_TIME_S = 60

    def __init__(self):

        self.channelConfigs: List[ChannelConfig] = []
        self._channelConfigByIdCache = {}

        self.scanWindowConfigs: List[ScanWindowConfig] = []

        self.receiverConfigs: List[ReceiverConfig] = []
        self._receiverProcesses = []  # tuples of (receiverConfig, receiver, pipe, process)

        self._defaultChannelConfig = ChannelConfig(0, 'DEFAULT')

        self._receiverCurrentScanWindow = {}
        self._windowLastScan = {}  # {windowId: time.time()}

        self._scanWindowConfigCallbacks = []

        self._inputQueues: List[queue.Queue] = []
        self._outputQueues: List[queue.Queue] = []
        self._processQueueCallbacks = []

        self.maxChannelsPerWindow = 16

        self._stopFlag = False
        self._configDirty = False
        self._nextMaintenanceTime = 0.0

    @classmethod
    def fromConfigFile(cls, configFilePath: str) -> "Scanner":
        with open(configFilePath, 'r') as F_CONFIG:
            configDict = yaml.safe_load(F_CONFIG)

            scanner = cls()

            ###
            # Scanner

            scannerDict = configDict.get('scanner', {})
            if 'maxChannelsPerWindow' in scannerDict:
                scanner.maxChannelsPerWindow = scannerDict['maxChannelsPerWindow']

            ###
            # Receiver

            for rx in configDict['receivers']:
                rxTypeStr = rx['type']
                del rx['type']
                rxConfig = ReceiverConfig(rxTypeStr, rx)
                scanner.receiverConfigs.append(rxConfig)

            ###
            # Channels

            if 'channel_defaults' in configDict:
                configDict['channel_defaults']['freq'] = 0
                scanner._defaultChannelConfig = ChannelConfig.fromConfigDict(configDict['channel_defaults'])

            for c in configDict['channels']:
                cc = ChannelConfig.fromConfigDict(c, scanner._defaultChannelConfig)

                scanner.channelConfigs.append(cc)
            scanner.buildWindows()

        return scanner

    def stop(self):
        self._stopFlag = True

    def addInputQueue(self, inQueue: queue.Queue):
        self._inputQueues.append(inQueue)

    def addOutputQueue(self, outQueue: queue.Queue):
        self._outputQueues.append(outQueue)

    def addProcessQueueCallback(self, cb):
        self._processQueueCallbacks.append(cb)

    def getChannelById(self, channelId) -> Optional[ChannelConfig]:
        if channelId in self._channelConfigByIdCache:
            return self._channelConfigByIdCache[channelId]
        for cc in self.channelConfigs:
            if cc.id == channelId:
                self._channelConfigByIdCache[channelId] = cc
                return cc
        return None

    def enableChannel(self, channelId, enable: bool=True):
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")
        if cc.isEnabled != enable:
            cc.enable(enable)
            self._configDirty = True

    def disableChannelUntil(self, channelId, disableUntil: float):
        if time.time() >= disableUntil:
            print("WARNING: disableUntil in past")
            return
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")
        cc.disableUntil = disableUntil
        self.enableChannel(channelId, False)

    def muteChannel(self, channelId, mute):
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")
        print(f"Set Channel Mute: {mute} {channelId}")
        cc.mute = mute

        for receiver, pipe, process in self._receiverProcesses:
            pipe.send([
                {
                    'type': 'ChannelMute',
                    'data': {
                        'id': cc.id,
                        'mute': mute,
                    }
                }
            ])
        self.sendUpdatedChannelConfig(cc)

    def soloChannel(self, channelId, solo: bool):
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")
        print(f"Set Channel Solo: {solo} {channelId}")
        cc.solo = solo

        soloActive = solo or any( c.solo for c in self.channelConfigs )

        for cc in self.channelConfigs:
            if soloActive:
                setSolo: Optional[bool] = bool(cc.solo)
            else:
                # Update all Channels to None
                cc.solo = None
                setSolo = None

            for receiver, pipe, process in self._receiverProcesses:
                pipe.send([
                    {
                        'type': 'ChannelSolo',
                        'data': {
                            'id': cc.id,
                            'solo': setSolo,
                        }
                    }
                ])
            self.sendUpdatedChannelConfig(cc)

    def holdChannel(self, channelId, hold):
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")
        print(f"Set Channel Hold: {hold} {channelId}")
        cc.hold = hold

        for receiver, pipe, process in self._receiverProcesses:
            pipe.send([
                {
                    'type': 'ChannelHold',
                    'data': {
                        'id': cc.id,
                        'hold': hold,
                    }
                }
            ])
        self.sendUpdatedChannelConfig(cc)

    def channelForceActive(self, channelId, forceActive=True):
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")
        print(f"Set Channel Force Active: {forceActive} {channelId}")
        cc.forceActive = forceActive

        for receiver, pipe, process in self._receiverProcesses:
            pipe.send([
                {
                    'type': 'ChannelForceActive',
                    'data': {
                        'id': cc.id,
                        'forceActive': forceActive,
                    }
                }
            ])
        self.sendUpdatedChannelConfig(cc)
        

    def sendUpdatedChannelConfig(self, channelConfig):
        """
        Send an update notification to Receivers and UI
        """
        for oq in self._outputQueues:
            oq.put({
                'type': 'ChannelConfig',
                'data': channelConfig.asConfigDict(),
            })

    def buildWindows(self):
        if not self.receiverConfigs:
            raise Exception("No Receivers Configured")
        bandwidth = min( [ max(r.getSampleRates()) for r in self.receiverConfigs] )

        BAND_EDGE_MARGIN = 200_000

        self.scanWindowConfigs = []

        freqsToAllocate = { cc.freq_hz for cc in self.channelConfigs if cc.isEnabled() }
        while freqsToAllocate:
            lowFreq = sorted(freqsToAllocate)[0]
            hardwareFreq = lowFreq + bandwidth / 2 - BAND_EDGE_MARGIN
            highFreq = 2*hardwareFreq - lowFreq

            ccs = [cc for cc in self.channelConfigs if cc.isEnabled() and cc.freq_hz >= lowFreq and cc.freq_hz <= highFreq]
            if len(ccs) > self.maxChannelsPerWindow:
                ccs = sorted(ccs, key=lambda x: x.freq_hz)[0:self.maxChannelsPerWindow]
            for cc in ccs:
                freqsToAllocate.remove(cc.freq_hz)
            swc = ScanWindowConfig(hardwareFreq, bandwidth, ccs)
            self.scanWindowConfigs.append(swc)

        for swc in self.scanWindowConfigs:
            swc.debugPrint()

        self.sendScannerMsg({
            "type": "ScanWindowConfigsChanged"
        })

    def sendScannerMsg(self, msg):
        for oq in self._outputQueues:
            oq.put(msg)
        for cb in self._processQueueCallbacks:
            cb()

    def processReceiverMsg(self, receiverId, msg):
        for item in msg:
            if item['type'] == 'window_done':
                windowId = item['data']
                self._windowLastScan[windowId] = time.time()
                self._receiverCurrentScanWindow[receiverId] = None
                self.sendScannerMsg({
                    "type": "ScanWindowDone",
                    "data": {
                        "id": windowId,
                    }
                })
            elif item['type'] == 'channel_status':
                self.sendScannerMsg({
                    "type": "ChannelStatus",
                    "data": item['data']
                })

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
        # Future:
        #   - Give priority to any windows with a Hold
        #   - Give priority to Windows with a Priority channel if an existing Window is Active on another receiver
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

    def runMaintenance(self):
        self._nextMaintenanceTime = time.time()

        ###
        # Check if there are any channels that need re-enabled.
        for cc in self.channelConfigs:
            if cc.disableUntil is not None and time.time() > cc.disableUntil:
                self.enableChannel(cc.id, True)

    def runReceiverProcesses(self):

        while True:

            self._runReceivers()

            if self._configDirty:
                self._configDirty = False
                self.buildWindows()

    def _runReceivers(self):

        self._receiverProcesses = []

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

            ###
            # Check input queues
            for iq in self._inputQueues:
                try:
                    while True:
                        data = iq.get(False)
                        if data['type'] == "ChannelEnable":
                            channelId = data['data']['id']
                            enabled = bool(data['data']['enabled'])
                            self.enableChannel(channelId, enabled)

                        iq.task_done()
                except queue.Empty:
                    pass

            ###
            # Run Maintenance

            if time.time() > self._nextMaintenanceTime:
                self.runMaintenance()

            ###
            # Check Receivers

            for rxConfig, pipe, process in self._receiverProcesses:

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
                    self.sendScannerMsg({
                        "type": "ScanWindowStart",
                        "data": {
                            "id": nextWindowId,
                            "rxId": rxConfig.id,
                        }
                    })

            time.sleep(0.001)

            if self._stopFlag or self._configDirty:
                for rxConfig, pipe, process in self._receiverProcesses:
                    pipe.send([{'type': 'kill'}])
                    print("sent kill")
                    process.join()
                    print("post join")
                if self._stopFlag:
                    sys.exit(0)
                return


###
# Message Types

# Direction        'type'           data
# Scanner <-> RX
#     -->          scan_window       <WINDOW_ID>
#     <--          window_done       <WINDOW_ID>

#     <--          channel_status    {'id':<CHANNEL_ID>, status: <ChannelStatus Enum>, ['rssi': <RSSI>] }

