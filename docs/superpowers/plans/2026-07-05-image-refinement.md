# Image Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A new `seestar-refine` MCP service that turns the QA keep-list into a DSS-stacked master + preview, plus PixInsight-path support (WBPP stack + handoff to the external `pixinsight-mcp`), driven by an `image-refinement` skill.

**Architecture:** New `src/seestar_refine/` package = a separate FastMCP server (`python -m seestar_refine.server`) on the single Windows/4090 host. Pure cores (keep-list parse, DSS/WBPP command building, stretch, handoff) unit-tested with mocked subprocesses / synthetic FITS; real DSS/PixInsight are external desktop apps, never invoked in tests.

**Tech Stack:** Python 3.12, `astropy`/`numpy` (present) + `Pillow` (new, PNG preview); optional `xisf`. FastMCP (`mcp`). `pytest`. `uv`.

## Global Constraints

- `uv run pytest`, `uv run ruff check src tests`. NEVER bare `python`.
- Never-raise on tool paths → `{"ok": false, "error": ...}`; pure cores never raise on bad input.
- **No real DSS/PixInsight in tests** — mock every subprocess; assert the command line + input file list are built correctly. No network in tests.
- Provenance-log every external invocation (command, input keep-list, output paths; no secrets). Validate the configured CLI path; never exec an arbitrary path from tool args.
- Refine **only the keep-list**; default stacking = register + integrate only (Seestar OSC subs are pre-calibrated).
- Spec of record: `docs/superpowers/specs/2026-07-05-image-refinement-design.md` (read it).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; `git -c core.autocrlf=false commit`.

## File Structure
- `src/seestar_refine/__init__.py`, `config.py`, `keeplist.py`, `backends.py`, `dss.py`, `wbpp.py`, `preview.py`, `handoff.py`, `server.py`
- `skills/image-refinement/SKILL.md`
- `pyproject.toml` (add `seestar_refine` to wheel packages + `Pillow` dep), `uv.lock`
- Tests: `tests/test_refine_config.py`, `_keeplist.py`, `_backends.py`, `_dss.py`, `_wbpp.py`, `_preview.py`, `_handoff.py`, `_refine_server.py`

---

### Task 1: Package foundation — config, keeplist, backends, server scaffold

**Files:** Create `src/seestar_refine/__init__.py`, `config.py`, `keeplist.py`, `backends.py`, `server.py`; Modify `pyproject.toml` (+`uv.lock`); Test `tests/test_refine_config.py`, `_keeplist.py`, `_backends.py`, `_refine_server.py`.

**Interfaces — Produces:**
- `config.py`: `RefineSettings(BaseSettings)` env prefix `SEESTAR_REFINE_`: `dss_cli: str = ""` (path to DeepSkyStackerCL), `pixinsight_exe: str = ""`, `data_dir: Path = Path("./data")`, `output_dir: Path = Path("./data/refine")`, `rejection: str = "kappa-sigma"`, `alignment: str = "auto"`. `get_settings()`.
- `keeplist.py`: `@dataclass KeepList(target:str, sub_paths:list[str])`; `load_keep_list(source, *, data_dir) -> KeepList` — accepts either a `qa_session_report` JSON path (reads its `keep_list` + resolves each sub name under `data_dir`) or a dict; missing subs are dropped with a note; raises nothing (returns empty on bad input).
- `backends.py`: `@dataclass Backends(dss:bool, pixinsight:bool, pixinsight_mcp:bool, notes:list[str])`; `detect_backends(settings, *, bridge_dir=None) -> Backends` — `dss` = `dss_cli` set and the file exists; `pixinsight` = `pixinsight_exe` set and exists; `pixinsight_mcp` = a bridge dir (default `~/.pixinsight-mcp/bridge`) exists. Pure filesystem checks; never raise.
- `server.py`: `mcp = FastMCP("seestar-refine")`; `get_controller()`/a `RefineController`; a `check_backends` tool registered now; `def main(): mcp.run()`.

