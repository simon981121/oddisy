# Schema för Oddisy (cron på macOS)

Oddisy körs via tre skript i `scripts/`. Varje skript startar sitt Pythonprogram med venv:ens python och skriver all utskrift med tidsstämpel till `logs/<namn>.log`. Ett lås gör att en ny körning hoppar över om den förra pågår, och en körning stoppas efter 10 minuter. En logg som blir större än cirka 1 MB flyttas till `<namn>.log.1`, och den äldre kopian skrivs då över.

| Skript | Kör | Logg |
|---|---|---|
| `scripts/run_main.sh` | `main.py` | `logs/main.log` |
| `scripts/run_clv.sh` | `clv.py` | `logs/clv.log` |
| `scripts/run_results.sh` | `results.py` | `logs/results.log` |

## Tidszon

Cron på macOS använder systemets tidszon. Det går inte att ange någon annan tidszon per rad. Kontrollera vilken tidszon Macen har:

```sh
readlink /etc/localtime        # visar .../Asia/Bangkok just nu
date                           # ska sluta med +07
```

Asia/Bangkok ligger alltid 7 timmar före UTC och har ingen sommartid. Tiderna i crontab nedan är skrivna i **lokal tid (Asia/Bangkok)**. Om du byter tidszon på datorn, till exempel när du reser, flyttas körningarna med. Räkna då om tiderna utifrån UTC-kolumnen.

## Föreslagna crontab-rader

```crontab
# m   h      dom mon dow  kommando
# main.py: 14:02 och 20:02 Bangkok (07:02 och 13:02 UTC, i UTC-cron: 2 7,13 * * *). Cirka 15–25 krediter per körning
#   (1 per vald sport: aktiva ligor i SOCCER_ALLOWLIST + ATP/WTA, h2h, 1 region; get_sports kostar 0).
2     14,20  *   *   *    /Users/simonsayadi/Dev/oddisy/scripts/run_main.sh
# clv.py: var 5:e minut dygnet runt (UTC samma minuter). 0 krediter när inget bet startar
#   inom 8 min, annars 1 kredit per match och marknad (h2h), en gång per match.
*/5   *      *   *   *    /Users/simonsayadi/Dev/oddisy/scripts/run_clv.sh
# results.py: 12:32 Bangkok (05:32 UTC). 2 krediter per sport som har öppna bets
#   som startade för minst 3 h sedan, normalt 2–10 krediter.
32    12     *   *   *    /Users/simonsayadi/Dev/oddisy/scripts/run_results.sh
```

| Skript | Bangkok (lokal tid) | UTC | Krediter per körning | Per vecka (ungefär) |
|---|---|---|---|---|
| main | 14:02, 20:02 | 07:02, 13:02 | ~15–25 | ~210–350 (spärr vid 500) |
| clv | var 5:e min | var 5:e min | 0, eller 1 per match med bet | beror på antal bets |
| results | 12:32 | 05:32 | 2 per sport med öppna bets | ~15–70 |

Varför de här tiderna:
- **main** körs `:02` i stället för `:00` så att den inte startar samtidigt som clv. 14:02 och 20:02 Bangkok motsvarar morgon och tidig eftermiddag i Europa (09:02 och 15:02 svensk sommartid, 08:02 och 14:02 vintertid). Det är före dagens matcher, medan Unibets och Pinnacles odds fortfarande uppdateras.
- **results** körs 12:32 Bangkok. Då har även europeiska kvällsmatcher (avspark omkring 03:00 Bangkok) passerat gränsen på 3 timmar i `results.py`.
- **clv** körs var 5:e minut med ett fönster på 8 minuter, så varje bet hamnar i minst en körning.

Kreditskyddet i `credits.py` gäller oavsett schema. main stoppas om dess förbrukning de senaste 7 dagarna plus körningens beräknade kostnad blir mer än 500, eller om det skulle lämna färre än 30 krediter kvar. clv och results stoppas om senast kända remaining är under 30. Följ förbrukningen med `venv/bin/python status.py`.

