#!/usr/bin/env python3
"""
Checker prostih terminov za vozniski izpit (eUprava / AVP).

Poklice interni AJAX endpoint strani e-uprava.gov.si za N tednov naprej,
razcleni termine, primerja z zadnjim znanim stanjem in poslje Telegram
obvestilo samo za NOVE termine.
"""

import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# NASTAVITVE  -- tukaj spreminjas, kaj se spremlja
# --------------------------------------------------------------------------

BASE_URL = (
    "https://e-uprava.gov.si/si/javne-evidence/"
    "prosti-termini-zemljevid/content/singleton.html"
)

# Filtri, kot jih stran kodira v URL.
#   type          1 = voznja (prakticni del), 2 = teorija
#   cat           kategorija: 6 = B  (izpusti kljuc za "vse kategorije")
#   izpitniCenter 18 = Kranj      (izpusti za vse centre)
#   lokacija      223 = Kranj, Kolodvorska cesta 5
FILTERS = {
    "lang": "si",
    "type": "1",
    "cat": "6",
    "izpitniCenter": "18",
    "lokacija": "223",
    "is_ajax": "1",
}

WEEKS_AHEAD = int(os.environ.get("WEEKS_AHEAD", "15"))
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# Lepa povezava, ki jo posljemo v obvestilu (odpre stran z istimi filtri).
PUBLIC_URL = "https://e-uprava.gov.si/si/javne-evidence/prosti-termini-zemljevid.html"

