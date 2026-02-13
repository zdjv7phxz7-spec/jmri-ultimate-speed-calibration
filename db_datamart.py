# db_datamart.py (ASCII only; no coding header)
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
    # Use autocommit=True for PRAGMA journal_mode=WAL and schema creation.
    # SQLite cannot change journal mode inside an explicit transaction.
    if Class is None or DriverManager is None:
        raise Exception("JDBC not available in this Jython environment.")
    _ensure_dir(os.path.dirname(settings.DB_PATH))
    Class.forName("org.sqlite.JDBC")
    conn = DriverManager.getConnection("jdbc:sqlite:" + settings.DB_PATH)
    # leave autocommit ON
    return conn

def connect():
    if Class is None or DriverManager is None:
        raise Exception("JDBC not available in this Jython environment.")
    _ensure_dir(os.path.dirname(settings.DB_PATH))
    Class.forName("org.sqlite.JDBC")
    conn = DriverManager.getConnection("jdbc:sqlite:" + settings.DB_PATH)
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
    if (not path) or (not os.path.exists(path)):
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            alt = os.path.join(here, 'schema_jmri_datamart.sql')
            if os.path.exists(alt):
                path = alt
        except:
            pass
    if not os.path.isfile(sql_path):
        raise Exception("Schema SQL not found: " + sql_path)
    sql_text = _read_text(sql_path)

    # IMPORTANT: journal_mode=WAL cannot be set from within a transaction.
    # Use an autocommit connection for schema initialization.
    conn = connect_autocommit()
    try:
        st = conn.createStatement()
        try:
            for part in sql_text.split(";"):
                s = part.strip()
                if s:
                    st.execute(s)
        finally:
            st.close()
    finally:
        conn.close()



def _exec(conn, sql, params):
    ps = conn.prepareStatement(sql)
    try:
        for i,v in enumerate(params):
            ps.setObject(i+1, v)
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

def upsert_loco(conn, loco_id, display_name, addr, addr_is_long, roster_fileName):
    _exec(conn,
          "INSERT INTO loco(loco_id, display_name, addr, addr_is_long, roster_fileName, updated_ts) "
          "VALUES(?,?,?,?,?,strftime('%s','now')) "
          "ON CONFLICT(loco_id) DO UPDATE SET display_name=excluded.display_name, addr=excluded.addr, "
          "addr_is_long=excluded.addr_is_long, roster_fileName=excluded.roster_fileName, updated_ts=strftime('%s','now')",
          [loco_id, display_name, int(addr), 1 if addr_is_long else 0, roster_fileName])

def begin_speed_run(conn, loco_id, mode, direction, target_min_mph, target_max_mph):
    _exec(conn,
          "INSERT INTO speed_run(loco_id, run_ts, mode, direction, target_min_mph, target_max_mph, notes, created_ts) "
          "VALUES(?,?,?,?,?,?,?,strftime('%s','now'))",
          [loco_id, int(time.time()), str(mode), str(direction), float(target_min_mph), float(target_max_mph), None])
    return _last_rowid(conn)

def log_sample(conn, run_id, step, measure_mode, mph):
    _exec(conn,
          "INSERT INTO speed_sample(run_id, step, measure_mode, mph, created_ts) VALUES(?,?,?,?,strftime('%s','now'))",
          [int(run_id), int(step), str(measure_mode), float(mph)])

def log_summary(conn, run_id, step, measure_mode, mean_mph, kept_n, total_n, mad, sigma_est, moe):
    _exec(conn,
          "INSERT INTO speed_step_summary(run_id, step, measure_mode, mean_mph, kept_n, total_n, mad, sigma_est, moe, created_ts) "
          "VALUES(?,?,?,?,?,?,?,?,?,strftime('%s','now'))",
          [int(run_id), int(step), str(measure_mode), float(mean_mph), int(kept_n), int(total_n),
           float(mad), float(sigma_est), float(moe)])

