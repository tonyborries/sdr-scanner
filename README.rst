
This is intended to be a general purpose communications scanner for SDR Radios. The scanner is
configured with a list of Channels and scans them for activity.

Unique from similar projects, this scanner builds a set of 'ScanWindows' where it can monitor
multiple Channels at once. For example, an RTL-SDR radio can monitor a 2+ MHz bandwidth, so we
can efficiently monitor all channels in the 146-148 MHz Amateur band at once.

Another unique feature is support for multiple receivers, both to speed up the scan loop time and so
that scanning can continue while a Channel is active.


Current Status
==============

This is in a working but early stage. 

Currently only support for RTL-SDR radios is enabled.


Architecture Overview
=====================

The application is ran with multiple processes, with one process for the Control / UI and an additional
process for each Receiver. The Control process continuously assigns scan tasks to the Receivers and
collects Channel status from them.


Config File
===========

The default config filename is 'sdrscan.yaml'

Minimal Example

::

    receivers:
      - type: RTL-SDR

    channel_defaults:
      squelchThreshold: -55

    channels:

      - freq: 146.52

      - freq: 144.39
        label: APRS
        audioGain_dB: 3.0
        dwellTime_s: 6.0
        squelchThreshold: -60

      - freq: 467.5625
        label: GMRS 8
        mode: NFM

Channels
--------

Channels are defined as entries under the 'channels' key, with the following keys available to specify:

- freq: (Required) Frequency in MHz.
- label: (Optional) Text label used when displaying the channel.
- mode: (Optional, default FM) FM, NFM, AM
- audioGain_dB: (Optional, default 0.0) Gain applied to the demodulated audio
- dwellTime_s: (Optional) Overrides the time spent monitoring the Channel after the last received activity.
- squelchThreshold: (Optional) Overrides the default squelch threshold.

The 'channel_defaults' allows overriding the default values for:

- mode
- audioGain_dB
- dwellTime_s
- squelchThreshold


Multiple Receivers
------------------

Multiple Receivers can be enabled in the config.

For RTL-SDR, Unique serial numbers must be assigned to each (see 'rtl_eeprom').

::

    receivers:
      - type: RTL-SDR
        deviceArg: serial=00000001
      - type: RTL-SDR
        deviceArg: serial=00000002


Installation
============

Install 'gnuradio' and optionally 'wxPython' to enable using the GUI mode.

::

    # Apt Based Systems
    apt-get install gnuradio python3-wxgtk4.0



Running
=======

CLI Mode
--------

A minimalist CLI app is provided.

    python3 cli_scan.py

To hide error and debug messages, it's generally useful to redirect stderr to null

    python3 cli_scan.py 2>/dev/null


GUI Mode
--------

A wxPython app provides a GUI for the scanner.

    python3 gui_scan.py


The interface display recently active channels. The background color indicates:

- **Green:** Currently active.
- **Yellow:** Recently active, Dwelling on the Channel.
- **Grey:** Recently active, scanning has resumed.

The Signal Strength bars indicate the signal strength above the Channel's Squelch Threshold. Each bar
is 10 dB, so the first bar indicates 0-10dB above the squelch, the second 10-20dB, etc...

The Noise Floor indication uses a long running average - since the Channels are scanned for only brief
periods, it may take a while for this value to stabilize. (Currently uses a 60 second time constant,
but may tune this in the future.)

Channel Modes
=============

Supported Modes:

- AM
- FM
- NFM

FM / NFM
--------

The correct 'frequency deviation' needs to be matched to the transmitted signal for best performance.
Most applications will be one of two settings, commonly referred to as Wide and Narrow.

- **Wide FM (FM)** +/- 5 KHz Deviation
- **Narrow FM (NFM)** +/- 2.5 KHz Deviation

**NOTE:** Broadcast FM (88-108 MHz) is +/- 75 KHz, MUCH wider than that used for two-way communications,
and sometimes also referred to as Wide FM. No support for this is included as of now.

If mismatched, the following will occur:

- A **Narrow** transmitted signal will be very quiet on a **Wide** receiver channel.
- A **Wide** transmitted signal will be loud and distorted on a **Narrow** receiver channel.

As a starting point...

- **Amateur:** Commonly **Wide**, but **Narrow** is sometimes used.
- **Public Safety/Commercial:** - most have migrated to **Narrow**
- **FRS / GMRS:** - These channels have a mix of **Wide** and **Narrow** - check frequency lists for details.
- **Marine VHF:** **Wide**
- **NOAA Weather Radio:** **Wide**


Future Work
===========

My non-committal TODO list:

- **General GUI Enhancement**
- **Additional Receiver Models**
- **Additional Demodulation Modes** - SSB, maybe incorporate digital voice.
- **Priority Channel Support** - Higher priority Channels preempt others by muting them or lowering
  their volume.
- **Automatic Adaptive Squelch**
- **CTCSS Squelch**
- **Channel Hold** - Interactively lock a Channel (and therefore it's containing ScanWindow) to a receiver
  to monitor continuously.
- **Channel Disable** - Interactively Disable a Channel permanently or for some time period (e.g., 1 Hour)
- **Channel Grouping** - Enable / Disable Groups of Channels
- **EAS Alerting** - Monitor NOAA and/or FM Broadcast stations for EAS Alerts.
- **Channel Inactivity Alerting** - Alert if a Channel has been inactive for a specified period.
- **Stereo Audio Support** - Manual or automatic assignment of Channels between Left and Right channels
  to improve listening to Channels simultaneously.

Stretch Goals include:

- Support for remote monitoring - especially for local but perhaps across internet. A web interface
  would be ideal.
- Support for remote receivers
- Activity Recording - Record and plot historical active periods and perhaps record audio for playback.

What I specifically don't intend to support:

- Trunking Systems (unless perhaps there's a convenient way to wrap existing projects and share the UI)
- Digital Data