- [ ] **Step 1: pyproject** — add `src/seestar_refine` to `[tool.hatch.build.targets.wheel] packages`, add `"pillow==<pin>"` to deps; `uv lock`; confirm resolve.
- [ ] **Step 2: failing tests**
```python
# tests/test_refine_keeplist.py
def test_load_keep_list_from_report(tmp_path):
    import json
    (tmp_path/"m27_sub1.fit").write_bytes(b"x"); (tmp_path/"m27_sub2.fit").write_bytes(b"x")
    rep = tmp_path/"qa.json"; rep.write_text(json.dumps({"target":"M27","keep_list":["m27_sub1.fit","m27_sub2.fit","missing.fit"]}))
    from seestar_refine.keeplist import load_keep_list
    kl = load_keep_list(rep, data_dir=tmp_path)
    assert kl.target=="M27" and len(kl.sub_paths)==2 and all(p.endswith(".fit") for p in kl.sub_paths)
# tests/test_refine_backends.py
def test_detect_backends(tmp_path):
    from seestar_refine.config import RefineSettings
    from seestar_refine.backends import detect_backends
    exe = tmp_path/"DeepSkyStackerCL.exe"; exe.write_bytes(b"x")
    b = detect_backends(RefineSettings(_env_file=None, dss_cli=str(exe)), bridge_dir=tmp_path/"nope")
    assert b.dss is True and b.pixinsight_mcp is False
# tests/test_refine_server.py
def test_check_backends_tool_registered():
    import asyncio; from seestar_refine.server import mcp
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "check_backends" in names
```
- [ ] **Step 3: run → FAIL.**
- [ ] **Step 4: implement** config/keeplist/backends + a minimal server with `check_backends` (returns `asdict(detect_backends(...))` wrapped `{"ok":True, "backends":...}`). `RefineController.from_settings()`.
- [ ] **Step 5: run → PASS** (`uv run pytest -v`) + `uv run ruff check src tests`.
- [ ] **Step 6: commit** `feat(refine): seestar-refine package foundation (config, keeplist, backends)`.

---

### Task 2: DSS stacking (`dss.py`) + `stack_keep_list` tool

**Files:** Create `src/seestar_refine/dss.py`; Modify `server.py`; Test `tests/test_refine_dss.py` + `_refine_server.py`.

**Interfaces — Produces:** `build_file_list(keep_list, *, dark_paths=None, flat_paths=None) -> str` (DSS file-list text: each light sub with `checked`/type flags per the DSS file-list format); `run_dss(file_list_path, output_dir, *, dss_cli, timeout_s=1800, runner=subprocess.run) -> dict` (`{ok, master_path, log, returncode}`; `runner` injectable for tests); `stack(keep_list, settings, *, runner=...) -> StackResult`. `StackResult` dataclass per spec (define in `dss.py` or a shared `models.py`).

- [ ] **Step 1: failing tests** (inject a fake `runner`)
```python
# tests/test_refine_dss.py
def test_build_file_list_lists_all_subs():
    from seestar_refine.dss import build_file_list
    from seestar_refine.keeplist import KeepList
    txt = build_file_list(KeepList("M27", ["/a/s1.fit","/a/s2.fit"]))
    assert "s1.fit" in txt and "s2.fit" in txt

def test_run_dss_builds_command_and_locates_master(tmp_path):
    from seestar_refine.dss import run_dss
    calls = {}
    def fake_runner(cmd, **kw):
        calls["cmd"] = cmd
        (tmp_path/"Autosave.tif").write_bytes(b"x")   # simulate DSS output
        class R: returncode=0; stdout="stacked"; stderr=""
        return R()
    r = run_dss(str(tmp_path/"list.txt"), tmp_path, dss_cli="C:/DSS/DeepSkyStackerCL.exe", runner=fake_runner)
    assert r["ok"] and r["master_path"].endswith("Autosave.tif")
    assert any("DeepSkyStackerCL" in str(c) for c in calls["cmd"])

def test_run_dss_nonzero_exit_is_error(tmp_path):
    from seestar_refine.dss import run_dss
    def fake(cmd, **kw):
        class R: returncode=1; stdout=""; stderr="boom"
        return R()
    r = run_dss(str(tmp_path/"l.txt"), tmp_path, dss_cli="x", runner=fake)
    assert r["ok"] is False and "boom" in (r["log"] or "")
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** `build_file_list` writes the DSS light-frame list (document the format; `# FORMAT-DEPENDENT` note — verify against the installed DSS during real use). `run_dss` builds `[dss_cli, "/L", file_list_path, ...]`, calls `runner` (default `subprocess.run` with `capture_output=True, text=True, timeout=timeout_s`), on returncode 0 locates the newest `Autosave.*`/master under `output_dir` (or DSS's default), returns the dict; nonzero/timeout → `{"ok":False, "log":..., "error":...}`. `stack()` writes the file list to `output_dir`, calls `run_dss`, computes basic stats (astropy open → min/median/max/shape) and returns a `StackResult`. Never raise.
- [ ] **Step 4:** `server.py` — add `RefineController.stack_keep_list(target, engine="auto")` (load keep-list from the newest `qa_*` report or an explicit path under `data_dir`; `engine` `dss`/`wbpp`/`auto`→dss for now; call `dss.stack`; provenance-log) + a `@mcp.tool()` wrapper (honest docstring, SIDE EFFECT: long external process). Test: `stack_keep_list` with a mocked `dss.stack`/`run_dss` returns `{"ok":True, "engine":"dss", ...}`; DSS-not-configured → `{"ok":False}`.
- [ ] **Step 5: run → PASS** (whole suite) + ruff.
- [ ] **Step 6: commit** `feat(refine): DSS stacking + stack_keep_list tool`.

