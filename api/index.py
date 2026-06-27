"""
Demo backend for the chatbot showroom.

Two bots share one Groq-backed brain but each lives behind its own "front desk":
the taverna desk answers questions freely, the clinic desk also runs a booking
wizard. Every profile is built from its own JSON file in this folder, so editing
content never means touching code.

Runs locally with:  uvicorn api.index:app --reload
On Vercel it's the serverless function at /api/index (see vercel.json).
"""

import os
import json
import time
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError

HERE = Path(__file__).resolve().parent
SITE = HERE.parent  # the landing pages sit at the repo root next to /api

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

# The OpenAI SDK pointed at Groq. Built lazily — a missing key shouldn't stop the
# app from booting, it should just make every chat fall back gracefully.
_groq = None


def groq():
    global _groq
    if _groq is None:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        _groq = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=key)
    return _groq


def load_profile(filename):
    with open(HERE / filename, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Front desks: each one knows how to turn its JSON into a system prompt and
# carries its own phone number for the "sorry, something broke" message.
# ---------------------------------------------------------------------------

def taverna_brief(taverna):
    h = taverna["hours"]
    week = "\n".join(f"  • {day}: {hrs}" for day, hrs in h["schedule"].items())
    plates = "\n".join(f"  – {x}" for x in taverna["specialties"])
    knowledge = "\n\n".join(
        f"[{row['tag']}] {row['q']}\n{row['a']}" for row in taverna["faq"]
    )
    c = taverna["contact"]
    return f"""Είσαι ο/η υπεύθυνος/η υποδοχής της ταβέρνας «{taverna['name']}».
Μιλάς ζεστά, σαν άνθρωπος του μαγαζιού, όχι σαν εγχειρίδιο. Σύντομες απαντήσεις,
2-4 προτάσεις, με ένα σταγονίδιο νησιώτικης φιλοξενίας — όχι θεατρινισμούς.

Η ταβέρνα με δυο λόγια: {taverna['pitch']}

Ωράριο:
{week}
Σημείωση: {h['note']}

Πιάτα που μας ξεχωρίζουν:
{plates}

Επικοινωνία: τηλέφωνο {c['phone']}, WhatsApp {c['whatsapp']}, {c['address']}.

Τι ξέρεις σίγουρα (απάντα από εδώ, μη φαντάζεσαι τιμές ή πιάτα που δεν αναφέρονται):
{knowledge}

Κανόνες:
- Αν δεν ξέρεις κάτι (π.χ. ακριβής τιμή ψαριού σήμερα), πες το ειλικρινά και
  παρέπεμψε στο τηλέφωνο — μην εφευρίσκεις νούμερα.
- Απάντα ΠΑΝΤΑ στη γλώσσα του τελευταίου μηνύματος του πελάτη. Αν σου γράψει
  αγγλικά, απάντα αγγλικά· αν γράψει ελληνικά, ελληνικά.
- Μη χρησιμοποιείς ποτέ άλλο αλφάβητο πέρα από ελληνικά/λατινικά."""


def clinic_brief(clinic):
    rows = "\n".join(
        f"  – {s['name']}: {s['minutes']}′, {s['price']}€ — {s['blurb']}"
        for s in clinic["services"]
    )
    win = []
    for day, blocks in clinic["hours"]["open"].items():
        if not blocks:
            win.append(f"  • {day}: κλειστά")
        else:
            spans = ", ".join(f"{a}-{b}" for a, b in blocks)
            win.append(f"  • {day}: {spans}")
    week = "\n".join(win)
    knowledge = "\n\n".join(
        f"[{row['tag']}] {row['q']}\n{row['a']}" for row in clinic["faq"]
    )
    c = clinic["contact"]
    return f"""Είσαι η ψηφιακή ρεσεψιόν του οδοντιατρείου «{clinic['name']}» ({clinic['dentist']}).
Τόνος καθησυχαστικός και επαγγελματικός — οι περισσότεροι που γράφουν είναι λίγο
αγχωμένοι. Σύντομα και καθαρά.

Το ιατρείο: {clinic['pitch']}

Υπηρεσίες και ενδεικτικές τιμές:
{rows}

Ωράριο (με ραντεβού):
{week}
Σημείωση: {clinic['hours']['note']}

Επικοινωνία: {c['phone']}, {c['address']}. {c['afterHours']}

Συχνές ερωτήσεις:
{knowledge}

Κανόνες:
- ΔΕΝ δίνεις ιατρική διάγνωση. Για πόνο ή κάτι επείγον, καθησύχασε και πες να
  κλείσουν ραντεβού ή να τηλεφωνήσουν.
- Αν κάποιος θέλει να κλείσει ραντεβού, πες του να πατήσει το κουμπί «Κλείσε
  ραντεβού» — εσύ απαντάς σε ερωτήσεις, η κράτηση γίνεται από εκεί.
- Μη φαντάζεσαι τιμές ή υπηρεσίες εκτός λίστας.
- Απάντα ΠΑΝΤΑ στη γλώσσα του τελευταίου μηνύματος. Μόνο ελληνικά/λατινικά."""


FRONT_DESKS = {
    "restaurant": {
        "data": load_profile("restaurant.json"),
        "brief": taverna_brief,
    },
    "clinic": {
        "data": load_profile("clinic.json"),
        "brief": clinic_brief,
    },
}


def desk(bot):
    return FRONT_DESKS.get(bot)


# ---------------------------------------------------------------------------
# Safety net for model output.
# ---------------------------------------------------------------------------

def keep_greek_and_latin(text):
    """gpt-oss occasionally slips a stray Cyrillic or CJK glyph into Greek text.
    Whitelist the scripts we actually want and drop the rest, keeping emoji and
    the euro sign which the bots genuinely use."""
    out = []
    for ch in text:
        cp = ord(ch)
        ok = (
            ch in "\n\r\t " or
            cp < 0x250 or                       # ASCII + Latin-1 + Latin Extended-A
            0x370 <= cp <= 0x3FF or             # Greek & Coptic
            0x1F00 <= cp <= 0x1FFF or           # Greek Extended (accented)
            0x2010 <= cp <= 0x206F or           # dashes, quotes, ellipsis…
            cp == 0x20AC or                     # €
            0x2190 <= cp <= 0x21FF or           # arrows
            0x2600 <= cp <= 0x27BF or           # dingbats / misc symbols
            cp >= 0x1F300                        # emoji planes
        )
        if ok:
            out.append(ch)
    cleaned = "".join(out)
    # collapse the holes a dropped glyph leaves behind
    return " ".join(cleaned.split()) if "\n" not in cleaned else cleaned


def recent_turns(messages, keep=10):
    """Conversations can run long; only the tail matters to gpt-oss and it keeps
    us well under the context window. Always preserve the latest user line."""
    trimmed = [m for m in messages if m.get("role") in ("user", "assistant")]
    return trimmed[-keep:]


def ask_groq(system_prompt, messages):
    """One round-trip with a couple of silent retries — Groq throws the odd 429
    or dropped connection under load and a half-second wait usually clears it.
    Auth and bad-request errors are not retried; they bubble up to the caller."""
    payload = [{"role": "system", "content": system_prompt}] + messages
    wait = 0.4
    for attempt in range(3):
        try:
            reply = groq().chat.completions.create(
                model=GROQ_MODEL,
                messages=payload,
                temperature=0.6,
                max_tokens=600,
            )
            return reply.choices[0].message.content or ""
        except (APIConnectionError, APITimeoutError, RateLimitError):
            if attempt == 2:
                raise
            time.sleep(wait)
            wait *= 2
        except APIStatusError as boom:
            # 5xx is worth one more shot; 4xx (incl. 401) is not.
            if boom.status_code >= 500 and attempt < 2:
                time.sleep(wait)
                wait *= 2
                continue
            raise


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Demo chatbots — showroom", docs_url=None, redoc_url=None)


class Ask(BaseModel):
    bot: str
    messages: list[dict] = []


@app.post("/api/chat")
async def chat(turn: Ask):
    front = desk(turn.bot)
    if front is None:
        return JSONResponse({"error": "unknown bot"}, status_code=400)

    user_lines = recent_turns(turn.messages)
    if not user_lines or not (user_lines[-1].get("content") or "").strip():
        return JSONResponse({"reply": "Πες μου τι θα ήθελες να μάθεις 🙂"})

    phone = front["data"]["contact"]["phone"]
    try:
        raw = ask_groq(front["brief"](front["data"]), user_lines)
        return {"reply": keep_greek_and_latin(raw).strip()}
    except RuntimeError:
        # no key configured — happens on a fresh clone before .env is filled in
        return JSONResponse(
            {"reply": f"Η υπηρεσία δεν είναι ρυθμισμένη ακόμη. Καλέστε μας στο {phone}."},
            status_code=200,
        )
    except APIStatusError as boom:
        if boom.status_code == 401:
            msg = f"Πρόβλημα με το κλειδί της υπηρεσίας. Προσωρινά καλέστε στο {phone}."
        elif boom.status_code == 429:
            msg = "Έχουμε λίγη κίνηση αυτή τη στιγμή — δοκιμάστε ξανά σε δευτερόλεπτα."
        else:
            msg = f"Κάτι στράβωσε για λίγο. Δοκιμάστε ξανά ή καλέστε στο {phone}."
        return JSONResponse({"reply": msg}, status_code=200)
    except (APIConnectionError, APITimeoutError):
        # the three quick retries already failed — Groq is unreachable, not the guest
        return JSONResponse(
            {"reply": "Αργεί η απάντηση παραπάνω απ’ το συνηθισμένο. Ξαναδοκιμάστε σε λίγο."},
            status_code=200,
        )
    except Exception:
        return JSONResponse(
            {"reply": f"Με συγχωρείτε, κόλλησε κάτι. Πάρτε μας τηλέφωνο στο {phone} 🙏"},
            status_code=200,
        )


# ---------------------------------------------------------------------------
# Clinic booking wizard — slots are generated from the published hours so they
# never go stale, with date-seeded "taken" times for a believable demo.
# Nothing is persisted beyond this process: it's a demo, not a real diary.
# ---------------------------------------------------------------------------

GR_WEEKDAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
SLOT_STEP = 30  # minutes between appointment starts
LEDGER = []     # in-memory "bookings" for the life of the process


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _clock(total):
    return f"{total // 60:02d}:{total % 60:02d}"


def service_minutes(code, clinic):
    for s in clinic["services"]:
        if s["code"] == code:
            return s["minutes"]
    return 30


def carve_day(day, need_minutes, clinic):
    """Slot starts available on `day` for an appointment of `need_minutes`,
    minus the ones a seeded RNG decides are already booked. Past times are
    dropped if the day is today."""
    weekday = GR_WEEKDAYS[day.weekday()]
    blocks = clinic["hours"]["open"].get(weekday, [])
    if not blocks:
        return []

    # Stable per-day randomness so a refresh shows the same "taken" picture.
    dice = random.Random(int(day.strftime("%Y%m%d")))
    now_min = datetime.now().hour * 60 + datetime.now().minute
    is_today = day == date.today()

    slots = []
    for start, end in blocks:
        t = _minutes(start)
        last_start = _minutes(end) - need_minutes
        while t <= last_start:
            label = _clock(t)
            taken = dice.random() < 0.38
            passed = is_today and t <= now_min + 30  # need a little lead time
            if not taken and not passed:
                slots.append(label)
            t += SLOT_STEP
    return slots


def open_days(need_minutes, clinic, horizon=14):
    out = []
    cursor = date.today()
    for _ in range(horizon):
        times = carve_day(cursor, need_minutes, clinic)
        if times:
            out.append({
                "iso": cursor.isoformat(),
                "weekday": GR_WEEKDAYS[cursor.weekday()],
                "pretty": f"{GR_WEEKDAYS[cursor.weekday()]} {cursor.day}/{cursor.month}",
                "times": times,
            })
        cursor += timedelta(days=1)
        if len(out) >= 6:  # six bookable days is plenty for the picker
            break
    return out


@app.get("/api/slots")
async def slots(service: str = "elegxos"):
    clinic = FRONT_DESKS["clinic"]["data"]
    need = service_minutes(service, clinic)
    chosen = next((s for s in clinic["services"] if s["code"] == service), None)
    return {
        "service": chosen,
        "catalog": clinic["services"],   # so the wizard can build its first menu
        "days": open_days(need, clinic),
    }


class Booking(BaseModel):
    service: str
    day: str      # iso date
    time: str     # HH:MM
    name: str = ""
    phone: str = ""


def looks_like_phone(raw):
    digits = [c for c in raw if c.isdigit()]
    # Greek numbers are 10 digits; allow a leading +30 / 0030.
    return 10 <= len(digits) <= 13


@app.post("/api/book")
async def book(req: Booking):
    clinic = FRONT_DESKS["clinic"]["data"]
    desk_phone = clinic["contact"]["phone"]

    chosen = next((s for s in clinic["services"] if s["code"] == req.service), None)
    if chosen is None:
        return JSONResponse({"ok": False, "why": "Διάλεξε μια υπηρεσία από τη λίστα."}, status_code=400)

    if not req.name.strip():
        return JSONResponse({"ok": False, "why": "Γράψε μου ένα όνομα για το ραντεβού."}, status_code=400)

    if not looks_like_phone(req.phone):
        return JSONResponse({"ok": False, "why": "Το τηλέφωνο δε μοιάζει σωστό — βάλε 10 ψηφία."}, status_code=400)

    try:
        when = date.fromisoformat(req.day)
    except ValueError:
        return JSONResponse({"ok": False, "why": "Λάθος ημερομηνία."}, status_code=400)

    # Re-derive the same day's availability and confirm the slot is still real.
    still_open = carve_day(when, chosen["minutes"], clinic)
    if req.time not in still_open:
        return JSONResponse(
            {"ok": False, "why": "Αυτή η ώρα μόλις πιάστηκε ή πέρασε — διάλεξε άλλη."},
            status_code=409,
        )

    code = "ΓΛΝ-" + "".join(random.choice("0123456789ΑΒΓΔΕΖ") for _ in range(4))
    LEDGER.append({
        "code": code,
        "service": chosen["name"],
        "day": req.day,
        "time": req.time,
        "name": req.name.strip(),
        "phone": req.phone.strip(),
        "stamped": datetime.now().isoformat(timespec="seconds"),
    })

    pretty_day = f"{GR_WEEKDAYS[when.weekday()]} {when.day}/{when.month}"
    return {
        "ok": True,
        "code": code,
        "service": chosen["name"],
        "when": f"{pretty_day}, {req.time}",
        "minutes": chosen["minutes"],
        "price": chosen["price"],
        # Clearly fenced as a demo — no SMS leaves this machine.
        "desk_note": f"📩 Στάλθηκε ειδοποίηση στη ρεσεψιόν στο {desk_phone} — DEMO, δεν έγινε πραγματική κράτηση.",
    }


@app.get("/api/health")
async def health():
    return PlainTextResponse("ok")


# ---------------------------------------------------------------------------
# Local-dev page serving. On Vercel the static HTML is served directly and
# these routes never fire; locally they let me drive the whole thing in a
# browser with a single uvicorn process.
# ---------------------------------------------------------------------------

def _page(name):
    return FileResponse(SITE / name, media_type="text/html")


@app.get("/")
async def home():
    return _page("demos.html")


@app.get("/demos")
async def demos_page():
    return _page("demos.html")


@app.get("/restaurant")
async def restaurant_page():
    return _page("restaurant.html")


@app.get("/clinic")
async def clinic_page():
    return _page("clinic.html")
