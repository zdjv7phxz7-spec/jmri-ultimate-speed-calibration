# ultimate_speed_matching.py
# JMRI Jython (Python 2.7) - Ultimate Speed Matching Dashboard + Automation + Results Tabs
# Runs from JMRI -> Scripting -> Run Script (no edits). ASCII only.

from __future__ import print_function

import jmri
import os
import time
import math
import datetime
import threading
import xml.etree.ElementTree as ET
import sys
from java.util.logging import Logger, Level
LOGGER = Logger.getLogger('UltimateSpeedMatching')
SCRIPT_DIR = r"C:\Users\rschneider\JMRI\jython\ultimate_speed_calibration"
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import speed_match_math

from java.awt import BorderLayout, GridBagLayout, GridBagConstraints, Insets, Dimension, Color, BasicStroke
from java.awt.event import ActionListener, WindowAdapter, MouseWheelListener
from java.beans import PropertyChangeListener
from java.io import File
from java.awt.image import BufferedImage
from javax.imageio import ImageIO
from java.awt import AlphaComposite
from java.awt.print import PrinterJob, Printable

from javax.swing import (
    JFrame, JPanel, JLabel, JButton, JTextField, JComboBox, JTable, JScrollPane,
    JOptionPane, JSpinner, SpinnerNumberModel, BoxLayout, BorderFactory,
    SwingUtilities, Timer, JTabbedPane, JSlider, JTextArea
)
from javax.swing.table import AbstractTableModel
from javax.swing.event import ChangeListener

# -------------------------------
# Authoritative track geometry
# -------------------------------
BLOCK_LENGTH_SCALE_FEET = 133.0
NUM_BLOCKS = 12
CIRCLE_LENGTH_SCALE_FEET = 1596.0

# -------------------------------
# Automation constants
# -------------------------------
MAD_K = 3.5
NO_ACTIVITY_TIMEOUT_SEC = 40.0
LIVE_REFRESH_MS = 250
MEASURE_SAMPLES_PER_STEP = 3  # test pacing

# Recommended CV damping (reduces overshoot/undershoot vs pure ratio)
CV_GAIN = 0.45
CV_RATIO_MIN = 0.80
CV_RATIO_MAX = 1.20
CV_MAX_STEP_DELTA = 20

# -------------------------------
# Helpers
# -------------------------------

def log_console(msg, level='INFO'):
    try:
        if level == 'SEVERE':
            LOGGER.severe(str(msg))
        elif level == 'WARNING':
            LOGGER.warning(str(msg))
        else:
            LOGGER.info(str(msg))
    except:
        try:
            print(str(msg))
        except:
            pass

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def now_compact():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def clamp_int(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v

def parse_int(s, default_val):
    try:
        return int(str(s).strip())
    except:
        return default_val

def parse_float(s, default_val):
    try:
        return float(str(s).strip())
    except:
        return default_val

def median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])

def mean(vals):
    if not vals:
        return None
    return sum(vals) / float(len(vals))

def stdev(vals):
    if not vals or len(vals) < 2:
        return 0.0
    m = mean(vals)
    ss = 0.0
    for v in vals:
        dv = v - m
        ss += dv * dv
    return math.sqrt(ss / float(len(vals) - 1))

def mad_stats(samples):
    if not samples:
        return (0.0, 0.0)
    m = median(samples)
    abs_dev = [abs(x - m) for x in samples]
    mad = median(abs_dev)
    if mad is None:
        mad = 0.0
    # robust sigma estimate from MAD
    sigma = 1.4826 * float(mad)
    return (float(mad), float(sigma))

def mad_filter(samples, k):
    if not samples:
        return []
    m = median(samples)
    abs_dev = [abs(x - m) for x in samples]
    mad = median(abs_dev)
    if mad is None:
        return []
    if mad == 0:
        return list(samples)
    lim = k * mad
    kept = [x for x in samples if abs(x - m) <= lim]
    return kept

def mph_from(distance_feet, dt_seconds):
    if dt_seconds <= 0.0:
        return None
    return (distance_feet / dt_seconds) * (3600.0 / 5280.0)

def safe_join(a, b):
    try:
        return os.path.join(a, b)
    except:
        return str(a) + os.sep + str(b)

def safe_dirname(p):
    try:
        return os.path.dirname(p)
    except:
        return None

def fmt_elapsed(seconds):
    try:
        s = int(max(0, seconds))
        h = s // 3600
        m = (s % 3600) // 60
        ss = s % 60
        if h > 0:
            return "%d:%02d:%02d" % (h, m, ss)
        return "%d:%02d" % (m, ss)
    except:
        return ""

# -------------------------------
# Roster integration (roster.xml + fileName attribute)
# -------------------------------
class RosterEntry(object):
    def __init__(self, display, fileName, dccAddress, rid, roadName, roadNumber):
        self.display = display
        self.fileName = fileName
        self.dccAddress = dccAddress
        self.rid = rid
        self.roadName = roadName
        self.roadNumber = roadNumber

class RosterManagerLite(object):
    """
    Prefer authoritative JMRI "User Files" directory.
    Expected:
      <HOME>\\roster.xml
      <HOME>\\roster\\<fileName>.xml
    """
    def __init__(self):
        self.home_dir = self._try_get_home_dir()
        self.profile_dir = self._try_get_profile_dir()
        self.roster_xml_path = self._find_roster_xml()
        self.entries = []
        self.override_loco_path_by_file = {}

    def _try_get_home_dir(self):
        try:
            return str(jmri.util.FileUtil.getUserFilesPath())
        except:
            pass
        try:
            fu = jmri.util.FileUtil
            if hasattr(fu, "getHomePath"):
                return str(fu.getHomePath())
        except:
            pass
        return None

    def _try_get_profile_dir(self):
        try:
            return str(jmri.util.FileUtil.getProfilePath())
        except:
            return None

    def _candidate_roster_paths(self):
        cands = []
        if self.home_dir:
            cands.append(safe_join(self.home_dir, "roster.xml"))
        if self.profile_dir:
            cands.append(safe_join(self.profile_dir, "roster.xml"))
            p = safe_dirname(self.profile_dir)
            if p:
                cands.append(safe_join(p, "roster.xml"))
        # Fallback for known Windows JMRI user directory (Russ)
        try:
            win_fallback = r"C:\\Users\\rschneider\\JMRI\\roster.xml"
            if win_fallback and (win_fallback not in cands) and os.path.isfile(win_fallback):
                cands.append(win_fallback)
        except:
            pass

        out = []
        seen = {}
        for pth in cands:
            if not pth:
                continue
            if pth in seen:
                continue
            seen[pth] = True
            out.append(pth)
        return out

    def _find_roster_xml(self):
        for pth in self._candidate_roster_paths():
            try:
                if os.path.isfile(pth):
                    return pth
            except:
                pass
        try:
            r = jmri.jmrit.roster.Roster.getDefault()
            try:
                f = r.getRosterFile()
                if f is not None:
                    pth = str(f.getAbsolutePath())
                    if os.path.isfile(pth):
                        return pth
            except:
                pass
            try:
                pth = str(r.getRosterFileName())
                if os.path.isfile(pth):
                    return pth
            except:
                pass
        except:
            pass
        return None

    def load_roster(self):
        self.entries = []
        if not self.roster_xml_path or (not os.path.isfile(self.roster_xml_path)):
            return
        try:
            tree = ET.parse(self.roster_xml_path)
            root = tree.getroot()

            roster_node = None
            if root.tag == "roster-config":
                for ch in list(root):
                    if ch.tag == "roster":
                        roster_node = ch
                        break
            if roster_node is None and root.tag == "roster":
                roster_node = root
            if roster_node is None:
                return

            for loco in list(roster_node):
                if loco.tag != "locomotive":
                    continue
                fileName = loco.get("fileName")
                if not fileName:
                    continue
                dccAddress = loco.get("dccAddress")
                rid = loco.get("id")
                roadName = loco.get("roadName")
                roadNumber = loco.get("roadNumber")

                name_parts = []
                if roadName:
                    name_parts.append(roadName)
                if roadNumber:
                    name_parts.append(roadNumber)
                if rid:
                    name_parts.append("[" + rid + "]")
                if dccAddress:
                    name_parts.append("(Addr " + str(dccAddress) + ")")
                base_display = " ".join(name_parts) if name_parts else str(fileName)
                display = base_display + "  -  file: " + str(fileName)

                self.entries.append(RosterEntry(display, fileName, dccAddress, rid, roadName, roadNumber))
        except:
            self.entries = []

    def _candidate_loco_paths(self, fileName):
        paths = []
        try:
            ov = self.override_loco_path_by_file.get(fileName, None)
            if ov:
                paths.append(ov)
        except:
            pass

        if self.home_dir:
            paths.append(safe_join(safe_join(self.home_dir, "roster"), fileName))

        if self.roster_xml_path:
            base = safe_dirname(self.roster_xml_path)
            if base:
                paths.append(safe_join(safe_join(base, "roster"), fileName))

        if self.profile_dir:
            paths.append(safe_join(safe_join(self.profile_dir, "roster"), fileName))
            p = safe_dirname(self.profile_dir)
            if p:
                paths.append(safe_join(safe_join(p, "roster"), fileName))

        out = []
        seen = {}
        for pth in paths:
            if not pth:
                continue
            if pth in seen:
                continue
            seen[pth] = True
            out.append(pth)
        return out

    def resolve_loco_path(self, roster_entry):
        if roster_entry is None or not roster_entry.fileName:
            return None, []
        tried = self._candidate_loco_paths(roster_entry.fileName)
        for pth in tried:
            try:
                if os.path.isfile(pth):
                    return pth, tried
            except:
                pass
        return None, tried

    def load_cv67_94(self, roster_entry):
        base = [int(round((i + 1) / 28.0 * 255.0)) for i in range(28)]
        if roster_entry is None or not roster_entry.fileName:
            return base, None, []

        loco_path, tried = self.resolve_loco_path(roster_entry)
        if loco_path is None:
            return base, None, tried

        try:
            tree = ET.parse(loco_path)
            root = tree.getroot()
            cv_map = {}
            for el in root.iter():
                if el.tag == "CVvalue":
                    n = el.get("name")
                    v = el.get("value")
                    if n is None or v is None:
                        continue
                    try:
                        nn = int(str(n))
                        vv = int(str(v))
                        cv_map[nn] = clamp_int(vv, 0, 255)
                    except:
                        pass

            ok = False
            out = []
            for cv in range(67, 95):
                if cv in cv_map:
                    ok = True
                    out.append(cv_map[cv])
                else:
                    out.append(None)
            if not ok:
                return base, loco_path, tried

            last_known = None
            for i in range(28):
                if out[i] is not None:
                    last_known = out[i]
                else:
                    if last_known is not None:
                        out[i] = last_known

            next_known = None
            for i in range(27, -1, -1):
                if out[i] is not None:
                    next_known = out[i]
                else:
                    if next_known is not None:
                        out[i] = next_known

            final = []
            for i in range(28):
                if out[i] is None:
                    final.append(base[i])
                else:
                    final.append(clamp_int(int(out[i]), 0, 255))
            return final, loco_path, tried
        except:
            return base, loco_path, tried

# -------------------------------
# Live table model (adds Expected + Variance)
# -------------------------------
class LiveTableModel(AbstractTableModel):
    def __init__(self, state):
        AbstractTableModel.__init__(self)
        self.state = state
        self.columns = ["Sensor", "Speed (mph)", "Expected (mph)", "Variance (mph)", "Lap Time (s)", "Speed Step", "Refresh"]
        self.rows = ["LS%d" % i for i in range(1, 13)] + ["LAP"]

    def getColumnCount(self):
        return len(self.columns)

    def getRowCount(self):
        return len(self.rows)

    def getColumnName(self, col):
        return self.columns[col]

    def getValueAt(self, row, col):
        key = self.rows[row]
        d = self.state.live_rows.get(key, None)
        if d is None:
            d = {"speed": "", "exp": "", "var": "", "lap": "", "step": "", "refresh": ""}
        if col == 0:
            return key
        if col == 1:
            return d.get("speed", "")
        if col == 2:
            return d.get("exp", "")
        if col == 3:
            return d.get("var", "")
        if col == 4:
            return d.get("lap", "")
        if col == 5:
            return d.get("step", "")
        if col == 6:
            return d.get("refresh", "")
        return ""

    def isCellEditable(self, row, col):
        return False

    def fire_all(self):
        self.fireTableDataChanged()

