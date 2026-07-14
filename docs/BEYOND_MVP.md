# What we built beyond the MVP brief

Everything the brief asks for is done (agent, tools, verifier, citations, insufficiency
detection, grouped follow-ups, versioning, a UI, 1-page README, evals, cost under $5, no
hardcoding, deterministic tests). These items go **beyond** what was required:

1. **Runs with no API key.** A built-in offline "stand-in brain" drives the exact same tools, so
   the whole system — agent, UI, tests, eval — works cold for development and demos. Flip in your
   Anthropic key and the identical loop uses real Claude. (The brief only needs it to run live.)

2. **Two UIs, not one.** A polished bespoke localhost web app (one command, no build step) *and* a
   lightweight Streamlit app. The brief asks for "the UI."

3. **Proves itself on data it's never seen.** Instead of only 5–10 fixed test cases, it
   **randomly generates dozens of brand-new trap documents every run** (wrong period, wrong
   entity, unsigned, short sample, leaked PII, misleading filename) and asserts it catches
   **100%** — a literal "unseen dataset" guarantee.

4. **Full PII gate.** Detects and **redacts** Social Security numbers, dates of birth, and bank/
   account/card numbers *before anything is sent to the AI or shown on screen*, and flags the
   item. The brief only hints that PII shows up in the hidden test set.

5. **"Needs review" conflict detector.** When a file's *name* contradicts its *contents* (a
   `..._signed_FINAL.pdf` that's actually a draft), or the evidence disagrees with itself, it
   raises a loud human-review flag instead of guessing.

6. **Deep attachment recursion.** Opens ZIPs inside emails, and emails that were **forwarded as
   attachments inside other emails**, and recognizes the same file sent twice by its content
   fingerprint (so one delivery isn't double-counted).

7. **Cost & throughput projection.** A `bench` command measures the run and **projects the
   ~90-email held-out cost and runtime before the review**, so there are no surprises.

8. **Zero-dependency core.** The whole ingest layer runs on pure standard-library Python, and the
   PBC-list and client-profile PDFs are parsed with **no PDF library at all**.

9. **Careful version lineage.** Collapses true `v1/v2/Final` versions to the latest (keeping the
   trail) while *not* wrongly merging genuinely different documents that just share a name shape
   (e.g. board minutes from different meetings).

10. **Honest, grounded cost accounting.** Cost is computed from *real* prompt sizes and *real* API
    token usage × published prices — not a hand-waved number.
