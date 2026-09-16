"""Conservative official-source updater. Any anomaly aborts before either file is written."""
import argparse
import copy
import hashlib
import json
import logging
import os
import re
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
FEED = 'Campobasso_FC_2026-27.ics'
LEAGUE = 'https://www.seriec.com/calendario'
CUP = 'https://www.seriec.com/coppa-italia/calendario'
ROME = ZoneInfo('Europe/Rome')
MONTHS = dict(zip('gen feb mar apr mag giu lug ago set ott nov dic'.split(), range(1, 13)))
# The official site populates unconfirmed rounds with these default times.
PLACEHOLDERS = {'00:00', '01:00', '15:00', '20:45'}
LOG = logging.getLogger('calendar')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFKD', s.lower()) if not unicodedata.combining(c))
    s = re.sub(r'[^a-z0-9 ]', '', s).strip()
    return {'campobasso fc': 'campobasso', 'atalanta u23': 'atalanta under 23',
            'guidonia montecelio': 'guidonia m'}.get(s, s)


def text(node, selector):
    found = node.select_one(selector)
    require(found is not None, f'Struttura fonte cambiata: manca {selector}')
    return found.get_text(' ', strip=True)


def official_date(day, month):
    month = MONTHS[month.lower()]
    result = date(2026 if month >= 7 else 2027, month, int(day))
    require(date(2026, 8, 1) <= result <= date(2027, 6, 30), 'Data fuori stagione')
    return result


def parse_league(html):
    soup = BeautifulSoup(html, 'html.parser')
    require('Stagione 2026/2027' in soup.get_text(' ', strip=True), 'Stagione campionato errata')
    group = soup.select_one('.calendario-girone-b')
    require(group is not None, 'Girone B assente')
    records = []
    for rnd in group.select('.cal-round-container'):
        for row in rnd.select('.match-row'):
            home = text(row, '.team-home .team-name-full')
            away = text(row, '.team-away .team-name-full')
            if 'campobasso' not in (norm(home), norm(away)):
                continue
            day = text(row, '.match-date').split()
            require(len(day) == 3, 'Formato data ufficiale inatteso')
            d = official_date(day[1], day[2])
            clock = text(row, '.match-time')
            require(re.fullmatch(r'\d{2}:\d{2}', clock), 'Orario ufficiale non interpretabile')
            score = row.select('.match-score .score-num')
            live = row.select_one('.match-score.live') is not None
            values = tuple(int(x.get_text(strip=True)) for x in score)
            require(len(values) in (0, 2) and all(0 <= v <= 15 for v in values), 'Risultato anomalo')
            records.append(dict(key=f'g{int(rnd["data-round"])}', home=norm(home), away=norm(away),
                                date=d.isoformat(), time=clock, score=list(values) if values and not live else None,
                                source=LEAGUE))
    require(len(records) == 38 and {r['key'] for r in records} == {f'g{i}' for i in range(1, 39)},
            'La fonte non contiene esattamente le 38 giornate del Campobasso')
    return records


def parse_cup(html):
    soup = BeautifulSoup(html, 'html.parser')
    require('Stagione 26/27' in soup.get_text(' ', strip=True), 'Stagione Coppa errata')
    records = []
    for row in soup.select('.coppa-match-card'):
        home = norm(text(row, '.coppa-team-home .coppa-team-name'))
        away = norm(text(row, '.coppa-team-away .coppa-team-name'))
        if 'campobasso' not in (home, away):
            continue
        d = official_date(text(row, '.coppa-date-day'), text(row, '.coppa-date-month'))
        clock_node = row.select_one('.coppa-score-time')
        clock = clock_node.get_text(strip=True) if clock_node else None
        if clock:
            require(re.fullmatch(r'\d{2}:\d{2}', clock), 'Orario Coppa non interpretabile')
        # Results require an explicit finished state; never infer penalties from a score.
        score = None
        if 'closed' in row.get('class', []):
            nums = row.select('.coppa-score-value')
            if len(nums) == 2 and not re.search(r'rigori|penalt|dcr', row.get_text(), re.I):
                score = [int(x.get_text(strip=True)) for x in nums]
                require(all(0 <= n <= 15 for n in score), 'Risultato Coppa anomalo')
        rnd = row.find_parent(class_='coppa-match-round')
        require(rnd is not None, 'Turno Coppa assente')
        records.append(dict(key=f'cup{rnd["data-round"]}', home=home, away=away,
                            date=d.isoformat(), time=clock, score=score, source=CUP))
    require(records and len({r['key'] for r in records}) == len(records), 'Coppa assente o duplicata')
    return records


