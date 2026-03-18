# speed_match_math.py
# Shared result computation helpers for JMRI Jython speed matching

CV_GAIN = 0.45
CV_RATIO_MIN = 0.80
CV_RATIO_MAX = 1.20
CV_MAX_STEP_DELTA = 10

def clamp_int(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v

def compute_targets_28(target_min, target_max):
    tmin = float(target_min)
    tmax = float(target_max)
    if tmax < tmin:
        a = tmin
        tmin = tmax
        tmax = a
    out = []
    for s in range(1, 29):
        frac = (s - 1) / 27.0
        out.append(tmin + frac * (tmax - tmin))
    return out

def interp_est_28(anchors):
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

def recommend_table_28_damped(base_28, targets_28, meas_28):
    rec = [0] * 28
    prev = 0
    for s in range(1, 29):
        target = targets_28[s - 1]
        meas = meas_28[s - 1]
        base = base_28[s - 1]
        if meas is None or meas <= 0.0:
            r = base
        else:
            ratio = target / float(meas)
            damped = 1.0 + CV_GAIN * (ratio - 1.0)
            if damped < CV_RATIO_MIN:
                damped = CV_RATIO_MIN
            if damped > CV_RATIO_MAX:
                damped = CV_RATIO_MAX
            r = int(round(base * damped))
        r = clamp_int(r, 0, 255)
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

def step_moe_for(stats_map, step):
    try:
        info = stats_map.get(int(step), None)
        if info is None:
            return None
        return float(info.get("moe", 0.0))
    except:
        return None

def compute_results_payload(target_min, target_max, measured_fwd, measured_rev, stats_fwd, stats_rev, baseline_table):
    targets = compute_targets_28(target_min, target_max)

    f_anchors = []
    for step, mphv in measured_fwd.items():
        try:
            s = int(step)
            m = float(mphv)
            if 1 <= s <= 28:
                f_anchors.append((s, m))
        except:
            pass

    r_anchors = []
    for step, mphv in measured_rev.items():
        try:
            s = int(step)
            m = float(mphv)
            if 1 <= s <= 28:
                r_anchors.append((s, m))
        except:
            pass

    f_est = interp_est_28(f_anchors)
    r_est = interp_est_28(r_anchors)

    current_cv = list(baseline_table) if baseline_table and len(baseline_table) == 28 else [int(round((i + 1) / 28.0 * 255.0)) for i in range(28)]
    f_tbl = recommend_table_28_damped(current_cv, targets, f_est)

    rows = []
    for s in range(1, 29):
        target = targets[s - 1]
        fm = f_est[s - 1]
        rm = r_est[s - 1]

        f_err_val = None
        if fm is not None:
            f_err_val = float(fm) - float(target)

        r_err_val = None
        if (rm is not None) and (fm is not None):
            r_err_val = float(rm) - float(fm)

        f_pct = ""
        if f_err_val is not None and float(target) != 0.0:
            f_pct = "%.2f" % (100.0 * f_err_val / float(target))

        r_pct = ""
        if r_err_val is not None and (fm is not None) and float(fm) != 0.0:
            r_pct = "%.2f" % (100.0 * r_err_val / float(fm))

        f_moe = step_moe_for(stats_fwd, s)
        r_moe = step_moe_for(stats_rev, s)

        rows.append({
            "step": s,
            "target": "%.2f" % float(target),
            "f_meas": "" if fm is None else "%.2f" % float(fm),
            "f_err": "" if f_err_val is None else "%.2f" % float(f_err_val),
            "f_pct": f_pct,
            "f_moe": "" if f_moe is None else "%.2f" % float(f_moe),
            "f_tbl": str(f_tbl[s - 1]),
            "cur_tbl": str(current_cv[s - 1]),
            "r_meas": "" if rm is None else "%.2f" % float(rm),
            "r_err": "" if r_err_val is None else "%.2f" % float(r_err_val),
            "r_pct": r_pct,
            "r_moe": "" if r_moe is None else "%.2f" % float(r_moe)
        })

    return {
        "rows": rows,
        "targets": targets,
        "f_est": f_est,
        "r_est": r_est,
        "f_tbl": f_tbl,
        "cur_cv": current_cv
    }
