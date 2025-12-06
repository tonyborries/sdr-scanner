import datetime
from functools import partial
import os.path
import queue
import threading
import time
from typing import Any, Dict, List, Optional
import wx
import wx.dataview as dv

from .Channel import ChannelConfig, ChannelStatus
from .Scanner import Scanner
from .wxConfigDisplayFrame import ConfigDisplayFrame


scannerToUiQueue = queue.Queue()
uiToScannerQueue = queue.Queue()


class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()


    def stop(self) -> None:
        self._stop_event.set()

    def stopped(self) -> None:
        return self._stop_event.is_set()


class ScannerControlThread(StoppableThread):

    def __init__(self, scanner: Scanner, processScannerDataFn, *args, **kwargs):
        super().__init__(*args, **kwargs)

        ###
        # Init Scanner

        self.scanner = scanner

        self._parent_processScannerDataFn = processScannerDataFn

        self.scanner.addInputQueue(uiToScannerQueue)
        self.scanner.addOutputQueue(scannerToUiQueue)
        self.scanner.addProcessQueueCallback(self.processScannerDataCb)

    def run(self) -> None:
        self.scanner.runReceiverProcesses()

    def stop(self) -> None:
        super().stop()
        self.scanner.stop()

    def processScannerDataCb(self) -> None:
        wx.CallAfter(self._parent_processScannerDataFn)
        

class BasePanelManager():
    def __init__(self, parentPanel):
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

    def getPanel(self) -> wx.Panel:
        return self.panel