# -------------------------------
# MPH chart panel (steps 1..28)
# Target BLACK, Forward RED, Reverse ORANGE
# -------------------------------
class SpeedChartPanel(JPanel, MouseWheelListener):
    def __init__(self):
        JPanel.__init__(self)
        self.target = []
        self.fwd = []
        self.rev = []
        self.zoom = 1.0
        self.pad_left = 55
        self.pad_right = 20
        self.pad_top = 48
        self.pad_bottom = 45
        self.base_w = 980
        self.base_h = 420
        self.meta_lines = []

    def setData(self, target, fwd, rev):
        self.target = list(target) if target else []
        self.fwd = list(fwd) if fwd else []
        self.rev = list(rev) if rev else []
        self.revalidate()
        self.repaint()

    def setMetaLines(self, lines):
        self.meta_lines = list(lines) if lines else []
        self.repaint()

    def setZoom(self, z):
        if z < 0.25:
            z = 0.25
        if z > 6.0:
            z = 6.0
        self.zoom = z
        self.revalidate()
        self.repaint()

    def getPreferredSize(self):
        return Dimension(int(self.base_w * self.zoom), int(self.base_h * self.zoom))

    def mouseWheelMoved(self, e):
        return

    def paint(self, g):
        JPanel.paint(self, g)

        w = self.getWidth()
        h = self.getHeight()
        if w <= 0 or h <= 0:
            return

        n = 0
        if self.target:
            n = len(self.target)
        elif self.fwd:
            n = len(self.fwd)
        elif self.rev:
            n = len(self.rev)
        if n < 2:
            return

        ymax = 10.0
        for arr in [self.target, self.fwd, self.rev]:
            for v in arr:
                if v is not None:
                    try:
                        fv = float(v)
                        if fv > ymax:
                            ymax = fv
                    except:
                        pass
        ymax = math.ceil(ymax / 5.0) * 5.0
        if ymax < 10.0:
            ymax = 10.0

        x0 = self.pad_left
        y0 = self.pad_top
        x1 = w - self.pad_right
        y1 = h - self.pad_bottom
        if x1 <= x0 + 10 or y1 <= y0 + 10:
            return

        g.setColor(Color(230, 230, 230))
        for i in range(0, n):
            x = x0 + int((x1 - x0) * (i / float(n - 1)))
            g.drawLine(x, y0, x, y1)

        g.setColor(Color(220, 220, 220))
        ytick = 0
        while ytick <= int(ymax):
            y = y1 - int((y1 - y0) * (ytick / ymax))
            g.drawLine(x0, y, x1, y)
            ytick += 5

        g.setColor(Color.BLACK)
        g.drawLine(x0, y0, x0, y1)
        g.drawLine(x0, y1, x1, y1)

        ytick = 0
        while ytick <= int(ymax):
            y = y1 - int((y1 - y0) * (ytick / ymax))
            g.drawString(str(ytick), 5, y + 4)
            ytick += 5

        ticks = [1, 4, 8, 12, 16, 20, 24, 28]
        for step in ticks:
            if step >= 1 and step <= n:
                i = step - 1
                x = x0 + int((x1 - x0) * (i / float(n - 1)))
                g.drawString(str(step), x - 6, y1 + 18)

        g.drawString("Speed Step", int((x0 + x1) / 2) - 35, h - 10)
        g.drawString("MPH", 10, y0 - 5)

        def xy(i0, mph_val):
            x = x0 + int((x1 - x0) * (i0 / float(n - 1)))
            y = y1 - int((y1 - y0) * (mph_val / ymax))
            return x, y

        def draw_series(arr, color, stroke_w):
            if not arr or len(arr) != n:
                return
            g.setColor(color)
            g.setStroke(BasicStroke(float(stroke_w)))
            prev = None
            for i0 in range(n):
                v = arr[i0]
                if v is None:
                    prev = None
                    continue
                try:
                    fv = float(v)
                except:
                    prev = None
                    continue
                x, y = xy(i0, fv)
                if prev is not None:
                    g.drawLine(prev[0], prev[1], x, y)
                prev = (x, y)
            g.setStroke(BasicStroke(1.0))

        draw_series(self.target, Color.BLACK, 2.0)
        draw_series(self.fwd, Color.RED, 2.0)
        draw_series(self.rev, Color(255, 140, 0), 2.0)

        g.setColor(Color.BLACK)
        g.drawString("Target", x0 + 10, y0 + 15)
        g.setColor(Color.RED)
        g.drawString("Forward", x0 + 70, y0 + 15)
        g.setColor(Color(255, 140, 0))
        g.drawString("Reverse", x0 + 140, y0 + 15)

        if self.meta_lines:
            box_w = min(max(320, x1 - x0 - 24), 760)
            box_h = 18 * len(self.meta_lines) + 12
            box_x = max(x0 + 10, x1 - box_w - 10)
            box_y = y0 + 26
            g.setColor(Color(255, 255, 210))
            g.fillRect(box_x, box_y, box_w, box_h)
            g.setColor(Color.BLACK)
            g.drawRect(box_x, box_y, box_w, box_h)
            yy = box_y + 18
            for line in self.meta_lines:
                g.drawString(str(line)[:140], box_x + 8, yy)
                yy += 18

