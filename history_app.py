from javax.swing import JFrame, JPanel, JScrollPane, JTable, JButton, JLabel, JOptionPane
from javax.swing.table import AbstractTableModel
from javax.swing.event import ListSelectionListener
from java.awt import BorderLayout, GridLayout, FlowLayout
from java.awt.event import ActionListener, MouseAdapter
import db_read
import results_db_view


class DictTableModel(AbstractTableModel):
    def __init__(self, cols, keys):
        AbstractTableModel.__init__(self)
        self.cols = cols
        self.keys = keys
        self.rows = []

    def set_rows(self, rows):
        self.rows = rows or []
        self.fireTableDataChanged()

    def getRowCount(self):
        return len(self.rows)

    def getColumnCount(self):
        return len(self.cols)

    def getColumnName(self, c):
        return self.cols[c]

    def getValueAt(self, r, c):
        v = self.rows[r].get(self.keys[c])
        return '' if v is None else v


class RunsModel(DictTableModel):
    def __init__(self):
        DictTableModel.__init__(self, ['RunId', 'When', 'Loco', 'Mode', 'Dir', 'Min', 'Max'], ['run_id', 'when', 'loco', 'mode', 'dir', 'min', 'max'])


class SumModel(DictTableModel):
    def __init__(self):
        DictTableModel.__init__(self, ['Step', 'Mode', 'MeanMPH', 'Kept', 'Total', 'MOE'], ['step', 'mode', 'mean', 'kept', 'total', 'moe'])


class ResultsRowsModel(DictTableModel):
    def __init__(self):
        DictTableModel.__init__(self,
            ['Step', 'Target', 'FwdMeasured', 'FwdError', 'Fwd%Dev', 'FwdMOE', 'FwdCV', 'CurCV', 'RevMeasured', 'RevError', 'Rev%Dev', 'RevMOE'],
            ['step', 'target', 'f_meas', 'f_err', 'f_pct', 'f_moe', 'f_tbl', 'cur_tbl', 'r_meas', 'r_err', 'r_pct', 'r_moe'])


def open_history_window(state):
    frame = JFrame('History - Speed Runs')
    frame.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE)

    runs_m = RunsModel()
    sums_m = SumModel()
    rows_m = ResultsRowsModel()

    runs_t = JTable(runs_m)
    sums_t = JTable(sums_m)
    rows_t = JTable(rows_m)

    btn_refresh = JButton('Refresh')
    btn_open = JButton('Open Results')
    status = JLabel('Runs loaded: 0')

    def selected_run_id():
        r = runs_t.getSelectedRow()
        if r < 0:
            return None
        try:
            return int(runs_m.rows[r].get('run_id'))
        except:
            return None

    def refresh():
        runs = db_read.list_speed_runs(limit=200)
        runs_m.set_rows(runs)
        sums_m.set_rows([])
        rows_m.set_rows([])
        status.setText('Runs loaded: %d' % len(runs))

    def load_selected():
        run_id = selected_run_id()
        if run_id is None:
            sums_m.set_rows([])
            rows_m.set_rows([])
            return
        sums_m.set_rows(db_read.list_summaries(run_id))
        rows_m.set_rows(db_read.list_results_rows(run_id))

    def open_selected_results():
        run_id = selected_run_id()
        if run_id is None:
            JOptionPane.showMessageDialog(frame, 'Select a run first.', 'History', JOptionPane.WARNING_MESSAGE)
            return
        results_db_view.open_results_for_run_id(run_id, frame)

    class AL(ActionListener):
        def __init__(self, fn):
            self.fn = fn
        def actionPerformed(self, e):
            self.fn()

    class LSL(ListSelectionListener):
        def valueChanged(self, e):
            if not e.getValueIsAdjusting():
                load_selected()

    class RunClicker(MouseAdapter):
        def mouseClicked(self, e):
            if e.getClickCount() >= 2:
                open_selected_results()

    btn_refresh.addActionListener(AL(refresh))
    btn_open.addActionListener(AL(open_selected_results))
    runs_t.getSelectionModel().addListSelectionListener(LSL())
    runs_t.addMouseListener(RunClicker())

    top = JPanel(FlowLayout(FlowLayout.LEFT))
    top.add(btn_refresh)
    top.add(btn_open)
    top.add(status)

    p1 = JPanel(BorderLayout())
    p1.add(JLabel('Runs'), BorderLayout.NORTH)
    p1.add(JScrollPane(runs_t), BorderLayout.CENTER)

    p2 = JPanel(BorderLayout())
    p2.add(JLabel('Step Summaries'), BorderLayout.NORTH)
    p2.add(JScrollPane(sums_t), BorderLayout.CENTER)

    p3 = JPanel(BorderLayout())
    p3.add(JLabel('Results Rows'), BorderLayout.NORTH)
    p3.add(JScrollPane(rows_t), BorderLayout.CENTER)

    center = JPanel(GridLayout(3, 1))
    center.add(p1)
    center.add(p2)
    center.add(p3)

    root = JPanel(BorderLayout())
    root.add(top, BorderLayout.NORTH)
    root.add(center, BorderLayout.CENTER)
    frame.getContentPane().add(root)
    frame.setSize(1200, 850)
    frame.setLocationRelativeTo(None)
    frame.setVisible(True)
    refresh()
