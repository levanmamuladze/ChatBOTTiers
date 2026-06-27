# Chatbot demos — showroom

Two self-contained demo bots we show prospects. Everything runs on dummy data,
no real client is involved, and there are no credentials baked in anywhere.

- **Tier 1 — Ταβέρνα «Το Κύμα».** A floating chat bubble on a taverna page that
  answers freely (hours, menu, reservations, parking, allergens) from one JSON
  file. → `/restaurant`
- **Tier 2 — Οδοντιατρείο «Γαλήνη».** A button-driven booking wizard for a dental
  clinic: service → day → time → name → phone → confirm. Slots are generated from
  the published hours so they never go stale; bookings are in-memory and clearly
  marked DEMO. It also answers free-text questions. → `/clinic`

The gallery that ties them together lives at `/demos`.

Both bots speak whatever language the visitor's last message was in (content is
authored in Greek). The same Groq-backed brain serves both — each "front desk"
just builds its own system prompt from its own data file in `api/`.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # add a GROQ_API_KEY (optional — see below)
uvicorn api.index:app --reload
```

Then open http://127.0.0.1:8000/demos.

Without a key the pages and the whole booking wizard still work; only the
free-text chat falls back to a "call us instead" message, with each bot quoting
its own phone number.

## Layout

| Path                | What it is                                            |
|---------------------|-------------------------------------------------------|
| `api/index.py`      | FastAPI app: `/api/chat`, `/api/slots`, `/api/book`   |
| `api/restaurant.json` | Taverna content — the single source of truth        |
| `api/clinic.json`   | Clinic services, hours, FAQ                            |
| `restaurant.html`   | Taverna landing page + chat bubble                    |
| `clinic.html`       | Clinic landing page + booking wizard                  |
| `demos.html`        | The showroom                                          |
| `vercel.json`       | Rewrites `/api/*` to the function and the clean paths  |

## Deploy (Vercel)

`api/index.py` is the serverless function; the HTML files are served as static
assets. `vercel.json` wires `/demos`, `/restaurant`, `/clinic` to their pages
and everything under `/api/` to the function. Set `GROQ_API_KEY` (and optionally
`GROQ_MODEL`) in the project's environment variables.
