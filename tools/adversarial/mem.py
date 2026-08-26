#!/usr/bin/env python3
"""Adversarial probe: hold 700MB in a double-forked orphan, emit nothing useful."""
import argparse, os, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("inputs"); ap.add_argument("--policy"); ap.add_argument("--output"); ap.add_argument("--seed"); ap.add_argument("--max-memory")
a = ap.parse_args(); os.makedirs(a.output, exist_ok=True)
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        blob = bytearray(700 * 1024 * 1024)
        for i in range(0, len(blob), 4096): blob[i] = 1
        time.sleep(6); os._exit(0)
    os._exit(0)
time.sleep(4)