HEADERS = {
    "User-Agent": (
        "prosti-termini-checker/1.0 (osebna uporaba; "
        "https://github.com/ - kontakt prek GitHub)"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Language": "sl,en;q=0.8",
}

DATE_RE = re.compile(r"^\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*$")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
NO_RESULTS = "ni rezultatov"


# --------------------------------------------------------------------------
# Pridobivanje in razclenjevanje
# --------------------------------------------------------------------------

def week_starts(n_weeks: int):
    """Ponedeljki (oz. danasnji dan za tekoci teden) za n tednov naprej."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    for i in range(n_weeks):
        yield monday + timedelta(weeks=i)


def fetch_week(session: requests.Session, day: date) -> str:
    params = dict(FILTERS)
    params["calendar_date"] = day.isoformat()
    resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_slots(html: str):
    """Vrne seznam slovarjev: {datum, ura, center, naslov, kategorije, mesta}.

    Struktura strani (september 2026):
      <tr class="js_dogodekBox ...">
        [<td class="noBorderRight" rowspan="N">2. 9. 2026</td>]  <- le prva vrstica dneva
        <td>07:30</td> <td>CELJE ...</td> <td>B, B1</td> <td>1</td> <td/>
      <tr class="js_dicDetails">   <- podrobnosti prejsnje vrstice
        <td>Ljubecna, Cesta v Celje 14 ... Preverjanje znanja voznje</td>
    """
    soup = BeautifulSoup(html, "html.parser")

    if NO_RESULTS in clean(soup.get_text(" ")).lower():
        return []

    rows = soup.select("tr")
    slots = []
    current_date = None

    for row in rows:
        classes = row.get("class") or []

        # Vrstica s podrobnostmi -> pripni k zadnjemu terminu.
        if "js_dicDetails" in classes:
            if slots:
                slots[-1]["naslov"] = clean(row.get_text(" "))[:250]
            continue

        cells = row.find_all("td")
        if not cells:
            continue

        texts = [clean(c.get_text(" ")) for c in cells]

        # Prva celica je lahko datum dneva (rowspan / class noBorderRight).
        if texts and DATE_RE.match(texts[0]):
            d, mth, y = (int(x) for x in DATE_RE.match(texts[0]).groups())
            current_date = f"{y:04d}-{mth:02d}-{d:02d}"
            texts = texts[1:]

        if not texts or not TIME_RE.fullmatch(texts[0]):
            continue

        ura = texts[0]
        center = texts[1] if len(texts) > 1 else ""
        # odstrani prazni "(cez priblizno vec kot )" repek, ki ga stran vedno izpise
        center = re.sub(r"\(\s*(?:cez|čez)?\s*(?:pribli[zž]no)?\s*(?:ve[cč] kot)?\s*\)", "", center)
        center = clean(center)
        kategorije = texts[2] if len(texts) > 2 else ""
        mesta = texts[3] if len(texts) > 3 and re.fullmatch(r"\d+", texts[3]) else ""

        slots.append(
            {
                "datum": current_date or "?",
                "ura": ura,
                "center": center,
                "naslov": "",
                "kategorije": kategorije,
                "mesta": mesta,
            }
        )

    return slots


def slot_key(s: dict) -> str:
    raw = f"{s['datum']}|{s['ura']}|{s['center']}|{s['kategorije']}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def collect_all(weeks: int):
    session = requests.Session()
    all_slots = []
    errors = []
    for i, day in enumerate(week_starts(weeks)):
        try:
            html = fetch_week(session, day)
            all_slots.extend(parse_slots(html))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{day.isoformat()}: {exc}")
        if i + 1 < weeks:
            time.sleep(0.6 + random.random() * 0.6)  # vljuden razmik
    return all_slots, errors


# --------------------------------------------------------------------------
# Stanje
# --------------------------------------------------------------------------

def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"znani": {}}


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# Obvescanje
#
# Skripta poslje sporocilo po VSEH kanalih, za katere so nastavljene
# ustrezne spremenljivke okolja. Ce ni nastavljen noben, sporocilo samo
# izpise v log.
# --------------------------------------------------------------------------

def mest(n: str) -> str:
    """Slovensko sklanjanje: 1 mesto, 2 mesti, 3 mesta, 5 mest."""
    try:
        i = int(n)
    except (TypeError, ValueError):
        return f"{n} mest"
    ostanek = i % 100
    if ostanek == 1:
        return f"{i} prosto mesto"
    if ostanek == 2:
        return f"{i} prosti mesti"
    if ostanek in (3, 4):
        return f"{i} prosta mesta"
    return f"{i} prostih mest"


DNI = ["pon", "tor", "sre", "čet", "pet", "sob", "ned"]


def format_message(new_slots: list, html: bool = False) -> str:
    """Sporocilo v navadnem besedilu ali v Telegramovem HTML."""
    if html:
        esc = lambda t: (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        b_, _b = "<b>", "</b>"
        i_, _i = "<i>", "</i>"
    else:
        esc = lambda t: t or ""
        b_ = _b = i_ = _i = ""

    lines = [f"\U0001F697 {b_}Novi prosti termini ({len(new_slots)}){_b}", ""]
    by_date = {}
    for s in new_slots:
        by_date.setdefault(s["datum"], []).append(s)

    for datum in sorted(by_date):
        try:
            d = date.fromisoformat(datum)
            naslov_dneva = f"{DNI[d.weekday()]} {d.day}. {d.month}. {d.year}"
        except ValueError:
            naslov_dneva = datum
        lines.append(f"{b_}{esc(naslov_dneva)}{_b}")
        for s in sorted(by_date[datum], key=lambda x: x["ura"]):
            bits = [f"\u2022 {b_}{esc(s['ura'])}{_b}"]
            if s["center"]:
                bits.append(esc(s["center"]))
            if s["kategorije"]:
                bits.append(f"[{esc(s['kategorije'])}]")
            if s["mesta"]:
                bits.append(f"({mest(s['mesta'])})")
            lines.append(" \u2014 ".join(bits))
            if s["naslov"]:
                lines.append(f"   {i_}{esc(s['naslov'][:140])}{_i}")
        lines.append("")

    if html:
        lines.append(f'<a href="{PUBLIC_URL}">Odpri eUpravo</a>')
    else:
        lines.append(PUBLIC_URL)
    lines.append("Prijava na izpit gre prek upravne enote.")

    msg = "\n".join(lines)
    if len(msg) > 4000:  # Telegram ima omejitev 4096 znakov
        msg = msg[:3900] + "\n\n\u2026 (skrajsano)"
    return msg


def _ids(raw: str) -> list:
    return [c.strip() for c in re.split(r"[,;\s]+", raw or "") if c.strip()]


# --- posamezni kanali ------------------------------------------------------

def posylji_telegram(slots: list) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_ids = _ids(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat_ids:
        return False

    text = format_message(slots, html=True)
    uspehov = 0
    for chat_id in chat_ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=30,
            )
            if r.ok:
                uspehov += 1
                print(f"  -> Telegram {chat_id}: OK")
            else:
                print(f"[napaka] Telegram {r.status_code} za {chat_id}: {r.text[:200]}",
                      file=sys.stderr)
        except requests.RequestException as exc:
            print(f"[napaka] Telegram za {chat_id}: {exc}", file=sys.stderr)
    return uspehov > 0


def posylji_ntfy(slots: list) -> bool:
    """ntfy.sh - push na telefon brez registracije. Nastavis samo NTFY_TOPIC."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    try:
        r = requests.post(
            f"{server}/{topic}",
            data=format_message(slots).encode("utf-8"),
            headers={
                "Title": f"Prosti termini ({len(slots)})".encode("utf-8"),
                "Priority": "high",
                "Tags": "car",
                "Click": PUBLIC_URL,
            },
            timeout=30,
        )
        r.raise_for_status()
        print(f"  -> ntfy {topic}: OK")
        return True
    except requests.RequestException as exc:
        print(f"[napaka] ntfy: {exc}", file=sys.stderr)
        return False


def posylji_webhook(slots: list) -> bool:
    """Discord ali Slack webhook - razlikujeta se samo po imenu polja."""
    url = os.environ.get("WEBHOOK_URL")
    if not url:
        return False

    text = format_message(slots)
    payload = {"content": text} if "discord" in url else {"text": text}
    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        print("  -> webhook: OK")
        return True
    except requests.RequestException as exc:
        print(f"[napaka] webhook: {exc}", file=sys.stderr)
        return False


def posylji_email(slots: list) -> bool:
    """Navadna e-posta prek SMTP."""
    host = os.environ.get("SMTP_HOST")
    prejemniki = _ids(os.environ.get("MAIL_TO", ""))
    if not host or not prejemniki:
        return False

    import smtplib
    import ssl
    from email.message import EmailMessage

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    geslo = os.environ.get("SMTP_PASS", "")
    posiljatelj = os.environ.get("MAIL_FROM") or user or "termini@localhost"

    msg = EmailMessage()
    msg["Subject"] = f"Prosti termini za vozniski izpit ({len(slots)})"
    msg["From"] = posiljatelj
    msg["To"] = ", ".join(prejemniki)
    msg.set_content(format_message(slots))

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
            smtp.starttls(context=ctx)
        with smtp:
            if user and geslo:
                smtp.login(user, geslo)
            smtp.send_message(msg)
        print(f"  -> e-posta ({len(prejemniki)} prejemnikov): OK")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[napaka] e-posta: {exc}", file=sys.stderr)
        return False


KANALI = (posylji_telegram, posylji_ntfy, posylji_webhook, posylji_email)


def obvesti(slots: list) -> None:
    """Poslje po vseh nastavljenih kanalih."""
    poslano = [k(slots) for k in KANALI]
    if not any(poslano):
        print("[opozorilo] Noben kanal za obvescanje ni nastavljen; "
              "sporocilo samo izpisujem.", file=sys.stderr)
        print(format_message(slots))


# --------------------------------------------------------------------------

def run_once() -> int:
    """En pregled. Vrne stevilo novih terminov (-1 ob popolni napaki)."""
    slots, errors = collect_all(WEEKS_AHEAD)
    for e in errors:
        print(f"[napaka pri pridobivanju] {e}", file=sys.stderr)

    # Ce so padli VSI tedni, ne diraj stanja - sicer bi ob vrnitvi
    # povezave vse termine poslal kot "nove".
    if errors and len(errors) == WEEKS_AHEAD:
        print("Vsi zahtevki so padli, stanja ne posodabljam.", file=sys.stderr)
        return -1

    state = load_state(STATE_FILE)
    znani = state.get("znani", {})

    trenutni = {slot_key(s): s for s in slots}
    novi = [s for k, s in trenutni.items() if k not in znani]

    stamp = time.strftime("%H:%M:%S", time.gmtime())
    print(f"[{stamp}Z] terminov: {len(trenutni)} | novih: {len(novi)}")

    if novi:
        obvesti(novi)

    # V datoteko NE pisemo casovne znacke, da se spremeni samo takrat,
    # ko se dejansko spremeni nabor terminov (manj commitov v GitHub Actions).
    state["znani"] = {k: f"{v['datum']} {v['ura']}" for k, v in trenutni.items()}
    state["stevilo"] = len(trenutni)
    save_state(STATE_FILE, state)
    return len(novi)


def main() -> int:
    repeat = max(1, int(os.environ.get("REPEAT", "1")))
    sleep_s = max(30, int(os.environ.get("REPEAT_SLEEP", "300")))

    skupaj_novih = 0
    zadnji = -1
    for i in range(repeat):
        if i:
            time.sleep(sleep_s)
        zadnji = run_once()
        if zadnji > 0:
            skupaj_novih += zadnji

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"new_count={skupaj_novih}\n")

    return 1 if zadnji < 0 else 0


if __name__ == "__main__":
    sys.exit(main())
