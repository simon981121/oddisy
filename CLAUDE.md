# Oddisy

Python-verktyg som jämför Unibet (SE) mot Pinnacles vig-borttagna odds och flaggar value bets.

## Filer
- main.py: loop över sporter, jämförelse, utskrift
- api.py: The Odds API (get_sports, get_odds)
- calculator.py: calculate_fair_odds (vig-borttagning), find_pinnacle
- logger.py: skriver bets till bets.xlsx via openpyxl
- tracker.py: dubblettspärr, minne i seen_bets.json
- credits.py: kreditlogg (tabell credit_log i bets.db), veckobudget och reserv (check_budget), "python3 credits.py reset" efter nyckelbyte
- status.py: förbrukning senaste 7 dagarna per skript, senast kända remaining, öppna bets, missade CLV-fönster

## Regler och lärdomar
- Jämför bara marknader med lika många utfall (Pinnacle ishockey-h2h är tvåvägs, Unibet trevägs, vilket gav falska träffar).
- Totals matchas via nyckeln "namn_punkt"; olika linjer ger ingen jämförelse.
- Edge-tröskel 2.5%. Planerat tak ~15% (högre är nästan alltid datafel).
- Dubblettspärr: flagga igen först när edgen vuxit minst 2 procentenheter.
- Planerat: bara förmatch, åldersfilter på last_update (max ~3 min), CLV-loggning.
- Excel: rader tas bort med "Ta bort rader", inte Delete, annars förskjuts ws.max_row.
- Projektet ligger i ~/Dev, inte på Skrivbordet (iCloud-problem med venv).
- Tomma resultat kan bero på slut API-nyckel, kolla krediter först.

## Säkerhet
- Läs aldrig .env och skriv aldrig ut API-nyckeln.
- seen_bets.json och bets.xlsx är lokal data, ska inte ändras utan att jag ber om det.