# -------------------------------
# Decoder speed table bar chart panel (steps 1..28)
# current vs calculated bars; CURRENT value on TOP of current bar
# -------------------------------
class DecoderTableBarChartPanel(JPanel, MouseWheelListener):
    def __init__(self):
        JPanel.__init__(self)
        self.calc_values = []
        self.curr_values = []
        self.zoom = 1.0
        self.pad_left = 60
        self.pad_right = 20
        self.pad_top = 48
        self.pad_bottom = 55
        self.base_w = 980
        self.base_h = 420
        self.meta_lines = []

    def setData(self, calc_values, curr_values):
        self.calc_values = list(calc_values) if calc_values else []
        self.curr_values = list(curr_values) if curr_values else []
        self.revalidate()
        self.repaint()

    def setMetaLines(self, lines):
        self.meta_lines = list(lines) if lines else []
        self.repaint()

    def setZoom(self, z):
        if z < 0.25:
            z = 0.25
        if z > 6.0:
            z = 6.0
        self.zoom = z
        self.revalidate()
        self.repaint()

    def getPreferredSize(self):
        return Dimension(int(self.base_w * self.zoom), int(self.base_h * self.zoom))

    def mouseWheelMoved(self, e):
        return

    def paint(self, g):
        JPanel.paint(self, g)
        w = self.getWidth()
        h = self.getHeight()
        if w <= 0 or h <= 0:
            return
        if not self.calc_values or len(self.calc_values) < 2:
            return

        n = len(self.calc_values)
        ymax = 255.0

        x0 = self.pad_left
        y0 = self.pad_top
        x1 = w - self.pad_right
        y1 = h - self.pad_bottom
        if x1 <= x0 + 10 or y1 <= y0 + 10:
            return

        g.setColor(Color(220, 220, 220))
        for ytick in [0, 64, 128, 192, 255]:
            y = y1 - int((y1 - y0) * (ytick / ymax))
            g.drawLine(x0, y, x1, y)

        g.setColor(Color.BLACK)
        g.drawLine(x0, y0, x0, y1)
        g.drawLine(x0, y1, x1, y1)

        for ytick in [0, 64, 128, 192, 255]:
            y = y1 - int((y1 - y0) * (ytick / ymax))
            g.drawString(str(ytick), 5, y + 4)

        ticks = [1, 4, 8, 12, 16, 20, 24, 28]
        for step in ticks:
            if step >= 1 and step <= n:
                i = step - 1
                x = x0 + int((x1 - x0) * (i / float(n - 1)))
                g.drawString(str(step), x - 6, y1 + 18)

        g.drawString("Speed Step", int((x0 + x1) / 2) - 35, h - 10)
        g.drawString("CV value", 10, y0 - 5)

        plot_w = (x1 - x0)
        bar_slot = plot_w / float(n)
        pair_w = max(6, int(bar_slot * 0.70))
        bar_w = max(3, int(pair_w * 0.45))
        gap = max(1, int(pair_w * 0.10))

        col_curr = Color(90, 90, 90)
        col_calc = Color(20, 120, 170)

        for i in range(n):
            cv_calc = self.calc_values[i]
            cv_curr = None
            if self.curr_values and i < len(self.curr_values):
                cv_curr = self.curr_values[i]

            try:
                vc = float(cv_calc)
            except:
                vc = None
            try:
                vv = float(cv_curr) if cv_curr is not None else None
            except:
                vv = None

            x_center = x0 + int((i / float(n - 1)) * plot_w) if n > 1 else (x0 + plot_w // 2)
            left_pair = x_center - (pair_w // 2)

            # Current bar (left)
            if vv is not None:
                if vv < 0: vv = 0
                if vv > 255: vv = 255
                y_top = y1 - int((y1 - y0) * (vv / ymax))
                g.setColor(col_curr)
                g.fillRect(left_pair, y_top, bar_w, y1 - y_top)
                g.setColor(Color.BLACK)
                # CURRENT on top (requested)
                g.drawString(str(int(round(vv))), left_pair + 1, max(y0 + 10, y_top - 4))

            # Calculated bar (right)
            if vc is not None:
                if vc < 0: vc = 0
                if vc > 255: vc = 255
                x_calc = left_pair + bar_w + gap
                y_top_c = y1 - int((y1 - y0) * (vc / ymax))
                g.setColor(col_calc)
                g.fillRect(x_calc, y_top_c, bar_w, y1 - y_top_c)
                g.setColor(Color.BLACK)
                g.drawString(str(int(round(vc))), x_calc + 1, max(y0 + 10, y_top_c - 4))

        g.setColor(col_curr)
        g.drawString("Current", x0 + 10, y0 + 15)
        g.setColor(col_calc)
        g.drawString("Calculated", x0 + 80, y0 + 15)

        if self.meta_lines:
            box_w = min(max(320, x1 - x0 - 24), 760)
            box_h = 18 * len(self.meta_lines) + 12
            box_x = max(x0 + 10, x1 - box_w - 10)
            box_y = y0 + 26
            g.setColor(Color(255, 255, 210))
            g.fillRect(box_x, box_y, box_w, box_h)
            g.setColor(Color.BLACK)
            g.drawRect(box_x, box_y, box_w, box_h)
            yy = box_y + 18
            for line in self.meta_lines:
                g.drawString(str(line)[:140], box_x + 8, yy)
                yy += 18

# -------------------------------
# Printing support for charts
# -------------------------------
def print_component(parent_frame, title, comp):
    try:
        pj = PrinterJob.getPrinterJob()
        pj.setJobName(str(title))

        class P(Printable):
            def print(self, graphics, pageFormat, pageIndex):
                if pageIndex > 0:
                    return Printable.NO_SUCH_PAGE
                try:
                    g2 = graphics
                    x = pageFormat.getImageableX()
                    y = pageFormat.getImageableY()
                    w = pageFormat.getImageableWidth()
                    h = pageFormat.getImageableHeight()

                    cw = comp.getWidth()
                    ch = comp.getHeight()
                    if cw <= 0 or ch <= 0:
                        pref = comp.getPreferredSize()
                        cw = int(pref.getWidth())
                        ch = int(pref.getHeight())
                        comp.setSize(cw, ch)

                    sx = w / float(cw)
                    sy = h / float(ch)
                    s = min(sx, sy)

                    g2.translate(x, y)
                    g2.scale(s, s)
                    comp.printAll(g2)
                except:
                    pass
                return Printable.PAGE_EXISTS

        pj.setPrintable(P())
        if pj.printDialog():
            pj.print()
    except:
        try:
            JOptionPane.showMessageDialog(parent_frame, "Print failed: %s" % str(sys.exc_info()[1]), "Print", JOptionPane.ERROR_MESSAGE)
        except:
            pass

# -------------------------------
# Core speed state + logic
# -------------------------------
class SpeedState(object):
    def __init__(self):
        self.sensorManager = jmri.InstanceManager.getDefault(jmri.SensorManager)
        self.throttleManager = jmri.InstanceManager.getDefault(jmri.ThrottleManager)

        self.throttle = None
        self.throttle_listener = None
        self.addr = 3
        self.addr_is_long = False

        self.dashboard = None
        self.results_window = None

        self.live_rows = {}
        for i in range(1, 13):
            self.live_rows["LS%d" % i] = {"speed": "", "exp": "", "var": "", "lap": "", "step": "", "refresh": ""}
        self.live_rows["LAP"] = {"speed": "", "exp": "", "var": "", "lap": "", "step": "", "refresh": ""}

        self.current_step = 0
        self.current_dir_forward = True
        self.forward_buf = []
        self.reverse_buf = []
        self.seq_hist = []
        self.seg4_anchor = None
        self.last_sensor_idx = None

        self.lap_start_time = None
        self.last_lap_speed = None

        self.last_sensor_activity_time = time.time()

        self.auto_run_mode = "FULL"
        self.automation_running = False
        self.automation_status = ""
        self.auto_step_plan = []
        self.auto_mode_by_step = {}

        # measured mph (after MAD) per direction
        self.auto_measured_fwd = {}
        self.auto_measured_rev = {}

        # per-step sample tracking and stats per direction:
        # dict: step -> {"mode":..., "samples":[...], "kept":[...], "mad":..., "sigma":..., "moe":...}
        self.auto_step_stats_fwd = {}
        self.auto_step_stats_rev = {}

        # ignore N samples after step/dir change during automation
        self.auto_skip_remaining = 0

        self.auto_current_step_index = 0
        self.auto_warmup_laps_remaining = 0
        self.auto_phase = "IDLE"  # FWD_WARMUP, FWD_MEASURE, REV_WARMUP, REV_MEASURE, DONE

        self.auto_start_time = None
        self.auto_last_elapsed = 0.0

        self.baseline_table = [int(round((i + 1) / 28.0 * 255.0)) for i in range(28)]

        self.roster = RosterManagerLite()
        self.roster.load_roster()
        self.selected_roster_entry = None

        self.sensors = {}
        self.sensor_listeners = {}

        self.live_timer = None
        self.activity_timer = None

        self.lock = threading.RLock()

        # history dataset paths
        self.history_dir = self._get_history_dir()
        self.history_summary_csv = safe_join(self.history_dir, "speedmatch_history_summary.csv")
        self.history_samples_csv = safe_join(self.history_dir, "speedmatch_history_samples.csv")
        self._ensure_history_headers()

        # results target range (for live expected mph)
        self.target_min_mph = 5.0
        self.target_max_mph = 70.0
        self.db_run_id = None

        self.auto_iter_enabled = False
        self.auto_iter_count = 0
        self.auto_iter_max = 6
        self.auto_iter_tolerance_pct = 15.0
        self.auto_last_recommended_table = None

    def _get_history_dir(self):
        try:
            base = self.roster.home_dir if self.roster.home_dir else None
            if not base:
                base = safe_dirname(self.roster.roster_xml_path) if self.roster.roster_xml_path else None
            if not base:
                base = os.path.expanduser("~")
            out = safe_join(base, "speedmatch_history")
            try:
                if not os.path.isdir(out):
                    os.makedirs(out)
            except:
                pass
            return out
        except:
            return "."

    def _ensure_history_headers(self):
        # Summary file
        try:
            if not os.path.isfile(self.history_summary_csv):
                f = open(self.history_summary_csv, "w")
                f.write("Timestamp,Engine,Addr,Direction,Step,Mode,MeanMph,KeptN,TotalN,MAD,SigmaEst,MOE\n")
                f.close()
        except:
            pass
        # Samples file
        try:
            if not os.path.isfile(self.history_samples_csv):
                f = open(self.history_samples_csv, "w")
                f.write("Timestamp,Engine,Addr,Direction,Step,Mode,SampleMph\n")
                f.close()
        except:
            pass

    def _engine_label_short(self):
        try:
            sel = self.selected_roster_entry
            if sel is not None:
                return str(sel.display).replace(",", " ")
        except:
            pass
        return "Addr %d" % int(self.addr)

    # --- DB logging (optional) ---
    def _db_loco_identity(self):
        try:
            sel = self.selected_roster_entry
            if sel is not None and sel.fileName:
                return "roster:" + str(sel.fileName)
        except:
            pass
        return "addr:" + str(int(self.addr))

    def db_begin_run(self, direction_str, mode):
        try:
            import db_datamart
            conn = db_datamart.connect()
            try:
                loco_id = self._db_loco_identity()
                disp = None
                roster_fn = None
                try:
                    ent = self.selected_roster_entry
                    if ent is not None:
                        disp = str(ent.display)
                        roster_fn = str(ent.fileName) if ent.fileName else None
                except:
                    pass

                db_datamart.upsert_loco(conn, loco_id, disp, int(self.addr), self.addr_is_long, roster_fn)
                self.db_run_id = db_datamart.begin_speed_run(conn, loco_id, str(mode), str(direction_str),
                                                             float(self.target_min_mph), float(self.target_max_mph))
                conn.commit()
            finally:
                conn.close()
        except:
            self.db_run_id = None
            try:
                if self.dashboard is not None:
                    self.dashboard.set_automation_status("DB begin run failed: %s" % str(sys.exc_info()[1]))
            except:
                pass

    def db_log_sample(self, step, mode, mph_val):
        if self.db_run_id is None:
            return
        try:
            import db_datamart
            conn = db_datamart.connect()
            try:
                db_datamart.log_sample(conn, int(self.db_run_id), int(step), str(mode), float(mph_val))
                conn.commit()
            finally:
                conn.close()
        except:
            pass

    def db_log_summary(self, step, mode, mean_mph, kept_n, total_n, madv, sigma_est, moe):
        if self.db_run_id is None:
            return
        try:
            import db_datamart
            conn = db_datamart.connect()
            try:
                db_datamart.log_summary(conn, int(self.db_run_id), int(step), str(mode), float(mean_mph),
                                        int(kept_n), int(total_n), float(madv), float(sigma_est), float(moe))
                conn.commit()
            finally:
                conn.close()
        except:
            pass

    def log_sample(self, direction_str, step, mode, mph_val):
        try:
            f = open(self.history_samples_csv, "a")
            f.write("%s,%s,%d,%s,%d,%s,%.4f\n" % (
                now_str().replace(",", " "),
                self._engine_label_short(),
                int(self.addr),
                str(direction_str),
                int(step),
                str(mode),
                float(mph_val)
            ))
            f.close()
        except:
            pass

    def log_summary(self, direction_str, step, mode, mean_mph, kept_n, total_n, madv, sigma_est, moe):
        try:
            f = open(self.history_summary_csv, "a")
            f.write("%s,%s,%d,%s,%d,%s,%.4f,%d,%d,%.4f,%.4f,%.4f\n" % (
                now_str().replace(",", " "),
                self._engine_label_short(),
                int(self.addr),
                str(direction_str),
                int(step),
                str(mode),
                float(mean_mph),
                int(kept_n),
                int(total_n),
                float(madv),
                float(sigma_est),
                float(moe)
            ))
            f.close()
        except:
            pass
        try:
            self.db_log_summary(step, mode, mean_mph, kept_n, total_n, madv, sigma_est, moe)
        except:
            pass

    def _elapsed_now(self):
        try:
            if self.auto_start_time is None:
                return float(self.auto_last_elapsed)
            return max(0.0, time.time() - float(self.auto_start_time))
        except:
            return float(self.auto_last_elapsed)

    def auto_elapsed_seconds(self):
        try:
            if self.automation_running:
                return self._elapsed_now()
            return float(self.auto_last_elapsed)
        except:
            return 0.0

    def _capture_elapsed_to_last(self):
        try:
            self.auto_last_elapsed = float(self._elapsed_now())
        except:
            self.auto_last_elapsed = 0.0

    def _get_throttle_forward(self):
        try:
            if self.throttle is not None:
                return bool(self.throttle.getIsForward())
        except:
            pass
        return self.current_dir_forward

    def _set_dir_forward(self, fwd):
        with self.lock:
            self.current_dir_forward = bool(fwd)
            try:
                if self.throttle is not None:
                    self.throttle.setIsForward(bool(fwd))
            except:
                pass
            self._clear_buffers_for_current_direction()
            self._clear_sequence_history()
            # ignore 2 samples after change (requested)
            self.auto_skip_remaining = 2

    def _apply_step(self, step):
        step = clamp_int(int(step), 0, 28)
        with self.lock:
            self.current_step = step
            self._clear_buffers_for_current_direction()
            self._clear_sequence_history()
            # ignore 2 samples after change (requested)
            self.auto_skip_remaining = 2
            try:
                if self.throttle is not None:
                    self.throttle.setSpeedSetting(step / 28.0)
            except:
                pass

    def _clear_sequence_history(self):
        self.seq_hist = []
        self.last_sensor_idx = None
        self.seg4_anchor = None

    def _clear_buffers_for_current_direction(self):
        if self.current_dir_forward:
            self.forward_buf = []
        else:
            self.reverse_buf = []

    def _ma3(self, raw_mph):
        if raw_mph is None:
            return None
        buf = self.forward_buf if self.current_dir_forward else self.reverse_buf
        buf.append(float(raw_mph))
        if len(buf) > 3:
            buf.pop(0)
        return mean(buf)

    def expected_next(self, idx_1based, forward, delta):
        if forward:
            return ((idx_1based - 1 + delta) % NUM_BLOCKS) + 1
        else:
            return ((idx_1based - 1 - delta) % NUM_BLOCKS) + 1

    def is_valid_transition(self, last_idx, current_idx, forward):
        return (last_idx is not None) and (current_idx == self.expected_next(last_idx, forward, 1))

    def compute_expected_mph_for_step(self, step):
        s = int(step)
        if s <= 0:
            return None
        tmin = float(self.target_min_mph)
        tmax = float(self.target_max_mph)
        if tmax < tmin:
            tmp = tmin
            tmin = tmax
            tmax = tmp
        if s < 1:
            s = 1
        if s > 28:
            s = 28
        frac = (s - 1) / 27.0
        return tmin + frac * (tmax - tmin)

    def set_live_row(self, row_key, speed_str, exp_str, var_str, lap_str, step_str):
        d = self.live_rows.get(row_key, None)
        if d is None:
            d = {"speed": "", "exp": "", "var": "", "lap": "", "step": "", "refresh": ""}
            self.live_rows[row_key] = d
        d["speed"] = speed_str
        d["exp"] = exp_str
        d["var"] = var_str
        d["lap"] = lap_str
        d["step"] = step_str
        d["refresh"] = now_str()

    def _fmt_mph(self, v):
        if v is None:
            return ""
        try:
            return "%.2f" % float(v)
        except:
            return ""

    def _fmt_var(self, v):
        if v is None:
            return ""
        try:
            return "%+.2f" % float(v)
        except:
            return ""

    def _fmt_sec(self, v):
        if v is None:
            return ""
        try:
            return "%.2f" % float(v)
        except:
            return ""

    def _automation_phase_label(self):
        if self.auto_phase.startswith("FWD"):
            return "Forward"
        if self.auto_phase.startswith("REV"):
            return "Reverse"
        return "Idle"

    def _record_lap(self, dt):
        sp = mph_from(CIRCLE_LENGTH_SCALE_FEET, dt)
        if sp is None:
            return
        self.last_lap_speed = sp

        exp = self.compute_expected_mph_for_step(self.current_step)
        var = (sp - exp) if (exp is not None) else None

        self.set_live_row("LAP", self._fmt_mph(sp), self._fmt_mph(exp), self._fmt_var(var), self._fmt_sec(dt), str(self.current_step))
        if self.dashboard is not None:
            self.dashboard.update_summary_fields()
        self._automation_consider_sample("LAP", sp)

    def _stats_dict_for_dir(self):
        if self.auto_phase.startswith("FWD"):
            return self.auto_step_stats_fwd
        if self.auto_phase.startswith("REV"):
            return self.auto_step_stats_rev
        # fallback to forward bucket
        return self.auto_step_stats_fwd

    def _measured_dict_for_dir(self):
        if self.auto_phase.startswith("FWD"):
            return self.auto_measured_fwd
        if self.auto_phase.startswith("REV"):
            return self.auto_measured_rev
        return self.auto_measured_fwd

    def _automation_consider_sample(self, mode, mph_val):
        if not self.automation_running:
            return

        # ignore 2 samples after speed/dir change (requested)
        if self.auto_skip_remaining > 0:
            self.auto_skip_remaining -= 1
            return

        step = int(self.current_step)
        expected_mode = self.auto_mode_by_step.get(step, None)
        if expected_mode != mode:
            return

        # log every captured sample (historical dataset)
        try:
            self.log_sample("FWD" if self.auto_phase.startswith("FWD") else "REV", step, mode, float(mph_val))
        except:
            pass

        if self.auto_phase in ["FWD_WARMUP", "REV_WARMUP"]:
            if mode == "LAP":
                self.auto_warmup_laps_remaining -= 1
                self.automation_status = "%s warmup: %d lap(s) remaining at step 28" % (self._automation_phase_label(), self.auto_warmup_laps_remaining)
                if self.dashboard is not None:
                    self.dashboard.set_automation_status(self.automation_status)
                if self.auto_warmup_laps_remaining <= 0:
                    if self.auto_phase == "FWD_WARMUP":
                        self.auto_phase = "FWD_MEASURE"
                    else:
                        self.auto_phase = "REV_MEASURE"
                    self._automation_reset_for_phase_and_advance(first_step=True)
            return

        stats_map = self._stats_dict_for_dir()
        info = stats_map.get(step, None)
        if info is None:
            info = {"mode": mode, "samples": [], "kept": [], "mad": 0.0, "sigma": 0.0, "moe": 0.0}
            stats_map[step] = info

        info["mode"] = mode
        info["samples"].append(float(mph_val))
        remaining = MEASURE_SAMPLES_PER_STEP - len(info["samples"])
        self.automation_status = "%s step %d (%s): %d/%d (remaining %d)" % (self._automation_phase_label(), step, mode, len(info["samples"]), MEASURE_SAMPLES_PER_STEP, max(0, remaining))
        if self.dashboard is not None:
            self.dashboard.set_automation_status(self.automation_status)

        if len(info["samples"]) >= MEASURE_SAMPLES_PER_STEP:
            kept = mad_filter(info["samples"], MAD_K)
            m = mean(kept)
            if m is None:
                kept = list(info["samples"])
                m = mean(kept)
            if m is None:
                m = 0.0

            madv, sigma = mad_stats(info["samples"])
            # Use robust sigma estimate as a "margin of error" proxy (sigma).
            # Also compute SEM of kept if possible and pick max to be conservative.
            sem = 0.0
            try:
                if kept and len(kept) >= 2:
                    sem = stdev(kept) / math.sqrt(float(len(kept)))
            except:
                sem = 0.0
            moe = max(float(sigma), float(sem))

            info["kept"] = list(kept)
            info["mad"] = float(madv)
            info["sigma"] = float(sigma)
            info["moe"] = float(moe)

            measured_map = self._measured_dict_for_dir()
            measured_map[step] = float(m)

            # summary dataset line (historical)
            try:
                self.log_summary("FWD" if self.auto_phase.startswith("FWD") else "REV",
                                 step, mode, float(m), len(kept), len(info["samples"]), float(madv), float(sigma), float(moe))
            except:
                pass

            self.automation_status = "%s step %d complete (%s): %.2f mph (kept %d/%d)" % (self._automation_phase_label(), step, mode, float(m), len(kept), len(info["samples"]))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
            self._automation_advance_step_locked()

    def _automation_reset_for_phase_and_advance(self, first_step=False):
        self.auto_current_step_index = -1
        if first_step:
            self._automation_advance_step_locked()

    def _compute_results_payload(self):
        return speed_match_math.compute_results_payload(
            self.target_min_mph,
            self.target_max_mph,
            self.auto_measured_fwd,
            self.auto_measured_rev,
            self.auto_step_stats_fwd,
            self.auto_step_stats_rev,
            self.baseline_table
        )

    def _forward_tolerance_check(self, rows, pct_limit):
        measured_steps = {}
        try:
            for k in self.auto_measured_fwd.keys():
                measured_steps[int(k)] = True
        except:
            pass
        if not measured_steps:
            return (False, 'No forward measurements available.')

        offenders = []
        for row in rows:
            try:
                step = int(row.get('step'))
            except:
                continue
            if not measured_steps.get(step, False):
                continue
            pct_s = str(row.get('f_pct', '')).strip()
            if not pct_s:
                continue
            try:
                pct_v = abs(float(pct_s))
            except:
                continue
            if pct_v > float(pct_limit):
                offenders.append((pct_v, step, pct_s))
        if offenders:
            offenders.sort(reverse=True)
            worst = offenders[0]
            return (False, 'Worst forward deviation step %d = %s%%' % (int(worst[1]), str(worst[2])))
        return (True, 'All measured forward steps within %.1f%%.' % float(pct_limit))

    def _get_addressed_programmer(self):
        """
        Best-effort lookup of an Ops Mode (Programming-on-the-Main) programmer for the selected address.

        JMRI API signatures vary by version; we try multiple call shapes.
        """
        # Primary: AddressedProgrammerManager
        try:
            apm = jmri.InstanceManager.getDefault(jmri.AddressedProgrammerManager)
        except:
            apm = None

        addr = None
        try:
            addr = jmri.DccLocoAddress(int(self.addr), bool(self.addr_is_long))
        except:
            addr = None

        if apm is not None:
            # Newer: getAddressedProgrammer(DccLocoAddress, ProgListener)
            try:
                p = apm.getAddressedProgrammer(addr, None)
                if p is not None:
                    return p
            except:
                pass
            # Older: getAddressedProgrammer(isLong, address)
            try:
                p = apm.getAddressedProgrammer(bool(self.addr_is_long), int(self.addr))
                if p is not None:
                    return p
            except:
                pass
            # Some builds: getAddressedProgrammer(DccLocoAddress)
            try:
                p = apm.getAddressedProgrammer(addr)
                if p is not None:
                    return p
            except:
                pass

        # Fallback: ProgrammerManager (some layouts expose addressed ops programmer here)
        try:
            pm = jmri.InstanceManager.getDefault(jmri.ProgrammerManager)
        except:
            pm = None
        if pm is not None:
            try:
                p = pm.getAddressedProgrammer(addr)
                if p is not None:
                    return p
            except:
                pass
            try:
                p = pm.getAddressedProgrammer(bool(self.addr_is_long), int(self.addr))
                if p is not None:
                    return p
            except:
                pass

        return None


    def _coerce_cv_table_pairs(self, cv_table):
        """Accept either flat CV values [v67..v94] or pair tuples [(67,v67)..(94,v94)].
        Always return sorted (cv, value) pairs for CV67..CV94.
        """
        if cv_table is None:
            raise TypeError('CV table is None')
        if not isinstance(cv_table, (list, tuple)):
            raise TypeError('CV table must be list/tuple, got %s' % str(type(cv_table)))
        if len(cv_table) == 0:
            raise TypeError('CV table is empty')

        first = cv_table[0]
        out = []
        if isinstance(first, (list, tuple)):
            for row in cv_table:
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    raise TypeError('CV pair row invalid: %s' % str(row))
                out.append((int(row[0]), int(row[1])))
        else:
            for i, val in enumerate(list(cv_table)):
                out.append((67 + i, int(val)))

        out.sort(key=lambda x: x[0])
        return out

    def _cv_pair_table_to_values(self, cv_table):
        pairs = self._coerce_cv_table_pairs(cv_table)
        vals = []
        for cv_num, cv_val in pairs:
            vals.append(int(cv_val))
        return vals

    def _build_pom_test_table(self):
        """Return a deterministic CV67-94 table for POM testing.

        This intentionally writes a visible ramp (5,10,15,...) capped at 255 so you can confirm
        Ops Mode writes from the decoder programmer UI.
        """
        table = []
        for i in range(28):
            cv = 67 + i
            val = 5 * (i + 1)
            if val > 255:
                val = 255
            table.append((cv, val))
        return table

    def _write_speed_table_pom(self, cv_table):
        """Write CV67-94 (speed table) using Ops Mode (POM) in a serialized queue.

        JMRI Programmer.writeCV() returns before the write is complete; callers must wait for
        ProgListener.programmingOpReply(...) before starting the next write, otherwise Digitrax
        LocoNet SlotManager will throw "programmer in use".  

        This implementation mirrors JMRI's own speed table writer behavior (write-next-on-busy-clear),
        but does it in a Jython-friendly queue with watchdog timeouts. 
        """
        prog = self._get_addressed_programmer()
        if prog is None:
            raise Exception('No addressed programmer available for POM.')

        try:
            log_console('POM programmer=%s' % str(prog.__class__), 'INFO')
            log_console('POM: attempting to set Ops mode', 'INFO')
        except:
            pass

        # Put programmer in Ops (on-main) mode if the API supports it.
        try:
            if hasattr(prog, 'setMode'):
                for mode_name in ['OPSBYTE', 'OPSByte', 'OPS', 'OPSBIT', 'OPSBit']:
                    try:
                        mode_obj = getattr(jmri.ProgMode, mode_name)
                        prog.setMode(mode_obj)
                        break
                    except:
                        pass
        except:
            pass

        # Defensive copy + normalize to sorted CV order.
        items = self._coerce_cv_table_pairs(cv_table)

        from javax.swing import Timer

        state = {
            'idx': 0,
            'requested': 0,
            'callbacks': 0,
            'timeouts': 0,
            'errors': [],
            't0': time.time(),
            'done': False,
            'watchdog': None,
            'retry_timer': None,
        }

        def _is_busy_text(err_text):
            t = (err_text or '').lower()
            return ('programmer in use' in t) or ('already in use' in t) or ('busy' in t)

        def _log_summary(level='INFO'):
            try:
                elapsed = max(0.0, time.time() - state['t0'])
            except:
                elapsed = -1
            try:
                log_console(
                    'POM summary: requested=%d/%d callbacks=%d timeouts=%d errors=%d elapsed_s=%.2f' % (
                        state['requested'],
                        len(items),
                        state['callbacks'],
                        state['timeouts'],
                        len(state['errors']),
                        elapsed,
                    ),
                    level,
                )
            except:
                pass
            if state['errors']:
                cv_num, cv_val, msg = state['errors'][0]
                try:
                    log_console('POM first error: CV%d=%d %s' % (cv_num, cv_val, msg), 'ERROR')
                except:
                    pass

        class _Listener(jmri.ProgListener):
            def programmingOpReply(self, value, status):
                # Called on the GUI thread per JMRI contract. 
                try:
                    state['callbacks'] += 1
                except:
                    pass

                try:
                    if state['watchdog'] is not None:
                        state['watchdog'].stop()
                        state['watchdog'] = None
                except:
                    pass

                st = 0
                try:
                    st = int(status)
                except:
                    st = 0

                if st != 0:
                    try:
                        msg = prog.decodeErrorCode(st)
                    except:
                        msg = 'status=%s' % str(st)
                    try:
                        cv_num, cv_val = items[state['idx']]
                        state['errors'].append((cv_num, cv_val, 'write failed: %s' % str(msg)))
                    except:
                        pass
                    state['done'] = True
                    _log_summary('ERROR')
                    return

                # Advance to next CV.
                state['idx'] += 1
                t = Timer(1, _start_next)
                t.setRepeats(False)
                t.start()

        listener = _Listener()

        def _on_watchdog_timeout():
            # If no callback arrives, treat as "unknown" completion and try to proceed.
            try:
                cv_num, cv_val = items[state['idx']]
            except:
                return
            try:
                state['timeouts'] += 1
                log_console('POM timeout (no callback): CV%d=%d (will proceed with retries)' % (cv_num, cv_val), 'INFO')
            except:
                pass
            state['idx'] += 1
            _start_next()

        def _schedule_watchdog():
            try:
                if state['watchdog'] is not None:
                    state['watchdog'].stop()
            except:
                pass
            wd = Timer(7000, lambda e: _on_watchdog_timeout())
            wd.setRepeats(False)
            state['watchdog'] = wd
            wd.start()

        def _start_next(event=None):
            if state['done']:
                return
            if state['idx'] >= len(items):
                state['done'] = True
                _log_summary('INFO')
                try:
                    self.auto_last_recommended_table = self._cv_pair_table_to_values(cv_table)
                    self.baseline_table = self._cv_pair_table_to_values(cv_table)
                except:
                    pass
                return

            cv_num, cv_val = items[state['idx']]

            try:
                log_console('POM write requested: CV%d=%d' % (cv_num, cv_val), 'INFO')
            except:
                pass

            # Attempt to start this CV write; if busy, schedule retry.
            try:
                # LnOpsModeProgrammer is picky about argument types; prefer String/String.
                try:
                    prog.writeCV(str(cv_num), str(cv_val), listener)
                except:
                    # Fallback signatures (varies by JMRI/Jython reflection)
                    try:
                        prog.writeCV(str(cv_num), int(cv_val), listener)
                    except:
                        prog.writeCV(int(cv_num), int(cv_val), listener)
                state['requested'] += 1
                _schedule_watchdog()
                return
            except:
                err = sys.exc_info()[1]
                if _is_busy_text(str(err)):
                    try:
                        log_console('POM busy, retrying: CV%d=%d (%s)' % (cv_num, cv_val, str(err)), 'INFO')
                    except:
                        pass
                    # retry after backoff
                    try:
                        if state['retry_timer'] is not None:
                            state['retry_timer'].stop()
                    except:
                        pass
                    rt = Timer(900, _start_next)
                    rt.setRepeats(False)
                    state['retry_timer'] = rt
                    rt.start()
                    return

                try:
                    state['errors'].append((cv_num, cv_val, 'start failed: %s' % str(err)))
                except:
                    pass
                state['done'] = True
                _log_summary('ERROR')
                return

        # Kick off the first write immediately.
        _start_next()


    def _stall_recovery_table(self, stalled_step):
        try:
            s = int(stalled_step)
        except:
            s = 1
        if s < 1:
            s = 1
        if s > 28:
            s = 28
        donor_idx = s
        if donor_idx > 27:
            donor_idx = 27
        table = list(self.baseline_table)
        donor_cv = int(table[donor_idx])
        for i in range(0, s):
            table[i] = donor_cv
        return table

    def _handle_fwd_pom_stall(self, reason_s):
        self._capture_elapsed_to_last()
        stalled_step = int(self.current_step)
        try:
            run_id, rows_written = self._persist_results_rows_to_db()
        except:
            run_id, rows_written = (None, 0)
        if int(self.auto_iter_count) >= int(self.auto_iter_max):
            self.automation_running = False
            self.auto_phase = 'DONE'
            self._apply_step(0)
            self.automation_status = 'Tune Loco stalled at step %d and reached max iterations (%d). Results saved to DB (run_id=%s, rows=%s).' % (stalled_step, int(self.auto_iter_max), str(run_id), str(rows_written))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)
            return
        try:
            rec_table = self._stall_recovery_table(stalled_step)
            rec_table = self._coerce_cv_table_pairs(rec_table)
            try:
                log_console('Tune Loco stall-recovery CV table=%s' % str(rec_table), 'INFO')
            except:
                pass
            if self.dashboard is not None:
                self.dashboard.set_automation_status('Tune Loco stall detected at step %d. Writing stall-recovery CV67-94 by POM...' % stalled_step)
            # Ensure locomotive is stopped on main before Ops-Mode writes
            try:
                self._apply_step(0)
            except:
                pass
            try:
                time.sleep(0.50)
            except:
                pass
            self._write_speed_table_pom(rec_table)
            self.auto_iter_count = int(self.auto_iter_count) + 1
            self._prepare_forward_iteration_restart()
            return
        except:
            self.automation_running = False
            self.auto_phase = 'DONE'
            self._apply_step(0)
            self.automation_status = 'Tune Loco stalled at step %d, then POM write failed: %s Results saved to DB (run_id=%s, rows=%s).' % (stalled_step, str(sys.exc_info()[1]), str(run_id), str(rows_written))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)
            return

    def _prepare_forward_iteration_restart(self):
        self.auto_measured_fwd = {}
        self.auto_step_stats_fwd = {}
        self._set_dir_forward(True)
        self.auto_phase = 'FWD_WARMUP'
        try:
            self.db_begin_run('FWD', self.auto_run_mode)
        except:
            pass
        self.auto_warmup_laps_remaining = 3
        self.auto_current_step_index = 0
        self.auto_skip_remaining = 2
        self._apply_step(28)
        self.last_sensor_activity_time = time.time()
        if self.dashboard is not None:
            self.dashboard.set_step_spinner_value(28)
            self.dashboard.update_summary_fields()
            self.dashboard.set_automation_status('Tune Loco iteration %d/%d: forward warmup restart at step 28...' % (int(self.auto_iter_count), int(self.auto_iter_max)))
        self.automation_status = 'Tune Loco iteration %d/%d: forward warmup restart at step 28...' % (int(self.auto_iter_count), int(self.auto_iter_max))


    def _final_tune_loco_pom_write(self, rec_vals, detail, final_label):
        rows_written = 0
        run_id = None
        final_pom = False
        final_pom_msg = ''
        try:
            self._apply_step(0)
        except:
            pass
        try:
            if rec_vals is not None:
                rec_table = self._coerce_cv_table_pairs(rec_vals)
                try:
                    log_console('Tune Loco final CV table=%s' % str(rec_table), 'INFO')
                except:
                    pass
                self._write_speed_table_pom(rec_table)
                final_pom = True
                final_pom_msg = ' Final POM wrote %d CVs.' % len(rec_table)
        except:
            final_pom = False
            final_pom_msg = ' Final POM failed: %s.' % str(sys.exc_info()[1])
            try:
                log_console('Tune Loco final POM failed: %s' % str(sys.exc_info()[1]), 'ERROR')
            except:
                pass
        try:
            run_id, rows_written = self._persist_results_rows_to_db()
        except:
            run_id, rows_written = (None, 0)
        self.automation_running = False
        self.auto_phase = 'DONE'
        try:
            self._apply_step(0)
        except:
            pass
        try:
            if self.dashboard is not None:
                self.dashboard.set_step_spinner_value(0)
        except:
            pass
        return (run_id, rows_written, final_pom_msg)

    def _handle_forward_iteration_complete(self):
        payload = self._compute_results_payload()
        rows = payload.get('rows', [])
        rec_vals = payload.get('f_tbl', None)
        ok, detail = self._forward_tolerance_check(rows, self.auto_iter_tolerance_pct)
        if ok:
            self._capture_elapsed_to_last()
            run_id, rows_written, final_pom_msg = self._final_tune_loco_pom_write(rec_vals, detail, 'complete')
            self.automation_status = 'Tune Loco complete after %d iteration(s). %s%s Results saved to DB (run_id=%s, rows=%s).' % (int(self.auto_iter_count), str(detail), str(final_pom_msg), str(run_id), str(rows_written))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)
            return

        if rec_vals is None:
            self._capture_elapsed_to_last()
            self.automation_running = False
            self.auto_phase = 'DONE'
            self._apply_step(0)
            run_id, rows_written = self._persist_results_rows_to_db()
            self.automation_status = 'Tune Loco stopped: no recommended CV table available. Results saved to DB (run_id=%s, rows=%s).' % (str(run_id), str(rows_written))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)
            return

        if int(self.auto_iter_count) >= int(self.auto_iter_max):
            self._capture_elapsed_to_last()
            run_id, rows_written, final_pom_msg = self._final_tune_loco_pom_write(rec_vals, detail, 'max')
            self.automation_status = 'Tune Loco reached max iterations (%d). %s%s Results saved to DB (run_id=%s, rows=%s).' % (int(self.auto_iter_max), str(detail), str(final_pom_msg), str(run_id), str(rows_written))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)
            return

        try:
            rec_table = self._coerce_cv_table_pairs(rec_vals)

            try:
                log_console('Tune Loco recommended CV table=%s' % str(rec_table), 'INFO')
            except:
                pass

            if self.dashboard is not None:
                self.dashboard.set_automation_status('Tune Loco iteration %d/%d outside tolerance. %s Starting POM write...' % (int(self.auto_iter_count), int(self.auto_iter_max), str(detail)))
            self._write_speed_table_pom(rec_table)
        except:
            self._capture_elapsed_to_last()
            self.automation_running = False
            self.auto_phase = 'DONE'
            self._apply_step(0)
            self.automation_status = 'Tune Loco POM failed: %s' % str(sys.exc_info()[1])
            try:
                log_console('Tune Loco POM failed: %s' % str(sys.exc_info()[1]), 'ERROR')
            except:
                pass
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)
            return

        self.auto_iter_count += 1
        self._prepare_forward_iteration_restart()

    def _stage_reverse_warmup(self):
        self._set_dir_forward(False)
        if self.dashboard is not None:
            self.dashboard.update_summary_fields()
        self.auto_phase = "REV_WARMUP"
        try:
            self.db_begin_run("REV", self.auto_run_mode)
        except:
            pass
        self.auto_warmup_laps_remaining = 3
        self.auto_current_step_index = 0
        self.auto_skip_remaining = 2
        self._apply_step(28)
        self.last_sensor_activity_time = time.time()
        if self.dashboard is not None:
            self.dashboard.set_step_spinner_value(28)
            self.dashboard.set_automation_status("Reverse warmup: 3 laps at step 28...")
        self.automation_status = "Reverse warmup: 3 laps at step 28..."

    def _automation_finish_forward_switch_to_reverse(self):
        if self.auto_run_mode == "FULL":
            self.automation_status = "Forward complete. Switching to reverse warmup automatically..."
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
            self._stage_reverse_warmup()
        elif self.auto_run_mode == "FWD_POM":
            self._handle_forward_iteration_complete()
        else:
            self._capture_elapsed_to_last()
            self.automation_running = False
            self.auto_phase = "DONE"
            self._apply_step(0)
            try:
                if self.dashboard is not None:
                    self.dashboard.set_step_spinner_value(0)
            except:
                pass
            run_id, rows_written = self._persist_results_rows_to_db()
            self.automation_status = "Forward automation complete. Results saved to DB (run_id=%s, rows=%s)." % (str(run_id), str(rows_written))
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
                self.dashboard.set_automation_buttons_enabled(True)

    def _automation_advance_step_locked(self):
        if not self.automation_running:
            return

        self.auto_current_step_index += 1
        if self.auto_current_step_index >= len(self.auto_step_plan):
            if self.auto_phase == "FWD_MEASURE":
                self._automation_finish_forward_switch_to_reverse()
                return
            if self.auto_phase == "REV_MEASURE":
                self._capture_elapsed_to_last()
                self.automation_running = False
                self.auto_phase = "DONE"
                self._apply_step(0)
                try:
                    if self.dashboard is not None:
                        self.dashboard.set_step_spinner_value(0)
                except:
                    pass
                run_id, rows_written = self._persist_results_rows_to_db()
                self.automation_status = "Automation complete. Results saved to DB (run_id=%s, rows=%s)." % (str(run_id), str(rows_written))
                if self.dashboard is not None:
                    self.dashboard.set_automation_status(self.automation_status)
                    self.dashboard.set_automation_buttons_enabled(True)
                return

            self._capture_elapsed_to_last()
            self.automation_running = False
            if self.dashboard is not None:
                self.dashboard.set_automation_buttons_enabled(True)
            return

        next_step = int(self.auto_step_plan[self.auto_current_step_index])
        self.auto_skip_remaining = 2
        self._apply_step(next_step)
        self.last_sensor_activity_time = time.time()
        if self.dashboard is not None:
            self.dashboard.set_step_spinner_value(next_step)
            self.dashboard.update_summary_fields()
        self.automation_status = "%s advancing to step %d (%s)..." % (self._automation_phase_label(), next_step, self.auto_mode_by_step.get(next_step, ""))
        if self.dashboard is not None:
            self.dashboard.set_automation_status(self.automation_status)

    def build_automation_plan(self):
        plan = [28, 24, 20, 16, 12, 8, 7, 6, 5, 4, 3, 2, 1]
        mode = {}
        for s in [28, 24, 20, 16]:
            mode[s] = "LAP"
        for s in [12, 8]:
            mode[s] = "SEG4"
        for s in [7, 6, 5, 4, 3, 2, 1]:
            mode[s] = "SEG1"
        self.auto_step_plan = plan
        self.auto_mode_by_step = mode

    def reset_automation_results(self):
        # clears results + per-step stats so routine can be rerun
        with self.lock:
            self.automation_running = False
            self.auto_phase = "IDLE"
            self.automation_status = "Automation reset."
            self.auto_measured_fwd = {}
            self.auto_measured_rev = {}
            self.auto_step_stats_fwd = {}
            self.auto_step_stats_rev = {}
            self.auto_current_step_index = 0
            self.auto_warmup_laps_remaining = 0
            self.auto_skip_remaining = 0
            self.auto_start_time = None
            self.auto_last_elapsed = 0.0
            self.last_sensor_activity_time = time.time()
            self.auto_iter_enabled = False
            self.auto_iter_count = 0
            self.auto_last_recommended_table = None
        if self.dashboard is not None:
            self.dashboard.set_automation_status(self.automation_status)
            self.dashboard.set_automation_buttons_enabled(True)

    def start_automation(self, run_mode):
        if self.automation_running:
            return
        self.build_automation_plan()

        self.auto_run_mode = str(run_mode)
        self.auto_iter_enabled = (self.auto_run_mode == "FWD_POM")
        self.auto_iter_count = 1 if self.auto_iter_enabled else 0
        self.auto_last_recommended_table = None

        self.automation_running = True
        self.auto_start_time = time.time()
        self.auto_last_elapsed = 0.0

        self.auto_warmup_laps_remaining = 3
        self.auto_current_step_index = 0
        self.auto_skip_remaining = 2
        self.last_sensor_activity_time = time.time()

        if self.auto_run_mode == "REV":
            self.auto_phase = "REV_WARMUP"
            self._set_dir_forward(False)
            try:
                self.db_begin_run("REV", self.auto_run_mode)
            except:
                pass
            self._apply_step(28)
            if self.dashboard is not None:
                self.dashboard.set_step_spinner_value(28)
                self.dashboard.update_summary_fields()
                self.dashboard.set_automation_buttons_enabled(False)
                self.dashboard.set_automation_status("Reverse warmup: 3 laps at step 28...")
            self.automation_status = "Reverse warmup: 3 laps at step 28..."
        else:
            self.auto_phase = "FWD_WARMUP"
            self._set_dir_forward(True)
            try:
                self.db_begin_run("FWD", self.auto_run_mode)
            except:
                pass
            self._apply_step(28)
            if self.dashboard is not None:
                self.dashboard.set_step_spinner_value(28)
                self.dashboard.update_summary_fields()
                self.dashboard.set_automation_buttons_enabled(False)
                if self.auto_run_mode == "FWD_POM":
                    self.dashboard.set_automation_status("Tune Loco iteration 1/%d: forward warmup at step 28..." % int(self.auto_iter_max))
                else:
                    self.dashboard.set_automation_status("Forward warmup: 3 laps at step 28...")
            if self.auto_run_mode == "FWD_POM":
                self.automation_status = "Tune Loco iteration 1/%d: forward warmup at step 28..." % int(self.auto_iter_max)
            else:
                self.automation_status = "Forward warmup: 3 laps at step 28..."


    def _current_activity_timeout_sec(self):
        try:
            step = int(self.current_step)
        except:
            step = 0
        mode = self.auto_mode_by_step.get(step, '')
        if self.auto_run_mode == 'FWD_POM' and self.auto_phase == 'FWD_MEASURE':
            return 40.0
        if self.auto_phase in ['FWD_WARMUP', 'REV_WARMUP']:
            return 75.0
        if mode == 'SEG1':
            if step <= 2:
                return 150.0
            if step <= 4:
                return 120.0
            return 90.0
        if mode == 'SEG4':
            return 90.0
        if mode == 'LAP':
            return 60.0
        return 75.0

    def _persist_results_rows_to_db(self):
        try:
            import db_datamart
            payload = self._compute_results_payload()
            rows = payload.get('rows', [])
            if not rows:
                return (None, 0)
            conn = db_datamart.connect()
            try:
                run_id = getattr(self, 'db_run_id', None)
                if run_id is None:
                    run_id = db_datamart.get_latest_run_id_for_loco(conn, self._db_loco_identity())
                if run_id is None:
                    return (None, 0)
                try:
                    ps = conn.prepareStatement('DELETE FROM speed_run_results_row WHERE run_id=?')
                    try:
                        ps.setInt(1, int(run_id))
                        ps.executeUpdate()
                    finally:
                        ps.close()
                except:
                    pass
                rows_written = db_datamart.log_results_rows(conn, run_id, rows)
                conn.commit()
                return (run_id, rows_written)
            finally:
                try:
                    conn.close()
                except:
                    pass
        except:
            return (None, 0)

    def stop_automation_only(self, reason):
        if not self.automation_running:
            return

        reason_s = str(reason)

        # In FULL mode: if forward times out, automatically switch to reverse warmup (no button).
        if ("No sensor activity" in reason_s) and self.auto_phase.startswith("FWD") and self.auto_run_mode == "FULL":
            self.automation_status = "Forward ended due to timeout. Switching to reverse warmup automatically..."
            if self.dashboard is not None:
                self.dashboard.set_automation_status(self.automation_status)
            self._stage_reverse_warmup()
            self.automation_running = True
            self.last_sensor_activity_time = time.time()
            if self.dashboard is not None:
                self.dashboard.set_automation_buttons_enabled(False)
            return

        if ("No sensor activity" in reason_s) and self.auto_phase == 'FWD_MEASURE' and self.auto_run_mode == 'FWD_POM':
            self._handle_fwd_pom_stall(reason_s)
            return

        self._capture_elapsed_to_last()
        self.automation_running = False
        try:
            run_id, rows_written = self._persist_results_rows_to_db()
        except:
            run_id, rows_written = (None, 0)
        self.automation_status = "Automation stopped: %s Results saved to DB (run_id=%s, rows=%s)" % (reason_s, str(run_id), str(rows_written))
        if self.dashboard is not None:
            self.dashboard.set_automation_status(self.automation_status)
            self.dashboard.set_automation_buttons_enabled(True)

    def on_sensor_active(self, sensor_system_name):
        self.last_sensor_activity_time = time.time()

        idx = None
        try:
            if sensor_system_name.startswith("LS"):
                idx = int(sensor_system_name[2:])
        except:
            idx = None
        if idx is None or idx < 1 or idx > 12:
            return

        with self.lock:
            forward = self._get_throttle_forward()

            if forward != self.current_dir_forward:
                self.current_dir_forward = forward
                self._clear_buffers_for_current_direction()
                self._clear_sequence_history()
                self.auto_skip_remaining = 2
                if self.dashboard is not None:
                    self.dashboard.update_summary_fields()

            tnow = time.time()

            if self.last_sensor_idx is None:
                self.last_sensor_idx = idx
                self.seq_hist = [(idx, tnow)]
                self.seg4_anchor = None
                if idx == 1:
                    self.lap_start_time = tnow
                return

            if not self.is_valid_transition(self.last_sensor_idx, idx, forward):
                self.last_sensor_idx = idx
                self.seq_hist = [(idx, tnow)]
                self.seg4_anchor = None
                if idx == 1:
                    self.lap_start_time = tnow
                return

            self.last_sensor_idx = idx
            self.seq_hist.append((idx, tnow))
            if len(self.seq_hist) > 8:
                self.seq_hist.pop(0)

            step = int(self.current_step)
            distance_feet = BLOCK_LENGTH_SCALE_FEET
            delta_blocks = 1

            if step >= 18 and step <= 28:
                distance_feet = 2.0 * BLOCK_LENGTH_SCALE_FEET
                delta_blocks = 2

            exp = self.compute_expected_mph_for_step(step)

            if delta_blocks == 1:
                if len(self.seq_hist) >= 2:
                    (idx_prev, t_prev) = self.seq_hist[-2]
                    dt = tnow - t_prev
                    raw_mph = mph_from(distance_feet, dt)
                    if raw_mph is not None:
                        ma = self._ma3(raw_mph)
                        var = (ma - exp) if (ma is not None and exp is not None) else None
                        self.set_live_row("LS%d" % idx, self._fmt_mph(ma), self._fmt_mph(exp), self._fmt_var(var), "", str(step))
                        self._automation_consider_sample("SEG1", ma)
            else:
                if len(self.seq_hist) >= 3:
                    (idx_2back, t_2back) = self.seq_hist[-3]
                    if idx == self.expected_next(idx_2back, forward, 2):
                        dt = tnow - t_2back
                        raw_mph = mph_from(distance_feet, dt)
                        if raw_mph is not None:
                            ma = self._ma3(raw_mph)
                            var = (ma - exp) if (ma is not None and exp is not None) else None
                            self.set_live_row("LS%d" % idx, self._fmt_mph(ma), self._fmt_mph(exp), self._fmt_var(var), "", str(step))
                    else:
                        self.seq_hist = [(self.seq_hist[-2][0], self.seq_hist[-2][1]), (idx, tnow)]

            if step in [12, 8]:
                if self.seg4_anchor is None:
                    self.seg4_anchor = (idx, tnow)
                else:
                    (idx_anchor, t_anchor) = self.seg4_anchor
                    if idx == self.expected_next(idx_anchor, forward, 4):
                        dt4 = tnow - t_anchor
                        mph4 = mph_from(4.0 * BLOCK_LENGTH_SCALE_FEET, dt4)
                        if mph4 is not None:
                            self._automation_consider_sample("SEG4", mph4)
                        self.seg4_anchor = (idx, tnow)

            if idx == 1:
                if self.lap_start_time is not None:
                    dtlap = tnow - self.lap_start_time
                    self._record_lap(dtlap)
                self.lap_start_time = tnow

    def attach_sensors(self):
        for i in range(1, 13):
            name = "LS%d" % i
            s = self.sensorManager.getSensor(name)
            if s is None:
                try:
                    s = self.sensorManager.provideSensor(name)
                except:
                    s = None
            if s is None:
                continue
            self.sensors[name] = s

        st = self

        class SListener(PropertyChangeListener):
            def __init__(self, sysname):
                self.sysname = sysname
            def propertyChange(self, ev):
                try:
                    if ev.getPropertyName() != "KnownState":
                        return
                    if int(ev.getNewValue()) == jmri.Sensor.ACTIVE:
                        st.on_sensor_active(self.sysname)
                except:
                    return

        for (name, s) in self.sensors.items():
            try:
                l = SListener(name)
                s.addPropertyChangeListener(l)
                self.sensor_listeners[name] = l
            except:
                pass

    def detach_sensors(self):
        for (name, s) in self.sensors.items():
            try:
                l = self.sensor_listeners.get(name, None)
                if l is not None:
                    s.removePropertyChangeListener(l)
            except:
                pass
        self.sensor_listeners = {}
        self.sensors = {}

    def release_throttle(self):
        try:
            if self.throttle is not None:
                try:
                    self.throttle.setSpeedSetting(0.0)
                except:
                    pass
                try:
                    self.throttleManager.releaseThrottle(self.throttle, self.throttle_listener)
                except:
                    try:
                        self.throttleManager.releaseThrottle(self.throttle, None)
                    except:
                        pass
        except:
            pass
        self.throttle = None
        self.throttle_listener = None

    def acquire_throttle_async(self, addr):
        addr = clamp_int(int(addr), 1, 99999)
        self.addr = addr
        self.addr_is_long = (addr > 127)

        self.release_throttle()

        st = self

        class TL(jmri.ThrottleListener):
            def notifyThrottleFound(self, t):
                st.throttle = t
                st.throttle_listener = self
                try:
                    t.setIsForward(bool(st.current_dir_forward))
                except:
                    pass
                try:
                    t.setSpeedSetting(st.current_step / 28.0)
                except:
                    pass
                if st.dashboard is not None:
                    st.dashboard.update_summary_fields()
                    st.dashboard.set_throttle_status("Throttle acquired for addr %d (%s)" % (st.addr, "long" if st.addr_is_long else "short"))

            def notifyFailedThrottleRequest(self, a, reason):
                if st.dashboard is not None:
                    st.dashboard.set_throttle_status("Throttle request failed for addr %d: %s" % (st.addr, str(reason)))

        listener = TL()

        try:
            if self.dashboard is not None:
                self.dashboard.set_throttle_status("Requesting throttle for addr %d (%s)..." % (self.addr, "long" if self.addr_is_long else "short"))
        except:
            pass

        requested = False
        last_err = None

        try:
            self.throttleManager.requestThrottle(int(self.addr), bool(self.addr_is_long), listener)
            requested = True
        except:
            err = sys.exc_info()[1]
            last_err = err
        if not requested:
            try:
                self.throttleManager.requestThrottle(int(self.addr), listener)
                requested = True
            except:
                err = sys.exc_info()[1]
                last_err = err
        if not requested:
            try:
                dcc = jmri.DccLocoAddress(int(self.addr), bool(self.addr_is_long))
                self.throttleManager.requestThrottle(dcc, listener)
                requested = True
            except:
                err = sys.exc_info()[1]
                last_err = err
        if not requested:
            if self.dashboard is not None:
                self.dashboard.set_throttle_status("Throttle request error: %s" % str(last_err))

