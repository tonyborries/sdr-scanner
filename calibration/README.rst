I've been experimenting with calibrating SDR radios, and realized this may be of interest
to a broader audience. Here I share some of my experimental GNU Radio graphs

While the idea is to test the radio's calibration against a known reference signal (e.g., WWV),
this can also test another radio, repeater, etc. against this radio.


calibration_pll
---------------

This is not well tuned - just a proof-of-concept, though seems to work. May be improved with
giving more attention to the filtering and PLL config.

The idea behind this one is ultra-simplistic, yet it seems to work fine.

We open an SDR radio source at our reference frequency of interest. After some filtering, we apply
a PLL Detector to track the carrier. We average the output and convert it into a parts-per-million
value.

This seems to work well against WWV time signals.

While I initially envisioned this for AM carriers, given the averaging, it may work for FM as well,
although the output will be much noisier given the modulation.

Broadcast FM definitely requires widening out the filters from the defaults.


