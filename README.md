# Checker prostih terminov za vozniški izpit (eUprava / AVP)

Redno preverja, ali se je na strani
[Prosti termini za opravljanje vozniškega izpita](https://e-uprava.gov.si/si/javne-evidence/prosti-termini-zemljevid.html)
pojavil nov prost termin, in ti pošlje obvestilo (Telegram, ntfy, Discord/Slack
webhook ali e-pošta). Teče brezplačno na GitHub Actions — tvojega računalnika
ni treba imeti prižganega.

## Kako pogosto dejansko preverja

Na ~5 minut, v oknih po ~10 minut. En zagon workflowa opravi dva pregleda
(ob 0 in 5 minutah), cron pa zagon sproži na 10 minut.

To je namerno. GitHubov `schedule` je **best effort**: sprožitev se lahko
zamakne, ob visoki obremenitvi pa se zavrže in **se ne nadomesti**. Zato veljata
dve pravili, ki sta vgrajeni v `preveri-termine.yml`:

1. **Trajanje joba mora biti krajše od cron intervala.** Ob `concurrency`
   skupini GitHub hrani največ en čakajoč zagon in vsak naslednji prekliče
   prejšnjega čakajočega. Če job traja dlje od intervala, efektivna frekvenca
   ni več cron, ampak trajanje joba. (Prej: job 67 min ob cronu 15 min → v
   praksi zagon na 3–4 ure.)
2. **Ne planiraj na vrh ure.** `*/15` pade vsakič četrtič na `:00`, kar je
   najbolj obremenjen trenutek. Zato `3,13,23,33,43,53 * * * *`.

Če potrebuješ zanesljivejši ritem, glej razdelek *Verižeje* spodaj.

## Namestitev

1. Forkaj oz. skopiraj repo. Datoteke:
   ```
   .github/workflows/preveri-termine.yml
   checker.py
   requirements.txt
   state.json
   tests/test_checker.py
   tests/fixture_empty.html
   tests/fixture_results.html
   ```
2. V `checker.py` nastavi `FILTERS` na svoj izpitni center in kategorijo.
   Vrednosti dobiš tako, da na e-upravi nastaviš filtre in pogledaš URL:
   | ključ | pomen |
   | --- | --- |
   | `type` | `1` = vožnja (praktični del), `2` = teorija |
   | `cat` | kategorija; `6` = B. Ključ izpusti za vse kategorije. |
   | `izpitniCenter` | npr. `18` = Kranj. Izpusti za vse centre. |
   | `lokacija` | npr. `223` = Kranj, Kolodvorska cesta 5 |
3. Dodaj vsaj en kanal med *Settings → Secrets and variables → Actions*.
4. Poženi ročno prek *Actions → Preveri proste termine → Run workflow* in
   preveri, da obvestilo pride.

## Kanali za obveščanje

Skripta pošlje po **vseh** kanalih, za katere so nastavljeni secreti. Če ni
nastavljen noben, sporočilo samo izpiše v log.

| Kanal | Secreti |
| --- | --- |
| Telegram | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (lahko več, ločenih z vejico) |
| ntfy.sh | `NTFY_TOPIC` (neobvezno `NTFY_SERVER`) |
| Discord / Slack | `WEBHOOK_URL` |
| E-pošta | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`, `MAIL_FROM` |

## Nastavitve (spremenljivke okolja v workflowu)

| Spremenljivka | Privzeto | Pomen |
| --- | --- | --- |
| `WEEKS_AHEAD` | `15` | Koliko tednov naprej pregleda **poln** pregled |
| `QUICK_WEEKS` | `0` | Koliko tednov pregledajo nadaljnji cikli znotraj zagona. `0` = vedno vsi. Manjša številka pomeni manj obremenitve e-uprave. |
| `RUN_SECONDS` | `0` | Časovni proračun zagona v sekundah. Ko je porabljen, se zagon konča. Ima prednost pred `REPEAT`. |
| `REPEAT` | `1` | Število ciklov, če `RUN_SECONDS` ni nastavljen |
| `REPEAT_SLEEP` | `300` | Korak med cikli v sekundah (min. 30) |
| `HTTP_TRIES` | `3` | Poskusi na teden ob 5xx / timeoutu |
| `HTTP_TIMEOUT` | `30` | Timeout posameznega zahtevka |
| `TZ` | `Europe/Ljubljana` | Časovni pas za izračun začetka tedna |
| `STATE_FILE` | `state.json` | Pot do datoteke s stanjem |

Prvi cikel vsakega zagona vedno pregleda vseh `WEEKS_AHEAD` tednov; nadaljnji
le prvih `QUICK_WEEKS`. Termini iz nepregledanih tednov ostanejo v stanju
nedotaknjeni, zato hitri cikli ne povzročajo lažnih obvestil.

## Verižeje (neobvezno, za stabilnejši ritem)

`workflow_dispatch` za razliko od crona ni podvržen zamikom, zato lahko vsak
zagon sam sproži naslednjega:

1. Ustvari fine-grained PAT s pravico **Actions: write** na tem repozitoriju.
2. Shrani ga kot secret `CHAIN_PAT`. Zadnji korak workflowa se aktivira sam.
3. V `preveri-termine.yml` zamenjaj cron z varovalko `7,37 * * * *`, da se
   urnika ne seštevata.

Vgrajeni `GITHUB_TOKEN` tega ne zmore — dogodki, sproženi z njim, namenoma
ne ustvarijo novega zagona.

Če hočeš res trdna jamstva, GitHubov cron ni ura. Postavi zunanji planer
(Cloudflare Workers Cron Trigger, cron-job.org, sistemski cron), ki kliče
`POST /repos/<user>/<repo>/actions/workflows/preveri-termine.yml/dispatches`.

## Stanje in odpravljanje težav

`state.json` se commita **samo takrat, ko se nabor terminov spremeni**. Če
kadence ocenjuješ po commitih, boš videl bistveno večje razmike od dejanskih.
Ritem preverjaj v zavihku Actions oziroma:

```bash
gh run list --workflow=preveri-termine.yml --limit 50 \
  --json event,status,conclusion,createdAt,startedAt,updatedAt
```

Razlika med `createdAt` in `startedAt` ti pove, kje je zamik: če je `createdAt`
pozen, sprožilca GitHub sploh ni ustvaril pravočasno (planer); če je pozen le
`startedAt`, je run čakal na runnerja.

Zagon je rdeč samo, če ni uspel **noben** cikel. Posamezne napake se izpišejo
v log, povzetek pa v *Summary* zagona.

## Testi

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Opomba o vljudnosti

Pregled na 5 minut × 15 tednov pomeni ~180 zahtevkov na uro proti državni
strani. `QUICK_WEEKS` in `POLITE_MIN` / `POLITE_MAX` sta tam prav zato — pusti
razmik med zahtevki in ne zmanjšuj `REPEAT_SLEEP` pod 300 s brez potrebe.
