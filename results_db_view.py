# -*- coding: utf-8 -*-
from javax.swing import JFrame, JPanel, JScrollPane, JTable, JButton, JLabel, JTabbedPane, JTextField, BorderFactory, JOptionPane
from java.awt import BorderLayout, GridBagLayout, GridBagConstraints, Insets, Dimension, Color, AlphaComposite, GridLayout
from java.awt.event import ActionListener, WindowAdapter
from java.awt.image import BufferedImage
from javax.imageio import ImageIO
from java.io import File
from java.awt.print import PrinterJob, Printable
import time
import os
import db_read
import __main__


def _fmt2(v):
    if v is None or v == '':
        return ''
    try:
        return '%.2f' % float(v)
    except:
        return str(v)


def _fmt_pct(v):
    if v is None or v == '':
        return ''
    try:
        return '%.2f' % float(v)
    except:
        return str(v)


def _safe_engine_text(header):
    v = header.get('display_name')
    if v:
        return str(v)
    v = header.get('loco_id')
    if v:
        return str(v)
    v = header.get('dcc_address')
    if v:
        return 'Addr %s' % str(v)
    return 'Unknown locomotive'


def _safe_decoder_text(header):
    v = header.get('roster_file')
    if v:
        return 'Decoder file: %s' % str(v)
    return 'Decoder file: (unknown)'


def _safe_generated_text(header):
    ts = header.get('run_ts')
    if ts is None:
        return ''
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(ts)))
    except:
        return str(ts)


def _normalize_rows(rows):
    out = []
    for row in rows or []:
        out.append({
            'step': row.get('step', ''),
            'target': _fmt2(row.get('target')),
            'f_meas': _fmt2(row.get('f_meas')),
            'f_err': _fmt2(row.get('f_err')),
            'f_pct': _fmt_pct(row.get('f_pct')),
            'f_moe': _fmt2(row.get('f_moe')),
            'f_tbl': '' if row.get('f_tbl') is None else str(row.get('f_tbl')),
            'cur_tbl': '' if row.get('cur_tbl') is None else str(row.get('cur_tbl')),
            'r_meas': _fmt2(row.get('r_meas')),
            'r_err': _fmt2(row.get('r_err')),
            'r_pct': _fmt_pct(row.get('r_pct')),
            'r_moe': _fmt2(row.get('r_moe'))
        })
    return out


