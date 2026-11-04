#!/usr/bin/env python3
"""Prompt-ready extraction for Defects4J 3.0.1 (in-scope bugs).

Ground truth for the buggy lines is the VCS diff between the buggy and fixed
revisions (git diff / svn diff).  The stored framework/patches/<bid>.src.patch
is only used as a FILTER, because the raw diff also carries unrelated edits
(license header bumps, ChangeLog) between the two revisions.

Pipeline per bug
  1. stored patch -> set of changed-code fingerprints (text of '+'/'-' lines)
  2. VCS diff buggy..fixed -> files + hunks
  3. keep hunks whose changed lines match a stored fingerprint (and whose file
     is touched by the stored patch); these constitute the actual fix
  4. failed-test info from framework/projects/<P>/trigger_tests/<bid>
  5. buggy lines = '-' side of kept hunks (old line numbers), or the insertion
     point for pure additions (omission bugs)
  6. buggy code = full Java method enclosing those lines in the buggy file
  7. failing line resolved by matching the exception message inside the test
     method (stack-trace lines drift by a few lines for some bugs)
"""
import argparse, collections, csv, json, os, re, subprocess, sys

sys.path.insert(0, "/home/yifeipei")
import d4j_function_granularity as G

BASE, REPOS = G.BASE, G.REPOS
OUTDIR = "/home/yifeipei/d4j_prompt"
SCOPE = "/home/yifeipei/d4j_granularity/function/final/d4j3_granularity_final.csv"
USE_POSITION_FALLBACK = False

_C, _T, _R, _D = {}, {}, {}, {}

def fetch(proj, rev, path):
    k = (proj, rev, path)
    if k not in _C:
        _C[k] = G.fetch_source(proj, rev, path)
    return _C[k]

def tree(proj, rev):
    k = (proj, rev)
    if k not in _T:
        if proj in G.GIT_REPO:
            r = subprocess.run(["git", "--git-dir", os.path.join(REPOS, G.GIT_REPO[proj]),
                                "ls-tree", "-r", "--name-only", rev], capture_output=True, env=G.ENV)
            files = r.stdout.decode("utf-8", "replace").splitlines()
        else:
            url = "file://%s/%s" % (REPOS, G.SVN_REPO[proj])
            r = subprocess.run([G.SVN, "ls", "-R", "%s@%s" % (url, rev)], capture_output=True, env=G.ENV)
            files = r.stdout.decode("utf-8", "replace").splitlines()
        _T[k] = files
    return _T[k]

def revs():
    if _R:
        return _R
    for proj in sorted(os.listdir(BASE)):
        ab = os.path.join(BASE, proj, "active-bugs.csv")
        if not os.path.exists(ab):
            continue
        with open(ab, newline="") as f:
            for r in list(csv.reader(f))[1:]:
                if len(r) >= 3:
                    _R.setdefault(proj, {})[r[0].strip()] = (r[1].strip(), r[2].strip())
    return _R

def vcs_diff(proj, rb, rf):
    k = (proj, rb, rf)
    if k not in _D:
        if proj in G.GIT_REPO:
            r = subprocess.run(["git", "--git-dir", os.path.join(REPOS, G.GIT_REPO[proj]),
                                "diff", "--no-color", rb, rf], capture_output=True, env=G.ENV)
            out = r.stdout.decode("utf-8", "replace")
        else:
            url = "file://%s/%s" % (REPOS, G.SVN_REPO[proj])
            r = subprocess.run([G.SVN, "diff", "-r", "%s:%s" % (rb, rf), url],
                               capture_output=True, env=G.ENV)
            out = r.stdout.decode("utf-8", "replace")
        _D[k] = out
    return _D[k]

# ---------------------------------------------------------------- parsing

def _n(s):
    return re.sub(r"\s+", "", s)

def parse_unified(text, svn=False):
    files, cur, h = {}, None, None
    for line in text.splitlines():
        if line.startswith("Index: ") and svn:
            cur = line[7:].strip()
            files.setdefault(cur, [])
            h = None
        elif line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = m.group(2) if m else line.split()[-1]
            files.setdefault(cur, [])
            h = None
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m or cur is None:
                h = None
                continue
            h = {"old_start": int(m.group(1)), "old_len": int(m.group(2) or 1),
                 "new_start": int(m.group(3)), "new_len": int(m.group(4) or 1),
                 "lines": []}
            files[cur].append(h)
        elif h is not None and cur is not None and line[:1] in ("+", "-", " "):
            if line.startswith("+++") or line.startswith("---"):
                continue
            h["lines"].append((line[0], line[1:]))
    return files

