#!/usr/bin/env python3
"""Properly daemonize the JARVIS desktop overlay."""
import os, sys, subprocess, time

def daemonize():
    """Double-fork daemonize."""
    if os.fork() > 0:
        sys.exit(0)   # parent exits
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)   # first child exits
    # We are the daemon
    devnull = open(os.devnull, 'r')
    logfile = open('/tmp/jarvis-desktop.log', 'w')
    os.dup2(devnull.fileno(), 0)
    os.dup2(logfile.fileno(), 1)
    os.dup2(logfile.fileno(), 2)
    os.environ['DISPLAY'] = os.environ.get('DISPLAY', ':0.0')
    os.environ['PYTHONUNBUFFERED'] = '1'
    jarvis_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(jarvis_dir)
    sys.path.insert(0, jarvis_dir)
    from src.desktop.app import main
    main()

if __name__ == '__main__':
    # Kill existing desktop instances
    import signal
    pid_file = f"/run/user/{os.getuid()}/jarvis-desktop.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(0.5)
        except Exception:
            pass

    print("Launching JARVIS desktop daemon...")
    daemonize()