class RSSIDisplayPanelManager(BasePanelManager):
    BAR_WIDTH = 5
    BAR_SPACING = 3
    BAR_HEIGHT_STEP = 5

    LABEL_WIDTH = 70
    NOISEFLOOR_LABEL_WIDTH = LABEL_WIDTH + BAR_WIDTH * 4 + BAR_SPACING * 3

    VOLUME_PANEL_WIDTH = NOISEFLOOR_LABEL_WIDTH
    VOLUME_PANEL_HEIGHT = 10

    def __init__(self, parentPanel, channelSelectCb):
        super().__init__(parentPanel)

        self._channelSelectCb = channelSelectCb

        sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # RSSI Bars and Text

        rssiSizer = wx.BoxSizer(wx.HORIZONTAL)

        meterPanelWidth = self.BAR_WIDTH * 4 + self.BAR_SPACING * 3
        self.meterPanel = wx.Panel(self.panel, size=(meterPanelWidth, self.BAR_HEIGHT_STEP * 5))
        rssiSizer.Add(self.meterPanel, 0, wx.FIXED_MINSIZE | wx.ALL, 2)

        self.stLabel = wx.StaticText(
            self.panel,
            label="",
            size=(self.LABEL_WIDTH, -1)
        )
        font = self.stLabel.GetFont()
        font.PointSize -= 2
        self.stLabel.SetFont(font)
        rssiSizer.Add(self.stLabel, 0, wx.ALIGN_BOTTOM, 0)

        sizer.Add(rssiSizer, 0, 0, 0)

        ###
        # Noise Floor Text

        self.stNoiseFloor = wx.StaticText(
            self.panel,
            label="",
            size=(self.NOISEFLOOR_LABEL_WIDTH, -1)
        )
        font = self.stNoiseFloor.GetFont()
        font.PointSize -= 2
        self.stNoiseFloor.SetFont(font)
        sizer.Add(self.stNoiseFloor, 0, 0, 0)

        ###
        # Volume Bar

        self.volumePanel = wx.Panel(self.panel, size=(self.VOLUME_PANEL_WIDTH, self.VOLUME_PANEL_HEIGHT))
        sizer.Add(self.volumePanel, 0, wx.FIXED_MINSIZE | wx.ALL, 2)


        self.panel.SetSizer(sizer)

        # Capture clicking on the panel - have to bind to all items because MouseEvents don't propagate up
        self.panel.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        self.meterPanel.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        self.stLabel.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        self.stNoiseFloor.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        self.volumePanel.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)

        self.meterPanel.Bind(wx.EVT_PAINT, self.OnPaintRSSI)
        self.volumePanel.Bind(wx.EVT_PAINT, self.OnPaintVolume)

        self.rssi_dBFS: Optional[float] = None
        self.rssiOverThreshold: Optional[float] = None
        self.noiseFloor_dBFS: Optional[float] = None
        self._volume_dBFS: Optional[float] = -999.9

    def onMouseDown(self, event):
        self._channelSelectCb()
        event.Skip()

    def OnPaintRSSI(self, event):
        # Create a Device Context (DC) for painting the panel
        dc = wx.PaintDC(self.meterPanel)

        dc.SetPen(wx.Pen('black', 1, wx.SOLID))

        i = 0
        for db in [0, 10, 20, 30]:
            if self.rssiOverThreshold is not None and self.rssiOverThreshold > db:
                dc.SetBrush(wx.Brush('black', wx.SOLID))
            else:
                dc.SetBrush(wx.Brush('black', wx.BRUSHSTYLE_TRANSPARENT))

            x1 = (self.BAR_WIDTH + self.BAR_SPACING) * i
            x2 = self.BAR_WIDTH
            y1 = self.BAR_HEIGHT_STEP * (4-i)
            y2 = self.BAR_HEIGHT_STEP * (i+1)
            dc.DrawRectangle(x1, y1, x2, y2)

            i += 1

    def OnPaintVolume(self, event):
        # Create a Device Context (DC) for painting the panel
        dc = wx.PaintDC(self.volumePanel)

        # draw border
        dc.SetPen(wx.Pen('black', 1, wx.SOLID))
        dc.SetBrush(wx.Brush('black', wx.BRUSHSTYLE_TRANSPARENT))
        x1 = 0
        y1 = 0
        x2 =self.VOLUME_PANEL_WIDTH
        y2 = self.VOLUME_PANEL_HEIGHT
        dc.DrawRectangle(x1, y1, x2, y2)
        
        # draw volume
        minVal = -50
        maxVal = 0
        if self._volume_dBFS is not None and self._volume_dBFS > minVal:
            dc.SetPen(wx.Pen('black', 0, wx.SOLID))
            dc.SetBrush(wx.Brush('green', wx.SOLID))
            if self._volume_dBFS >= maxVal:
                x2 = self.VOLUME_PANEL_WIDTH
            else:
                x2 = int(self.VOLUME_PANEL_WIDTH * ((self._volume_dBFS - minVal) / (maxVal - minVal)))
            dc.DrawRectangle(x1, y1, x2, y2)

    def setRSSI(self, rssi: float, rssiOverThreshold: float, noiseFloor: Optional[float]):
        self.rssi_dBFS = rssi
        self.rssiOverThreshold = rssiOverThreshold
        self.noiseFloor_dBFS = noiseFloor
        self.stLabel.SetLabel(f"{self.rssi_dBFS:4.0f} dBFS")
        if noiseFloor is None:
            self.stNoiseFloor.SetLabel('')
        else:
            self.stNoiseFloor.SetLabel(f"Noise: {self.noiseFloor_dBFS:4.0f} dBFS")
        self.meterPanel.Refresh()

    def setVolume(self, volume_dBFS: Optional[float]):
        self._volume_dBFS = volume_dBFS
        self.volumePanel.Refresh()


class ChannelStripPanelManager(BasePanelManager):

    LABEL_WIDTH = 250
