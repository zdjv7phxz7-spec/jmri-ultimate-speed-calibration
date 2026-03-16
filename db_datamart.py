import os, time, shutil

try:
    from java.lang import Class
    from java.sql import DriverManager
except:
    Class = None
    DriverManager = None

import settings

def _ensure_dir(p):
    if p and (not os.path.isdir(p)):
        os.makedirs(p)

def connect_autocommit():
    if Class is None or DriverManager is None:
        raise Exception("JDBC not available in this Jython environment.")
    _ensure_dir(os.path.dirname(settings.DB_PATH))
    Class.forName("org.sqlite.JDBC")
    return DriverManager.getConnection("jdbc:sqlite:" + settings.DB_PATH)

def connect():
    conn = connect_autocommit()
    conn.setAutoCommit(False)
    return conn

def _read_text(path):
    f = open(path, "r")
    try:
        return f.read()
    finally:
        f.close()

def init_schema():
    sql_path = settings.SCHEMA_SQL_PATH
    if (not sql_path) or (not os.path.exists(sql_path)):
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            alt = os.path.join(here, 'schema_jmri_datamart.sql')
            if os.path.exists(alt):
                sql_path = alt
        except:
            pass
    if (not sql_path) or (not os.path.isfile(sql_path)):
        raise Exception("Schema SQL not found: " + str(sql_path))
    sql_text = _read_text(sql_path)
    conn = connect_autocommit()
    try:
        st = conn.createStatement()
        try:
            for part in sql_text.split(';'):
                q = part.strip()
                if q:
                    st.execute(q)
        finally:
            st.close()
    finally:
        conn.close()

def _exec(conn, sql, params):
    ps = conn.prepareStatement(sql)
    try:
        for i, v in enumerate(params):
            ps.setObject(i + 1, v)
        ps.executeUpdate()
    finally:
        ps.close()

def _last_rowid(conn):
    st = conn.createStatement()
    try:
        rs = st.executeQuery("SELECT last_insert_rowid()")
        try:
            if rs.next():
                return int(rs.getLong(1))
        finally:
            rs.close()
    finally:
        st.close()
    return None

def _table_columns(conn, table):
    st = conn.createStatement()
    try:
        rs = st.executeQuery("PRAGMA table_info(%s)" % table)
        cols = []
        try:
            while rs.next():
                cols.append(str(rs.getString("name")))
        finally:
            rs.close()
        return cols
    finally:
        st.close()

def _insert_dynamic(conn, table, data, replace=False):
    names = []
    vals = []
    for k in data.keys():
        names.append(k)
        vals.append(data[k])
    verb = 'INSERT OR REPLACE' if replace else 'INSERT'
    sql = "%s INTO %s(%s) VALUES(%s)" % (verb, table, ', '.join(names), ','.join(['?'] * len(names)))
    _exec(conn, sql, vals)

def upsert_loco(conn, loco_id, display_name, addr, addr_is_long, roster_fileName):
    cols = _table_columns(conn, 'loco')
    data = {'loco_id': str(loco_id)}
    if 'display_name' in cols:
        data['display_name'] = display_name
    if 'dcc_address' in cols:
        data['dcc_address'] = str(addr)
    elif 'addr' in cols:
        data['addr'] = int(addr)
    if 'addr_is_long' in cols:
        data['addr_is_long'] = 1 if addr_is_long else 0
    if 'roster_file' in cols:
        data['roster_file'] = roster_fileName
    elif 'roster_fileName' in cols:
        data['roster_fileName'] = roster_fileName
    if 'updated_ts' in cols:
        data['updated_ts'] = int(time.time())
    update_cols = [k for k in data.keys() if k != 'loco_id']
    sql = "INSERT INTO loco(%s) VALUES(%s)" % (', '.join(data.keys()), ','.join(['?'] * len(data)))
    if update_cols:
        sql += " ON CONFLICT(loco_id) DO UPDATE SET " + ', '.join(["%s=excluded.%s" % (k, k) for k in update_cols])
    _exec(conn, sql, [data[k] for k in data.keys()])

