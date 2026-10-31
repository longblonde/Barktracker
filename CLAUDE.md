# BarkTracker: notes for Claude

A Raspberry Pi bark logger: FastAPI + vanilla JS dashboard, YAMNet (TFLite) detection, per-dog
identification, ordinance-based complaint reports. Repo: github.com/longblonde/Barktracker.

## Where things stand

- `main` is v0.16.1 (v0.15.0 was the `refactor` branch; v0.16.0 `theme-liveband`; v0.16.1 sped up Dogs/dashboard on a long direction log; all 2026-10-30). `main` gets one squashed commit per
  release, titled `vX.Y.Z: ...`; work happens on a dev branch the owner tests on the Pi first (they
  call it "the test branch"). Don't merge to main unless asked.
- The owner's questions in `docs/REFACTOR-QUESTIONS.md` are all answered (#6, labelling a whole
  discovered group, done 2026-10-30).
- The Pi: Raspberry Pi 4 (4 GB), 64-bit Raspberry Pi OS, at 192.168.1.185, service `barktracker`
  (systemd), dashboard on port 8080, repo at `~/barktracker`. Microphone: Google AIY Voice HAT v1
  (two MEMS mics 7.1 cm apart, stereo at 48 kHz, `dtoverlay=googlevoicehat-soundcard`), direction on.
  The HAT has no hardware gain: set to boost +18 dB, ignore quieter than -62 dBFS (2026-10-30, measured
  with a clap test; see `docs/MICROPHONES.md`). Bark threshold is on trial at 0.15 (default 0.35).

## Layout

- `barktracker/` Python package. Key modules: `detector.py` (live detection), `refine.py` (second pass
  over finished clips), `identify.py` (kNN dog matching, direction priors), `array.py` (direction from
  two mics, live wedge scans), `ordinance.py` (rules, quiet hours incl. weekends, reports), `alerts.py`
  (alert list: clip ends, still barking, daily/weekly summaries), `themes.py` (themes + picture packs),
  `advanced.py` (Settings > Advanced: module constants that can be overridden at runtime),
  `discover.py` (unnamed-dog groups, rhythm), `web.py` (all endpoints), `settings.py` (dashboard
  overrides of config.yaml), `backup.py` (USB backup/restore).
- `barktracker/static/`: `core.js` (helpers, router, live band), one file per page (`dashboard.js`,
  `clips.js`, `report.js`, `dogs.js`, `tuning.js`, `settings.js`), `main.js` starts it. No build step.
- Settings saved from the dashboard live in the database (meta `app_settings`), not config.yaml.

## Working rules (from the owner)

- Run tests before committing: `find . -name __pycache__ -prune -exec rm -rf {} +; python3 -m pytest -q -p no:cacheprovider`
  (on the Pi use the venv: `.venv/bin/python -m pytest ...`).
- Commit as `longblonde <longblonde@users.noreply.github.com>`; end messages with
  `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Never commit the owner's audio, recordings or database, and keep personal data (real names,
  addresses, emails) out of the repo, screenshots and commit messages. Demo data uses placeholders
  (123 Elm St, J. Smith, Jane Doe, City of Anytown).
- Don't break existing features while experimenting; keep the README in step with changes.
- On the Pi, restart after code changes: `sudo systemctl restart barktracker`; logs:
  `journalctl -u barktracker -f`. Careful with `pkill -f` patterns that match your own shell.
- Browser checks: the page is served with `Cache-Control: no-cache` and versioned script URLs, so a
  restart is enough; don't use `window.prompt` (embedded browsers ignore it).
