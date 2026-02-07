from multiprocessing import Pipe, Process
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import yaml

from .const import MAX_RF_SAMPLERATE
from .AudioServer import AudioServerConfig, AudioSender
from .Channel import ChannelConfig
from .Receiver import ReceiverConfig, runAsProcess
from .ScanWindow import ScanWindowConfig


class Scanner():
    MAINTENANCE_LOOP_TIME_S = 60

    def __init__(self, controlWebsocketHost: Optional[str] = None, controlWebsocketPort: Optional[int] = None) -> None:
        """
        controlWebsocketHost / controlWebsocketPort
            If both are specified, enable the Control Websocket
        """

        self.channelConfigs: List[ChannelConfig] = []
        self._channelConfigByIdCache: Dict[str, ChannelConfig] = {}

        self.scanWindowConfigs: List[ScanWindowConfig] = []

        self.receiverConfigs: List[ReceiverConfig] = []
        self._receiverProcesses = []  # tuples of (receiverConfig, receiver, pipe, process)
        self._receiverSampleRates: Dict[Any, List[int]] = {}

        self._defaultChannelConfig = ChannelConfig(0, 'DEFAULT')

        self._controlWsHost = controlWebsocketHost
        self._controlWsPort = controlWebsocketPort
        self._controlWsStopEvent: Optional[threading.Event] = None
        self._controlWsThread: Optional[threading.Thread] = None

        self._receiverCurrentScanWindow = {}
        self._windowLastScan = {}  # {windowId: time.time()}

        self._scanWindowConfigCallbacks = []

        self._inputQueues: List[queue.Queue] = []
        self._outputQueues: List[queue.Queue] = []
        self._processQueueCallbacks = []

        self.audioOutputConfigDicts: List[Dict[str, Any]] = []

        self.maxChannelsPerWindow = 16

        self.audioServerProcess: Optional[Process] = None

        self._stopFlag = False
        self._configDirty = False
        self._nextMaintenanceTime = 0.0

    @classmethod
    def fromConfigFile(cls, configFilePath: str, controlWebsocketHost: Optional[str] = None, controlWebsocketPort: Optional[int] = None) -> "Scanner":
        with open(configFilePath, 'r') as F_CONFIG:
            configDict = yaml.safe_load(F_CONFIG)

            scanner = cls(controlWebsocketHost, controlWebsocketPort)

            ###
            # Scanner

            scannerDict = configDict.get('scanner', {})
            if 'maxChannelsPerWindow' in scannerDict:
                scanner.maxChannelsPerWindow = scannerDict['maxChannelsPerWindow']

            ###
            # Audio Outputs
            if 'outputs' in configDict:
                scanner.audioOutputConfigDicts = configDict['outputs']

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

        return scanner

    def stop(self):
        self._stopFlag = True

    def addInputQueue(self, inQueue: queue.Queue):
        self._inputQueues.append(inQueue)

    def addOutputQueue(self, outQueue: queue.Queue):
        self._outputQueues.append(outQueue)

    def addProcessQueueCallback(self, cb):
        self._processQueueCallbacks.append(cb)

    def getChannelById(self, channelId: str) -> Optional[ChannelConfig]:
        if channelId in self._channelConfigByIdCache:
            return self._channelConfigByIdCache[channelId]
        for cc in self.channelConfigs:
            if cc.id == channelId:
                self._channelConfigByIdCache[channelId] = cc
                return cc
        return None

    ###################################################################
    #                                                                 #
    #                  Scanner Commands / UI Actions                  #

    def sendScannerMsg(self, msg):
        for oq in self._outputQueues:
            oq.put(msg)
        for cb in self._processQueueCallbacks:
            cb()

    def getJsonConfigMsg(self):
        return {
            'type': 'config',
            'data': {
                'scanWindows': [swc.getJson() for swc in self.scanWindowConfigs],
            },
        }

    def _checkInputQueues(self):
        """
        Check the Scanner input queues for commands / config updates
        """
        for iq in self._inputQueues:
            try:
                while True:
                    data = iq.get(False)
                    if data['type'] == "ChannelEnable":
                        channelId = data['data']['id']
                        enabled = bool(data['data']['enabled'])
                        self._channelEnable(channelId, enabled)
                    elif data['type'] == "ChannelMute":
                        channelId = data['data']['id']
                        mute = bool(data['data']['mute'])
                        self._channelMute(channelId, mute)
                    elif data['type'] == "ChannelSolo":
                        channelId = data['data']['id']
                        solo = bool(data['data']['solo'])
                        self._channelSolo(channelId, solo)
                    elif data['type'] == "ChannelHold":
                        channelId = data['data']['id']
                        hold = bool(data['data']['hold'])
                        self._channelHold(channelId, hold)
                    elif data['type'] == "ChannelDisableUntil":
                        channelId = data['data']['id']
                        disableUntil = float(data['data']['disableUntil'])
                        self._channelDisableUntil(channelId, disableUntil)
                    elif data['type'] == "ChannelForceActive":
                        channelId = data['data']['id']
                        forceActive = bool(data['data']['forceActive'])
                        self._channelForceActive(channelId, forceActive)


                    iq.task_done()
            except queue.Empty:
                pass

    def _channelEnable(self, channelId: str, enable: bool=True):
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")

        if cc.isEnabled() != enable:
            self._configDirty = True
        cc.enable(enable)

    def _channelDisableUntil(self, channelId: str, disableUntil: float):
        """
        disableUnitl
            (float) Unix time
        """
        if time.time() >= disableUntil:
            print("WARNING: disableUntil in past")
            return
        cc = self.getChannelById(channelId)
        if not cc:
            raise Exception(f"Channel '{channelId}' not found")

        if cc.isEnabled():
            self._configDirty = True
        cc.disableUntil(disableUntil)

    def _channelMute(self, channelId: str, mute):
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

    def _channelSolo(self, channelId: str, solo: bool):
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

    def _channelHold(self, channelId: str, hold):
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

    def _channelForceActive(self, channelId: str, forceActive=True):
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
        
    #                  Scanner Commands / UI Actions                  #
    #                                                                 #
    ###################################################################

    ###################################################################
    #                                                                 #
    #                Receiver Communicaiton / Control                 #

    def sendUpdatedChannelConfig(self, channelConfig):
        """
        Send an update notification to Receivers and UI
        """
        for oq in self._outputQueues:
            oq.put({
                'type': 'ChannelConfig',
                'data': channelConfig.getJson(),
            })

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
            elif item['type'] == 'sample_rates':
                self._receiverSampleRates[receiverId] = item['data']

    def syncToReceivers(self):
        for receiver, pipe, process in self._receiverProcesses:
            pipe.send([
                {
                    'type': 'config',
                    'data': {
                        'scanWindows': [swc.getJson() for swc in self.scanWindowConfigs],
                    },
                }
            ])

    #                Receiver Communicaiton / Control                 #
    #                                                                 #
    ###################################################################

    ###################################################################
    #                                                                 #
    #                          Scan Windows                           #

    def buildWindows(self):
        if not self.receiverConfigs:
            raise Exception("No Receivers Configured")

        bandwidth = min(
            [ max( 
                [rate for rate in self._receiverSampleRates[r.id] if rate <= MAX_RF_SAMPLERATE]
            ) for r in self.receiverConfigs ]
        )

        print(bandwidth)

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

    #                          Scan Windows                           #
    #                                                                 #
    ###################################################################

    def runMaintenance(self):
        self._nextMaintenanceTime = time.time()

        ###
        # Check if there are any channels that need re-enabled.
        for cc in self.channelConfigs:
            if cc._disableUntil is not None and time.time() > cc._disableUntil:
                self._channelEnable(cc.id, True)

    def runReceiverProcesses(self):

        ###
        # Launch Audio Server

        self.audioServerConfig = AudioServerConfig(numInputStreams=len(self.receiverConfigs), outputConfigDicts=self.audioOutputConfigDicts)
        self.audioServerProcess = self.audioServerConfig.getProcess()
        self.audioServerProcess.daemon = True
        self.audioServerProcess.start()

        ###
        # Start Control Websocket

        if self._controlWsHost and self._controlWsPort:
            from .ControlWeb import controlWebsocketRun

            self._controlWsStopEvent = threading.Event()
            self._controlWsThread = threading.Thread(
                target=controlWebsocketRun,
                daemon=True,
                args=(self, self._controlWsHost, self._controlWsPort, self._controlWsStopEvent))
            self._controlWsThread.start()

        while True:

            try:

                self._runReceivers()

                if self._configDirty:
                    self._configDirty = False
                    # self.buildWindows()
            except Exception as e:
                print(e)
                print("Killing Scanner")
                self._stopFlag = True

            if self._stopFlag:
                self.audioServerProcess.kill()
                self.audioServerConfig.cleanup()
                if self._controlWsStopEvent is not None:
                    self._controlWsStopEvent.set()
                return

    def _runReceivers(self):

        self._receiverProcesses = []
        self._receiverSampleRates = {}


        ###
        # Init Receiver Processes

        i = 0
        for rxConfig in self.receiverConfigs:

            receiverPipe, remotePipe = Pipe()

            p = Process(target=runAsProcess, daemon=True, args=(remotePipe, rxConfig, *self.audioServerConfig.getInputShmBuffers(i) ))
            self._receiverProcesses.append( (rxConfig, receiverPipe, p) )
            p.start()

            # Wait for the receiver to report back it's sample rates
            timeoutTime = time.time() + 10.0
            wait = True
            while wait:
                time.sleep(0.001)
                if time.time() > timeoutTime:
                    raise Exception("Timed out waiting for Receiver SampleRates")
                while receiverPipe.poll():
                    msg = receiverPipe.recv()
                    self.processReceiverMsg(rxConfig.id, msg)
                wait = rxConfig.id not in self._receiverSampleRates


            self._receiverCurrentScanWindow[rxConfig.id] = None
            i += 1


        # Now we can build the windows
        self.buildWindows()

        ###
        # Sync Config

        self.syncToReceivers()

        ###
        # Loop

        while True:

            ###
            # Check input queues
            
            self._checkInputQueues()

            ###
            # Run Maintenance

            if time.time() > self._nextMaintenanceTime:
                self.runMaintenance()

            ###
            # Check Receivers

            for rxConfig, pipe, process in self._receiverProcesses:

                # Check if there are any messages in the Pipes
                while pipe.poll():
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

            if not self.audioServerProcess.is_alive():
                print("ERROR: AudioServer Not Alive")
                self._stopFlag = True

            if self._stopFlag or self._configDirty:
                for rxConfig, pipe, process in self._receiverProcesses:
                    # pipe.send([{'type': 'kill'}])
                    # print("sent kill")
                    process.kill()
                    process.join()
                # if self._stopFlag:
                #     sys.exit(0)
                return


###
# Message Types

# Direction        'type'           data
# Scanner <-> RX
#     -->          scan_window       <WINDOW_ID>
#     <--          window_done       <WINDOW_ID>

#     <--          channel_status    {'id':<CHANNEL_ID>, status: <ChannelStatus Enum>, ['rssi': <RSSI>] }

