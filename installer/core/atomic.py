"""Bounded retries for atomic record replacement during brief Windows file locks."""
from __future__ import annotations
import os
import time


def replace(source,target):
    for attempt in range(6):
        try:
            os.replace(source,target)
            return
        except OSError as error:
            # Windows scanners/readers may briefly deny delete/replace sharing.
            # Persistent permissions and all other failures still surface, and
            # callers retain the original record until atomic replacement wins.
            if getattr(error,'winerror',None) not in {5,32,33} or attempt==5:raise
            time.sleep(.02*2**attempt)