## Lägga in schemat

Jag har inte ändrat din crontab. Så här lägger du in raderna själv:

```sh
crontab -l > ~/crontab.backup 2>/dev/null   # spara den nuvarande först
crontab -e                                  # klistra in raderna ovan, spara
crontab -l                                  # kontrollera
```

Om du vill ta bort allt igen kör du `crontab ~/crontab.backup`. Om du inte hade någon crontab innan kör du `crontab -r`.

## Testa ett skript för hand först

1. Visa status. Det gör inga API-anrop:
   ```sh
   cd /Users/simonsayadi/Dev/oddisy && venv/bin/python status.py
   ```
2. Kör ett skript i en miljö som liknar crons, med tom miljö och kort PATH. Börja med clv, som oftast kostar 0 krediter:
   ```sh
   env -i HOME="$HOME" PATH=/usr/bin:/bin /Users/simonsayadi/Dev/oddisy/scripts/run_clv.sh
   echo "kod $?"
   tail -n 20 /Users/simonsayadi/Dev/oddisy/logs/clv.log
   ```
   Det här är en riktig körning. Om ett bet startar inom 8 minuter hämtas Pinnacle-odds, och det kostar krediter.
3. Gör samma sak med `run_results.sh` och `run_main.sh`. main kostar cirka 15–25 krediter.
4. När schemat är inlagt, följ loggarna med `tail -f /Users/simonsayadi/Dev/oddisy/logs/*.log`.

Skripten avslutas med följande koder, och slutraden i loggen förklarar dem:

| Kod | Betydelse |
|---|---|
| 0 | klar |
| 2 | kreditskyddet stoppade körningen (se raden `Avbryter:` i loggen) |
| 124 | timeout, stoppad efter 10 min |
| 127 | hittar inte `venv/bin/python` |
| 143 | avbruten av signal |

En körning som hoppar över för att den förra pågår avslutas med kod 0 och raden `Hoppar över: förra körningen pågår fortfarande`.

## Fallgropar på macOS

**Datorn måste vara vaken.** Cron kör inte när datorn sover, och missade körningar körs inte i efterhand. Några sätt att hålla den vaken:
- Systeminställningar → Batteri (eller Energi) → Alternativ: slå på "Förhindra automatisk vila när skärmen är avstängd" när datorn är ansluten till ström.
- Eller kör i ett terminalfönster som får vara öppet: `caffeinate -s`. Det håller datorn vaken så länge den är ansluten till ström och kommandot körs. Avsluta med Ctrl+C.
- En bärbar dator med stängt lock somnar ändå, utom när den har extern skärm och ström.
- `sudo pmset repeat wakeorpoweron MTWRFSU 13:58:00` väcker datorn vid en viss tid, men den kan somna igen innan körningen är klar. Det hjälper alltså main men inte clv.

**Full Disk Access.** Projektet ligger i `~/Dev`, som inte är en skyddad mapp. Skrivbord, Dokument, Hämtade filer och iCloud är skyddade. Därför behövs det oftast inte. Om loggen ändå inte skapas, eller om `/var/mail/$USER` innehåller "Operation not permitted", gör så här: Systeminställningar → Integritet och säkerhet → Fullständig skivåtkomst → `+`, tryck Cmd+Shift+G, skriv `/usr/sbin/cron` och slå på den.

**Felutskrift från cron.** Skripten skriver bara till `logs/`. Om ett skript inte ens kan starta, till exempel för att sökvägen är fel, lägger cron felet som lokal post. Läs den med `mail` eller med `tail /var/mail/$USER`.

**Byta nyckel.** Ändra raden i `.env` och kör sedan `venv/bin/python credits.py reset`. Annars ligger den gamla nyckelns låga remaining kvar och reservskyddet fortsätter att stoppa. Veckobudgeten för main påverkas inte, eftersom den räknas för alla nycklar tillsammans.
