# Deploying the hosted demo (Render)

The app ships as a container. Anyone with the link gets a working tracker running on **real Claude**,
paid from a server-held key, with a hard spend cap that quietly degrades to the offline engine once
the budget is spent. No visitor ever sees or sets a key.

## What's in the box

| File | Role |
|---|---|
| `Dockerfile` | Production image: Python 3.11 + `tesseract-ocr` (OCR), installs `.[parse,llm,report,web]`, runs uvicorn on `$PORT`. |
| `render.yaml` | Render blueprint: one web service, health check `/healthz`, the three env vars below. |
| `.dockerignore` | Keeps `data/`, `.env`, `.git/`, caches out of the image. |

## Hosted-mode behavior (how the budget is protected)

Set by the image / blueprint — no code change needed to run locally vs. hosted:

- **`PBC_HOSTED=1`** — turns on hosted mode. The in-browser "paste your key" box is **hidden**, and
  `POST /api/key` returns **403**, so a public visitor can't replace or clear the server's key.
- **`PBC_MAX_SPEND_USD=45`** — cumulative spend ceiling across every visitor. Each tracker run and
  live-test batch adds its measured cost to a process counter; once it crosses the cap, the app
  **automatically falls back to the offline mock** (still 13/13 correct, just $0). Set it a few
  dollars under the card balance for headroom.
- **`ANTHROPIC_API_KEY`** — the demo key, set **in the Render dashboard only** (never in git).

The cap is a process counter, so it resets when the container cold-starts. On Render Free that's fine
for a small demo audience; the real ceiling is the `$5` per-run budget baked into the engine plus the
card balance. For a hard, durable cap, use Anthropic's own **usage limit on the API key** as the
backstop (recommended below).

## One-time setup

1. **Rotate the key first.** If any key was ever pasted into a chat or file, generate a fresh one in
   the Anthropic console and set a **monthly usage limit** on it (e.g. `$50`) — this is the real
   safety net, independent of the app.
2. **Create the service.** In Render: **New → Blueprint**, connect this GitHub repo, pick the branch.
   Render reads `render.yaml` and provisions the `pbc-tracker` web service.
3. **Set the secret.** In the service's **Environment** tab, set `ANTHROPIC_API_KEY` to the rotated
   key. (`PBC_HOSTED` and `PBC_MAX_SPEND_USD` come from the blueprint.)
4. **Deploy.** Render builds the Dockerfile and gives you a public URL. First build ~3–5 min; first
   request after an idle cold start (Free plan) can take ~30 s while the container wakes.

## Verify after deploy

- `GET /healthz` → `{"ok": true}` (Render uses this as the health check).
- Open the URL → **📥 New audit** screen. The 🔑 key box is **absent** (hosted mode).
- Drop a `.zip` bundle or `.mbox` → the tracker renders, engine pill reads **Claude (live)**.
- 🧪 **Live tests** tab → runs fresh random traps through the real agent; catches all of them.
- Spend counter is visible via `GET /api/status` → `{"spend": {"usd": …, "cap": 45}}`.

## Notes

- **No data ships.** `data/` is git-ignored and dockerignored; visitors run their own uploads. Uploads
  live in a temp dir and are cleaned on the next upload and at shutdown.
- **Upload cap:** 80 MB per file (`_MAX_UPLOAD_BYTES` → HTTP 413 above it).
- **Any container host works** (Fly, Railway, Cloud Run) — same three env vars, same `$PORT` binding.
  Locally: `docker build -t pbc . && docker run -e ANTHROPIC_API_KEY=sk-… -p 8000:8000 pbc`.
- **Localhost dev is unchanged:** without `PBC_HOSTED`, the key box and `/api/key` work as before, and
  with no key at all everything runs on the offline mock.