def stored_fingerprints(patch):
    out, files = set(), set()
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            files.add(m.group(2) if m else line.split()[-1])
        elif line.startswith("Index: "):
            files.add(line[7:].rstrip("$"))
        elif line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
            t = _n(line[1:])
            if len(t) >= 8:
                out.add(t)
    return out, files

def path_match(a, b):
    return a == b or a.endswith("/" + b) or b.endswith("/" + a) or a.split("/")[-1] == b.split("/")[-1]

def pick_hunks(vcs_files, stored_texts, stored_files, extra_files=None):
    """Keep only hunks that belong to the fix."""
    kept = []
    allowed = list(stored_files) + list(extra_files or [])
    for path, hs in vcs_files.items():
        if not any(path_match(path, sf) for sf in allowed):
            continue
        for h in hs:
            changed = [t for k, t in h["lines"] if k in "+-" and len(_n(t)) >= 8]
            score = sum(1 for t in changed
                        if any(_n(t) in s or s in _n(t) for s in stored_texts))
            # drop pure license-header hunks: they sit above the package line
            if not changed:
                continue
            tot = len(changed)
            if score == 0:
                continue
            kept.append((path, h, score, tot))
    return kept

# ---------------------------------------------------------------- tests

def parse_trigger(proj, bid):
    path = os.path.join(BASE, proj, "trigger_tests", bid)
    entries, cur = [], None
    if not os.path.exists(path):
        return entries
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\n")
        m = re.match(r"^---\s+([\w.$]+)::([\w$]+)\s*$", line)
        if m:
            cur = {"class": m.group(1), "method": m.group(2), "trace": [],
                   "fail_line": None, "exception": ""}
            entries.append(cur)
            continue
        if cur is None:
            continue
        if not cur["trace"] and line.strip():
            cur["exception"] = line.strip()
        cur["trace"].append(line)
        if cur["fail_line"] is None:
            mm = re.match(r"^\s*at\s+([\w.$]+)\.([\w$]+)\(([\w$]+)\.java:(\d+)\)", line)
            if mm and mm.group(3) == cur["class"].split(".")[-1]:
                cur["fail_line"] = int(mm.group(4))
    return entries

def find_test_file(proj, rev, fqcn):
    files = tree(proj, rev)
    rel = fqcn.replace(".", "/") + ".java"
    exact = [f for f in files if f.endswith("/" + rel) or f == rel]
    if exact:
        pref = [c for c in exact if "/src/test/" in c or c.startswith("src/test/")
                or "/test/" in c or c.startswith("test/")]
        return (pref or exact)[0]
    simple = fqcn.split(".")[-1] + ".java"
    cands = [f for f in files if f.endswith("/" + simple) or f == simple]
    if not cands:
        return None
    pref = [c for c in cands if "/src/test/" in c or c.startswith("src/test/")
            or "/test/" in c or c.startswith("test/")]
    return (pref or cands)[0]

def resolve_test_method(proj, rev, fqcn, method, depth=3):
    """Find `method` in the test class, walking up the extends chain."""
    f = find_test_file(proj, rev, fqcn)
    seen = set()
    while f and depth > 0 and f not in seen:
        seen.add(f)
        src, _ = fetch(proj, rev, f)
        if not src:
            return None
        ms = G.parse_java_methods(src)
        for m in ms:
            if m["name"] == method:
                return f, src, m
        f = superclass_file(proj, rev, f, src)
        depth -= 1
    return None

def resolve_fail_line(tlines, m, trace_line, exception):
    lo, hi = m["start"], m["end"]
    body = [(i, tlines[i - 1]) for i in range(lo, hi + 1)]

    def find(sub):
        hits = [i for i, t in body if sub in t]
        if not hits:
            return None
        return min(hits, key=lambda i: abs(i - trace_line) if trace_line else i)

    mm = re.search(r"expected:<(.*?)> but was:<(.*?)>", exception or "")
    if mm and mm.group(1):
        exp = mm.group(1).strip()
        for pat in ("assertEquals(%s," % exp, 'assertEquals("%s"' % exp, '"%s"' % exp):
            if len(pat) < 5:
                continue
            h = find(pat)
            if h:
                return h, "message"
    mm = re.search(r"(?:AssertionFailedError|ComparisonFailure|AssertionError):\s*(.+)$", exception or "")
    if mm:
        msg = mm.group(1).strip()
        if msg and len(msg) <= 80:
            h = find(msg)
            if h:
                return h, "message"
    if trace_line and lo <= trace_line <= hi and tlines[trace_line - 1].strip():
        return trace_line, "trace"
    base = trace_line if trace_line else lo
    for d in (1, -1, 2, -2, 3, -3, 4, -4):
        ln = base + d
        if lo <= ln <= hi and tlines[ln - 1].strip():
            return ln, "trace_shift"
    for i, t in body:
        if re.search(r"\bassert\w*\s*\(", t):
            return i, "first_assert"
    return base, "trace_only"

