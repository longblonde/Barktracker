# BarkTracker: notes for Claude

A Raspberry Pi bark logger: FastAPI + vanilla JS dashboard, YAMNet (TFLite) detection, per-dog
identification, ordinance-based complaint reports. Repo: github.com/longblonde/Barktracker.

## Where things stand

- `main` is v0.14.0. The `refactor` branch is v0.15.0, a dev branch the owner tests on the Pi before
  anything merges. Don't merge to main unless asked.
- Open question from the owner's list: `docs/REFACTOR-QUESTIONS.md` (#6, label a whole discovered dog
  group at once?).
- The Pi: Raspberry Pi 4 (4 GB), 64-bit Raspberry Pi OS, at 192.168.1.185, service `barktracker`
  (systemd), dashboard on port 8080, repo at `~/barktracker`. Microphone: Google AIY Voice HAT v1
  (two MEMS mics 7.1 cm apart, stereo at 48 kHz, `dtoverlay=googlevoicehat-soundcard`), direction on.
  The HAT is quiet: background about -80 dBFS, so "ignore quieter than" (-55 dBFS) may need lowering.

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
