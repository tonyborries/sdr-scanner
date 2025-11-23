import wx

from .wxMainFrame import MainFrame

from .Scanner import Scanner


def run(scanner: Scanner):

    wxApp = wx.App()

    mainFrame = MainFrame(scanner, None, title="wxSDRScanner", size=(600,400))
    mainFrame.Show()

    wxApp.MainLoop()