def _sha256(path):
    try:
        import hashlib
        h = hashlib.sha256()
        f = open(path, "rb")
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
        return "Inbox not found: " + inbox

    _ensure_dir(lib_root)
    conn = connect()
    try:
        imported = 0
        for car_key in os.listdir(inbox):
            if str(car_key).startswith("_"):
                continue
            car_folder = os.path.join(inbox, car_key)
            if not os.path.isdir(car_folder):
                continue

            _exec(conn,
                 "INSERT INTO car_ext(car_key, updated_ts) VALUES(?,strftime('%s','now')) "
                 "ON CONFLICT(car_key) DO UPDATE SET updated_ts=strftime('%s','now')",
                 [str(car_key)])

            dest_car = os.path.join(lib_root, str(car_key))
            _ensure_dir(dest_car)

            for fn in os.listdir(car_folder):
                src = os.path.join(car_folder, fn)
                if (not os.path.isfile(src)) or (not _is_img(fn)):
                    continue
                ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
                dest_name = ts + "_" + os.path.basename(fn)
                dest = os.path.join(dest_car, dest_name)
                sha = _sha256(src)
                shutil.copy2(src, dest)

                rel_path = "images/cars/%s/%s" % (str(car_key), str(dest_name))
                _exec(conn,
                      "INSERT INTO car_media(car_key, event_id, media_type, file_path, caption, taken_ts, imported_ts, source, sha256, created_ts) "
                      "VALUES(?,NULL,'photo',?,NULL,NULL,?,'iphone',?,strftime('%s','now'))",
                      [str(car_key), str(rel_path), int(time.time()), str(sha)])

                proc = os.path.join(inbox, "_processed", str(car_key))
                _ensure_dir(proc)
                try:
                    shutil.move(src, os.path.join(proc, os.path.basename(fn)))
                except:
                    pass

                imported += 1
                if status_cb and (imported % 5 == 0):
                    status_cb("Imported %d..." % imported)

        conn.commit()
        return "Photo import complete. Imported %d photo(s)." % int(imported)
    finally:
        conn.close()

def add_maintenance(car_key, category, summary, details):
    conn = connect()
    try:
        _exec(conn,
              "INSERT INTO car_ext(car_key, updated_ts) VALUES(?,strftime('%s','now')) "
              "ON CONFLICT(car_key) DO UPDATE SET updated_ts=strftime('%s','now')",
              [str(car_key)])
        _exec(conn,
              "INSERT INTO car_maintenance_event(car_key, event_ts, category, summary, details, created_ts) "
              "VALUES(?,?,?,?,?,strftime('%s','now'))",
              [str(car_key), int(time.time()), str(category), str(summary), str(details)])
        conn.commit()
        return "Inserted maintenance for %s" % str(car_key)
    finally:
        conn.close()


def _set_nullable(ps, idx, val, cast_fn=None):
    if val is None:
        ps.setObject(idx, None)
    else:
        if cast_fn:
            ps.setObject(idx, cast_fn(val))
        else:
            ps.setObject(idx, val)

def log_results_row(conn, run_id, step, target_mph,
                    f_meas, f_err, f_pct, f_moe, f_tbl, cur_tbl,
                    r_meas, r_err, r_pct, r_moe):
    # Store one row from the Results spreadsheet for a given run and step.
    # Uses INSERT OR REPLACE so repeated Calculate is idempotent for the same run/step.
    sql = ("INSERT OR REPLACE INTO speed_run_results_row("
           "run_id, step, target_mph, "
           "fwd_measured_mph, fwd_error_mph, fwd_pct_dev, fwd_moe, fwd_cv_value, current_cv_value, "
           "rev_measured_mph, rev_error_mph, rev_pct_dev, rev_moe, created_ts"
           ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,strftime('%s','now'))")
    ps = conn.prepareStatement(sql)
    try:
        ps.setObject(1, int(run_id))
        ps.setObject(2, int(step))
        ps.setObject(3, float(target_mph))
        _set_nullable(ps, 4, f_meas, float)
        _set_nullable(ps, 5, f_err, float)
        _set_nullable(ps, 6, f_pct, float)
        _set_nullable(ps, 7, f_moe, float)
        _set_nullable(ps, 8, f_tbl, int)
        _set_nullable(ps, 9, cur_tbl, int)
        _set_nullable(ps,10, r_meas, float)
        _set_nullable(ps,11, r_err, float)
        _set_nullable(ps,12, r_pct, float)
        _set_nullable(ps,13, r_moe, float)
        ps.executeUpdate()
    finally:
        ps.close()

def get_latest_run_id_for_loco(conn, loco_id):
    # Returns most recent run_id for a loco_id, or None.
    sql = "SELECT run_id FROM speed_run WHERE loco_id=? ORDER BY run_ts DESC, run_id DESC LIMIT 1"
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
    # rows: list of dicts from ResultsWindow._compute_results()
    if run_id is None:
        return 0
    count = 0
    for r in (rows or []):
        step = int(r.get("step", 0) or 0)
        if step <= 0:
            continue
        log_results_row(
            conn,
            int(run_id),
            step,
            r.get("target"),
            r.get("f_meas"),
            r.get("f_err"),
            r.get("f_pct"),
            r.get("f_moe"),
            r.get("f_tbl"),
            r.get("cur_tbl"),
            r.get("r_meas"),
            r.get("r_err"),
            r.get("r_pct"),
            r.get("r_moe"),
        )
        count += 1
    return count
