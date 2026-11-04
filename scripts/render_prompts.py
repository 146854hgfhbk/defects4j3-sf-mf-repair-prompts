#!/usr/bin/env python3
"""Render SF/MF direct-repair prompts for ALL 827 bugs.

Missing pieces are omitted, the bug itself is never skipped:
  * no failing-test method source  -> keep test id + failure trace, drop the code block
  * no buggy function              -> keep test + failure, add target class/file,
                                      and ask for a unified diff instead of a function
"""
import json, os, re, sys

sys.path.insert(0, "/home/yifeipei")
import d4j_function_granularity as G
import d4j_prompt_extract4 as X

DATA = "/home/yifeipei/d4j_prompt/d4j_prompt_data.jsonl"
OUTDIR = "/home/yifeipei/d4j_prompt"

def marked_source(f):
    lines = (f.get("source") or "").split("\n")
    if not lines or not f.get("start"):
        return ""
    start = f["start"]; marks = {}; miss = {}
    for b in f.get("buggy_lines", []):
        i = b["line"] - start
        if 0 <= i < len(lines):
            if b.get("code") is None:
                miss.setdefault(i, b)
            else:
                marks[i] = True
    out = []
    for i, l in enumerate(lines):
        if i in miss:
            out.append(re.match(r"\s*", l).group(0) + "// BUGGY LINE: missing code (omission)")
        out.append(l + ("  // BUGGY LINE" if i in marks else ""))
    return "\n".join(out)

def failure_text(t):
    lines = ["Test: %s::%s" % (t["class"], t["method"])]
    fl = (t.get("fail_statement") or t.get("fail_line_code") or "").strip()
    if fl:
        lines.append("Failure line: %s" % fl.replace("\n", " "))
    if t.get("exception"):
        lines.append("Error message: %s" % t["exception"])
    for x in (t.get("trace_head") or [])[1:4]:
        lines.append(x)
    return "\n".join(lines)

def target_files(rec):
    """Resolve modified classes to file paths (for the no-function case)."""
    proj, bid = rec["project"], rec["bug_id"]
    mc = os.path.join(G.BASE, proj, "modified_classes", bid + ".src")
    out = []
    if not os.path.exists(mc):
        return out
    rb = rec["buggy_revision"]
    for cls in [x.strip() for x in open(mc, encoding="utf-8", errors="replace").read().split() if x.strip()]:
        f = X.find_class_file(proj, rb, cls)
        out.append((cls, f))
    return out

def pseudo_tests(rec):
    """Trigger metadata for bugs whose test method could not be extracted."""
    out = []
    for t in X.parse_trigger(rec["project"], rec["bug_id"]):
        t = dict(t)
        t["method_source"] = None
        t["fail_statement"] = ""
        t["fail_line_code"] = ""
        t["trace_head"] = [x.strip() for x in t.get("trace", [])[:6]]
        out.append(t)
    return out

