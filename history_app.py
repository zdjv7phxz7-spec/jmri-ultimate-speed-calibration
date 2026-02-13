# history_app.py (ASCII only)
# Displays past calibration runs from SQLite (speed_run + speed_step_summary)
from javax.swing import JFrame, JPanel, JScrollPane, JTable, JButton, JLabel
from javax.swing.table import AbstractTableModel
from java.awt import BorderLayout
from javax.swing.event import ListSelectionListener
from java.awt.event import ActionListener

import db_read


class ResultsRowsModel(AbstractTableModel):
    COLS = ["Step","Target","FwdMeasured","FwdError","Fwd%Dev","FwdMOE","FwdCV","CurCV","RevMeasured","RevError","Rev%Dev","RevMOE"]
    def __init__(self):
        self.rows=[]
    def set_rows(self, rows):
        self.rows = rows or []
        self.fireTableDataChanged()
    def getColumnCount(self):
        return len(self.COLS)
    def getRowCount(self):
        return len(self.rows)
    def getColumnName(self, c):
        return self.COLS[c]
    def getValueAt(self, r, c):
        row=self.rows[r]
        keys=["step","target","f_meas","f_err","f_pct","f_moe","f_tbl","cur_tbl","r_meas","r_err","r_pct","r_moe"]
        v=row.get(keys[c])
        return "" if v is None else v


class RunsModel(AbstractTableModel):
    def __init__(self):
        AbstractTableModel.__init__(self)
        self.cols = ["RunId","When","Loco","Mode","Dir","Min","Max"]
        self.rows = []
    def set_rows(self, rows):
        self.rows = rows or []
        self.fireTableDataChanged()
    def getRowCount(self): return len(self.rows)
    def getColumnCount(self): return len(self.cols)
    def getColumnName(self, c): return self.cols[c]
    def getValueAt(self, r, c): return self.rows[r][c]

class SumModel(AbstractTableModel):
    def __init__(self):
        AbstractTableModel.__init__(self)
        self.cols = ["Step","Mode","MeanMPH","Kept","Total","MOE"]
        self.rows = []
    def set_rows(self, rows):
        self.rows = rows or []
        self.fireTableDataChanged()
    def getRowCount(self): return len(self.rows)
    def getColumnCount(self): return len(self.cols)
    def getColumnName(self, c): return self.cols[c]
    def getValueAt(self, r, c): return self.rows[r][c]

def open_history_window(state):
    f = JFrame("History - Speed Runs")
    f.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE)

    runs_m = RunsModel()
    runs_t = JTable(runs_m)
    sums_m = SumModel()
    sums_t = JTable(sums_m)

    btn_refresh = JButton("Refresh")

    def refresh():
        runs = db_read.list_speed_runs(limit=200)
        runs_m.set_rows(runs)
        sums_m.set_rows([])

    def load_selected():
        r = runs_t.getSelectedRow()
        if r < 0: return
        run_id = int(runs_m.rows[r][0])
        sums = db_read.get_run_summaries(run_id)
        sums_m.set_rows(sums)

    class AL(ActionListener):
        def __init__(self, fn): self.fn = fn
        def actionPerformed(self, e): self.fn()

    btn_refresh.addActionListener(AL(refresh))
    class LSL(ListSelectionListener):
        def valueChanged(self, e):
            if not e.getValueIsAdjusting():
                load_selected()
    runs_t.getSelectionModel().addListSelectionListener(LSL())

    p = JPanel(BorderLayout())
    top = JPanel()
    top.add(btn_refresh)
    p.add(top, BorderLayout.NORTH)

    mid = JPanel(BorderLayout())
    mid.add(JLabel("Runs"), BorderLayout.NORTH)
    mid.add(JScrollPane(runs_t), BorderLayout.CENTER)

    bot = JPanel(BorderLayout())
    bot.add(JLabel("Summaries (select a run)"), BorderLayout.NORTH)
    bot.add(JScrollPane(sums_t), BorderLayout.CENTER)

    p.add(mid, BorderLayout.CENTER)
    p.add(bot, BorderLayout.SOUTH)

    f.getContentPane().add(p)
    f.setSize(900, 700)
    f.setLocationRelativeTo(None)
    f.setVisible(True)
    refresh()
