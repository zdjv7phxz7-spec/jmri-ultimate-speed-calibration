from javax.swing import JFrame, JPanel, JScrollPane, JTable, JButton, JLabel
from javax.swing.table import AbstractTableModel
from java.awt import BorderLayout, GridLayout
from javax.swing.event import ListSelectionListener
from java.awt.event import ActionListener
import db_read

class DictTableModel(AbstractTableModel):
    def __init__(self, cols, keys):
        AbstractTableModel.__init__(self)
        self.cols = cols
        self.keys = keys
        self.rows = []
    def set_rows(self, rows):
        self.rows = rows or []
        self.fireTableDataChanged()
    def getRowCount(self): return len(self.rows)
    def getColumnCount(self): return len(self.cols)
    def getColumnName(self, c): return self.cols[c]
    def getValueAt(self, r, c):
        v = self.rows[r].get(self.keys[c])
        return '' if v is None else v

class RunsModel(DictTableModel):
    def __init__(self):
        DictTableModel.__init__(self, ['RunId','When','Loco','Mode','Dir','Min','Max'], ['run_id','when','loco','mode','dir','min','max'])
class SumModel(DictTableModel):
    def __init__(self):
        DictTableModel.__init__(self, ['Step','Mode','MeanMPH','Kept','Total','MOE'], ['step','mode','mean','kept','total','moe'])
class ResultsRowsModel(DictTableModel):
    def __init__(self):
        DictTableModel.__init__(self, ['Step','Target','FwdMeasured','FwdError','Fwd%Dev','FwdMOE','FwdCV','CurCV','RevMeasured','RevError','Rev%Dev','RevMOE'], ['step','target','f_meas','f_err','f_pct','f_moe','f_tbl','cur_tbl','r_meas','r_err','r_pct','r_moe'])

def open_history_window(state):
    f = JFrame('History - Speed Runs')
    f.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE)
    runs_m = RunsModel(); runs_t = JTable(runs_m)
    sums_m = SumModel(); sums_t = JTable(sums_m)
    rows_m = ResultsRowsModel(); rows_t = JTable(rows_m)
    btn_refresh = JButton('Refresh')
    lbl = JLabel('Runs loaded: 0')
    def refresh():
        runs = db_read.list_speed_runs(limit=200)
        runs_m.set_rows(runs)
        sums_m.set_rows([])
        rows_m.set_rows([])
        lbl.setText('Runs loaded: %d' % len(runs))
    def load_selected():
        r = runs_t.getSelectedRow()
        if r < 0: return
        run_id = int(runs_m.rows[r].get('run_id'))
        sums_m.set_rows(db_read.list_summaries(run_id))
        rows_m.set_rows(db_read.list_results_rows(run_id))
    class AL(ActionListener):
        def __init__(self, fn): self.fn = fn
        def actionPerformed(self, e): self.fn()
    btn_refresh.addActionListener(AL(refresh))
    class LSL(ListSelectionListener):
        def valueChanged(self, e):
            if not e.getValueIsAdjusting():
                load_selected()
    runs_t.getSelectionModel().addListSelectionListener(LSL())
    root = JPanel(BorderLayout())
    top = JPanel(); top.add(btn_refresh); top.add(lbl); root.add(top, BorderLayout.NORTH)
    center = JPanel(GridLayout(3,1))
    p1 = JPanel(BorderLayout()); p1.add(JLabel('Runs'), BorderLayout.NORTH); p1.add(JScrollPane(runs_t), BorderLayout.CENTER)
    p2 = JPanel(BorderLayout()); p2.add(JLabel('Step Summaries'), BorderLayout.NORTH); p2.add(JScrollPane(sums_t), BorderLayout.CENTER)
    p3 = JPanel(BorderLayout()); p3.add(JLabel('Results Rows'), BorderLayout.NORTH); p3.add(JScrollPane(rows_t), BorderLayout.CENTER)
    center.add(p1); center.add(p2); center.add(p3)
    root.add(center, BorderLayout.CENTER)
    f.getContentPane().add(root)
    f.setSize(1100, 800)
    f.setLocationRelativeTo(None)
    f.setVisible(True)
    refresh()
