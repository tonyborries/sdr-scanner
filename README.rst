
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

This was designed around RTL-SDR radios, but minimal support is provided for others which can be accessed
through Soapy. (See notes in config section below.)

I'm currently migrating away from the wxPython interface to a web interface, hosted in Docker. This is still
a work in progress.


Architecture Overview
=====================

The application is ran with multiple processes, with one process for the Control / UI and an additional
process for each Receiver. The Control process continuously assigns scan tasks to the Receivers and
collects Channel status from them.

A separate AudioServer process runs to mix the audio streams from the individual Receivers, and send the
output stream to the configured outputs.


Config File
===========

The default config filename is 'sdrscan.yaml'

Minimal Example::

    scanner:
      maxChannelsPerWindow: 16

    outputs: 
      - type: local

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
- mode: (Optional, default FM)
- audioGain_dB: (Optional, default 0.0) Gain applied to the demodulated audio
- dwellTime_s: (Optional) Overrides the time spent monitoring the Channel after the last received activity.
- squelchThreshold: (Optional) Overrides the default squelch threshold.

The 'channel_defaults' allows overriding the default values for:

- mode
- audioGain_dB
- dwellTime_s
- squelchThreshold


Receivers
---------

Multiple Receivers
^^^^^^^^^^^^^^^^^^

Multiple Receivers can be enabled in the config.

For RTL-SDR, Unique serial numbers must be assigned to each (see 'rtl_eeprom').

::

    receivers:
      - type: RTL-SDR
        deviceArg: serial=00000001
      - type: RTL-SDR
        deviceArg: serial=00000002


Other Receiver Types
^^^^^^^^^^^^^^^^^^^^

Minimal experimental support is provided for receivers which Soapy supports::

      - type: SOAPY
        driver: airspy
        gain: 32

      - type: SOAPY
        driver: airspy
        gains:
          LNA: 10
          MIX: 10
          VGA: 10

For individual gain settings, use the 'gains' key to set the specific gains. Details on these can be provied by SoapySDRUtil::

    $ SoapySDRUtil --probe="driver=airspy"
    ...
      Full gain range: [0, 45] dB
        LNA gain range: [0, 15] dB
        MIX gain range: [0, 15] dB
        VGA gain range: [0, 15] dB
    ...


However, expect some issues. Not all radios may work with the current processing pipelines.

Some radios, for example RTL-SDR, offer sample rates which decimate down cleanly to common audio frequencies.

For others, such as my Airspy, resampling of the data stream is needed somewhere in the pipeline. Currently this
is implemented in the final Audio output from the Receiver block, where the Receiver itself runs at a different audio
rate and is resampled to the Global audio rate as the last step. This resampling and higher audio rates will introduce
performance impacts.

Another issue with sampling rates is that the internal signal processing chain uses one or multiple intermediate
rates through filtering and decimation. Naive logic is in place to determine viable options for these internal rates, but
if it is unable to find an acceptable solution, it will crash at startup. Even if it does find a solution, it may not be
optimal from a processing perspective.

For example, in my setup the Airspy uses much more CPU for processing than the RTL-SDRs, as the intermediate rates it uses internally
are much higher.

Mixing Receiver Types
^^^^^^^^^^^^^^^^^^^^^

Using multiple receiver types simultaneously is not recommended, especially starting out, but not inherently prohibited.
Careful testing may be needed to get a performant configuration.

For example, since the squelch is set relative to the receiver's dBFS, receivers with different overall gains will have
a different dBFS reading for the same absolute power input to it. An initial solution is to adjust the gain of one of
the receivers so that it has similar RSSI readings as a reference one.

The Scan Windows that are built share the same Channel list between all of the Receivers - as such, they must
be compatible for the receiver with the lowest bandwidth. This means that any receiver with a higher bandwidth is
not achieving a wider Scan Window, but still suffers from the increased processing needs. As an example, using the
RTL-SDR (2,048,000 samp/s) and the Airspy (2,500,000 samp/s) simultaneously, the Airspy is using ~20% more samples to
monitor the same window.


Scanner Settings
----------------

- **maxChannelsPerWindow:** - Configures the number of Channels allowed per ScanWindow - if your CPU is
  unable to keep up with the processing (audio dropouts / high latency / lagging UI), lower this to limit
  the number of parallel Channels.

Audio outputs
-------------

::
  
    outputs: 
      - type: local
      - type: udp
        serverIp: 127.0.0.1
        serverPort: 12345

If no outputs section is defined, a Local is generated by default.

Local
^^^^^

Plays audio using the default device with pyAudio / PortAudio

UDP
^^^

Sends raw audio to a UDP port. Sends a single channel, 16-bit short int at 16KHz.

Example to listen in Bash::

    nc -l -u 12345 | sox -t raw -r 16k -e signed-integer -b 16 -c 1 - -t alsa

Icecast
^^^^^^^

Sends MP3 audio to an Icecast server::

    - type: icecast
      url: http://127.0.0.1:8000/scanner
      password: hackme

Websocket
^^^^^^^^^

Sends 16-bit signed integer at 16KHz.

*NOTE:* Support for this is dependent on the Python / websockets versions installed. Python3.12+ recommended.
Older versions may fail to accept connections.