# -------------------------------
# Results window + Dashboard
# -------------------------------
class ResultsTableModel(AbstractTableModel):
    def __init__(self, rows):
        AbstractTableModel.__init__(self)
        self.columns = [
            "Step", "Target",
            "FwdMeasuredEst", "FwdError", "Fwd%Dev", "FwdMOE",
            "FwdCVValue", "CurrentCVValue",
            "RevMeasuredEst", "RevError", "Rev%Dev", "RevMOE"
        ]
        self.rows = rows

    def getColumnCount(self):
        return len(self.columns)

    def getRowCount(self):
        return len(self.rows)

    def getColumnName(self, col):
        return self.columns[col]

    def getValueAt(self, row, col):
        r = self.rows[row]
        if col == 0:  return r.get("step", "")
        if col == 1:  return r.get("target", "")
        if col == 2:  return r.get("f_meas", "")
        if col == 3:  return r.get("f_err", "")
        if col == 4:  return r.get("f_pct", "")
        if col == 5:  return r.get("f_moe", "")
        if col == 6:  return r.get("f_tbl", "")
        if col == 7:  return r.get("cur_tbl", "")
        if col == 8:  return r.get("r_meas", "")
        if col == 9:  return r.get("r_err", "")
        if col == 10: return r.get("r_pct", "")
        if col == 11: return r.get("r_moe", "")
        return ""

    def isCellEditable(self, row, col):
        return False

