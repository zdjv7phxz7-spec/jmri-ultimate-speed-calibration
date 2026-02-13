# -*- coding: utf-8 -*-
# Warmup controller for JMRI Jython
#
# Runs the selected locomotive forward at speed step 28 for N minutes, then stops.
# Best-effort: never throws into the caller UI thread.

import jmri
from java.util import Timer, TimerTask

def _safe_call(fn, *args):
    try:
        return fn(*args)
    except:
        return None

def _status(cb, msg):
    try:
        if cb:
            cb(msg)
    except:
        pass

def _acquire_throttle(state):
    # Reuse an existing throttle if available
    th = getattr(state, "throttle", None)
    if th is not None:
        return th

    # Need address + long/short flag
    addr = getattr(state, "addr", None)
    if addr is None:
        addr = getattr(state, "address", None)
    if addr is None:
        return None

    is_long = bool(getattr(state, "addr_is_long", False))

    try:
        tm = getattr(state, "throttleManager", None)
        if tm is None:
            tm = jmri.InstanceManager.getDefault(jmri.ThrottleManager)
        # requestThrottle returns a throttle or None depending on JMRI version.
        th = tm.requestThrottle(int(addr), is_long)
        if th is None:
            # Some JMRI builds require the DccLocoAddress object
            try:
                a = jmri.DccLocoAddress(int(addr), is_long)
                th = tm.requestThrottle(a, None)
            except:
                th = None
        if th is not None:
            try:
                state.throttle = th
            except:
                pass
        return th
    except:
        return None

class _StopTask(TimerTask):
    def __init__(self, ctrl):
        self.ctrl = ctrl
    def run(self):
        self.ctrl._stop_internal("Warmup complete. Stopped.")

class WarmupController(object):
    def __init__(self, state, dashboard=None, status_cb=None):
        self.state = state
        self.dashboard = dashboard
        self.status_cb = status_cb
        self._timer = None
        self._running = False
        self._throttle = None

    def is_running(self):
        return bool(self._running)

    def start(self, minutes):
        self.cancel()

        try:
            minutes = int(minutes)
        except:
            minutes = 5
        if minutes < 1:
            minutes = 1
        if minutes > 60:
            minutes = 60

        _status(self.status_cb, "Warmup: acquiring throttle...")
        th = _acquire_throttle(self.state)
        if th is None:
            _status(self.status_cb, "Warmup: ERROR - throttle not available (check roster selection / address).")
            return

        self._throttle = th

        # Forward at step 28 (normalized 1.0)
        try:
            _safe_call(th.setIsForward, True)
            _safe_call(th.setSpeedSetting, 1.0)
        except:
            pass

        self._running = True
        _status(self.status_cb, "Warmup: running forward at step 28 for %d minute(s)..." % minutes)

        try:
            self._timer = Timer("JMRI_WarmupTimer", True)
            self._timer.schedule(_StopTask(self), int(minutes) * 60 * 1000)
        except:
            self._timer = None

    def _stop_internal(self, msg):
        try:
            if self._throttle is not None:
                _safe_call(self._throttle.setSpeedSetting, 0.0)
        except:
            pass
        self._running = False
        _status(self.status_cb, msg)

    def cancel(self):
        # Stop and cancel timer
        try:
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except:
                    pass
                self._timer = None
        except:
            self._timer = None

        if self._running:
            self._stop_internal("Warmup cancelled. Stopped.")
        else:
            # still ensure stopped
            try:
                if self._throttle is not None:
                    _safe_call(self._throttle.setSpeedSetting, 0.0)
            except:
                pass