---

### Task 3: Preview stretch (`preview.py`) + `stretch_master` tool

**Files:** Create `src/seestar_refine/preview.py`; Modify `server.py`; Test `tests/test_refine_preview.py`.

**Interfaces — Produces:** `auto_stretch(data: np.ndarray, *, black_point_sigma=2.8, midtone=0.25) -> np.ndarray` (uint8, an MTF/asinh screen-transfer stretch, pure); `make_preview(master_path, out_png, *, params=None) -> dict` (`{ok, preview_path, stats}`; loads FITS/TIFF via astropy/Pillow, stretches, writes PNG).

- [ ] **Step 1: failing tests**
```python
# tests/test_refine_preview.py
import numpy as np
def test_auto_stretch_maps_to_uint8():
    from seestar_refine.preview import auto_stretch
    data = np.random.default_rng(0).normal(1000, 50, (64,64)).astype("float32")
    out = auto_stretch(data)
    assert out.dtype==np.uint8 and out.shape==(64,64) and out.max()>out.min()

def test_make_preview_writes_png(tmp_path):
    from astropy.io import fits; import numpy as np
    d = np.random.default_rng(0).normal(1000,50,(64,64)).astype("float32")
    d[30:34,30:34]+=5000  # a star
    fp = tmp_path/"master.fit"; fits.writeto(fp, d)
    from seestar_refine.preview import make_preview
    r = make_preview(fp, tmp_path/"prev.png")
    assert r["ok"] and (tmp_path/"prev.png").exists() and "median" in r["stats"]
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** `auto_stretch` (sigma-clipped black point + midtone transfer function → 8-bit; handle mono + 3-channel; NaN-safe) and `make_preview` (load via astropy for FITS / Pillow for TIFF, stretch, save PNG via Pillow, return stats). Never raise → `{"ok":False,"error"}` on bad input.
- [ ] **Step 4:** `server.py` — `stretch_master(master_path, params?)` controller method + tool; provenance-log. `stack_keep_list(dss)` also auto-produces a preview (call `make_preview` on the master) and includes `preview_path` in the result.
- [ ] **Step 5: run → PASS** (whole suite) + ruff.
- [ ] **Step 6: commit** `feat(refine): auto-stretch preview + stretch_master tool`.

---

### Task 4: PixInsight path — WBPP runner + handoff + tools

**Files:** Create `src/seestar_refine/wbpp.py`, `src/seestar_refine/handoff.py`; Modify `server.py`; Test `tests/test_refine_wbpp.py`, `_handoff.py`, `_refine_server.py`.

**Interfaces — Produces:**
- `wbpp.py`: `build_wbpp_command(keep_list, output_dir, *, pixinsight_exe, runner_script) -> list[str]`; `run_wbpp(keep_list, settings, *, runner=subprocess.run) -> StackResult` — invokes PixInsight to run a bundled PJSR WBPP runner over the keep-list; requires `pixinsight_exe`; unavailable → structured error. Ship the bundled `pjsr/wbpp_runner.js` as a data file (documented `# PIXINSIGHT-DEPENDENT`, validated on the user's install). Subprocess isolated + injectable.
- `handoff.py`: `write_pixinsight_config(master_path, target, output_dir) -> dict` (`{ok, config_path, config}`) writing the JSON `pixinsight-mcp` expects (target, absolute channel paths — a single OSC master goes to the RGB/L slot per its schema, output dir, defaults); `to_xisf(master_path, out_path) -> dict` — convert via the optional `xisf` package if importable, else `{"ok":False,"error":"xisf not installed — pass the FITS master to WBPP/PixInsight"}` (documented fallback).

