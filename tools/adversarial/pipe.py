#!/usr/bin/env python3
"""Adversarial probe: exit 0 immediately, leaving a double-forked grandchild
holding stdout/stderr open forever. An unhardened verifier deadlocks in
communicate(); a hardened one kills the session and scores 0."""
import argparse, os, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("inputs"); ap.add_argument("--policy"); ap.add_argument("--output"); ap.add_argument("--seed"); ap.add_argument("--max-memory")
a = ap.parse_args(); os.makedirs(a.output, exist_ok=True)
if os.fork() == 0:
    if os.fork() == 0:
        while True:
            time.sleep(3600)     # inherits our stdout/stderr pipes, never exits
    os._exit(0)
os._exit(0)