def statement_at(lines, lineno, max_extra=20):
    if not lineno or not (1 <= lineno <= len(lines)):
        return ""
    out, depth, i = [lines[lineno - 1]], 0, lineno - 1
    while True:
        s = lines[i]
        depth += s.count("(") - s.count(")")
        if depth <= 0 and s.rstrip().endswith(";"):
            break
        i += 1
        if i >= len(lines) or i - (lineno - 1) > max_extra:
            break
        out.append(lines[i])
    return "\n".join(x.strip() for x in out)

# ---------------------------------------------------------------- per bug


def find_class_file(proj, rev, fqcn):
    suffix = "/" + fqcn.replace(".", "/") + ".java"
    cands = [f for f in tree(proj, rev) if f.endswith(suffix) or f == suffix[1:]]
    return cands[0] if cands else None

def trace_methods(proj, bid, rev_buggy):
    """Methods of modified classes that appear in the failure stack trace."""
    mc_path = os.path.join(BASE, proj, "modified_classes", bid + ".src")
    if not os.path.exists(mc_path):
        return []
    classes = [x.strip() for x in open(mc_path, encoding="utf-8", errors="replace").read().split() if x.strip()]
    if not classes:
        return []
    frames = []
    for t in parse_trigger(proj, bid):
        for line in t["trace"]:
            mm = re.match(r"^\s*at\s+([\w.$]+)\.([\w$]+)\(([\w$]+\.java):(\d+)\)", line)
            if mm:
                frames.append((mm.group(1), mm.group(2)))
    out = []
    for cls in classes:
        hit = next((m for c, m in frames if c == cls), None)
        if hit is None:
            continue
        f = find_class_file(proj, rev_buggy, cls)
        if not f:
            continue
        src, real = fetch(proj, rev_buggy, f)
        if src is None:
            continue
        ms = G.parse_java_methods(src)
        mb = next((m for m in ms if m["name"] == hit), None)
        if mb is None:
            continue
        lines = [x.rstrip("\r") for x in src.split("\n")]
        out.append({"file": real, "name": mb["name"], "sig": mb["sig"],
                    "start": mb["start"], "end": mb["end"],
                    "source": "\n".join(lines[mb["start"] - 1:mb["end"]]),
                    "approximate": True,
                    "buggy_lines": [{"line": mb["start"], "code": lines[mb["start"] - 1],
                                     "note": "method from failure stack trace"}],
                    "in_function": True})
    return out

def superclass_file(proj, rev, path, src):
    m = re.search(r"\bclass\s+\w+[^{]*?\bextends\s+([\w.$]+)", src)
    if not m:
        return None
    sup = m.group(1)
    f = find_class_file(proj, rev, sup)
    if f:
        return f
    simple = sup.split(".")[-1] + ".java"
    cands = [x for x in tree(proj, rev) if x.endswith("/" + simple)]
    return cands[0] if len(cands) == 1 else None

