from typing import List, Optional
import wx
import wx.dataview as dv

from .Channel import ChannelStatus
from .Scanner import Scanner


class ConfigListModel(dv.DataViewIndexListModel):
    def __init__(self, data):
        dv.DataViewIndexListModel.__init__(self, len(data))
        self.data = data

        self.channelIdToRow = {}
        self.rowStatus = {}

        for i in range(0, len(self.data)):
            cc = self.data[i]
            self.channelIdToRow[cc.id] = i
            self.rowStatus[i] = None

    def SetChannelStatus(self, channelId, status: ChannelStatus):
        rowId = self.channelIdToRow[channelId]
        item = self.GetItem(rowId)
        self.rowStatus[rowId] = status
        self.ItemChanged(item)

    def GetColumnType(self, col):
        return "string"

    # This method is called to provide the data object for a particular row,col
    def GetValueByRow(self, row, col):
        cc = self.data[row]
        if col == 0:
            return f"{cc.freq_hz/1e6:6.3f}"
        elif col == 1:
            return cc.label
        elif col == 2:
            return cc.mode.name
        elif col == 3:
            return str(cc.squelchThreshold)
        elif col == 4:
            return str(cc.dwellTime_s)
        elif col == 5:
            return str(cc.audioGain_dB)
        elif col == 6:
            return str(cc.id)

        raise Exception(f"Invalid col: {col}")

    # This method is called when the user edits a data item in the view.
    def SetValueByRow(self, value, row, col):
        raise NotImplementedError()

    # Report how many columns this model provides data for.
    def GetColumnCount(self):
        return 7

    # Report the number of rows in the model
    def GetCount(self):
        return len(self.data)

    # Called to check if non-standard attributes should be used in the cell at (row, col)
    def GetAttrByRow(self, row, col, attr):
        if self.rowStatus[row] == ChannelStatus.ACTIVE:
            attr.SetColour('green')
            # apparently only supported in wxGTK 4.1+ - use text color for now
            #attr.SetBackgroundColour('green')
            attr.SetBold(True)
            return True
        if self.rowStatus[row] == ChannelStatus.DWELL:
            attr.SetColour(wx.Colour(192, 192, 0))
            attr.SetBold(True)
            return True
        return False

    # This is called to assist with sorting the data in the view.  The
    # first two args are instances of the DataViewItem class, so we
    # need to convert them to row numbers with the GetRow method.
    # Then it's just a matter of fetching the right values from our
    # data set and comparing them.  The return value is -1, 0, or 1,
    # just like Python's cmp() function.
    def Compare(self, item1, item2, col, ascending):
        if not ascending: # swap sort order?
            item2, item1 = item1, item2
        row1 = self.GetRow(item1)
        row2 = self.GetRow(item2)
        a = self.GetValueByRow(row1, col)
        b = self.GetValueByRow(row2, col)
        if col in [0, 3, 4, 5]:
            a = float(a)
            b = float(b)
        if a < b: return -1
        if a > b: return 1
        return 0

    def DeleteRows(self, rows):
        # make a copy since we'll be sorting(mutating) the list
        # use reverse order so the indexes don't change as we remove items
        rows = sorted(rows, reverse=True)

        for row in rows:
            # remove it from our data structure
            del self.data[row]
            # notify the view(s) using this model that it has been removed
            self.RowDeleted(row)

    def AddRow(self, value):
        # update data structure
        self.data.append(value)
        # notify views
        self.RowAppended()


class ConfigDisplayFrame(wx.Frame):

    _instance = None
    _frameInitialized = False

    def __new__(cls, scanner: Scanner, *args, **kwargs):
        """
        Ensure only one instance of the Frame is created.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        elif not cls._instance:  # wx Dead object check
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self, scanner: Scanner, *args, **kw):

        if self._frameInitialized:
            return
        self._frameInitialized = True
        
        super().__init__(*args, **kw)

        self.panel = wx.Panel(self)

        self.sizer = wx.BoxSizer(wx.VERTICAL)

        ###
        # DataViewListCtrl Init

        self.dvlc = dv.DataViewListCtrl(self.panel, style=dv.DV_ROW_LINES | dv.DV_HORIZ_RULES)

        # Define the columns
        self.dvlc.AppendTextColumn(
            "Freq",
            width=75,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
        self.dvlc.AppendTextColumn("Label",
            width=150,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
        self.dvlc.AppendTextColumn(
            "Mode",
            width=75,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
        self.dvlc.AppendTextColumn(
            "Squelch",
            width=60,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
        self.dvlc.AppendTextColumn(
            "Dwell Time",
            width=60,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
        self.dvlc.AppendTextColumn(
            "Audio Gain",
            width=60,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
        self.dvlc.AppendTextColumn(
            "ID",
            width=250,
            flags=dv.DATAVIEW_COL_RESIZABLE | dv.DATAVIEW_COL_SORTABLE,
            mode=dv.DATAVIEW_CELL_INERT
        )
    
        # Build Data from Config
        channelData = []
        for cc in scanner.channelConfigs:
            channelData.append(cc)

        # Associate Model
        self.dataModel = ConfigListModel(channelData)
        self.dvlc.AssociateModel(self.dataModel)


        self.sizer.Add(self.dvlc, 1, wx.EXPAND | wx.ALL, 10)
        self.panel.SetSizer(self.sizer)
        self.Layout()

    def SetChannelStatus(self, channelId, status: ChannelStatus):
        self.dataModel.SetChannelStatus(channelId, status)

