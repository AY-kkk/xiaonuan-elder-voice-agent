# Errors

Command failures and integration errors.

---

## [ERR-20260702-001] python_command_missing

**Logged**: 2026-07-02
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
The project shell on this machine does not provide a `python` command; test commands should use `python3` or the project virtualenv interpreter.

### Error
```text
/bin/bash: python: command not found
```

### Context
- Command attempted: `python -m backend.scripts.test_trajectory_export`
- Environment: macOS workspace for `/Users/bytedance/Desktop/语音项目`
- This occurred while following the implementation plan for VoiceBench and trajectory export tooling.

### Suggested Fix
Use `python3 -m ...` for local test commands unless `.venv/bin/python` exists and is explicitly selected.

### Metadata
- Reproducible: yes
- Related Files: docs/plans/2026-07-02-voicebench-distillation-tooling.md

---

## [ERR-20260702-002] system_python_missing_backend_dependencies

**Logged**: 2026-07-02
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
Running backend integration tests with system `python3` can miss project dependencies such as `httpx`; use `.venv/bin/python` for tests that import backend requirements.

### Error
```text
ModuleNotFoundError: No module named 'httpx'
```

### Context
- Command attempted: `python3 -m backend.scripts.test_e2e`
- The project virtualenv had the needed dependencies.

### Suggested Fix
Use `.venv/bin/python -m ...` for backend integration tests, especially those using `httpx`, `websockets`, or `uvicorn`.

### Metadata
- Reproducible: yes
- Related Files: backend/scripts/test_e2e.py
- See Also: ERR-20260702-001

---

## [ERR-20260702-003] manual_cached_patch_corrupt

**Logged**: 2026-07-02
**Priority**: low
**Status**: pending
**Area**: workflow

### Summary
Applying a long hand-written `git apply --cached` patch failed with a corrupt patch error while trying to stage only part of a dirty file.

### Error
```text
error: corrupt patch at line 69
```

### Context
- Goal: stage only trajectory-export changes in `backend/server.py` while preserving unrelated user edits in the worktree.
- The failed patch did not alter the index.

### Suggested Fix
For dirty files with overlapping hunks, prefer staging the full file and then applying a smaller reverse patch to the cached index, or use a temporary index.

### Metadata
- Reproducible: unknown
- Related Files: backend/server.py

---
