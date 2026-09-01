# Checker prostih terminov za vozniški izpit (eUprava / AVP)

Vsakih ~5 minut preveri, ali se je na strani
[Prosti termini za opravljanje vozniškega izpita](https://e-uprava.gov.si/si/javne-evidence/prosti-termini-zemljevid.html)
pojavil nov prost termin, in ti pošlje **Telegram** sporočilo.
Teče brezplačno na GitHub Actions — tvojega računalnika ni treba imeti prižganega.

Privzeti filtri: **preverjanje znanja vožnje, kategorija B, izpitni center KRANJ (Kolodvorska cesta 5)**, 15 tednov naprej.

> Podatki so informativni. Na izpit se še vedno prijaviš pri upravni enoti.

---

## 1. Naredi Telegram bota

1. V Telegramu odpri pogovor z **[@BotFather](https://t.me/BotFather)** → `/newbot` → izberi ime.
   Dobiš **token**, npr. `8123456789:AAF...`.
2. Odpri pogovor s **svojim novim botom** in mu pošlji karkoli (npr. `/start`).
   Brez tega ti bot ne sme pisati.
3. Poišči svoj **chat ID**: odpri v brskalniku
   `https://api.telegram.org/bot<TVOJ_TOKEN>/getUpdates`
   in prepiši številko iz `"chat":{"id":123456789`.
   (Alternativa: pogovor z **[@userinfobot](https://t.me/userinfobot)**.)

## 2. Postavi repozitorij

1. Naredi **nov GitHub repozitorij** in vanj naloži te datoteke
   (`checker.py`, `requirements.txt`, `state.json`, `.github/workflows/preveri-termine.yml`).

   ```bash
   git init
   git add .
   git commit -m "checker prostih terminov"
   git branch -M main
   git remote add origin git@github.com:<uporabnik>/<repo>.git
   git push -u origin main
   ```

2. **Naj bo repozitorij javen (public).** Actions je za javne repozitorije
   neomejeno brezplačen; pri zasebnih imaš na brezplačnem planu le 2.000 minut/mesec,
   kar zadostuje za ~1 pregled na 20 min, ne za 5.
   Token in chat ID gresta v *Secrets* in v javnem repozitoriju **nista** vidna.

3. **Settings → Secrets and variables → Actions → New repository secret**, dodaj:

   | Ime | Vrednost |
   |---|---|
   | `TELEGRAM_TOKEN` | token od BotFatherja |
   | `TELEGRAM_CHAT_ID` | tvoj chat ID |

4. Zavihek **Actions** → potrdi omogočitev workflowov → izberi
   *Preveri proste termine* → **Run workflow** za prvi ročni test.

Prvi zagon shrani vse trenutne termine kot "znane" in **ne** pošlje obvestila
(oz. pošlje enkratni seznam, če jih najde). Od takrat naprej dobiš sporočilo
samo za **nove** termine.

---

## 3. Kako spremenim, kaj se spremlja

V `checker.py`, slovar `FILTERS`:

```python
FILTERS = {
    "lang": "si",
    "type": "1",            # 1 = vožnja, 2 = teorija
    "cat": "6",             # 6 = B   (kljuc izpusti = vse kategorije)
    "izpitniCenter": "18",  # 18 = Kranj  (izpusti = vsi centri)
    "lokacija": "223",      # 223 = KRANJ Kolodvorska cesta 5  (izpusti = vse lokacije v centru)
    "is_ajax": "1",
}
```

Najlažji način, da dobiš prave številke za druge filtre: na eUpravi nastavi
filtre v brskalniku, nato dekodiraj base64 niz iz naslovne vrstice (vse za `#`) —
notri je JSON s točno temi ključi:

```bash
python3 -c "import base64,sys,json;print(json.dumps(json.loads(base64.b64decode(sys.argv[1])),indent=1,ensure_ascii=False))" 'eyJwYWdl...'
```

Ostale nastavitve (prek `env:` v workflowu):

| Spremenljivka | Privzeto | Pomen |
|---|---|---|
| `WEEKS_AHEAD` | `15` | koliko tednov naprej pregleda (~3,5 meseca) |
| `REPEAT` | `3` | koliko pregledov znotraj enega zagona |
| `REPEAT_SLEEP` | `300` | sekund med pregledi (300 = 5 min) |

---

## 4. Kako to deluje

- Stran ima interni AJAX endpoint, ki vrne majhen HTML delček s tabelo terminov:
  `…/prosti-termini-zemljevid/content/singleton.html?lang=si&type=1&cat=6&…&calendar_date=YYYY-MM-DD&is_ajax=1`
  Skripta ga pokliče enkrat na teden naprej (`calendar_date` = ponedeljek tistega tedna).
- Vsak termin dobi stabilen ključ (datum + ura + center + kategorije).
  Ključi se hranijo v `state.json`, ki ga workflow commita **samo, kadar se nabor spremeni** —
  zato ni 288 commitov na dan.
- Če padejo vsi zahtevki (izpad strani), se stanje **ne** posodobi, da ob vrnitvi
  ne dobiš vseh terminov naenkrat kot "nove".

## 5. Na kaj paziti

- **GitHubov cron zamuja.** `*/15` v praksi pomeni 15–35 min; zato en zagon
  interno preveri 3× na 5 minut. Če hočeš res trdnih 5 minut, potrebuješ
  VPS/Raspberry Pi z `while true; do python checker.py; sleep 300; done`.
- **Scheduled workflowi se po 60 dneh brez aktivnosti v repozitoriju samodejno izklopijo.**
  Commiti `state.json` to večinoma preprečijo; sicer občasno kaj potisni v repo.
- Bodi **vljuden do strežnika**: 15 tednov × 3 pregledi na 15 min je ~180 zahtevkov/uro.
  Če ti to zadostuje počasneje, znižaj `WEEKS_AHEAD` ali `REPEAT`.
- Termini gredo hitro. Obvestilo je le opozorilo — prijava teče prek upravne enote.

## 6. Lokalni test

```bash
pip install -r requirements.txt
TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... WEEKS_AHEAD=4 REPEAT=1 python checker.py
```

Brez `TELEGRAM_*` skripta sporočilo samo izpiše v konzolo.