def build_record(proj, bid, cls, sub):
    rev_buggy, rev_fixed = revs()[proj][bid]
    patch = open(os.path.join(BASE, proj, "patches", bid + ".src.patch"),
                 encoding="utf-8", errors="replace").read()
    st_texts, st_files = stored_fingerprints(patch)
    vcs = parse_unified(vcs_diff(proj, rev_buggy, rev_fixed), svn=(proj in G.SVN_REPO))
    extra = []
    mc = os.path.join(BASE, proj, "modified_classes", bid + ".src")
    if os.path.exists(mc):
        for cn in open(mc, encoding="utf-8", errors="replace").read().split():
            cn = cn.strip()
            if cn:
                extra.append(cn.replace(".", "/") + ".java")
    kept = pick_hunks(vcs, st_texts, st_files, extra)
    if not kept:
        # last resort: any hunk in an allowed file below its package line
        for path, hs in vcs.items():
            if not any(path_match(path, sf) for sf in list(st_files) + extra):
                continue
            for h in hs:
                if h["old_start"] > 45:
                    kept.append((path, h, 0, 0))
    stats = collections.Counter()
    if not kept:
        stats["no_fix_hunk"] += 1

    funcs = {}
    for (path, h, score, tot) in kept:
        fpath = path
        for sf in list(st_files) + extra:
            if path_match(path, sf):
                fpath = sf
                break
        bsrc, real = fetch(proj, rev_buggy, fpath)
        if bsrc is None:
            stats["src_missing"] += 1
            continue
        blines = [x.rstrip("\r") for x in bsrc.split("\n")]
        bms = G.parse_java_methods(bsrc)
        adds = [(h["old_start"] + i, t) for i, (k, t) in enumerate(h["lines"]) if k == "+"]
        # '-'-side line numbers: walk the old side
        pos, dels = h["old_start"], []
        plus_runs = 0
        for k, t in h["lines"]:
            if k == " ":
                pos += 1
            elif k == "-":
                dels.append((pos, t))
                pos += 1
        if not dels:
            # pure addition: insertion point = after the leading context lines
            # (i.e. the buggy line where the missing code should be inserted)
            p = h["old_start"] - 1
            ins = h["old_start"] - 1
            for k, _t in h["lines"]:
                if k == " ":
                    p += 1
                    ins = p
                elif k == "+":
                    ins = p
                    break
            dels = [(ins + 1 if ins < h["old_start"] + h["old_len"] else ins, None)]
        before_n = len(funcs)
        for (ln, code) in dels:
            mi = G.containing_method(bms, ln)
            if mi is None and ln > 1:
                mi = G.containing_method(bms, ln - 1)
            if mi is None:
                stats["outside_method"] += 1
                continue
            m = bms[mi]
            key = (real, m["name"], m["start"])
            if key not in funcs:
                funcs[key] = {"file": real, "name": m["name"], "sig": m["sig"],
                              "start": m["start"], "end": m["end"],
                              "source": "\n".join(blines[m["start"] - 1:m["end"]]),
                              "buggy_lines": [], "in_function": True}
            funcs[key]["buggy_lines"].append(
                {"line": ln, "code": code if code is not None else None,
                 "note": None if code is not None else "missing code (omission)"})
        has_real_minus = any(k == "-" for k, _ in h["lines"])
        if USE_POSITION_FALLBACK and len(funcs) == before_n and has_real_minus:
            lead = 0
            for k, _t in h["lines"]:
                if k == " ":
                    lead += 1
                else:
                    break
            pos = h["old_start"] + lead
            mi = G.containing_method(bms, pos)
            if mi is None:
                starts = [i for i, m in enumerate(bms) if m["start"] <= pos]
                mi = starts[-1] if starts else (0 if bms else None)
            if mi is not None:
                m = bms[mi]
                ln = max(m["start"], min(pos, m["end"]))
                key = (real, m["name"], m["start"])
                if key not in funcs:
                    funcs[key] = {"file": real, "name": m["name"], "sig": m["sig"],
                                  "start": m["start"], "end": m["end"],
                                  "source": "\n".join(blines[m["start"] - 1:m["end"]]),
                                  "buggy_lines": [], "in_function": True}
                funcs[key]["buggy_lines"].append(
                    {"line": ln, "code": blines[ln - 1], "note": "hunk located by position"})
                stats["fallback_position"] += 1
    funcs = list(funcs.values())

    if not funcs:
        # Fallback: locate the target method from the stored patch's own context
        # (some bugs' repo revisions do not contain the fix commit at all).
        cand_names = []
        for line in patch.splitlines():
            if line[:1] not in (" ", "+", "-") or line.startswith(("+++", "---")):
                continue
            mm = re.search(r"\b(?:public|protected|private|static|final|\s)+"
                           r"[\w$<>\[\],.\s]+?\s(\w+)\s*\(", line)
            if mm:
                cand_names.append(mm.group(1))
        for sf in st_files:
            if not sf.endswith(".java"):
                continue
            bsrc, real = fetch(proj, rev_buggy, sf)
            if bsrc is None:
                continue
            blines = [x.rstrip("\r") for x in bsrc.split("\n")]
            bms = G.parse_java_methods(bsrc)
            for nm in cand_names:
                mb = next((m for m in bms if m["name"] == nm), None)
                if mb is None:
                    continue
                funcs.append({"file": real, "name": mb["name"], "sig": mb["sig"],
                              "start": mb["start"], "end": mb["end"],
                              "source": "\n".join(blines[mb["start"] - 1:mb["end"]]),
                              "approximate": True,
                              "buggy_lines": [{"line": mb["start"],
                                               "code": blines[mb["start"] - 1],
                                               "note": "method located from the patch signature"}],
                              "in_function": True})
                break
            if funcs:
                break
        if not funcs:
            tm = trace_methods(proj, bid, rev_buggy)
            if tm:
                funcs.extend(tm)
                stats["fallback_trace"] += 1
            else:
                stats["fallback_failed"] += 1

    tests = []
    for t in parse_trigger(proj, bid):
        base = find_test_file(proj, rev_fixed, t["class"])
        t["file"] = base
        found = resolve_test_method(proj, rev_fixed, t["class"], t["method"], depth=3)
        if not found:
            stats["test_method_missing"] += 1
            continue
        tpath, tsrc, m = found
        t["file"] = tpath
        tlines = [x.rstrip("\r") for x in tsrc.split("\n")]
        if base and tpath != base:
            stats["test_from_super"] += 1
        fl, how = resolve_fail_line(tlines, m, t["fail_line"], t["exception"])
        t.update({"method_source": "\n".join(tlines[m["start"] - 1:m["end"]]),
                  "method_start": m["start"], "method_end": m["end"],
                  "fail_line": fl, "fail_line_how": how,
                  "fail_line_code": tlines[fl - 1] if fl and 1 <= fl <= len(tlines) else "",
                  "fail_statement": statement_at(tlines, fl),
                  "trace_head": [x.strip() for x in t["trace"][:6]]})
        tests.append(t)
        stats["tests_ok"] += 1

    return {"bug": "%s-%s" % (proj, bid), "project": proj, "bug_id": bid,
            "class": cls, "subtype": sub,
            "buggy_revision": rev_buggy, "fixed_revision": rev_fixed,
            "buggy_functions": funcs, "failing_tests": tests}, stats

