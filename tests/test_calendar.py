import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import update_calendar as u


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.raw = (u.ROOT/'tests/fixtures/initial.ics').read_bytes()
        self.base = (u.ROOT/'data/baseline-original.ics').read_bytes()
        self.state = json.loads((u.ROOT/'tests/fixtures/initial-state.json').read_text())
        self.cal = u.Calendar.from_ical(self.raw)
        self.league = (u.ROOT/'tests/fixtures/league.html').read_text()
        self.cup = (u.ROOT/'tests/fixtures/cup.html').read_text()
        self.records = u.parse_league(self.league)+u.parse_cup(self.cup)
        self.now = datetime(2026,9,17,0,tzinfo=timezone.utc)

    def test_integrity_and_consolidated_results(self):
        u.validate(self.raw,self.base,self.state['locked_results'])
        self.assertEqual(len(u.events(self.cal)),41)
        self.assertEqual(self.state['locked_results']['campobasso-2026-27-g5@calendario-rossoblu'],'2-2')

    def test_live_official_structure(self):
        self.assertEqual(len(self.records),41)
        self.assertEqual(self.records[4]['score'],[2,2])

    def test_unchanged_and_placeholders(self):
        cal,state,changed=u.apply(self.cal,self.records,self.state,self.now)
        self.assertEqual(changed,[])
        self.assertEqual(state,self.state)
        self.assertEqual(cal.to_ical(),self.cal.to_ical())

    def test_wrong_season_and_missing_round(self):
        with self.assertRaises(ValueError):u.parse_league(self.league.replace('Stagione 2026/2027','Stagione 2025/2026'))
        with self.assertRaises(ValueError):u.parse_league(self.league.replace('data-round="38"','data-round="37"'))

    def test_wrong_pair_and_abnormal_date(self):
        rec=copy.deepcopy(self.records);rec[5]['away']='juventus'
        with self.assertRaises(ValueError):u.apply(self.cal,rec,self.state,self.now)
        rec=copy.deepcopy(self.records);rec[5]['date']='2027-01-01'
        with self.assertRaises(ValueError):u.apply(self.cal,rec,self.state,self.now)

    def test_locked_result_cannot_be_overwritten(self):
        rec=copy.deepcopy(self.records);rec[4]['score']=[9,9]
        cal,_,_=u.apply(self.cal,rec,self.state,self.now)
        self.assertEqual(u.result_in(u.events(cal)[self.state['mapping']['g5']]),'2-2')

    def test_new_time_sequence_and_timezone(self):
        rec=copy.deepcopy(self.records);rec[8]['time']='18:30'
        cal,state,changed=u.apply(self.cal,rec,self.state,self.now)
        uid=self.state['mapping']['g9']; e=u.events(cal)[uid]
        self.assertEqual(changed,[uid])
        self.assertEqual(e.decoded('DTSTART').utcoffset(),timedelta(hours=2))
        self.assertEqual(int(e['SEQUENCE']),int(u.events(self.cal)[uid]['SEQUENCE'])+1)
        u.validate(cal.to_ical(),self.base,state['locked_results'])

    def test_result_requires_two_observations(self):
        rec=copy.deepcopy(self.records);rec[5]['score']=[1,0]
        now=datetime(2026,9,21,tzinfo=timezone.utc)
        cal,state,changed=u.apply(self.cal,rec,self.state,now)
        self.assertEqual(changed,[])
        cal,state,changed=u.apply(cal,rec,state,now+timedelta(hours=6))
        self.assertEqual(changed,[self.state['mapping']['g6']])
        self.assertEqual(state['locked_results'][changed[0]],'1-0')
        _,_,again=u.apply(cal,rec,state,now+timedelta(hours=12))
        self.assertEqual(again,[])

    def test_live_score_excluded(self):
        html=self.league.replace('class="match-score"','class="match-score live"')
        self.assertTrue(all(x['score'] is None for x in u.parse_league(html)))

    def test_cup_completed_without_time(self):
        rec=copy.deepcopy(self.records)
        rec[-1]['score']=[2,1];rec[-1]['time']=None
        now=datetime(2026,10,29,tzinfo=timezone.utc)
        cal,state,changed=u.apply(self.cal,rec,self.state,now)
        cal,state,changed=u.apply(cal,rec,state,now+timedelta(hours=6))
        self.assertEqual(changed,[self.state['mapping']['cup3']])
        self.assertEqual(state['locked_results'][changed[0]],'2-1')

    def test_removed_uid_rejected(self):
        self.cal.subcomponents.pop()
        with self.assertRaises(ValueError):u.validate(self.cal.to_ical(),self.base,self.state['locked_results'])

    def test_mass_changes_rejected(self):
        rec=copy.deepcopy(self.records)
        for r in rec[8:25]:r['time']='18:30'
        with self.assertRaises(ValueError):u.apply(self.cal,rec,self.state,self.now)


if __name__=='__main__':unittest.main()