def render(rec, variant="A"):
    fs = [f for f in rec["buggy_functions"] if f.get("in_function") and f.get("source")]
    ts = rec["failing_tests"] or pseudo_tests(rec)
    t = ts[0] if ts else None
    n = len(fs)
    missing = []
    if not fs:
        missing.append("buggy_function")
    if not t:
        missing.append("failing_test")
    elif not t.get("method_source"):
        missing.append("test_source")

    p = ["You are a helpful assistant that fixes Java code."]
    if variant == "B1":
        p = ["Provide a fix for the buggy code below."]

    # ---------------- buggy code / target
    if n == 0:
        tf = target_files(rec)
        p.append("")
        p.append("The defect is in the following Java class%s:" % ("es" if len(tf) > 1 else ""))
        for cls, f in tf:
            p.append("- %s%s" % (cls, ("  (%s)" % f) if f else ""))
        p.append("")
        p.append("The exact buggy function could not be extracted automatically, so no code block is given.")
        if t:
            p.append("The failure is described below; use it to locate the fault.")
        p.append("")
        p.append("Return a unified diff (git format) that fixes the bug in the file(s) above:")
        p.append("```diff")
        first = tf[0][1] if tf and tf[0][1] else "<file>"
        p.append("--- a/%s" % first)
        p.append("+++ b/%s" % first)
        p.append("@@ ... @@")
        p.append("```")
        p.append("Do not modify tests. Output only the diff.")
        diff_mode = True
    else:
        diff_mode = False
        if n == 1:
            p.append("The following Java function contains a bug.")
            p.append("")
            p.append("```java")
            p.append(marked_source(fs[0]))
            p.append("```")
            p.append("")
            p.append('The buggy line(s) are marked with "// BUGGY LINE". The fix may involve changes around the marked lines.')
        else:
            p.append("The following %d Java functions together contain a bug: a single defect whose fix spans multiple functions." % n)
            p.append("")
            for i, f in enumerate(fs, 1):
                p.append("### Function %d of %d -- %s -- %s (lines %s-%s)"
                         % (i, n, f["file"], f.get("sig") or f["name"], f["start"], f["end"]))
                p.append("```java")
                p.append(marked_source(f))
                p.append("```")
                p.append("")
            p.append('The buggy line(s) are marked with "// BUGGY LINE". The defect can only be eliminated by coordinated changes in more than one of the functions above.')

    # ---------------- test / failure
    if t:
        p.append("")
        if t.get("method_source"):
            if n == 0:
                p.append("The bug manifests in the following test.")
            else:
                p.append("The function%s fail%s on the following test." % ("s" if n > 1 else "", "" if n > 1 else "s"))
            p.append("")
            p.append("Test code:")
            p.append("```java")
            p.append(t["method_source"])
            p.append("```")
        else:
            p.append("The failure comes from the following test (its source could not be extracted):")
            p.append("`%s::%s`" % (t["class"], t["method"]))
        if len(ts) > 1:
            p.append("Other failing tests: " + ", ".join("%s::%s" % (x["class"], x["method"]) for x in ts[1:6]))
        p.append("")
        p.append("Failure information:")
        p.append("```")
        p.append(failure_text(t))
        p.append("```")

    # ---------------- task
    p.append("")
    if variant == "B1":
        p.append("Do NOT write the patch yet. First analyse the root cause and describe a concrete repair plan "
                 "in natural language (which function/line is wrong, what must change, and how the changes stay "
                 "consistent if several functions are involved). Keep it under 200 words.")
        return "\n".join(p), missing

    p.append("Based on all the information above, please provide a correct fix for the bug.")
    if diff_mode:
        p.append("Return only the unified diff, inside a single ```diff ... ``` block.")
    elif n <= 1:
        p.append("Return the complete fixed function inside a single ```java ... ``` block, and nothing else.")
    else:
        p.append("Return the complete fixed body of EVERY function listed above, in the same order, using exactly this format and no other text:")
        p.append("=== FILE: <file path> ===")
        p.append("=== FUNCTION: <signature> ===")
        p.append("```java")
        p.append("<fixed function>")
        p.append("```")
        p.append("Do not omit, merge, rename or reorder the functions; keep every signature unchanged; do not modify the test.")
    return "\n".join(p), missing

def main():
    rows = [json.loads(l) for l in open(DATA, encoding="utf-8")]
    A, B, partial = [], [], []
    for r in rows:
        pa, miss = render(r, "A")
        pb, _ = render(r, "B1")
        A.append({"bug": r["bug"], "class": r["class"], "n_functions": len([f for f in r["buggy_functions"] if f.get("in_function")]), "missing": miss, "prompt": pa})
        B.append({"bug": r["bug"], "class": r["class"], "n_functions": len([f for f in r["buggy_functions"] if f.get("in_function")]), "missing": miss, "prompt": pb})
        if miss:
            partial.append((r["bug"], ",".join(miss)))
    for name, data in (("prompts_A.jsonl", A), ("prompts_B_stage1.jsonl", B)):
        with open(os.path.join(OUTDIR, name), "w", encoding="utf-8") as f:
            for x in data:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    with open(os.path.join(OUTDIR, "prompts_partial.txt"), "w", encoding="utf-8") as f:
        for bug, miss in partial:
            f.write("%s\t%s\n" % (bug, miss))
    print("wrote", len(A), "prompts; partial:", len(partial))

if __name__ == "__main__":
    main()
