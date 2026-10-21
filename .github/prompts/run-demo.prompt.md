---
mode: agent
description: Set up and run the HEDIS POC demo page (gap prioritization + AI evidence)
---

Help me run the HEDIS POC demo page on this machine. All data in this project is synthetic.

1. Check that a `.env` file exists in the repo root with a `DATABASE_URL` line and an `OPENROUTER_API_KEY` line.
   Only check that the lines exist. Never print, read out or ask me for the values.
   - If `.env` is missing, tell me to run
     `powershell -ExecutionPolicy Bypass -File scripts/db/create_local_db.ps1 -DbName HEDISPOCDB` and stop.
   - If `OPENROUTER_API_KEY` is missing, tell me to get a free key at https://openrouter.ai/keys and add it
     to `.env` myself, then continue.
2. Ask me if this is the first run (or if I want to reset the test data).
   - Yes: run `powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Setup` in the terminal.
   - No: run `powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1` in the terminal.
3. When the terminal shows the page has started, tell me to open http://localhost:8771.
4. If a step fails, show me the error line and explain in plain words what to fix. Do not change any code,
   the database schema or `.env` to work around it.

To change the AI model, I add or edit `EXTRACTION_MODEL=<openrouter model name>` in `.env` and run step 2 again
without -Setup.