::

    - type: websocket
      host: 0.0.0.0
      port: 8123

An example HTML file is available in web/ws_audio.html that provides an audio player (adjust the `wsUrl` string in the file to match your IP:Port).


Installation
============

Install 'gnuradio' and optionally 'wxPython' to enable using the GUI mode.

::

    # Apt Based Systems
    apt-get install gnuradio python3-wxgtk4.0

For Icecast outputs, the 'lameenc' python package must be available:: 

    pip3 install lameenc

Docker
------

Minimal docker support is included for those who want it, limited to running the CLI app (no GUI support).

A work-in-progress web interface is also included.

Blacklist kernel modules on host and reboot (or rmmod each)::

    $ cat /etc/modprobe.d/blacklist-rtl.conf 
    blacklist dvb_usb_rtl28xxu
    blacklist rtl2832
    blacklist rtl2830
    blacklist dvb_core
    blacklist dvb_usb_rtl2832u
    blacklist dvb_usb_v2
    blacklist r820t
    blacklist rtl2832_sdr
    blacklist rtl2838


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

Clicking on a Channel enables interactive commands in the lower panel. Note that currently these status
changes are not persisted, and will be reset upon Scanner restart. The following buttons are available:

- **H** old: Locks the containing ScanWindow in the Receiver for continuous monitoring. Stops scanning for that receiver.
- **S** olo: Mutes all other non-soloed Channels. (Multiple Channels can be Soloed simultaneously.)
- **M** ute: Mutes the Channel - will still be scanned and can be Active.
- **D** isable: Removes the Channel from the scan list.
- **Disable 1 Hr**: Temporarily disables the Channel; it will be automatically re-enabled after 1 Hour.
- **Play** - Breaks the Squelch and forces the Channel Active. For example, on a NOAA channel, bypass the EAS Alert detection and listen live.
- **Pause** - Resets the Squelch from a Forced Active, or reset the Alert detection on an EAS Channel.


Channel Modes
=============

Supported Modes:

- AM
- FM
- NFM
- NOAA
- BFM_EAS
- USB / LSB

FM / NFM
--------

The correct 'frequency deviation' needs to be matched to the transmitted signal for best performance.
Most applications will be one of two settings, commonly referred to as Wide and Narrow.

- **Wide FM (FM)** +/- 5 KHz Deviation
- **Narrow FM (NFM)** +/- 2.5 KHz Deviation

**NOTE:** Broadcast FM (BFM) (88-108 MHz) is +/- 75 KHz, MUCH wider than that used for two-way communications,
and sometimes also referred to as Wide FM. Limited support is available for this in the EAS monitoring.

If mismatched, the following will occur:

- A **Narrow** transmitted signal will be very quiet on a **Wide** receiver channel.
- A **Wide** transmitted signal will be loud and distorted on a **Narrow** receiver channel.

As a starting point...

- **Amateur:** Commonly **Wide**, but **Narrow** is sometimes used.
- **Public Safety/Commercial:** - most have migrated to **Narrow**
- **FRS / GMRS:** - These channels have a mix of **Wide** and **Narrow** - check frequency lists for details.
- **Marine VHF:** **Wide**
- **NOAA Weather Radio:** **Wide**

NOAA Emergency Alerts
---------------------

Example Config

::

      - freq: 162.525
        label: "Local NOAA"
        mode: NOAA
        dwellTime_s: 120

This mode scans a channel looking for the EAS Alert Tone (1050 Hz) on NOAA Weather Stations.
If detected, the Channel is activated for the duration of the Dwell Time. No SAME decoding
or End of Message is detected. Scanning resumes at the end of the Dwell Time.

Broadcast FM EAS
----------------

Example Config

::

      - freq: 92.7
        mode: BFM_EAS
        dwellTime_s: 120

This mode scans Broadcast FM radio stations for the dual (853 / 960 Hz) EAS Alert Tones.
Similar to the NOAA mode, this plays the Channel for the duration of the configured Dwell Time.

Single Sideband
---------------

A simple sideband demodulator is provided, though given the simple squelching system currently used
it may prove to be annoying for sustained use. My current use case is merely to monitor for activity then
switch to a separate radio if interested.


Future Work
===========

My non-committal TODO list:

- Deprecate the GUI (wxPython) for a web-based interface.
- Improved support around using multiple receivers at once (for example, each receier tracks it's own noise floor independently)
- **Priority Channel Support** - Higher priority Channels preempt others by muting them or lowering
  their volume.
- **Automatic Adaptive Squelch**
- **CTCSS Squelch**
- **Channel Grouping** - Enable / Disable Groups of Channels
- **Channel Inactivity Alerting** - Alert if a Channel has been inactive for a specified period.
- **Stereo Audio Support** - Manual or automatic assignment of Channels between Left and Right channels
  to improve listening to Channels simultaneously.

Stretch Goals include:

- Support for remote receivers
- Activity Recording - Record and plot historical active periods and perhaps record audio for playback.

What I specifically don't intend to support:

- Trunking Systems (unless perhaps there's a convenient way to wrap existing projects and share the UI)
- Digital Data