class ResultsWindow(object):
    def __init__(self, state, target_min, target_max):
        self.state = state
        self.target_min = float(target_min)
        self.target_max = float(target_max)
        self.frame = JFrame("Results - %s" % state._engine_label_short())
        self.frame.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE)

        self.csv_path_field = JTextField(60)
        self.csv_path_field.setEditable(False)
        self.png_path_field = JTextField(60)
        self.png_path_field.setEditable(False)

        self.btn_export = JButton("Export CSV+PNG")
        self.btn_print_mph = JButton("Print MPH Chart")
        self.btn_print_cv = JButton("Print CV Chart")

        self.engine_label = self._engine_label()
        self.decoder_label = self._decoder_label()
        self.ts = now_str()

        self.results_rows, self.target_arr, self.fwd_arr, self.rev_arr, self.fwd_cv_arr, self.cur_cv_arr = self._compute_results()
        run_id = None
        rows_written = 0
        # Persist derived results rows to SQLite (best-effort; never break UI)
        try:
            import db_datamart
            conn = db_datamart.connect()
            try:
                run_id = getattr(self.state, "db_run_id", None)
                if run_id is None:
                    run_id = db_datamart.get_latest_run_id_for_loco(conn, self.state._db_loco_identity())
                rows_written = db_datamart.log_results_rows(conn, run_id, self.results_rows)
                conn.commit()
            finally:
                try:
                    conn.close()
                except:
                    pass
            try:
                if hasattr(self.state, "dashboard") and self.state.dashboard is not None:
                    self.state.dashboard.set_automation_status("Results saved to DB (run_id=%s, rows=%s)" % (str(run_id), str(rows_written)))
            except:
                pass
        except:
            try:
                if hasattr(self.state, "dashboard") and self.state.dashboard is not None:
                    self.state.dashboard.set_automation_status("Results DB save failed: %s" % str(sys.exc_info()[1]))
            except:
                pass

        # Unified DB-backed results screen for both live runs and history recall.
        if run_id is not None and int(rows_written) > 0:
            try:
                import results_db_view
                self.db_results_window = results_db_view.open_results_for_run_id(run_id, None)
                if self.db_results_window is not None:
                    self.frame = self.db_results_window.frame
                    return
            except:
                try:
                    if hasattr(self.state, "dashboard") and self.state.dashboard is not None:
                        self.state.dashboard.set_automation_status("Results DB-backed open failed: %s" % str(sys.exc_info()[1]))
                except:
                    pass

        self.table_model = ResultsTableModel(self.results_rows)
        self.table = JTable(self.table_model)
        self.table.setFillsViewportHeight(True)

        self.chart_mph = SpeedChartPanel()
        self.chart_mph.setData(self.target_arr, self.fwd_arr, self.rev_arr)
        self.chart_mph.setMetaLines(["Engine: %s" % self.engine_label, self.decoder_label, "Generated: %s" % self.ts])

        self.chart_cv = DecoderTableBarChartPanel()
        self.chart_cv.setData(self.fwd_cv_arr, self.cur_cv_arr)
        self.chart_cv.setMetaLines(["Engine: %s" % self.engine_label, self.decoder_label, "Generated: %s" % self.ts])

        self._build_ui()
        self._wire_actions()


        rw = self
        class W(WindowAdapter):
            def windowClosing(self, e):
                rw.shutdown()
            def windowClosed(self, e):
                rw.shutdown()
        self.frame.addWindowListener(W())

        self.frame.pack()
        self.frame.setLocationRelativeTo(None)
        self.frame.setVisible(True)

    def _engine_label(self):
        try:
            sel = self.state.selected_roster_entry
            if sel is not None:
                return str(sel.display)
        except:
            pass
        return "Addr %d" % int(self.state.addr)

    def _decoder_label(self):
        try:
            sel = self.state.selected_roster_entry
            if sel is not None and sel.fileName:
                return "Decoder file: %s" % str(sel.fileName)
        except:
            pass
        return "Decoder file: (unknown)"

    def _compute_targets_28(self):
        if self.target_max < self.target_min:
            a = self.target_min
            self.target_min = self.target_max
            self.target_max = a
        t = []
        for s in range(1, 29):
            frac = (s - 1) / 27.0
            t.append(self.target_min + frac * (self.target_max - self.target_min))
        return t

    def _interp_est_28(self, anchors):
        est = [None] * 28
        anchors = sorted(anchors, key=lambda x: x[0])
        if not anchors:
            return est
        (s0, m0) = anchors[0]
        for s in range(1, s0 + 1):
            est[s - 1] = m0
        for i in range(len(anchors) - 1):
            (sa, ma) = anchors[i]
            (sb, mb) = anchors[i + 1]
            if sb <= sa:
                continue
            for s in range(sa, sb + 1):
                frac = (s - sa) / float(sb - sa)
                est[s - 1] = ma + frac * (mb - ma)
        (sl, ml) = anchors[-1]
        for s in range(sl, 29):
            est[s - 1] = ml
        return est

    def _recommend_table_28_damped(self, base_28, targets_28, meas_28):
        rec = [0] * 28
        prev = 0
        for s in range(1, 29):
            target = targets_28[s - 1]
            meas = meas_28[s - 1]
            base = base_28[s - 1]
            if meas is None or meas <= 0.0:
                r = base
            else:
                # ratio-based correction with damping
                ratio = target / float(meas)
                # Adaptive gain based on forward percent deviation vs target.
                # When within 15% we take smaller steps to reduce oscillation.
                dev_abs = 1.0
                try:
                    if float(target) != 0.0:
                        dev_abs = abs((float(meas) - float(target)) / float(target))
                    else:
                        dev_abs = 1.0
                except:
                    dev_abs = 1.0
                scale = dev_abs / 0.15
                if scale < 0.10:
                    scale = 0.10
                if scale > 1.0:
                    scale = 1.0
                eff_gain = CV_GAIN * scale
                damped = 1.0 + eff_gain * (ratio - 1.0)
                if damped < CV_RATIO_MIN:
                    damped = CV_RATIO_MIN
                if damped > CV_RATIO_MAX:
                    damped = CV_RATIO_MAX
                r = int(round(base * damped))

            r = clamp_int(r, 0, 255)

            # limit delta per step vs current base to reduce oscillation
            try:
                b = int(base)
                if r > b + CV_MAX_STEP_DELTA:
                    r = b + CV_MAX_STEP_DELTA
                if r < b - CV_MAX_STEP_DELTA:
                    r = b - CV_MAX_STEP_DELTA
                r = clamp_int(r, 0, 255)
            except:
                pass

            if r < prev:
                r = prev
            rec[s - 1] = r
            prev = r
        return rec

    def _step_moe_for(self, stats_map, step):
        try:
            info = stats_map.get(int(step), None)
            if info is None:
                return None
            return float(info.get("moe", 0.0))
        except:
            return None

    def _compute_results(self):
        payload = speed_match_math.compute_results_payload(
            self.target_min,
            self.target_max,
            self.state.auto_measured_fwd,
            self.state.auto_measured_rev,
            self.state.auto_step_stats_fwd,
            self.state.auto_step_stats_rev,
            self.state.baseline_table
        )
        return payload.get('rows', []), payload.get('targets', []), payload.get('f_est', []), payload.get('r_est', []), payload.get('f_tbl', []), payload.get('cur_cv', [])

    def _build_ui(self):
        root = JPanel()
        root.setLayout(BorderLayout(8, 8))
        root.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8))

        top = JPanel()
        top.setLayout(GridBagLayout())
        g = GridBagConstraints()
        g.insets = Insets(2, 2, 2, 2)
        g.fill = GridBagConstraints.HORIZONTAL

        title = JLabel("Engine: %s   %s   Generated: %s" % (self.engine_label, self.decoder_label, self.ts))
        g.gridx = 0; g.gridy = 0; g.weightx = 1.0
        top.add(title, g)

        btnp = JPanel()
        btnp.setLayout(BoxLayout(btnp, BoxLayout.X_AXIS))
        btnp.add(self.btn_print_mph)
        btnp.add(self.btn_print_cv)
        btnp.add(self.btn_export)

        g.gridx = 0; g.gridy = 1; g.weightx = 1.0
        top.add(btnp, g)

        exp = JPanel()
        exp.setLayout(GridBagLayout())
        exp.setBorder(BorderFactory.createTitledBorder("Export Paths"))
        ge = GridBagConstraints()
        ge.insets = Insets(2, 2, 2, 2)
        ge.fill = GridBagConstraints.HORIZONTAL

        ge.gridx = 0; ge.gridy = 0; ge.weightx = 0.0
        exp.add(JLabel("CSV:"), ge)
        ge.gridx = 1; ge.gridy = 0; ge.weightx = 1.0
        exp.add(self.csv_path_field, ge)

        ge.gridx = 0; ge.gridy = 1; ge.weightx = 0.0
        exp.add(JLabel("PNG:"), ge)
        ge.gridx = 1; ge.gridy = 1; ge.weightx = 1.0
        exp.add(self.png_path_field, ge)

        tabs = JTabbedPane()

        tpanel = JPanel(BorderLayout())
        tpanel.setBorder(BorderFactory.createEmptyBorder(6, 6, 6, 6))
        tpanel.add(JScrollPane(self.table), BorderLayout.CENTER)
        tabs.addTab("Spreadsheet", tpanel)

        cpanel1 = JPanel(BorderLayout())
        cpanel1.setBorder(BorderFactory.createEmptyBorder(6, 6, 6, 6))
        cpanel1.add(JScrollPane(self.chart_mph), BorderLayout.CENTER)
        tabs.addTab("MPH Chart", cpanel1)

        cpanel2 = JPanel(BorderLayout())
        cpanel2.setBorder(BorderFactory.createEmptyBorder(6, 6, 6, 6))
        cpanel2.add(JScrollPane(self.chart_cv), BorderLayout.CENTER)
        tabs.addTab("Decoder Speed Table Calc", cpanel2)

        root.add(top, BorderLayout.NORTH)
        root.add(tabs, BorderLayout.CENTER)
        root.add(exp, BorderLayout.SOUTH)

        self.frame.getContentPane().add(root)
        self.frame.setPreferredSize(Dimension(1200, 900))

    def _wire_actions(self):
        rw = self
        st = self.state

        class AL(ActionListener):
            def __init__(self, fn):
                self.fn = fn
            def actionPerformed(self, e):
                self.fn()

        def do_print_mph():
            print_component(rw.frame, "MPH Chart", rw.chart_mph)

        def do_print_cv():
            print_component(rw.frame, "Decoder Speed Table Calc", rw.chart_cv)

        def export():
            try:
                base_dir = st.roster.home_dir if st.roster.home_dir else (safe_dirname(st.roster.roster_xml_path) if st.roster.roster_xml_path else None)
                if not base_dir:
                    base_dir = os.path.expanduser("~")
            except:
                base_dir = "."

            safe_engine = rw.engine_label.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_")
            ts = now_compact()
            base = os.path.join(base_dir, "speedmatch_%s_%s" % (safe_engine, ts))
            csv_path = base + ".csv"
            png_path = base + ".png"

            try:
                f = open(csv_path, "w")
                f.write("# Ultimate Speed Matching Results\n")
                f.write("# Engine: %s\n" % rw.engine_label)
                f.write("# Decoder: %s\n" % rw.decoder_label)
                f.write("# Generated: %s\n" % rw.ts)
                f.write("# Target Min mph: %.2f\n" % rw.target_min)
                f.write("# Target Max mph: %.2f\n" % rw.target_max)
                f.write("Step,Target,FwdMeasuredEst,FwdError,FwdPctDev,FwdMOE,FwdCVValue,CurrentCVValue,RevMeasuredEst,RevError,RevPctDev,RevMOE\n")
                for r in rw.results_rows:
                    f.write("%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" % (
                        str(r.get("step", "")),
                        str(r.get("target", "")),
                        str(r.get("f_meas", "")),
                        str(r.get("f_err", "")),
                        str(r.get("f_pct", "")),
                        str(r.get("f_moe", "")),
                        str(r.get("f_tbl", "")),
                        str(r.get("cur_tbl", "")),
                        str(r.get("r_meas", "")),
                        str(r.get("r_err", "")),
                        str(r.get("r_pct", "")),
                        str(r.get("r_moe", ""))
                    ))
                f.close()
            except:
                JOptionPane.showMessageDialog(rw.frame, "CSV export failed: %s" % str(sys.exc_info()[1]), "Export", JOptionPane.ERROR_MESSAGE)
                return

            try:
                w = rw.chart_mph.getWidth()
                h = rw.chart_mph.getHeight()
                if w <= 0 or h <= 0:
                    pref = rw.chart_mph.getPreferredSize()
                    w = int(pref.getWidth())
                    h = int(pref.getHeight())
                    rw.chart_mph.setSize(w, h)

                img = BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB)
                g2 = img.createGraphics()
                rw.chart_mph.paint(g2)

                # overlay engine/decoder box in upper-left below legend area
                x = 60
                y = 40
                box_w = min(w - 80, 560)
                box_h = 44
                try:
                    g2.setComposite(AlphaComposite.getInstance(AlphaComposite.SRC_OVER, 0.85))
                except:
                    pass
                g2.setColor(Color(255, 255, 255, 220))
                g2.fillRect(x, y, box_w, box_h)
                try:
                    g2.setComposite(AlphaComposite.getInstance(AlphaComposite.SRC_OVER, 1.0))
                except:
                    pass
                g2.setColor(Color.BLACK)
                g2.drawRect(x, y, box_w, box_h)
                g2.drawString("Engine: %s" % rw.engine_label[:70], x + 8, y + 16)
                g2.drawString("%s" % rw.decoder_label[:70], x + 8, y + 34)

                g2.dispose()
                ImageIO.write(img, "png", File(png_path))
            except:
                JOptionPane.showMessageDialog(rw.frame, "PNG export failed: %s" % str(sys.exc_info()[1]), "Export", JOptionPane.ERROR_MESSAGE)
                return

            rw.csv_path_field.setText(csv_path)
            rw.png_path_field.setText(png_path)

        self.btn_print_mph.addActionListener(AL(do_print_mph))
        self.btn_print_cv.addActionListener(AL(do_print_cv))
        self.btn_export.addActionListener(AL(export))

    def shutdown(self):
        try:
            self.frame.dispose()
        except:
            pass
        try:
            if self.state.results_window is self:
                self.state.results_window = None
        except:
            pass