#    LABEL_HEIGHT = 40
    FREQ_WIDTH = 120

    DISPLAY_TIMEOUT_S = 15

    def __init__(self, parentPanel, channelConfig: ChannelConfig, channelSelectCb):
        super().__init__(parentPanel)
        self._channelSelectCb = channelSelectCb

        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.channelConfig = channelConfig

        ###
        # Label
        labelSizer = wx.BoxSizer(wx.VERTICAL)

        stLabel = wx.StaticText(
            self.panel,
            label=f"{channelConfig.label}",
            size=(self.LABEL_WIDTH, -1)
        )
        font = stLabel.GetFont()
        font.PointSize += 4
        font = font.Bold()
        stLabel.SetFont(font)
        labelSizer.Add(stLabel, 0, wx.ALL, 2)

        # Freq
        stFreq = wx.StaticText(
            self.panel,
            label=f"{channelConfig.freq_hz / 1e6:6.3f}",
            size=(self.FREQ_WIDTH, -1)
        )
        labelSizer.Add(stFreq, 0, wx.BOTTOM, 2)

        sizer.Add(labelSizer, 0, 0, 0)

        # RSSI
        self.rssiPM = RSSIDisplayPanelManager(self.panel, partial(self._channelSelectCb, channelConfig.id))
        sizer.Add(self.rssiPM.getPanel(), 0, wx.RESERVE_SPACE_EVEN_IF_HIDDEN, 0)

        # Mouse Click
        self.panel.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        stLabel.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        stFreq.Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)
        self.rssiPM.getPanel().Bind(wx.EVT_LEFT_DOWN, self.onMouseDown)

        self.panel.SetSizer(sizer)

        self._lastActive = 0.0
        self._lastStatus: Optional[ChannelStatus] = None
        self._isHidden = False

    def onMouseDown(self, event: wx.MouseEvent):
        self._channelSelectCb(self.channelConfig.id)
        event.Skip()

    def setRSSI(self, rssi: float, noiseFloor: Optional[float]):
        rssiOverThreshold = rssi - self.channelConfig.squelchThreshold
        self.rssiPM.setRSSI(rssi, rssiOverThreshold, noiseFloor)

    def setVolume(self, volume_dBFS: float):
        self.rssiPM.setVolume(volume_dBFS)

    def setChannelStatus(self, status: ChannelStatus):
        bgColor = wx.Colour(192, 192, 192)  # IDLE

        if status == ChannelStatus.ACTIVE:
            self._lastActive = time.time()
            bgColor = wx.Colour(0, 192, 0)
        elif status == ChannelStatus.DWELL:
            self._lastActive = time.time()
            bgColor = wx.Colour(192, 192, 0)
        elif status == ChannelStatus.HOLD:
            self._lastActive = time.time()
            bgColor = wx.Colour(192, 192, 0)
        elif status == ChannelStatus.FORCE_ACTIVE:
            self._lastActive = time.time()
            bgColor = wx.Colour(224, 96, 96)

        if status != self._lastStatus:
            self.panel.SetBackgroundColour(bgColor)
            self._lastStatus = status
            self.panel.Refresh()
            self.updateHiddenStatus()

    def channelConfigUpdated(self):
        self.panel.Refresh()
        self.updateHiddenStatus()

    def updateHiddenStatus(self):
        shouldHide = time.time() - self._lastActive > self.DISPLAY_TIMEOUT_S
        if shouldHide != self._isHidden:
            self._isHidden = shouldHide
            if shouldHide:
                self.panel.Hide()
            else:
                self.panel.Show()
            self.parentPanel.Layout()

    def runMaintenance(self):
        """
        Called periodically to see if the channel should be timed out and hidden
        """
        if self._lastStatus in [ChannelStatus.ACTIVE, ChannelStatus.FORCE_ACTIVE]:
            self._lastActive = time.time()
        self.updateHiddenStatus()


class ActiveChannelPanelManager(BasePanelManager):
    """
    Creates a Panel for displaying and managing the active Channels
    """
    def __init__(self, parentPanel, channelConfigs: List[ChannelConfig], channelSelectCb):
        self._channelSelectCb = channelSelectCb
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # Add Channels

        self.channelStripPanelManagersById: Dict[Any, ChannelStripPanelManager] = {}

        self.resetConfig(channelConfigs)

        self.panel.SetSizer(self.sizer)

    def resetConfig(self, channelConfigs: List[ChannelConfig]):
        """
        Called on init or whenever the Scanner Config changes.
        """
        # For now do a complete rebuild
        for cspm in self.channelStripPanelManagersById.values():
            cspm.getPanel().Destroy()
        self.channelStripPanelManagersById = {}

        for cc in channelConfigs:
            cspm = ChannelStripPanelManager(self.panel, cc, self._channelSelectCb)
            self.channelStripPanelManagersById[cc.id] = cspm
            self.sizer.Add(cspm.getPanel(), 0, 0, 0)

        self.panel.Layout()

    def setChannelRSSI(self, channelId, rssi: float, noiseFloor: Optional[float]):
        cspm = self.channelStripPanelManagersById.get(channelId)
        if not cspm:
            print("*** CHANNEL NOT FOUND - ActiveChannelPanelManager")
            return
        cspm.setRSSI(rssi, noiseFloor)

    def setChannelVolume(self, channelId, volume_dBFS: float):
        cspm = self.channelStripPanelManagersById.get(channelId)
        if not cspm:
            print("*** CHANNEL NOT FOUND - ActiveChannelPanelManager")
            return
        cspm.setVolume(volume_dBFS)

    def setChannelStatus(self, channelId, status: ChannelStatus):
        cspm = self.channelStripPanelManagersById.get(channelId)
        if not cspm:
            print("*** CHANNEL NOT FOUND - ActiveChannelPanelManager")
            return
        cspm.setChannelStatus(status)

    def channelConfigUpdated(self, channelId):
        cspm = self.channelStripPanelManagersById.get(channelId)
        if not cspm:
            print("*** CHANNEL NOT FOUND - ActiveChannelPanelManager")
            return
        cspm.channelConfigUpdated()

    def runMaintenance(self):
        for cspm in self.channelStripPanelManagersById.values():
            cspm.runMaintenance()