def _float_or_none(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except:
        return None


def _int_or_none(v):
    if v is None or v == '':
        return None
    try:
        return int(float(v))
    except:
        return None


def _arrays_from_rows(rows):
    target_arr = []
    fwd_arr = []
    rev_arr = []
    fwd_cv_arr = []
    cur_cv_arr = []
    for row in rows or []:
        target_arr.append(_float_or_none(row.get('target')))
        fwd_arr.append(_float_or_none(row.get('f_meas')))
        rev_arr.append(_float_or_none(row.get('r_meas')))
        fwd_cv_arr.append(_int_or_none(row.get('f_tbl')))
        cur_cv_arr.append(_int_or_none(row.get('cur_tbl')))
    return target_arr, fwd_arr, rev_arr, fwd_cv_arr, cur_cv_arr


class DbResultsWindow(object):
    def __init__(self, run_id, payload):
        self.run_id = int(run_id)
        self.payload = payload or {}
        self.header = self.payload.get('header') or {}
        self.rows = _normalize_rows(self.payload.get('rows') or [])
        self.summaries = self.payload.get('summaries') or []
        self.engine_label = _safe_engine_text(self.header)
        self.decoder_label = _safe_decoder_text(self.header)
        self.generated_label = _safe_generated_text(self.header)
        self.target_min = self.header.get('target_min_mph')
        self.target_max = self.header.get('target_max_mph')
        self.frame = JFrame(self._window_title())
        self.frame.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE)

        self.csv_path_field = JTextField(60)
        self.csv_path_field.setEditable(False)
        self.png_path_field = JTextField(60)
        self.png_path_field.setEditable(False)

        self.btn_print_charts = JButton('Print Charts')
        self.btn_export = JButton('Export CSV+PNG')

        self.table_model = __main__.ResultsTableModel(self.rows)
        self.table = JTable(self.table_model)
        self.table.setFillsViewportHeight(True)

        target_arr, fwd_arr, rev_arr, fwd_cv_arr, cur_cv_arr = _arrays_from_rows(self.rows)
        self.chart_mph = __main__.SpeedChartPanel()
        self.chart_mph.setData(target_arr, fwd_arr, rev_arr)
        self.chart_mph.setMetaLines([])

        self.chart_cv = __main__.DecoderTableBarChartPanel()
        self.chart_cv.setData(fwd_cv_arr, cur_cv_arr)
        self.chart_cv.setMetaLines([])

        self._build_ui()
        self._wire_actions()

        dw = self
        class W(WindowAdapter):
            def windowClosing(self, e):
                dw.shutdown()
            def windowClosed(self, e):
                dw.shutdown()
        self.frame.addWindowListener(W())
        self.frame.pack()
        self.frame.setLocationRelativeTo(None)
        self.frame.setVisible(True)

    def _window_title(self):
        parts = [self.engine_label]
        if self.header.get('direction'):
            parts.append('Dir %s' % str(self.header.get('direction')))
        if self.header.get('mode'):
            parts.append('Mode %s' % str(self.header.get('mode')))
        parts.append('Run %s' % str(self.run_id))
        return 'Results - ' + ' | '.join(parts)

    def _meta_lines(self):
        lines = [
            'Engine: %s' % self.engine_label,
            self.decoder_label,
            'Run ID: %s   Mode: %s   Direction: %s   Generated: %s' % (
                str(self.run_id),
                str(self.header.get('mode', '') or ''),
                str(self.header.get('direction', '') or ''),
                self.generated_label
            )
        ]
        if self.target_min is not None or self.target_max is not None:
            lines.append('Target Min: %s mph   Target Max: %s mph' % (
                _fmt2(self.target_min), _fmt2(self.target_max)
            ))
        return lines

    def _build_ui(self):
        root = JPanel(BorderLayout(8, 8))
        root.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8))

        top = JPanel(GridBagLayout())
        g = GridBagConstraints()
        g.insets = Insets(2, 2, 2, 2)
        g.fill = GridBagConstraints.HORIZONTAL
        g.gridx = 0
        g.gridy = 0
        g.weightx = 1.0
        title = 'Engine: %s   %s   Run ID: %s   Mode: %s   Direction: %s   Generated: %s' % (
            self.engine_label, self.decoder_label, str(self.run_id),
            str(self.header.get('mode', '') or ''), str(self.header.get('direction', '') or ''), self.generated_label)
        top.add(JLabel(title), g)

        btnp = JPanel()
        btnp.add(self.btn_print_charts)
        btnp.add(self.btn_export)
        g.gridy = 1
        top.add(btnp, g)

        exp = JPanel(GridBagLayout())
        exp.setBorder(BorderFactory.createTitledBorder('Export Paths'))
        ge = GridBagConstraints()
        ge.insets = Insets(2, 2, 2, 2)
        ge.fill = GridBagConstraints.HORIZONTAL
        ge.gridx = 0
        ge.gridy = 0
        exp.add(JLabel('CSV:'), ge)
        ge.gridx = 1
        ge.weightx = 1.0
        exp.add(self.csv_path_field, ge)
        ge.gridx = 0
        ge.gridy = 1
        ge.weightx = 0.0
        exp.add(JLabel('PNG:'), ge)
        ge.gridx = 1
        ge.weightx = 1.0
        exp.add(self.png_path_field, ge)

        tabs = JTabbedPane()
        tpanel = JPanel(BorderLayout())
        tpanel.setBorder(BorderFactory.createEmptyBorder(6, 6, 6, 6))
        tpanel.add(JScrollPane(self.table), BorderLayout.CENTER)
        tabs.addTab('Spreadsheet', tpanel)

        cpanel1 = JPanel(BorderLayout())
        cpanel1.setBorder(BorderFactory.createEmptyBorder(6, 6, 6, 6))
        cpanel1.add(JScrollPane(self.chart_mph), BorderLayout.CENTER)
        tabs.addTab('MPH Chart', cpanel1)

        cpanel2 = JPanel(BorderLayout())
        cpanel2.setBorder(BorderFactory.createEmptyBorder(6, 6, 6, 6))
        cpanel2.add(JScrollPane(self.chart_cv), BorderLayout.CENTER)
        tabs.addTab('Decoder Speed Table Calc', cpanel2)

        root.add(top, BorderLayout.NORTH)
        root.add(tabs, BorderLayout.CENTER)
        root.add(exp, BorderLayout.SOUTH)
        self.frame.getContentPane().add(root)
        self.frame.setPreferredSize(Dimension(1200, 900))

    def _wire_actions(self):
        rw = self
        class AL(ActionListener):
            def __init__(self, fn):
                self.fn = fn
            def actionPerformed(self, e):
                self.fn()

        def do_print_charts():
            try:
                pj = PrinterJob.getPrinterJob()
                pj.setJobName('Speed Charts Run %s' % str(rw.run_id))

                class P(Printable):
                    def print_page(self, graphics, pageFormat, pageIndex):
                        if pageIndex > 0:
                            return Printable.NO_SUCH_PAGE
                        try:
                            g2 = graphics
                            x = int(pageFormat.getImageableX())
                            y = int(pageFormat.getImageableY())
                            w = int(pageFormat.getImageableWidth())
                            h = int(pageFormat.getImageableHeight())

                            header_h = 42
                            gap = 14
                            each_h = max(100, int((h - header_h - gap) / 2))

                            g2.translate(x, y)
                            g2.setColor(Color.BLACK)
                            g2.drawString('Engine: %s' % rw.engine_label[:120], 0, 12)
                            g2.drawString('%s   Run ID: %s   Mode: %s   Direction: %s' % (rw.decoder_label[:80], str(rw.run_id), str(rw.header.get('mode', '') or ''), str(rw.header.get('direction', '') or '')), 0, 26)
                            g2.drawString('Generated: %s' % rw.generated_label, 0, 40)

                            def paint_scaled(comp, top_y, avail_w, avail_h):
                                cw = comp.getWidth()
                                ch = comp.getHeight()
                                if cw <= 0 or ch <= 0:
                                    pref = comp.getPreferredSize()
                                    cw = int(pref.getWidth())
                                    ch = int(pref.getHeight())
                                    comp.setSize(cw, ch)
                                sx = avail_w / float(cw)
                                sy = avail_h / float(ch)
                                s = min(sx, sy)
                                g3 = g2.create()
                                g3.translate(0, top_y)
                                g3.scale(s, s)
                                comp.printAll(g3)
                                g3.dispose()

                            paint_scaled(rw.chart_mph, header_h, w, each_h)
                            paint_scaled(rw.chart_cv, header_h + each_h + gap, w, each_h)
                        except:
                            pass
                        return Printable.PAGE_EXISTS

                printer = P()
                printer.print = printer.print_page
                pj.setPrintable(printer)
                if pj.printDialog():
                    pj.print()
            except Exception, e:
                JOptionPane.showMessageDialog(rw.frame, 'Print failed: %s' % str(e), 'Print', JOptionPane.ERROR_MESSAGE)

        def export():
            try:
                base_dir = os.path.expanduser('~')
            except:
                base_dir = '.'
            safe_engine = self.engine_label.replace(' ', '_').replace(':', '_').replace('/', '_').replace('\\', '_')
            ts = __main__.now_compact()
            base = os.path.join(base_dir, 'speedmatch_%s_run%s_%s' % (safe_engine, str(self.run_id), ts))
            csv_path = base + '.csv'
            png_path = base + '.png'

            try:
                f = open(csv_path, 'w')
                f.write('# Ultimate Speed Matching Results\n')
                f.write('# Engine: %s\n' % self.engine_label)
                f.write('# Decoder: %s\n' % self.decoder_label)
                f.write('# Run ID: %s\n' % str(self.run_id))
                f.write('# Generated: %s\n' % self.generated_label)
                if self.target_min is not None:
                    f.write('# Target Min mph: %s\n' % _fmt2(self.target_min))
                if self.target_max is not None:
                    f.write('# Target Max mph: %s\n' % _fmt2(self.target_max))
                f.write('Step,Target,FwdMeasuredEst,FwdError,FwdPctDev,FwdMOE,FwdCVValue,CurrentCVValue,RevMeasuredEst,RevError,RevPctDev,RevMOE\n')
                for r in self.rows:
                    f.write('%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' % (
                        str(r.get('step', '')), str(r.get('target', '')), str(r.get('f_meas', '')), str(r.get('f_err', '')),
                        str(r.get('f_pct', '')), str(r.get('f_moe', '')), str(r.get('f_tbl', '')), str(r.get('cur_tbl', '')),
                        str(r.get('r_meas', '')), str(r.get('r_err', '')), str(r.get('r_pct', '')), str(r.get('r_moe', ''))
                    ))
                f.close()
            except Exception, e:
                JOptionPane.showMessageDialog(rw.frame, 'CSV export failed: %s' % str(e), 'Export', JOptionPane.ERROR_MESSAGE)
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
                x = 60
                y = 40
                box_w = min(w - 80, 720)
                box_h = 56
                try:
                    g2.setComposite(AlphaComposite.getInstance(AlphaComposite.SRC_OVER, 0.90))
                except:
                    pass
                g2.setColor(Color(255, 255, 255, 230))
                g2.fillRect(x, y, box_w, box_h)
                try:
                    g2.setComposite(AlphaComposite.getInstance(AlphaComposite.SRC_OVER, 1.0))
                except:
                    pass
                g2.setColor(Color.BLACK)
                g2.drawRect(x, y, box_w, box_h)
                g2.drawString('Engine: %s' % self.engine_label[:90], x + 8, y + 16)
                g2.drawString('%s' % self.decoder_label[:90], x + 8, y + 32)
                g2.drawString('Run ID: %s  Generated: %s' % (str(self.run_id), self.generated_label[:50]), x + 8, y + 48)
                g2.dispose()
                ImageIO.write(img, 'png', File(png_path))
            except Exception, e:
                JOptionPane.showMessageDialog(rw.frame, 'PNG export failed: %s' % str(e), 'Export', JOptionPane.ERROR_MESSAGE)
                return

            self.csv_path_field.setText(csv_path)
            self.png_path_field.setText(png_path)

        self.btn_print_charts.addActionListener(AL(do_print_charts))
        self.btn_export.addActionListener(AL(export))

    def shutdown(self):
        try:
            self.frame.dispose()
        except:
            pass


def open_results_for_run_id(run_id, parent_frame=None):
    payload = db_read.get_run_payload(run_id)
    header = payload.get('header')
    rows = payload.get('rows') or []
    if header is None:
        JOptionPane.showMessageDialog(parent_frame, 'Run header not found for run_id=%s' % str(run_id), 'Results', JOptionPane.WARNING_MESSAGE)
        return None
    if not rows:
        JOptionPane.showMessageDialog(parent_frame, 'No saved result rows found for run_id=%s' % str(run_id), 'Results', JOptionPane.WARNING_MESSAGE)
        return None
    return DbResultsWindow(run_id, payload)