def begin_speed_run(conn, loco_id, mode, direction, target_min_mph, target_max_mph):
    cols = _table_columns(conn, 'speed_run')
    data = {'loco_id': str(loco_id), 'run_ts': int(time.time())}
    if 'direction' in cols:
        data['direction'] = str(direction)
    if 'run_mode' in cols:
        data['run_mode'] = str(mode)
    elif 'mode' in cols:
        data['mode'] = str(mode)
    if 'target_min_mph' in cols:
        data['target_min_mph'] = float(target_min_mph)
    if 'target_max_mph' in cols:
        data['target_max_mph'] = float(target_max_mph)
    if 'notes' in cols:
        data['notes'] = None
    if 'created_ts' in cols:
        data['created_ts'] = int(time.time())
    _insert_dynamic(conn, 'speed_run', data)
    return _last_rowid(conn)

def log_sample(conn, run_id, step, measure_mode, mph):
    cols = _table_columns(conn, 'speed_sample')
    mm = str(measure_mode)
    data = {'run_id': int(run_id), 'mph': float(mph)}
    if 'sample_ts' in cols:
        data['sample_ts'] = int(time.time())
    if 'direction' in cols:
        data['direction'] = 'REV' if mm.upper().startswith('REV') else 'FWD'
    if 'speed_step' in cols:
        data['speed_step'] = int(step)
    elif 'step' in cols:
        data['step'] = int(step)
    if 'mode' in cols:
        data['mode'] = mm
    elif 'measure_mode' in cols:
        data['measure_mode'] = mm
    if 'created_ts' in cols:
        data['created_ts'] = int(time.time())
    _insert_dynamic(conn, 'speed_sample', data)

def log_summary(conn, run_id, step, measure_mode, mean_mph, kept_n, total_n, mad, sigma_est, moe):
    cols = _table_columns(conn, 'speed_step_summary')
    mm = str(measure_mode)
    data = {'run_id': int(run_id), 'mean_mph': float(mean_mph), 'kept_n': int(kept_n), 'total_n': int(total_n)}
    if 'direction' in cols:
        data['direction'] = 'REV' if mm.upper().startswith('REV') else 'FWD'
    if 'speed_step' in cols:
        data['speed_step'] = int(step)
    elif 'step' in cols:
        data['step'] = int(step)
    if 'mode' in cols:
        data['mode'] = mm
    elif 'measure_mode' in cols:
        data['measure_mode'] = mm
    if 'mad' in cols:
        data['mad'] = float(mad)
    if 'sigma_est' in cols:
        data['sigma_est'] = float(sigma_est)
    if 'moe' in cols:
        data['moe'] = float(moe)
    if 'created_ts' in cols:
        data['created_ts'] = int(time.time())
    _insert_dynamic(conn, 'speed_step_summary', data)

def _sha256(path):
    try:
        import hashlib
        h = hashlib.sha256()
        f = open(path, 'rb')
        try:
            while True:
                b = f.read(1024*1024)
                if not b:
                    break
                h.update(b)
        finally:
            f.close()
        return h.hexdigest()
    except:
        return None

def _is_img(name):
    lf = str(name).lower()
    for e in settings.IMAGE_EXTS:
        if lf.endswith(e):
            return True
    return False

def import_iphone_photos(status_cb=None):
    inbox = settings.PHOTO_INBOX_ROOT
    lib_root = settings.IMAGE_LIBRARY_ROOT
    if not os.path.isdir(inbox):
        return 'Inbox not found: ' + inbox
    _ensure_dir(lib_root)
    conn = connect()
    try:
        imported = 0
        for car_key in os.listdir(inbox):
            if str(car_key).startswith('_'):
                continue
            car_folder = os.path.join(inbox, car_key)
            if not os.path.isdir(car_folder):
                continue
            _exec(conn, "INSERT INTO car_ext(car_key, updated_ts) VALUES(?,strftime('%s','now')) ON CONFLICT(car_key) DO UPDATE SET updated_ts=strftime('%s','now')", [str(car_key)])
            dest_car = os.path.join(lib_root, str(car_key))
            _ensure_dir(dest_car)
            for fn in os.listdir(car_folder):
                src = os.path.join(car_folder, fn)
                if (not os.path.isfile(src)) or (not _is_img(fn)):
                    continue
                ts = time.strftime('%Y%m%d_%H%M%S', time.localtime())
                dest_name = ts + '_' + os.path.basename(fn)
                dest = os.path.join(dest_car, dest_name)
                sha = _sha256(src)
                shutil.copy2(src, dest)
                rel_path = 'images/cars/%s/%s' % (str(car_key), str(dest_name))
                _exec(conn, "INSERT INTO car_media(car_key, event_id, media_type, file_path, caption, taken_ts, imported_ts, source, sha256, created_ts) VALUES(?,NULL,'photo',?,NULL,NULL,?,'iphone',?,strftime('%s','now'))", [str(car_key), str(rel_path), int(time.time()), str(sha)])
                proc = os.path.join(inbox, '_processed', str(car_key))
                _ensure_dir(proc)
                try:
                    shutil.move(src, os.path.join(proc, os.path.basename(fn)))
                except:
                    pass
                imported += 1
                if status_cb and (imported % 5 == 0):
                    status_cb('Imported %d...' % imported)
        conn.commit()
        return 'Photo import complete. Imported %d photo(s).' % int(imported)
    finally:
        conn.close()

