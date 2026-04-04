"""Index of all command specs."""

from __future__ import annotations

from .alias import alias
from .nohup import nohup
from .pyright import pyright
from .sleep import sleep
from .srun import srun
from .time import time
from .timeout import timeout

specs = [pyright, timeout, sleep, alias, nohup, time, srun]
