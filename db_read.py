# db_read.py (ASCII only)
import db_datamart

def _colnames(conn, table):
    st = conn.createStatement()
    try:
        rs = st.executeQuery("PRAGMA table_info(%s)" % table)
        cols = []
        try:
            while rs.next():
                cols.append(rs.getString("name"))
        finally:
            rs.close()
        return cols
    finally:
        st.close()

def list_speed_runs(limit=200):
    conn = db_datamart.connect()
    try:
        cols = _colnames(conn, 'speed_run')
        mode_col = 'run_mode' if 'run_mode' in cols else ('mode' if 'mode' in cols else None)
        sel = ['run_id', 'loco_id', 'run_ts', 'direction']
        sel.insert(3, (mode_col + ' AS mode') if mode_col else "'' AS mode")
        sel.append('target_min_mph' if 'target_min_mph' in cols else 'NULL AS target_min_mph')
        sel.append('target_max_mph' if 'target_max_mph' in cols else 'NULL AS target_max_mph')
        sql = 'SELECT ' + ', '.join(sel) + ' FROM speed_run ORDER BY run_ts DESC, run_id DESC LIMIT %d' % int(limit)
        st = conn.createStatement()
        try:
            rs = st.executeQuery(sql)
            out = []
            try:
                while rs.next():
                    out.append({'run_id': int(rs.getLong('run_id')), 'when': int(rs.getLong('run_ts')), 'loco': rs.getString('loco_id'), 'mode': rs.getString('mode'), 'dir': rs.getString('direction'), 'min': rs.getDouble('target_min_mph') if rs.getObject('target_min_mph') is not None else None, 'max': rs.getDouble('target_max_mph') if rs.getObject('target_max_mph') is not None else None})
            finally:
                rs.close()
            return out
        finally:
            st.close()
    finally:
        conn.close()

def list_summaries(run_id):
    conn = db_datamart.connect()
    try:
        cols = _colnames(conn, 'speed_step_summary')
        step_col = 'speed_step' if 'speed_step' in cols else 'step'
        mode_col = 'mode' if 'mode' in cols else ('measure_mode' if 'measure_mode' in cols else None)
        sql = 'SELECT %s, %s, mean_mph, kept_n, total_n, moe FROM speed_step_summary WHERE run_id=? ORDER BY %s' % (step_col, (mode_col if mode_col else "''"), step_col)
        ps = conn.prepareStatement(sql)
        try:
            ps.setLong(1, int(run_id))
            rs = ps.executeQuery()
            out = []
            try:
                while rs.next():
                    out.append({'step': int(rs.getInt(1)), 'mode': rs.getString(2), 'mean': rs.getDouble(3) if rs.getObject(3) is not None else None, 'kept': rs.getInt(4) if rs.getObject(4) is not None else None, 'total': rs.getInt(5) if rs.getObject(5) is not None else None, 'moe': rs.getDouble(6) if rs.getObject(6) is not None else None})
            finally:
                rs.close()
            return out
        finally:
            ps.close()
    finally:
        conn.close()

def list_results_rows(run_id):
    conn = db_datamart.connect()
    try:
        sql = ('SELECT step,target_mph,fwd_measured_mph,fwd_error_mph,fwd_pct_dev,fwd_moe,'
               'fwd_cv_value,current_cv_value,rev_measured_mph,rev_error_mph,rev_pct_dev,rev_moe '
               'FROM speed_run_results_row WHERE run_id=? ORDER BY step')
        ps = conn.prepareStatement(sql)
        try:
            ps.setLong(1, int(run_id))
            rs = ps.executeQuery()
            out = []
            try:
                while rs.next():
                    out.append({'step': int(rs.getInt(1)), 'target': rs.getDouble(2) if rs.getObject(2) is not None else None, 'f_meas': rs.getDouble(3) if rs.getObject(3) is not None else None, 'f_err': rs.getDouble(4) if rs.getObject(4) is not None else None, 'f_pct': rs.getDouble(5) if rs.getObject(5) is not None else None, 'f_moe': rs.getDouble(6) if rs.getObject(6) is not None else None, 'f_tbl': rs.getInt(7) if rs.getObject(7) is not None else None, 'cur_tbl': rs.getInt(8) if rs.getObject(8) is not None else None, 'r_meas': rs.getDouble(9) if rs.getObject(9) is not None else None, 'r_err': rs.getDouble(10) if rs.getObject(10) is not None else None, 'r_pct': rs.getDouble(11) if rs.getObject(11) is not None else None, 'r_moe': rs.getDouble(12) if rs.getObject(12) is not None else None})
            finally:
                rs.close()
            return out
        finally:
            ps.close()
    finally:
        conn.close()