class Dashboard(object):
    def __init__(self, state):
        self.state = state
        self.frame = JFrame("Ultimate Speed Matching Dashboard")
        self.frame.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE)

        self.live_model = LiveTableModel(state)
        self.live_table = JTable(self.live_model)
        self.live_table.setFillsViewportHeight(True)

        self.addr_field = JTextField(6)
        self.throttle_status = JTextField(90)
        self.throttle_status.setEditable(False)

        self.roster_combo = JComboBox()
        self.roster_display_map = {}
        self._populate_roster_combo()

        # Roster controls order: Get by addr, Load CV's, dropdown xml list
        self.btn_use_addr = JButton("Get by addr")
        self.btn_load_cv = JButton("Load CV's")

        self.btn_acquire = JButton("Acquire")

        self.step_value_field = JTextField("0", 4)
        self.step_value_field.setEditable(False)
        self.step_slider = JSlider(0, 28, 0)
        self.step_slider.setMajorTickSpacing(4)
        self.step_slider.setMinorTickSpacing(1)
        self.step_slider.setPaintTicks(True)
        self.step_slider.setPaintLabels(True)
        self._step_slider_internal = False
        self.btn_fwd = JButton("Forward")
        self.btn_rev = JButton("Reverse")
        self.btn_stop = JButton("STOP")

        self.auto_mode_combo = JComboBox(["Full (FWD-REV)", "Tune Loco (FWD-POM)", "Rev Trim (REV)"])
        self.btn_start_auto = JButton("Start")
        self.btn_stop_auto = JButton("Stop Automation")
        self.btn_reset_auto = JButton("Reset Auto Results")
        self.btn_warmup = JButton("Warmup")
        self.warmup_minutes_combo = JComboBox(["1","2","3","4","5"])
        try:
            self.warmup_minutes_combo.setSelectedItem("5")
        except:
            pass
        self.btn_cancel_warmup = JButton("Cancel Warmup")
        self.btn_history = JButton("History")
        self.btn_cars = JButton("Cars")
        self.btn_pom_test = JButton("POM Write Test (CV67-94)")
        self.auto_status_area = JTextArea(3, 90)
        self.auto_status_area.setEditable(False)
        self.auto_status_area.setLineWrap(True)
        self.auto_status_area.setWrapStyleWord(True)

        self.target_min_field = JTextField("5", 6)
        self.target_max_field = JTextField("70", 6)
        self.btn_calc = JButton("Calculate (Open Results)")

        # Datamart status
        self.db_status_field = JTextField(90)
        self.db_status_field.setEditable(False)
        self.btn_db_init = JButton("Init DB")

        self.throttle_setting_field = JTextField(14)
        self.throttle_setting_field.setEditable(False)
        self.step_field = JTextField(4)
        self.step_field.setEditable(False)
        self.last_lap_field = JTextField(10)
        self.last_lap_field.setEditable(False)
        self.auto_elapsed_field = JTextField(10)
        self.auto_elapsed_field.setEditable(False)
        self._summary_addr = JTextField(6)
        self._summary_addr.setEditable(False)

        self._build_ui()
        self._wire_actions()
        self._start_timers()

        dash = self
        class W(WindowAdapter):
            def windowClosing(self, e):
                dash.shutdown()
            def windowClosed(self, e):
                dash.shutdown()
        self.frame.addWindowListener(W())

        self.frame.pack()
        self.frame.setLocationRelativeTo(None)
        self.frame.setVisible(True)

        self.update_summary_fields()

    def _populate_roster_combo(self):
        self.roster_combo.removeAllItems()
        self.roster_display_map = {}
        self.roster_combo.addItem("<Select roster>")

        # If roster.xml was not found or could not be parsed, keep the dropdown usable and
        # surface the path so the user can diagnose quickly.
        try:
            if not self.state.roster.entries:
                pth = self.state.roster.roster_xml_path
                if pth:
                    self.roster_combo.addItem("<No roster entries found - roster.xml: %s>" % str(pth))
                else:
                    self.roster_combo.addItem("<No roster entries found - roster.xml not found>")
        except:
            pass

        for ent in self.state.roster.entries:
            disp = str(ent.display)
            # last-write-wins is OK; display strings are usually unique. If not, Get by addr still works.
            self.roster_display_map[disp] = ent
            self.roster_combo.addItem(disp)

        # Ensure selection renders immediately (fixes occasional blank selection on some JMRI/Swing combos)
        try:
            self.roster_combo.setSelectedIndex(0)
            self.roster_combo.revalidate()
            self.roster_combo.repaint()
        except:
            pass

    def _get_selected_roster_entry(self):
        sel = self.roster_combo.getSelectedItem()
        if sel is None:
            return None
        s = str(sel)
        return self.roster_display_map.get(s, None)

    def _find_roster_by_addr(self, addr):
        try:
            a = int(addr)
        except:
            return None
        for ent in self.state.roster.entries:
            try:
                ea = parse_int(ent.dccAddress, -1)
                if ea == a:
                    return ent
            except:
                pass
        return None

    def _select_roster_entry(self, entry):
        if entry is None:
            return
        try:
            for disp, ent in self.roster_display_map.items():
                if ent is entry:
                    self.roster_combo.setSelectedItem(disp)
                    return
        except:
            pass

    def _build_ui(self):
        root = JPanel()
        root.setLayout(BorderLayout(8, 8))
        root.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8))

        sec1 = JPanel()
        sec1.setLayout(GridBagLayout())
        sec1.setBorder(BorderFactory.createTitledBorder("1) Throttle / Roster / Manual Control"))
        g1 = GridBagConstraints()
        g1.insets = Insets(2, 2, 2, 2)
        g1.fill = GridBagConstraints.HORIZONTAL

        r = 0
        g1.gridx = 0; g1.gridy = r; g1.weightx = 0.0
        sec1.add(JLabel("Loco address:"), g1)
        g1.gridx = 1; g1.gridy = r; g1.weightx = 0.0
        sec1.add(self.addr_field, g1)
        g1.gridx = 2; g1.gridy = r; g1.weightx = 0.0
        sec1.add(self.btn_acquire, g1)
        g1.gridx = 3; g1.gridy = r; g1.weightx = 1.0
        g1.gridwidth = 3
        sec1.add(self.throttle_status, g1)
        g1.gridwidth = 1

        r += 1
        g1.gridx = 0; g1.gridy = r; g1.weightx = 0.0
        sec1.add(JLabel("Roster:"), g1)
        g1.gridx = 1; g1.gridy = r; g1.weightx = 0.0
        sec1.add(self.btn_use_addr, g1)
        g1.gridx = 2; g1.gridy = r; g1.weightx = 0.0
        sec1.add(self.btn_load_cv, g1)
        g1.gridx = 3; g1.gridy = r; g1.weightx = 1.0
        g1.gridwidth = 3
        sec1.add(self.roster_combo, g1)
        g1.gridwidth = 1

        r += 1
        g1.gridx = 0; g1.gridy = r; g1.weightx = 0.0
        sec1.add(JLabel("Current speed step:"), g1)
        g1.gridx = 1; g1.gridy = r; g1.weightx = 0.0
        sec1.add(self.step_value_field, g1)

        man_panel = JPanel()
        man_panel.setLayout(BoxLayout(man_panel, BoxLayout.X_AXIS))
        man_panel.add(self.btn_fwd)
        man_panel.add(self.btn_rev)
        man_panel.add(self.btn_stop)

        g1.gridx = 2; g1.gridy = r; g1.weightx = 0.0
        sec1.add(man_panel, g1)
        g1.gridx = 3; g1.gridy = r; g1.weightx = 1.0
        g1.gridwidth = 3
        sec1.add(self.step_slider, g1)
        g1.gridwidth = 1

        sec2 = JPanel()
        sec2.setLayout(GridBagLayout())
        sec2.setBorder(BorderFactory.createTitledBorder("2) Automation"))
        g2 = GridBagConstraints()
        g2.insets = Insets(2, 2, 2, 2)
        g2.fill = GridBagConstraints.HORIZONTAL

        g2.gridx = 0; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(JLabel("Mode:"), g2)
        g2.gridx = 1; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.auto_mode_combo, g2)
        g2.gridx = 2; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_start_auto, g2)
        g2.gridx = 3; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_stop_auto, g2)
        g2.gridx = 4; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_reset_auto, g2)
        g2.gridx = 5; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_warmup, g2)
        g2.gridx = 6; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.warmup_minutes_combo, g2)
        g2.gridx = 7; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_cancel_warmup, g2)
        g2.gridx = 8; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_history, g2)
        g2.gridx = 9; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_cars, g2)
        g2.gridx = 10; g2.gridy = 0; g2.weightx = 0.0
        sec2.add(self.btn_pom_test, g2)
        g2.gridx = 0; g2.gridy = 1; g2.weightx = 1.0
        g2.gridwidth = 12
        sec2.add(JScrollPane(self.auto_status_area), g2)
        g2.gridwidth = 1

        sec3 = JPanel()
        sec3.setLayout(GridBagLayout())
        sec3.setBorder(BorderFactory.createTitledBorder("3) Results"))
        g3 = GridBagConstraints()
        g3.insets = Insets(2, 2, 2, 2)
        g3.fill = GridBagConstraints.HORIZONTAL

        g3.gridx = 0; g3.gridy = 0; g3.weightx = 0.0
        sec3.add(JLabel("Target Min mph:"), g3)
        g3.gridx = 1; g3.gridy = 0; g3.weightx = 0.0
        sec3.add(self.target_min_field, g3)
        g3.gridx = 2; g3.gridy = 0; g3.weightx = 0.0
        sec3.add(JLabel("Target Max mph:"), g3)
        g3.gridx = 3; g3.gridy = 0; g3.weightx = 0.0
        sec3.add(self.target_max_field, g3)
        g3.gridx = 0; g3.gridy = 1; g3.weightx = 1.0
        g3.gridwidth = 5
        sec3.add(self.btn_calc, g3)
        g3.gridwidth = 1

        summary = JPanel()

        # DB init/status row
        g3.gridx = 0; g3.gridy = 2; g3.weightx = 0.0
        sec3.add(self.btn_db_init, g3)
        g3.gridx = 1; g3.gridy = 2; g3.gridwidth = 4; g3.weightx = 1.0
        sec3.add(self.db_status_field, g3)
        g3.gridwidth = 1
        summary.setLayout(GridBagLayout())
        summary.setBorder(BorderFactory.createTitledBorder("Dashboard Summary"))
        gs = GridBagConstraints()
        gs.insets = Insets(2, 2, 2, 2)
        gs.fill = GridBagConstraints.HORIZONTAL

        gs.gridx = 0; gs.gridy = 0; gs.weightx = 0.0
        summary.add(JLabel("Loco address:"), gs)
        gs.gridx = 1; gs.gridy = 0; gs.weightx = 0.0
        summary.add(self._summary_addr, gs)

        gs.gridx = 2; gs.gridy = 0; gs.weightx = 0.0
        summary.add(JLabel("Throttle+Dir:"), gs)
        gs.gridx = 3; gs.gridy = 0; gs.weightx = 0.0
        summary.add(self.throttle_setting_field, gs)

        gs.gridx = 4; gs.gridy = 0; gs.weightx = 0.0
        summary.add(JLabel("Current step:"), gs)
        gs.gridx = 5; gs.gridy = 0; gs.weightx = 0.0
        summary.add(self.step_field, gs)

        gs.gridx = 0; gs.gridy = 1; gs.weightx = 0.0
        summary.add(JLabel("Last Lap Speed (mph):"), gs)
        gs.gridx = 1; gs.gridy = 1; gs.weightx = 0.0
        summary.add(self.last_lap_field, gs)

        gs.gridx = 2; gs.gridy = 1; gs.weightx = 0.0
        summary.add(JLabel("Automation elapsed:"), gs)
        gs.gridx = 3; gs.gridy = 1; gs.weightx = 0.0
        summary.add(self.auto_elapsed_field, gs)

        sec4 = JPanel()
        sec4.setLayout(BorderLayout())
        sec4.setBorder(BorderFactory.createTitledBorder("4) Live Table"))
        sec4.add(JScrollPane(self.live_table), BorderLayout.CENTER)

        topwrap = JPanel()
        topwrap.setLayout(GridBagLayout())
        gt = GridBagConstraints()
        gt.insets = Insets(4, 4, 4, 4)
        gt.fill = GridBagConstraints.HORIZONTAL

        gt.gridx = 0; gt.gridy = 0; gt.weightx = 1.0
        topwrap.add(sec1, gt)
        gt.gridx = 0; gt.gridy = 1; gt.weightx = 1.0
        topwrap.add(sec2, gt)
        gt.gridx = 0; gt.gridy = 2; gt.weightx = 1.0
        topwrap.add(sec3, gt)
        gt.gridx = 0; gt.gridy = 3; gt.weightx = 1.0
        topwrap.add(summary, gt)

        root.add(topwrap, BorderLayout.NORTH)
        root.add(sec4, BorderLayout.CENTER)
        self.frame.getContentPane().add(root)

    def _wire_actions(self):
        st = self.state
        dash = self

        class SimpleAL(ActionListener):
            def __init__(self, fn):
                self.fn = fn
            def actionPerformed(self, e):
                self.fn()

        def _sync_targets_to_state():
            tmin = parse_float(dash.target_min_field.getText(), st.target_min_mph)
            tmax = parse_float(dash.target_max_field.getText(), st.target_max_mph)
            st.target_min_mph = float(tmin)
            st.target_max_mph = float(tmax)

        def do_acquire():
            addr = parse_int(dash.addr_field.getText(), st.addr)
            st.acquire_throttle_async(addr)
            dash.update_summary_fields()

        def do_get_by_addr():
            addr = parse_int(dash.addr_field.getText(), 0)
            if addr <= 0:
                JOptionPane.showMessageDialog(dash.frame, "Enter a loco address first.", "Roster", JOptionPane.WARNING_MESSAGE)
                return
            ent = dash._find_roster_by_addr(addr)
            if ent is None:
                JOptionPane.showMessageDialog(dash.frame, "No roster entry found with dccAddress=%d." % addr, "Roster", JOptionPane.WARNING_MESSAGE)
                return
            dash._select_roster_entry(ent)
            st.selected_roster_entry = ent
            st.acquire_throttle_async(addr)
            dash.update_summary_fields()

        def do_load_cv():
            ent = dash._get_selected_roster_entry()
            if ent is None:
                JOptionPane.showMessageDialog(dash.frame, "Select a roster entry from the dropdown first.", "Roster", JOptionPane.WARNING_MESSAGE)
                return
            st.selected_roster_entry = ent
            table, loco_path, tried = st.roster.load_cv67_94(ent)
            st.baseline_table = table
            if loco_path:
                dash.set_throttle_status("Loaded baseline CV67-94 from: %s" % str(loco_path))
            else:
                msg = "Could not locate loco XML for:\n%s\n\nExpected fileName:\n%s\n\nPaths tried:\n" % (str(ent.display), str(ent.fileName))
                for pth in tried:
                    msg += "  " + str(pth) + "\n"
                msg += "\nUsing synthetic baseline."
                JOptionPane.showMessageDialog(dash.frame, msg, "Load CV's", JOptionPane.WARNING_MESSAGE)
                dash.set_throttle_status("Using synthetic baseline (loco XML not found).")

        def do_forward():
            st._set_dir_forward(True)
            dash.update_summary_fields()

        def do_reverse():
            st._set_dir_forward(False)
            dash.update_summary_fields()

        def do_stop():
            st._apply_step(0)
            dash.set_step_spinner_value(0)
            dash.update_summary_fields()

        def do_start_auto():
            if st.throttle is None:
                JOptionPane.showMessageDialog(dash.frame, "Acquire a throttle first.", "Automation", JOptionPane.WARNING_MESSAGE)
                return
            _sync_targets_to_state()
            sel = str(dash.auto_mode_combo.getSelectedItem())
            mode = "FULL"
            if sel.startswith("Tune Loco"):
                mode = "FWD_POM"
            elif sel.startswith("Rev Trim"):
                mode = "REV"
            st.start_automation(mode)

        def do_stop_auto():
            st.stop_automation_only("Stopped by user")

        
        # --- Optional modules (split-file Option B2) ---
        # Imports are done inside handlers to avoid failing dashboard startup if files are missing.

        # Warmup controller (created lazily)
        warm_ctrl = {"obj": None}

        def _get_warm():
            if warm_ctrl["obj"] is None:
                import warmup
                warm_ctrl["obj"] = warmup.WarmupController(st, dash)
            return warm_ctrl["obj"]

        def do_warmup():
            try:
                minutes = 5
                try:
                    minutes = int(str(dash.warmup_minutes_combo.getSelectedItem()))
                except:
                    minutes = 5
                _get_warm().start(minutes)
            except:
                dash.set_automation_status("Warmup error: %s" % str(sys.exc_info()[1]))

        def do_cancel_warmup():
            try:
                _get_warm().cancel()
            except:
                pass

        def do_db_init():
            try:
                import db_datamart
                db_datamart.init_schema()
                dash.db_status_field.setText("DB ready")
            except:
                dash.db_status_field.setText("DB init failed: %s" % str(sys.exc_info()[1]))

        def do_history():
            try:
                import history_app
                history_app.open_history_window(st)
            except:
                dash.set_automation_status("History error: %s" % str(sys.exc_info()[1]))

        def do_cars():
            try:
                import cars_app
                cars_app.open_cars_window()
            except:
                err = sys.exc_info()[1]
                dash.set_automation_status("Cars error: %s" % str(err))

        def do_pom_test():
            try:
                dash.set_automation_status("POM test: stopping loco and writing CV67-94 (Ops Mode)...")
                try:
                    st._apply_step(0)
                except:
                    pass
                time.sleep(0.50)
                table = st._build_pom_test_table()
                st._write_speed_table_pom(table)
            except:
                err = sys.exc_info()[1]
                dash.set_automation_status("POM test failed: %s" % str(err))

        def do_reset_auto():
            st.reset_automation_results()

        def do_calc():
            _sync_targets_to_state()
            if st.results_window is not None:
                try:
                    st.results_window.frame.toFront()
                    return
                except:
                    pass
            tmin = float(st.target_min_mph)
            tmax = float(st.target_max_mph)
            rw = ResultsWindow(st, tmin, tmax)
            st.results_window = rw

        class StepCL(ChangeListener):
            def stateChanged(self, e):
                try:
                    if dash._step_slider_internal:
                        return
                except:
                    pass
                try:
                    step = int(dash.step_slider.getValue())
                    dash.step_value_field.setText(str(step))
                    st._apply_step(step)
                    dash.update_summary_fields()
                except:
                    pass

        self.step_slider.addChangeListener(StepCL())

        self.btn_acquire.addActionListener(SimpleAL(do_acquire))
        self.btn_use_addr.addActionListener(SimpleAL(do_get_by_addr))
        self.btn_load_cv.addActionListener(SimpleAL(do_load_cv))
        self.btn_fwd.addActionListener(SimpleAL(do_forward))
        self.btn_rev.addActionListener(SimpleAL(do_reverse))
        self.btn_stop.addActionListener(SimpleAL(do_stop))
        self.btn_start_auto.addActionListener(SimpleAL(do_start_auto))
        self.btn_stop_auto.addActionListener(SimpleAL(do_stop_auto))
        self.btn_reset_auto.addActionListener(SimpleAL(do_reset_auto))
        self.btn_warmup.addActionListener(SimpleAL(do_warmup))
        self.btn_cancel_warmup.addActionListener(SimpleAL(do_cancel_warmup))
        self.btn_db_init.addActionListener(SimpleAL(do_db_init))
        self.btn_history.addActionListener(SimpleAL(do_history))
        self.btn_cars.addActionListener(SimpleAL(do_cars))
        self.btn_pom_test.addActionListener(SimpleAL(do_pom_test))
        self.btn_calc.addActionListener(SimpleAL(do_calc))

    def _start_timers(self):
        st = self.state
        dash = self

        class LiveAL(ActionListener):
            def actionPerformed(self, e):
                dash.live_model.fire_all()
                dash.update_summary_fields()

        self.live_timer = Timer(LIVE_REFRESH_MS, LiveAL())
        self.live_timer.start()

        class ActAL(ActionListener):
            def actionPerformed(self, e):
                if st.automation_running:
                    # prevent false timeout right at start by always refreshing activity timer on phase/step changes
                    timeout_sec = st._current_activity_timeout_sec()
                    if (time.time() - st.last_sensor_activity_time) > timeout_sec:
                        st.stop_automation_only("No sensor activity for %.0f seconds at step %s (%s)" % (timeout_sec, str(st.current_step), str(st.auto_mode_by_step.get(st.current_step, ''))))

        self.activity_timer = Timer(1000, ActAL())
        self.activity_timer.start()

        st.live_timer = self.live_timer
        st.activity_timer = self.activity_timer

    def set_step_spinner_value(self, v):
        try:
            iv = int(v)
        except:
            iv = 0
        iv = clamp_int(iv, 0, 28)
        try:
            self._step_slider_internal = True
            self.step_slider.setValue(iv)
        except:
            pass
        try:
            self.step_value_field.setText(str(iv))
        except:
            pass
        self._step_slider_internal = False

    def set_throttle_status(self, msg):
        try:
            self.throttle_status.setText(str(msg))
        except:
            pass

    def set_automation_status(self, msg):
        try:
            self.auto_status_area.setText(str(msg))
        except:
            pass

    def set_automation_buttons_enabled(self, enabled):
        try:
            self.btn_start_auto.setEnabled(bool(enabled))
            self.btn_stop_auto.setEnabled(not bool(enabled))
        except:
            pass

    def update_summary_fields(self):
        st = self.state
        try:
            self._summary_addr.setText(str(st.addr))
        except:
            pass
        try:
            if st.throttle is None:
                self.throttle_setting_field.setText("No throttle")
            else:
                try:
                    ss = float(st.throttle.getSpeedSetting())
                except:
                    ss = st.current_step / 28.0
                dir_s = "FWD" if st._get_throttle_forward() else "REV"
                self.throttle_setting_field.setText("%.3f %s" % (ss, dir_s))
        except:
            pass
        try:
            self.step_field.setText(str(st.current_step))
            self.step_value_field.setText(str(st.current_step))
        except:
            pass
        try:
            self.last_lap_field.setText(st._fmt_mph(st.last_lap_speed))
        except:
            pass
        try:
            self.auto_elapsed_field.setText(fmt_elapsed(st.auto_elapsed_seconds()))
        except:
            pass

    def shutdown(self):
        st = self.state
        try:
            if self.live_timer is not None:
                self.live_timer.stop()
        except:
            pass
        try:
            if self.activity_timer is not None:
                self.activity_timer.stop()
        except:
            pass
        try:
            if st.results_window is not None:
                st.results_window.shutdown()
        except:
            pass
        st.results_window = None
        try:
            st.stop_automation_only("Dashboard closing")
        except:
            pass
        try:
            st.detach_sensors()
        except:
            pass
        try:
            st.release_throttle()
        except:
            pass
        try:
            self.frame.dispose()
        except:
            pass

# -------------------------------
# Main bootstrap
# -------------------------------
STATE = SpeedState()

def build_and_run():
    STATE.attach_sensors()
    dash = Dashboard(STATE)
    STATE.dashboard = dash

    try:
        dash.addr_field.setText(str(STATE.addr))
    except:
        pass

    rx = STATE.roster.roster_xml_path if STATE.roster.roster_xml_path else "(not found)"
    hd = STATE.roster.home_dir if STATE.roster.home_dir else "(unknown)"
    dash.set_throttle_status("Home: %s   Roster.xml: %s   History: %s" % (str(hd), str(rx), str(STATE.history_dir)))
    dash.set_automation_status("Automation idle.")
    dash.set_automation_buttons_enabled(True)

SwingUtilities.invokeLater(build_and_run)