class ChannelConfigPanelManager(BasePanelManager):

    LABEL_WIDTH = 250
    FREQ_WIDTH = 120

    CMD_BUTTON_WIDTH = 30
    CMD_BUTTON_HEIGHT = 25

    def __init__(self, parentPanel, scanner: Scanner):
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.channelConfig: Optional[ChannelConfig] = None
        self._scanner = scanner

        ###
        # Label
        labelSizer = wx.BoxSizer(wx.VERTICAL)

        self.stLabel = wx.StaticText(
            self.panel,
            label="",
            size=(self.LABEL_WIDTH, -1)
        )
        font = self.stLabel.GetFont()
        font.PointSize += 4
        font = font.Bold()
        self.stLabel.SetFont(font)
        labelSizer.Add(self.stLabel, 0, wx.ALL, 2)

        # Freq
        self.stFreq = wx.StaticText(
            self.panel,
            label="",
            size=(self.FREQ_WIDTH, -1)
        )
        labelSizer.Add(self.stFreq, 0, wx.BOTTOM, 2)

        sizer.Add(labelSizer, 0, 0, 0)

        ###
        # Command Buttons

        self.btnHold = wx.ToggleButton(
            self.panel, label="H",
            size=(self.CMD_BUTTON_WIDTH,self.CMD_BUTTON_HEIGHT),
        )
        self.btnHold.SetToolTip("Hold")
        self.btnHold.Bind(wx.EVT_TOGGLEBUTTON, self.onBtnHold)
        sizer.Add(self.btnHold, 0, wx.ALL, 2)

        self.btnSolo = wx.ToggleButton(
            self.panel, label="S",
            size=(self.CMD_BUTTON_WIDTH,self.CMD_BUTTON_HEIGHT),
        )
        self.btnSolo.SetToolTip("Solo")
        self.btnSolo.Bind(wx.EVT_TOGGLEBUTTON, self.onBtnSolo)
        sizer.Add(self.btnSolo, 0, wx.ALL, 2)

        self.btnMute = wx.ToggleButton(
            self.panel, label="M",
            size=(self.CMD_BUTTON_WIDTH,self.CMD_BUTTON_HEIGHT),
        )
        self.btnMute.SetToolTip("Mute")
        self.btnMute.Bind(wx.EVT_TOGGLEBUTTON, self.onBtnMute)
        sizer.Add(self.btnMute, 0, wx.ALL, 2)

        self.btnDisable = wx.ToggleButton(
            self.panel,
            label="D",
            size=(self.CMD_BUTTON_WIDTH, self.CMD_BUTTON_HEIGHT),
        )
        self.btnDisable.SetToolTip("Disable")
        self.btnDisable.Bind(wx.EVT_TOGGLEBUTTON, self.onBtnDisable)
        sizer.Add(self.btnDisable, 0, wx.ALL, 2)

        self.btnDisable_1hr = wx.Button(
            self.panel,
            label="Disable 1 Hr",
            size=(-1, self.CMD_BUTTON_HEIGHT),
        )
        self.btnDisable_1hr.Bind(wx.EVT_BUTTON, self.onBtnDisable1Hr)
        sizer.Add(self.btnDisable_1hr, 0, wx.ALL, 2)

        self.btnPlay = wx.Button(
            self.panel,
            label="",
            size=(self.CMD_BUTTON_WIDTH, self.CMD_BUTTON_HEIGHT),
        )
        playImage = wx.Image(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img/play.png'), wx.BITMAP_TYPE_ANY)
        playBitmap = wx.Bitmap( playImage.Scale(self.CMD_BUTTON_HEIGHT // 2, self.CMD_BUTTON_HEIGHT // 2, wx.IMAGE_QUALITY_HIGH) ) 
        self.btnPlay.SetBitmap(playBitmap)
        self.btnPlay.SetToolTip("Force Active")
        self.btnPlay.Bind(wx.EVT_BUTTON, self.onBtnPlay)
        sizer.Add(self.btnPlay, 0, wx.ALL, 2)

        self.btnPause = wx.Button(
            self.panel,
            label="",
            size=(self.CMD_BUTTON_WIDTH, self.CMD_BUTTON_HEIGHT),
        )
        pauseImage = wx.Image(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img/pause.png'), wx.BITMAP_TYPE_ANY)
        pauseBitmap = wx.Bitmap( pauseImage.Scale(self.CMD_BUTTON_HEIGHT // 2, self.CMD_BUTTON_HEIGHT // 2, wx.IMAGE_QUALITY_HIGH) ) 
        self.btnPause.SetBitmap(pauseBitmap)
        self.btnPause.SetToolTip("Reset Squelch")
        self.btnPause.Bind(wx.EVT_BUTTON, self.onBtnPause)
        sizer.Add(self.btnPause, 0, wx.ALL, 2)

        self._defaultBtnBackgroundColor = self.btnDisable.GetBackgroundColour()

        self.panel.SetSizer(sizer)

    def resetConfig(self):
        if self.channelConfig is not None:
            cId = self.channelConfig.id
            cc = self._scanner.getChannelById(cId)
            if cc is not None:
                self.setChannel(cc)
            else:
                self.setChannel(self._scanner.channelConfigs[0])

    def channelConfigUpdated(self, channelId):
        if channelId == self.channelConfig.id:
            self.resetConfig()

    def setChannel(self, channelConfig: ChannelConfig):
        self.channelConfig = channelConfig
        self.stLabel.SetLabel(channelConfig.label)
        self.stFreq.SetLabel(f"{channelConfig.freq_hz / 1e6:6.3f}")

        self.btnHold.SetValue(channelConfig.hold)
        self.btnHold.SetBackgroundColour(wx.Colour('yellow') if channelConfig.hold else self._defaultBtnBackgroundColor)

        self.btnSolo.SetValue(bool(channelConfig.solo))
        self.btnSolo.SetBackgroundColour(wx.Colour('yellow') if channelConfig.solo else self._defaultBtnBackgroundColor)

        self.btnMute.SetValue(channelConfig.mute)
        self.btnMute.SetBackgroundColour(wx.Colour('red') if channelConfig.mute else self._defaultBtnBackgroundColor)
        
        disabled = not channelConfig.isEnabled()
        self.btnDisable.SetValue(disabled)
        self.btnDisable.SetBackgroundColour(wx.Colour('red') if disabled else self._defaultBtnBackgroundColor)

        forceActive = channelConfig.forceActive
        self.btnPlay.SetBackgroundColour(wx.Colour('red') if forceActive else self._defaultBtnBackgroundColor)

        self.panel.Refresh()

    def onBtnHold(self, event):
        hold = self.btnHold.GetValue()
        self._scanner.holdChannel(self.channelConfig.id, hold)

    def onBtnSolo(self, event):
        solo = self.btnSolo.GetValue()
        self._scanner.soloChannel(self.channelConfig.id, solo)

    def onBtnMute(self, event):
        mute = self.btnMute.GetValue()
        self._scanner.muteChannel(self.channelConfig.id, mute)

    def onBtnDisable(self, event):
        enabled = not self.btnDisable.GetValue()
        self._scanner.enableChannel(self.channelConfig.id, enabled)

    def onBtnDisable1Hr(self, event):
        self._scanner.disableChannelUntil(self.channelConfig.id, time.time() + 3600.0)

    def onBtnPlay(self, event):
        self._scanner.channelForceActive(self.channelConfig.id, not self.channelConfig.forceActive)

    def onBtnPause(self, event):
        self._scanner.channelForceActive(self.channelConfig.id, False)


class MainFrame(wx.Frame):

    def __init__(self, scanner: Scanner, *args, **kw):
        # ensure the parent's __init__ is called
        super().__init__(*args, **kw)

        self.configDisplayFrame = None

        ###
        # Setup Scanner

        self._scanner = scanner

        ##################################
        #                                #
        #            Build UI            #
        #                                #
        ##################################

        # create a panel in the frame
        self.panel = wx.Panel(self)

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # Menu Bar

        fileMenu = wx.Menu()
        # The "\t..." syntax defines an accelerator key that also triggers the same event
        # When using a stock ID we don't need to specify the menu item's label
        exitItem = fileMenu.Append(wx.ID_EXIT)

        windowMenu = wx.Menu()
        showConfigItem = windowMenu.Append(-1, "Show Config &Detail...\tCtrl-D")

        menuBar = wx.MenuBar()
        menuBar.Append(fileMenu, "&File")
        menuBar.Append(windowMenu, "&Window")

        # Give the menu bar to the frame
        self.SetMenuBar(menuBar)

        # Bind Menu Bar Events
        self.Bind(wx.EVT_MENU, self.onShowConfigFrame, showConfigItem)
        self.Bind(wx.EVT_MENU, self.OnExit,  exitItem)

        ###
        # Active Channel Manager

        self.activeChannelPanelManager = ActiveChannelPanelManager(self.panel, self._scanner.channelConfigs, self.channelSelect)
        self.sizer.Add(self.activeChannelPanelManager.getPanel(), 1, wx.TOP|wx.LEFT, 5)

        ###
        # ChannelConfigPanelManager

        self.channelConfigPanelManager = ChannelConfigPanelManager(self.panel, self._scanner)
        self.sizer.Add(self.channelConfigPanelManager.getPanel(), 0, wx.ALL, 2)
        self.channelConfigPanelManager.setChannel(self._scanner.channelConfigs[0])

        self.panel.SetSizer(self.sizer)

        ###
        # Misc Events

        self.Bind(wx.EVT_CLOSE, self.OnFrameClose)

        ##################################
        #                                #
        #         Launch Scanner         #
        #                                #
        ##################################

        self._scannerControlThread = ScannerControlThread(
            self._scanner,
            self.processScannerData,
        )
        self._scannerControlThread.start()

        ###
        # Maintenance Timer

        self.maintenanceTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.onMaintenanceTimer, self.maintenanceTimer)
        self.maintenanceTimer.Start(2000) # 2 seconds

    def onMaintenanceTimer(self, event):
        self.activeChannelPanelManager.runMaintenance()

    def resetConfig(self):
        self.activeChannelPanelManager.resetConfig(self._scanner.channelConfigs)
        
        self.channelConfigPanelManager.resetConfig()

        if self.configDisplayFrame:
            self.configDisplayFrame.resetConfig()
        self.Layout()

    def processScannerData(self):
        try:
            while True:
                data = scannerToUiQueue.get(False)
                if data['type'] == "ChannelStatus":
                    self.setChannelStatus(data["data"])
                elif data['type'] == "ScanWindowConfigsChanged":
                    self.resetConfig()
                elif data['type'] == "ChannelConfig":
                    self.channelConfigUpdated(data['data']['id'])

                scannerToUiQueue.task_done()
        except queue.Empty:
            pass

    def channelConfigUpdated(self, channelId):
        """
        Notification to UI elements that the indicated channel's config has updated
        """

        # Update Active Channel PanelMan
        self.activeChannelPanelManager.channelConfigUpdated(channelId)

        # Update Config PanelMan
        self.channelConfigPanelManager.channelConfigUpdated(channelId)

        # Update Config Display Frame
        if self.configDisplayFrame:
            self.configDisplayFrame.channelConfigUpdated(channelId)

    def setChannelStatus(self, data):
        print(data)

        volume_dBFS = data.get('volume')
        self.activeChannelPanelManager.setChannelVolume(data['id'], volume_dBFS)

        rssi = data.get('rssi')
        noiseFloor = data.get('noiseFloor')
        if rssi is not None:
            self.activeChannelPanelManager.setChannelRSSI(data['id'], rssi, noiseFloor)

        self.activeChannelPanelManager.setChannelStatus(data['id'], data['status'])
        
        # Update ConfigDisplay Frame
        if self.configDisplayFrame:
            self.configDisplayFrame.SetChannelStatus(data['id'], data['status'])

    def channelSelect(self, channelId):
        cc = self._scanner.getChannelById(channelId)
        if cc:
            self.channelConfigPanelManager.setChannel(cc)

    def onShowConfigFrame(self, event):
        self.configDisplayFrame = ConfigDisplayFrame(self._scanner, self.channelSelect, None, title="Scanner Config", size=(600,400))
        self.configDisplayFrame.Show()
        self.configDisplayFrame.Raise()
        self.configDisplayFrame.SetFocus()

    def OnExit(self, event):
        """Close the frame, terminating the application."""
        self.Close(True)

    def OnFrameClose(self, event):
        if self.configDisplayFrame:
            self.configDisplayFrame.Close(True)
        self._scannerControlThread.stop()
        self._scannerControlThread.join()

        event.Skip()