- [ ] **Step 1: failing tests** (mock subprocess; xisf test guarded by import)
```python
# tests/test_refine_handoff.py
def test_write_pixinsight_config(tmp_path):
    from seestar_refine.handoff import write_pixinsight_config
    m = tmp_path/"M27_master.fit"; m.write_bytes(b"x")
    r = write_pixinsight_config(m, "M27", tmp_path)
    import json; cfg = json.loads((tmp_path/ r["config"]["_name"]).read_text()) if False else r["config"]
    assert r["ok"] and cfg["target"]=="M27" and str(m) in json.dumps(cfg)
# tests/test_refine_wbpp.py
def test_run_wbpp_requires_pixinsight(tmp_path):
    from seestar_refine.wbpp import run_wbpp
    from seestar_refine.keeplist import KeepList
    from seestar_refine.config import RefineSettings
    r = run_wbpp(KeepList("M27",["/a/s1.fit"]), RefineSettings(_env_file=None, pixinsight_exe=""))
    assert r.ok is False and "pixinsight" in (r.error or "").lower()
def test_run_wbpp_builds_command(tmp_path):
    from seestar_refine.wbpp import run_wbpp
    from seestar_refine.keeplist import KeepList
    from seestar_refine.config import RefineSettings
    exe = tmp_path/"PixInsight.exe"; exe.write_bytes(b"x")
    calls={}
    def fake(cmd, **kw):
        calls["cmd"]=cmd; (tmp_path/"masterLight.xisf").write_bytes(b"x")
        class R: returncode=0; stdout="ok"; stderr=""
        return R()
    r = run_wbpp(KeepList("M27",["/a/s1.fit"]), RefineSettings(_env_file=None, pixinsight_exe=str(exe), output_dir=str(tmp_path)), runner=fake)
    assert any("PixInsight" in str(c) for c in calls["cmd"])
```
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** `wbpp.build_wbpp_command` → `[pixinsight_exe, "-r=<wbpp_runner.js>", "--automation-mode", ...]` passing the keep-list + output via a params file (document the runner contract); `run_wbpp` validates `pixinsight_exe`, calls the runner, locates the master XISF, returns a `StackResult(engine="wbpp")`; unavailable/nonzero → structured error. `handoff.write_pixinsight_config` writes `<output>/<target>_pixinsight.json` matching `pixinsight-mcp`'s config schema (target, absolute paths, output dir); `to_xisf` uses `xisf` if importable else the documented fallback. Bundle `pjsr/wbpp_runner.js` (a minimal WBPP-over-a-file-list script, marked PIXINSIGHT-DEPENDENT).
- [ ] **Step 4:** `server.py` — `stack_keep_list(engine="wbpp")` routes to `wbpp.run_wbpp`; add `prepare_pixinsight_handoff(master_path, target)` + `list_masters()` tools; provenance-log. Test: `stack_keep_list("M27", engine="wbpp")` with PixInsight unconfigured → `{"ok":False}`; `prepare_pixinsight_handoff` writes a config; tools registered.
- [ ] **Step 5: run → PASS** (whole suite) + ruff.
- [ ] **Step 6: commit** `feat(refine): PixInsight WBPP runner + handoff tools`.

---

### Task 5: `image-refinement` skill + docs

**Files:** Create `skills/image-refinement/SKILL.md`; Modify `README.md` (register line + tools), `SECURITY.md` (external-app note).

- [ ] **Step 1** `skills/image-refinement/SKILL.md` (frontmatter `name: image-refinement`, description covering "stack my subs", "process the session", "refine the images", "make a final image"): Phase 0 confirm target + keep-list (`qa_session_report`), `check_backends`. Phase 1 stack (`stack_keep_list` — DSS default/always; WBPP only if PixInsight available AND the user wants the full finish; state engine + params). Phase 2 finish: DSS → `stretch_master` → present preview + master path; PixInsight (if available) → `prepare_pixinsight_handoff` → drive the EXTERNAL `pixinsight-mcp` tools/runner with the config → present the finished XISF+JPG; if `pixinsight-mcp` unreachable, fall back to DSS preview. Hard rules: keep-list only; state backend+params; the DSS/PixInsight choice is the user's (don't silently launch a long PixInsight run); log every external invocation.
- [ ] **Step 2** `README.md`: add a "Image refinement (`seestar-refine`)" section — the separate service, its register line (`claude mcp add seestar-refine -- uv --directory <repo> run python -m seestar_refine.server`), the `SEESTAR_REFINE_*` config (DSS/PixInsight paths, dirs), the tools, and that PixInsight is optional via the external `pixinsight-mcp`.
- [ ] **Step 3** `SECURITY.md`: note refinement shells out to configured desktop apps only (never an arbitrary path from tool args), provenance-logs each run, adds no network host; the external `pixinsight-mcp` is the user's own local install.
- [ ] **Step 4** `uv run pytest -q` (green) + `uv run ruff check src tests`; skill frontmatter valid.
- [ ] **Step 5: commit** `feat(refine): image-refinement skill + docs`.

---

## Self-Review

**Spec coverage:** foundation/config/keeplist/backends (T1) ✓, DSS stacking + tool (T2) ✓, preview stretch + tool (T3) ✓, PixInsight WBPP runner + handoff + tools (T4) ✓, image-refinement skill + docs (T5) ✓, keep-list-only + provenance + never-raise (Global + each task) ✓, mocked subprocesses / no real apps in tests ✓.

**Placeholder scan:** none (formats flagged `# FORMAT-DEPENDENT`/`# PIXINSIGHT-DEPENDENT` for on-machine validation, with tests on the buildable logic).

**Type consistency:** `KeepList`, `Backends`, `StackResult` used consistently T1↔T4; `stack_keep_list(engine)` routes dss(T2)/wbpp(T4); `make_preview`/`auto_stretch` T3; handoff config schema T4↔skill T5.
