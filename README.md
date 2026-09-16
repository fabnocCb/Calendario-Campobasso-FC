# Calendario Campobasso FC 2026/27

Feed invariato: https://fabnocCb.github.io/Calendario-Campobasso-FC/Campobasso_FC_2026-27.ics

Il calendario conserva le 38 giornate e le 3 gare di Coppa esistenti, con gli stessi UID.
La copia originale byte per byte del commit `608a3f6` è in `data/baseline-original.ics`.
Il risultato Grosseto–Campobasso 2–2, assente in quel commit, è stato aggiunto dopo
verifica sul calendario ufficiale Lega Pro il 16 settembre 2026.

## Automazione

`.github/workflows/update-calendar.yml` controlla le fonti alle 00:17, 06:17,
12:17 e 18:17 UTC (GitHub può ritardare le esecuzioni). È avviabile da
**Actions → Aggiorna calendario Campobasso → Run workflow**, e parte anche su push a main.
Lo script usa soltanto il sito ufficiale della Lega Pro, https://www.seriec.com/calendario
e https://www.seriec.com/coppa-italia/calendario. Il precedente dominio lega-pro.com
rimanda al nuovo sito. Non sono usate fonti secondarie né servizi a pagamento.

Il workflow testa e valida prima di fare commit. Commit/push avvengono solo se
cambiano feed o stato delle osservazioni. Lo stato conserva le prime letture dei
risultati: servono due letture identiche distanti almeno 30 minuti e almeno quattro
ore dall'inizio della partita; i punteggi live sono ignorati. I risultati consolidati
sono bloccati, inclusi quelli delle prime cinque giornate e delle due Coppe disputate.

La pubblicazione Pages è esplicita: i commit effettuati con GITHUB_TOKEN non
innescano da soli una build Pages. Non servono PAT o segreti aggiuntivi.
Impostare una sola volta **Settings → Pages → Source → GitHub Actions**.
L'URL e il basename restano identici. Il calendario sul telefono segue poi i tempi
di sincronizzazione della propria app.

## Protezioni e limiti dichiarati

- Errori HTTP, cambio stagione/formato, UID mancanti/duplicati, abbinamenti errati,
  spostamenti oltre 21 giorni o più di 12 eventi modificati interrompono il processo.
- Ogni scrittura del file è atomica; il workflow non pubblica se un controllo fallisce.
- SEQUENCE cresce solo per gli eventi modificati; DTSTAMP e LAST-MODIFIED sono aggiornati.
  Gli appuntamenti usano Europe/Rome e gestiscono automaticamente l'ora legale.
- Il sito ufficiale espone anche orari predefiniti (00:00, 01:00, 15:00, 20:45).
  Questi non trasformano un evento giornaliero in un orario confermato e non
  sostituiscono appuntamenti già confermati. L'orario rimane da definire finché
  arriva un dato distinguibile dai segnaposto oppure il risultato è consolidabile.
  È una scelta prudente: una partita realmente fissata alle 15:00 può rimanere
  senza ora nel feed. I comunicati PDF e i post del club non sono interpretati automaticamente.
- La Coppa sul sito può essere arretrata: non cancella mai i risultati già presenti.
  Il parser accetta nuovi punteggi solo con stato esplicito `closed`; i rigori
  non vengono dedotti automaticamente. Nuove gare di Coppa non già mappate
  interrompono l'aggiornamento e richiedono un'estensione verificata della mappa UID.
- Un'anomalia appare come workflow fallito, con log e fonti scaricate conservate
  per 7 giorni negli artifact. Abilitare le notifiche Actions del proprio account
  per ricevere gli errori. Non viene richiesta manutenzione per le esecuzioni ordinarie,
  ma cambiamenti dei siti sorgente possono richiedere manutenzione del codice.
- GitHub può disabilitare le schedulazioni nei repository pubblici dopo 60 giorni
  senza attività. Durante la stagione i commit di aggiornamento mantengono attività;
  in lunghi periodi senza modifiche può essere necessaria la riattivazione.

## Verifica locale

Python 3.12 consigliato:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update_calendar.py --validate-only
python scripts/update_calendar.py --dry-run
```

Le fixture HTML riproducono solo le righe del Campobasso osservate il 16/09/2026.
I test usano un calendario di prova immutabile, indipendente dagli aggiornamenti futuri.
