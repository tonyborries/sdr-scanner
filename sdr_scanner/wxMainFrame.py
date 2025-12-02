import datetime
import threading
import time
from typing import List, Optional
import wx
import wx.dataview as dv

from .Channel import ChannelConfig, ChannelStatus
from .Scanner import Scanner
from .wxConfigDisplayFrame import ConfigDisplayFrame


class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()


    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()


class ScannerControlThread(StoppableThread):

    def __init__(self, scanner: Scanner, channelStatusCb, scanWindowStartCb, scanWindowDoneCb, *args, **kwargs):
        super().__init__(*args, **kwargs)

        ###
        # Init Scanner

        self.scanner = scanner

        self.parent_channelStatusCb = channelStatusCb
        self.parent_scanWindowStartCb = scanWindowStartCb
        self.parent_scanWindowDoneCb = scanWindowDoneCb

        self.scanner.addChannelStatusCb(self.channelStatusCb)
        self.scanner.addScanWindowStartCb(self.scanWindowStartCb)
        self.scanner.addScanWindowDoneCb(self.scanWindowDoneCb)


    def run(self):
        self.scanner.runReceiverProcesses()

    def stop(self):
        super().stop()
        self.scanner.stop()

    def scanWindowStartCb(self, scanWindowId, rxId):
        wx.CallAfter(self.parent_scanWindowStartCb, scanWindowId, rxId)

    def scanWindowDoneCb(self, scanWindowId):
        wx.CallAfter(self.parent_scanWindowDoneCb, scanWindowId)

    def channelStatusCb(self, data):
        wx.CallAfter(self.parent_channelStatusCb, data)


class BasePanelManager():
    def getPanel(self):
        return self.panel


class RSSIDisplayPanelManager(BasePanelManager):
    BAR_WIDTH = 5
    BAR_SPACING = 3
    BAR_HEIGHT_STEP = 5

    LABEL_WIDTH = 70
    NOISEFLOOR_LABEL_WIDTH = LABEL_WIDTH + BAR_WIDTH * 4 + BAR_SPACING * 3

    VOLUME_PANEL_WIDTH = NOISEFLOOR_LABEL_WIDTH
    VOLUME_PANEL_HEIGHT = 10

    def __init__(self, parentPanel):
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

        sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # RSSI Bars and Text

        rssiSizer = wx.BoxSizer(wx.HORIZONTAL)

        meterPanelWidth = self.BAR_WIDTH * 4 + self.BAR_SPACING * 3
        self.meterPanel = wx.Panel(self.panel, size=(meterPanelWidth, self.BAR_HEIGHT_STEP * 5))
        rssiSizer.Add(self.meterPanel, 0, wx.FIXED_MINSIZE | wx.ALL, 2)

        self.stLabel = wx.StaticText(
            self.panel,
            label=f"",
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
            label=f"",
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

        self.meterPanel.Bind(wx.EVT_PAINT, self.OnPaintRSSI)
        self.volumePanel.Bind(wx.EVT_PAINT, self.OnPaintVolume)

        self.rssi_dBFS = -999
        self.rssiOverThreshold = -999
        self._volume_dBFS: Optional[float] = -999.9

    def OnPaintRSSI(self, event):
        # Create a Device Context (DC) for painting the panel
        dc = wx.PaintDC(self.meterPanel)

        dc.SetPen(wx.Pen('black', 1, wx.SOLID))

        i = 0
        for db in [0, 10, 20, 30]:
            if self.rssiOverThreshold > db:
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
    FREQ_WIDTH = 120

    DISPLAY_TIMEOUT_S = 15

    def __init__(self, parentPanel, channelConfig: ChannelConfig):
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

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
        self.rssiPM = RSSIDisplayPanelManager(self.panel)
        sizer.Add(self.rssiPM.getPanel(), 0, wx.RESERVE_SPACE_EVEN_IF_HIDDEN, 0)


        self.panel.SetSizer(sizer)

        self._lastActive = 0.0
        self._lastStatus: Optional[ChannelStatus] = None
        self._isHidden = False

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
            bgColor = wx.Colour(192, 192, 0)

        if status != self._lastStatus:
            self.panel.SetBackgroundColour(bgColor)
            self._lastStatus = status
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
        if self._lastStatus == ChannelStatus.ACTIVE:
            self._lastActive = time.time()
        self.updateHiddenStatus()



class ActiveChannelPanelManager(BasePanelManager):
    """
    Creates a Panel for displaying and managing the active Channels
    """
    def __init__(self, parentPanel, channelConfigs: List[ChannelConfig]):
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

        sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # Add Channels

        self.channelStripPanelManagersById = {}

        for cc in channelConfigs:
            cspm = ChannelStripPanelManager(self.panel, cc)
            self.channelStripPanelManagersById[cc.id] = cspm
            sizer.Add(cspm.getPanel(), 0, 0, 0)

        self.panel.SetSizer(sizer)

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

    def runMaintenance(self):
        for cspm in self.channelStripPanelManagersById.values():
            cspm.runMaintenance()


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
        #fileMenu.AppendSeparator()
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

        self.activeChannelPanelManager = ActiveChannelPanelManager(self.panel, self._scanner.channelConfigs)

        self.sizer.Add(self.activeChannelPanelManager.getPanel(), 0, wx.TOP|wx.LEFT, 5)

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
            self.channelStatusCb,
            self.scanWindowStartCb,
            self.scanWindowDoneCb
        )
        self._scannerControlThread.start()

        ###
        # Maintenance Timer

        self.maintenanceTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.onMaintenanceTimer, self.maintenanceTimer)
        self.maintenanceTimer.Start(2000) # 2 seconds


    def onMaintenanceTimer(self, event):
        self.activeChannelPanelManager.runMaintenance()

    def scanWindowStartCb(self, scanWindowId, rxId):
        pass

    def scanWindowDoneCb(self, scanWindowId):
        pass

    def channelStatusCb(self, data):
        print(data)

        volume_dBFS = data.get('volume')
        self.activeChannelPanelManager.setChannelVolume(data['id'], volume_dBFS)

        rssi = data.get('rssi')
        noiseFloor = data.get('noiseFloor')
        if rssi is not None:
            self.activeChannelPanelManager.setChannelRSSI(data['id'], rssi, noiseFloor)

        if data['status'] == ChannelStatus.ACTIVE:
            channel = self._scanner.getChannelById(data['id'])
            if channel:
                print(f"\n {datetime.datetime.now().time().isoformat()[0:8]}  {channel.label} ({channel.freq_hz/1e6})")
        self.activeChannelPanelManager.setChannelStatus(data['id'], data['status'])
        
        # Update ConfigDisplay Frame
        if self.configDisplayFrame:
            self.configDisplayFrame.SetChannelStatus(data['id'], data['status'])

    def onShowConfigFrame(self, event):
        self.configDisplayFrame = ConfigDisplayFrame(self._scanner, None, title="Scanner Config", size=(600,400))
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

