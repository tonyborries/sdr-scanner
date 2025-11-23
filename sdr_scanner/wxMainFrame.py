import datetime
import threading
import time
from typing import List, Optional
import wx

from sdr_scanner.Channel import ChannelConfig, ChannelStatus
from sdr_scanner.Scanner import Scanner


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


class ChannelStripPanelManager(BasePanelManager):

    LABEL_WIDTH = 250
    FREQ_WIDTH = 120

    DISPLAY_TIMEOUT_S = 15

    def __init__(self, parentPanel, channelConfig: ChannelConfig):
        self.parentPanel = parentPanel
        self.panel = wx.Panel(parentPanel)

        sizer = wx.BoxSizer(wx.HORIZONTAL)

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

        self.panel.SetSizer(sizer)

        self._lastActive = 0.0
        self._lastStatus: Optional[ChannelStatus] = None
        self._isHidden = False

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
#        shouldHide = False
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


        ###
        # Setup Scanner

        self._scanner = scanner

        ###
        # Build UI

        # create a panel in the frame
        self.panel = wx.Panel(self)

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # Active Channel Manager

        self.activeChannelPanelManager = ActiveChannelPanelManager(self.panel, self._scanner.channelConfigs)

        self.sizer.Add(self.activeChannelPanelManager.getPanel(), 0, wx.TOP|wx.LEFT, 5)

        self.panel.SetSizer(self.sizer)


        ###
        # Misc Events

        self.Bind(wx.EVT_CLOSE, self.OnFrameClose)


        ###
        # Launch Scanner

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
        if data['status'] == ChannelStatus.ACTIVE:
            channel = self._scanner.getChannelById(data['id'])
            if channel:
                print(f"\n {datetime.datetime.now().time().isoformat()[0:8]}  {channel.label} ({channel.freq_hz/1e6})")
        self.activeChannelPanelManager.setChannelStatus(data['id'], data['status'])

    def OnExit(self, event):
        """Close the frame, terminating the application."""
        self.Close(True)

    def OnFrameClose(self, event):
        self._scannerControlThread.stop()
        self._scannerControlThread.join()

        event.Skip()