def events(cal):
    result = {}
    for e in cal.walk('VEVENT'):
        uid = str(e.get('UID', ''))
        require(uid and uid not in result, 'UID assente o duplicato')
        result[uid] = e
    return result


def pair(e):
    title = str(e['SUMMARY']).split('\n')[0].replace('🔴🔵 ', '')
    title = re.sub(r' \d+-\d+.*$', '', title)
    sides = title.split(' – ')
    require(len(sides) == 2, f'Abbinamento ambiguo: {title}')
    return tuple(norm(x) for x in sides)


def result_in(e):
    match = re.search(r' (\d+)-(\d+)(.*)$', str(e['SUMMARY']).split('\n')[0])
    return match.group(0).strip() if match else None


def validate(raw, baseline, locks):
    cal = Calendar.from_ical(raw)
    ev = events(cal)
    original = events(Calendar.from_ical(baseline))
    require(set(ev) == set(original), 'UID aggiunti, rimossi o modificati')
    require(sum(bool(re.search(r'-g\d+@', uid)) for uid in ev) == 38, 'Non ci sono 38 giornate')
    for uid, e in ev.items():
        require(not e.errors, f'ICS non valido: {uid}')
        for k in ('DTSTART', 'DTEND', 'DTSTAMP', 'SUMMARY', 'SEQUENCE'):
            require(k in e and not isinstance(e[k], list), f'Campo {k} assente o duplicato: {uid}')
        start, end = e.decoded('DTSTART'), e.decoded('DTEND')
        require(type(start) == type(end) and end > start, f'Intervallo errato: {uid}')
        if isinstance(start, datetime):
            require(start.tzinfo is not None, f'Fuso orario assente: {uid}')
        d = start.date() if isinstance(start, datetime) else start
        require(date(2026, 8, 1) <= d <= date(2027, 6, 30), f'Data fuori stagione: {uid}')
        require(pair(e) == pair(original[uid]), f'Squadre cambiate: {uid}')
        if uid in locks:
            require(result_in(e) == locks[uid], f'Risultato consolidato alterato: {uid}')
    return cal


def fetch(url, folder):
    session = requests.Session()
    session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=2,
                  status_forcelist=[429, 500, 502, 503, 504])))
    response = session.get(url, timeout=(15, 60), headers={'User-Agent': 'CampobassoCalendar/1.0'}, stream=True)
    response.raise_for_status()
    require(response.url.split('/')[2] == 'www.seriec.com', 'Redirect fuori dalla fonte ufficiale')
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        require(size <= 12_000_000, 'Fonte eccessivamente grande')
        chunks.append(chunk)
    data = b''.join(chunks)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ('coppa.html' if url == CUP else 'campionato.html')).write_bytes(data)
    LOG.info('Fonte %s: %d byte, sha256 %s', url, size, hashlib.sha256(data).hexdigest())
    return data.decode('utf-8')