def add_maintenance(car_key, category, summary, details):
    conn = connect()
    try:
        _exec(conn, "INSERT INTO car_ext(car_key, updated_ts) VALUES(?,strftime('%s','now')) ON CONFLICT(car_key) DO UPDATE SET updated_ts=strftime('%s','now')", [str(car_key)])
        _exec(conn, "INSERT INTO car_maintenance_event(car_key, event_ts, category, summary, details, created_ts) VALUES(?,?,?,?,?,strftime('%s','now'))", [str(car_key), int(time.time()), str(category), str(summary), str(details)])
        conn.commit()
        return 'Inserted maintenance for %s' % str(car_key)
    finally:
        conn.close()

def log_results_row(conn, run_id, step, target_mph, f_meas, f_err, f_pct, f_moe, f_tbl, cur_tbl, r_meas, r_err, r_pct, r_moe):
    cols = _table_columns(conn, 'speed_run_results_row')
    data = {'run_id': int(run_id), 'step': int(step)}
    if 'target_mph' in cols: data['target_mph'] = None if target_mph in [None, ''] else float(target_mph)
    if 'fwd_measured_mph' in cols: data['fwd_measured_mph'] = None if f_meas in [None, ''] else float(f_meas)
    if 'fwd_error_mph' in cols: data['fwd_error_mph'] = None if f_err in [None, ''] else float(f_err)
    if 'fwd_pct_dev' in cols: data['fwd_pct_dev'] = None if f_pct in [None, ''] else float(f_pct)
    if 'fwd_moe' in cols: data['fwd_moe'] = None if f_moe in [None, ''] else float(f_moe)
    if 'fwd_cv_value' in cols: data['fwd_cv_value'] = None if f_tbl in [None, ''] else int(f_tbl)
    if 'current_cv_value' in cols: data['current_cv_value'] = None if cur_tbl in [None, ''] else int(cur_tbl)
    if 'rev_measured_mph' in cols: data['rev_measured_mph'] = None if r_meas in [None, ''] else float(r_meas)
    if 'rev_error_mph' in cols: data['rev_error_mph'] = None if r_err in [None, ''] else float(r_err)
    if 'rev_pct_dev' in cols: data['rev_pct_dev'] = None if r_pct in [None, ''] else float(r_pct)
    if 'rev_moe' in cols: data['rev_moe'] = None if r_moe in [None, ''] else float(r_moe)
    _insert_dynamic(conn, 'speed_run_results_row', data, replace=True)

def get_latest_run_id_for_loco(conn, loco_id):
    sql = 'SELECT run_id FROM speed_run WHERE loco_id=? ORDER BY run_ts DESC, run_id DESC LIMIT 1'
    ps = conn.prepareStatement(sql)
    try:
        ps.setString(1, str(loco_id))
        rs = ps.executeQuery()
        try:
            if rs.next():
                return int(rs.getLong(1))
            return None
        finally:
            rs.close()
    finally:
        ps.close()

def log_results_rows(conn, run_id, rows):
    if run_id is None:
        return 0
    count = 0
    for r in (rows or []):
        step = int(r.get('step', 0) or 0)
        if step <= 0:
            continue
        log_results_row(conn, int(run_id), step, r.get('target'), r.get('f_meas'), r.get('f_err'), r.get('f_pct'), r.get('f_moe'), r.get('f_tbl'), r.get('cur_tbl'), r.get('r_meas'), r.get('r_err'), r.get('r_pct'), r.get('r_moe'))
        count += 1
    return count
