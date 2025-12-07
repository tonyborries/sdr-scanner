import signal
import wx

from .wxMainFrame import MainFrame

from .Scanner import Scanner


def run(scanner: Scanner):

    wxApp = wx.App()

    mainFrame = MainFrame(scanner, None, title="wxSDRScanner", size=(600,400))
    mainFrame.Show()

    def signal_handler(sig, frame):
        print('SIGINT - Running cleanup...')
        mainFrame.Close()

    # Set the signal handler for SIGINT (Ctrl+C)
    signal.signal(signal.SIGINT, signal_handler)

    wxApp.MainLoop()

