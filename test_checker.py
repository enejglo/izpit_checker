#!/usr/bin/env python3
"""Testi za checker.py.  Zazeni z:  python -m unittest discover -s tests

Datoteka naj bo v mapi tests/ poleg fixture_*.html.
"""

import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "checker", os.path.join(ROOT, "checker.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["checker"] = mod
    spec.loader.exec_module(mod)
    return mod


checker = _load_checker()


def fixture(ime: str) -> str:
    with open(os.path.join(TESTS_DIR, ime), encoding="utf-8") as fh:
        return fh.read()


def slot(datum: str, ura: str, center: str = "KRANJ Območje 2") -> dict:
    return {
        "datum": datum, "ura": ura, "center": center,
        "naslov": "", "kategorije": "B", "mesta": "1",
    }


class TestRazclenjevanje(unittest.TestCase):

    def test_prazen_teden(self):
        self.assertEqual(checker.parse_slots(fixture("fixture_empty.html")), [])

    def test_termini(self):
        slots = checker.parse_slots(fixture("fixture_results.html"))
        self.assertEqual(len(slots), 3)

        prvi = slots[0]
        self.assertEqual(prvi["datum"], "2026-09-02")
        self.assertEqual(prvi["ura"], "07:30")
        self.assertEqual(prvi["kategorije"], "B, B1")
        self.assertEqual(prvi["mesta"], "1")
        # prazni "(cez priblizno vec kot )" repek mora izginiti
        self.assertEqual(prvi["center"], "CELJE Območje 3")
        self.assertIn("Ljubečna", prvi["naslov"])

        # datum z rowspan se prenese na naslednjo vrstico istega dne
        self.assertEqual(slots[1]["datum"], "2026-09-02")
        # nov rowspan zacne nov dan
        self.assertEqual(slots[2]["datum"], "2026-10-14")

    def test_kljuc_je_stabilen(self):
        a = checker.slot_key(slot("2026-10-14", "13:00"))
        b = checker.slot_key(slot("2026-10-14", "13:00"))
        c = checker.slot_key(slot("2026-10-14", "14:00"))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class TestStanje(unittest.TestCase):

    def test_bere_stari_format(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump({"stevilo": 1, "znani": {"abc": "2026-12-14 13:00"}}, fh)
            pot = fh.name
        try:
            state = checker.load_state(pot)
            self.assertEqual(state["znani"]["abc"]["datum"], "2026-12-14")
            self.assertEqual(state["znani"]["abc"]["ura"], "13:00")
        finally:
            os.unlink(pot)

    def test_manjkajoca_datoteka(self):
        self.assertEqual(checker.load_state("/ne/obstaja.json"), {"znani": {}})


class TestDelneNapake(unittest.TestCase):
    """Najpomembnejsi test: teden, ki ni uspel, ne sme povzrociti laznih obvestil."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        checker.STATE_FILE = self.tmp.name

        self.poslano = []
        self._obvesti = checker.obvesti
        self._collect = checker.collect
        checker.obvesti = lambda slots: self.poslano.append(len(slots))

        self.t1 = checker.week_of("2026-09-08")
        self.t2 = self.t1 + dt.timedelta(weeks=1)

    def tearDown(self):
        checker.obvesti = self._obvesti
        checker.collect = self._collect
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _collect_vrni(self, slots, uspeli, napake=()):
        checker.collect = lambda weeks: (slots, set(uspeli), list(napake))

    def test_padel_teden_ne_povzroci_laznih_obvestil(self):
        a, b = slot("2026-09-08", "09:00"), slot("2026-09-15", "10:00")

        # 1. krog: oba tedna OK -> 2 nova
        self._collect_vrni([a, b], [self.t1, self.t2])
        self.assertEqual(checker.run_once(2), 2)

        # 2. krog: drugi teden pade -> nic novega in termin se OHRANI
        self._collect_vrni([a], [self.t1], ["2026-09-14: timeout"])
        self.assertEqual(checker.run_once(2), 0)
        self.assertEqual(json.load(open(self.tmp.name, encoding="utf-8"))["stevilo"], 2)

        # 3. krog: teden spet dela -> NE sme javiti starega termina kot novega
        self._collect_vrni([a, b], [self.t1, self.t2])
        self.assertEqual(checker.run_once(2), 0)

        self.assertEqual(self.poslano, [2])  # obvestilo samo enkrat

    def test_zaseden_termin_izgine_brez_obvestila(self):
        a, b = slot("2026-09-08", "09:00"), slot("2026-09-15", "10:00")
        self._collect_vrni([a, b], [self.t1, self.t2])
        checker.run_once(2)

        # teden je uspel in termina ni vec -> odstrani se iz stanja
        self._collect_vrni([a], [self.t1, self.t2])
        self.assertEqual(checker.run_once(2), 0)
        self.assertEqual(json.load(open(self.tmp.name, encoding="utf-8"))["stevilo"], 1)

    def test_popolna_napaka_ne_dira_stanja(self):
        a = slot("2026-09-08", "09:00")
        self._collect_vrni([a], [self.t1])
        checker.run_once(1)
        prej = open(self.tmp.name, encoding="utf-8").read()

        self._collect_vrni([], [], ["vse je padlo"])
        self.assertEqual(checker.run_once(1), -1)
        self.assertEqual(open(self.tmp.name, encoding="utf-8").read(), prej)


class TestZanka(unittest.TestCase):

    def test_izhodna_koda_ob_delnem_uspehu(self):
        """En padel cikel ne sme obarvati celega zagona rdece."""
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        stari = dict(os.environ)
        klici = {"n": 0}

        def izmenicno(weeks):
            klici["n"] += 1
            if klici["n"] % 2:
                raise RuntimeError("simulirana napaka")
            return ([], {checker.week_of("2026-09-08")}, [])

        _collect = checker.collect
        checker.collect = izmenicno
        try:
            os.environ["RUN_SECONDS"] = "0"
            os.environ["REPEAT"] = "2"
            os.environ["REPEAT_SLEEP"] = "30"
            checker.STATE_FILE = tmp.name
            # REPEAT=2, drugi cikel uspe -> izhodna koda 0
            self.assertEqual(checker.main(), 0)
        finally:
            checker.collect = _collect
            os.environ.clear()
            os.environ.update(stari)
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