def render_prompt(rec):
    p = ["You are fixing a real bug in the Java project `%s` (%s, %s)."
         % (rec["project"], rec["bug"], rec["class"]), ""]
    t = rec["failing_tests"][0] if rec["failing_tests"] else None
    if t:
        p += ["## Failing test method", "```java", t["method_source"], "```", ""]
        p += ["## Failing test line (%s:%d)" % (t["file"], t["fail_line"]),
              "```java", t["fail_statement"] or t["fail_line_code"], "```", ""]
        p += ["## Failure", "```"] + t["trace_head"][:4] + ["```", ""]
    p += ["## Buggy code"]
    for f in rec["buggy_functions"]:
        p += ["### %s:%s (lines %d-%d)" % (f["file"], f["name"], f["start"], f["end"]),
              "```java", f["source"], "```", ""]
    p += ["## Buggy line(s)"]
    for f in rec["buggy_functions"]:
        for b in f["buggy_lines"]:
            if b["code"] is None:
                p.append("- %s:%d  (missing code -- omission bug)" % (f["file"], b["line"]))
            else:
                p.append("- %s:%d  `%s`" % (f["file"], b["line"], b["code"].strip()))
    p += ["", "Task: modify the buggy code so that the failing test passes. Do not modify the test."]
    return "\n".join(p)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(OUTDIR, "d4j_prompt_data.jsonl"))
    ap.add_argument("--prompts", default=os.path.join(OUTDIR, "d4j_prompts.jsonl"))
    args = ap.parse_args()
    scope = []
    with open(SCOPE, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["in_scope"] == "yes":
                scope.append((r["project"], r["bug_id"], r["class"], r["subtype"]))
    os.makedirs(OUTDIR, exist_ok=True)
    stats, n = collections.Counter(), 0
    with open(args.out, "w", encoding="utf-8") as fo, open(args.prompts, "w", encoding="utf-8") as fp:
        for (proj, bid, cls, sub) in scope:
            if args.limit and n >= args.limit:
                break
            try:
                rec, st = build_record(proj, bid, cls, sub)
            except Exception as ex:
                stats["exception"] += 1
                stats["exc_" + type(ex).__name__] += 1
                continue
            rec["prompt"] = render_prompt(rec)
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fp.write(json.dumps({"bug": rec["bug"], "prompt": rec["prompt"]}, ensure_ascii=False) + "\n")
            n += 1
            for k, v in st.items():
                stats[k] += v
    print("wrote", n)
    print(dict(stats))

if __name__ == "__main__":
    main()