def apply(cal, records, state, now):
    cal, state = copy.deepcopy(cal), copy.deepcopy(state)
    ev = events(cal)
    mapping = state['mapping']
    changed = []
    seen = set()
    for r in records:
        uid = mapping.get(r['key'])
        require(uid is not None, f'Nuova gara non mappata: {r["key"]}; verifica richiesta')
        require(uid not in seen, f'Gara duplicata {uid}')
        seen.add(uid)
        e = ev[uid]
        require(pair(e) == (r['home'], r['away']), f'Squadre incompatibili: {uid}')
        if uid in state['locked_results']:
            LOG.info('Preservato risultato consolidato %s: %s', uid, state['locked_results'][uid])
            continue
        old_start = e.decoded('DTSTART')
        old_date = old_start.date() if isinstance(old_start, datetime) else old_start
        new_date = date.fromisoformat(r['date'])
        require(abs((new_date-old_date).days) <= 21, f'Spostamento oltre 21 giorni: {uid}')
        clock = r['time']
        if clock:
            hour, minute = map(int, clock.split(':'))
            proposed = datetime(new_date.year, new_date.month, new_date.day, hour, minute, tzinfo=ROME)
        else:
            proposed = None
        # Wait at least four hours and two runs >=30 min apart, with identical scores.
        score = r['score']
        score_ready = False
        result_after = (proposed + timedelta(hours=4)) if proposed else datetime.combine(new_date + timedelta(days=1), datetime.min.time(), tzinfo=ROME)
        if score is not None and now >= result_after:
            observation = state.setdefault('pending_results', {}).get(uid)
            if observation and observation['score'] == score:
                score_ready = now - datetime.fromisoformat(observation['first_seen']) >= timedelta(minutes=30)
            else:
                state['pending_results'][uid] = {'score': score, 'first_seen': now.isoformat()}
                LOG.info('Risultato in attesa di seconda lettura: %s %s', uid, score)
        else:
            state.setdefault('pending_results', {}).pop(uid, None)
        before = e.to_ical()
        # Never publish default times as confirmed; retain existing confirmed time/date together.
        if proposed and (clock not in PLACEHOLDERS or score_ready):
            new_start = proposed
        elif isinstance(old_start, datetime):
            new_start = old_start
            if proposed != old_start:
                LOG.warning('Orario ambiguo; conservata data/ora confermata: %s', uid)
        else:
            new_start = new_date
            LOG.info('Orario non confermato, evento giornaliero: %s', uid)
        if new_start != old_start:
            e.pop('DTSTART'); e.add('DTSTART', new_start)
            e.pop('DTEND'); e.add('DTEND', new_start + (timedelta(hours=2) if isinstance(new_start, datetime) else timedelta(days=1)))
            desc = str(e.get('DESCRIPTION', ''))
            desc = re.sub(r'Data di calendario; orario da definire e soggetto ad anticipi/posticipi\.', '', desc).strip()
            if not isinstance(new_start, datetime):
                desc += ' Orario da definire.'
            e['DESCRIPTION'] = desc
        if score_ready:
            value = f'{score[0]}-{score[1]}'
            title = str(e['SUMMARY']).split('\n')[0]
            title = re.sub(r' \d+-\d+.*$', '', title)
            e['SUMMARY'] = f'{title} {value}'
            e['DESCRIPTION'] = str(e.get('DESCRIPTION', '')) + f' Risultato: {value}. Fonte: {r["source"]}'
            state['locked_results'][uid] = value
            state['pending_results'].pop(uid, None)
        if e.to_ical() != before:
            e['SEQUENCE'] = int(e.get('SEQUENCE', 0)) + 1
            for k in ('DTSTAMP', 'LAST-MODIFIED'):
                e.pop(k, None); e.add(k, now)
            e['X-SOURCE-URL'] = r['source']
            changed.append(uid)
            LOG.info('Aggiornata gara %s', uid)
    require(len(changed) <= 12, 'Oltre 12 eventi modificati: aggiornamento sospeso')
    return cal, state, changed


def atomic_write(path, data):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.calendar-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    baseline = (ROOT/'data/baseline-original.ics').read_bytes()
    path, state_path = ROOT/FEED, ROOT/'data/state.json'
    raw, state = path.read_bytes(), json.loads(state_path.read_text())
    cal = validate(raw, baseline, state['locked_results'])
    if args.validate_only:
        LOG.info('ICS valido: 38 giornate, 3 gare Coppa, UID e risultati preservati')
        return
    now = datetime.now(timezone.utc).replace(microsecond=0)
    records = parse_league(fetch(LEAGUE, ROOT/'artifacts')) + parse_cup(fetch(CUP, ROOT/'artifacts'))
    require({r['key'] for r in records} == set(state['mapping']), 'Gare mancanti o nuove: aggiornamento sospeso')
    updated, next_state, changed = apply(cal, records, state, now)
    output = updated.to_ical() if changed else raw
    validate(output, baseline, next_state['locked_results'])
    # Re-parse round trip before persisting. No network failure can partially update the feed.
    if not args.dry_run:
        if output != raw:
            atomic_write(path, output)
        if next_state != state:
            atomic_write(state_path, (json.dumps(next_state, ensure_ascii=False, indent=2)+'\n').encode())
    LOG.info('%s: %d eventi aggiornati', 'Simulazione' if args.dry_run else 'Completato', len(changed))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        LOG.exception('AGGIORNAMENTO INTERROTTO: nessuna pubblicazione; controllare la fonte e i log')
        raise SystemExit(1)
