"""The Nest: the always-on mind for Ah-Ah, Tee-Tee and Reachy Mini.

Runs on the Jetson Orin Nano Super on the TV stand. It talks to both Microducks over the LAN
(the same WebRTC control channel the duck's own console page uses), drives Reachy Mini Lite over
its local REST API, and keeps each duck's needs, tiredness, memory and messages.

The robots' own software always has the final say on movement: everything here proposes
intent, and the duck's controller and safety decide how to move, or whether to.
"""

__version__ = "0.1.0"
