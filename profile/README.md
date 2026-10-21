# Your resume goes here

Put the resume you want uploaded in this folder, at the path your regions name in
`config/rules.toml`. The example config uses `profile/resume.pdf` for every region, so the
simplest setup is one file:

```
profile/resume.pdf
```

Use a PDF exported from your editor, not a scan, so the job sites' resume parsers can read the text.

## More than one version

If you want a different resume for some cities, add a second file and point those regions at it:

```toml
[regions.new_york]
resume = "profile/resume-us.pdf"

[regions.berlin]
resume = "profile/resume-eu.pdf"
```

## Lock it

Once the files are in place, run this in your own terminal:

```bash
python3 scripts/lock_resumes.py
```

It writes a SHA-256 fingerprint of each resume to `config/resume.lock` and makes the files
read-only. From then on `tracker.py` and the upload hook refuse any resume whose bytes don't
match its fingerprint, so the agent can only upload the exact file you put here. The agent is
denied permission to run the lock script or write to this folder (see `.claude/settings.json`).

## Replacing a resume

```bash
python3 scripts/lock_resumes.py --unlock
```

Swap the PDF, keeping the same file name, then run `python3 scripts/lock_resumes.py` again.

Everything in this folder except this README is gitignored.
