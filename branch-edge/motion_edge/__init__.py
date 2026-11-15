"""Motion branch edge: on-premise offline till server.

Runs on a small PC on the branch LAN.  Tills talk to it; it talks to the
central system (Render) when the internet is available.  While offline it
keeps selling from a local snapshot and queues immutable sale events, which
are posted exactly once centrally when connectivity returns.
"""

__version__ = "0.1.0"
