#!/usr/bin/env python3
"""
test_teacher_apis.py — Manual smoke test for the Teacher Dashboard endpoints
before flipping them on for real users.

Grok's warning was right: these routes call Database methods
(create_class_code / join_class / get_class_info / get_teacher_classes) that
are easy to typo or leave half-wired, and a broken method here is a silent
500 in prod. Run this against a STAGING deploy (or localhost) first.

Usage:
    python test_teacher_apis.py --base-url https://your-staging-domain.com
    python test_teacher_apis.py --base-url http://localhost:5000

Exits non-zero if any check fails, so you can wire it into CI later.
"""
from __future__ import annotations

import argparse
import sys
import uuid

import requests

PASS = "✅"
FAIL = "❌"


def check(label: str, cond: bool, extra: str = "") -> bool:
    mark = PASS if cond else FAIL
    print(f"{mark} {label}" + (f" — {extra}" if extra and not cond else ""))
    return cond


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="e.g. https://saarthibhai.example.com")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    failures = 0
    teacher_id = f"testteacher_{uuid.uuid4().hex[:8]}"
    student_id = f"teststudent_{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # 1) Health check first — no point testing teacher APIs if the app
    #    itself or Redis is down.
    # ------------------------------------------------------------------
    try:
        r = requests.get(f"{base}/health", timeout=15)
        data = r.json()
        if not check("GET /health returns 200", r.status_code == 200):
            failures += 1
        if not check("Redis connected", data.get("redis") is True,
                     "redis=false — teacher/quiz state needs Redis, fix this first"):
            failures += 1
    except Exception as e:
        check("GET /health reachable", False, str(e))
        print("\nAborting — app isn't reachable at all, fix that before testing teacher APIs.")
        return 1

    # ------------------------------------------------------------------
    # 2) Create a class as a teacher
    # ------------------------------------------------------------------
    class_code = None
    try:
        r = requests.post(f"{base}/api/teacher/create-class", json={
            "client_id": teacher_id, "class_name": "Automated Test Class",
        }, timeout=15)
        data = r.json()
        ok = check("POST /api/teacher/create-class → 200", r.status_code == 200, str(data))
        ok = check("response has ok:true", data.get("ok") is True, str(data)) and ok
        class_code = data.get("class_code")
        ok = check("response has a class_code", bool(class_code), str(data)) and ok
        if not ok:
            failures += 1
    except Exception as e:
        check("POST /api/teacher/create-class did not throw", False, str(e))
        failures += 1

    if not class_code:
        print("\nCan't continue — class creation failed, stopping here.")
        return 1

    # ------------------------------------------------------------------
    # 3) Join the class as a student
    # ------------------------------------------------------------------
    try:
        r = requests.post(f"{base}/api/join-class", json={
            "client_id": student_id, "code": class_code,
        }, timeout=15)
        data = r.json()
        if not check("POST /api/join-class → 200 + ok:true", r.status_code == 200 and data.get("ok") is True, str(data)):
            failures += 1
    except Exception as e:
        check("POST /api/join-class did not throw", False, str(e))
        failures += 1

    # ------------------------------------------------------------------
    # 4) Join with a garbage code — should 404, not 500
    # ------------------------------------------------------------------
    try:
        r = requests.post(f"{base}/api/join-class", json={
            "client_id": student_id, "code": "NOTAREALCODE99",
        }, timeout=15)
        if not check("Invalid class code → 404 (not 500)", r.status_code == 404, f"got {r.status_code}"):
            failures += 1
    except Exception as e:
        check("Invalid-code request did not throw", False, str(e))
        failures += 1

    # ------------------------------------------------------------------
    # 5) Fetch class detail, confirm the student shows up
    # ------------------------------------------------------------------
    try:
        r = requests.get(f"{base}/api/teacher/class/{class_code}", timeout=15)
        data = r.json()
        ok = check("GET /api/teacher/class/<code> → 200", r.status_code == 200, str(data))
        students = data.get("students", [])
        ok = check("joined student appears in class detail",
                   any(s.get("uid") == f"web:{student_id}" for s in students),
                   f"students={students}") and ok
        if not ok:
            failures += 1
    except Exception as e:
        check("GET class detail did not throw", False, str(e))
        failures += 1

    # ------------------------------------------------------------------
    # 6) Teacher's class list should include this class
    # ------------------------------------------------------------------
    try:
        r = requests.get(f"{base}/api/teacher/classes", params={"client_id": teacher_id}, timeout=15)
        data = r.json()
        codes = [c.get("code") for c in data.get("classes", [])]
        if not check("class_code appears in /api/teacher/classes", class_code in codes, f"codes={codes}"):
            failures += 1
    except Exception as e:
        check("GET /api/teacher/classes did not throw", False, str(e))
        failures += 1

    # ------------------------------------------------------------------
    # 7) Nonexistent class detail → 404 not 500
    # ------------------------------------------------------------------
    try:
        r = requests.get(f"{base}/api/teacher/class/ZZZNOPE99", timeout=15)
        if not check("Nonexistent class detail → 404 (not 500)", r.status_code == 404, f"got {r.status_code}"):
            failures += 1
    except Exception as e:
        check("Nonexistent class-detail request did not throw", False, str(e))
        failures += 1

    print(f"\n{'='*50}")
    if failures:
        print(f"{FAIL} {failures} check(s) FAILED — do not flip teacher features live yet.")
        return 1
    print(f"{PASS} All teacher API checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
