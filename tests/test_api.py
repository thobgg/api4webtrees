#!/usr/bin/env python3
"""Tests fuer api4webtrees gegen ein echtes webtrees (siehe umgebung.py). Aufruf: python3 tests/test_api.py

Kern ist der Leck-Test: Die Testdaten tragen Markierungswoerter in allem, was eine Rolle NICHT sehen darf (lebende
Person, vertrauliche Person, Ereignis mit RESN privacy, privater Baum). Jede Rolle ruft jede Leseroute auf - kein
Markierungswort darf in irgendeiner Antwort stehen. So faellt ein Leck auf, egal ueber welches Feld es kaeme.
"""
import datetime
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import sys
import unittest

import manifest
import umgebung

U = None

# Was welche Rolle nicht sehen darf (webtrees: lebende Personen fuer Gaeste verborgen, RESN privacy fuer Mitglieder
# sichtbar, RESN confidential nur fuer Verwalter, privater Baum nur mit Rolle darin).
LEBEND = "Markerlebend"        # Lebhart /Markerlebend/, geboren 1990, auch sein Geburtsort Markerlebendort
BERUF = "Markerberuf"          # OCCU mit 2 RESN privacy bei Theodor (I1)
KONFIDENZ = "Markerkonfidenz"  # Daten von Konrad (I5, 1 RESN confidential): Notiz und Geburtsort
# Den NAMEN einer privaten Person zeigt webtrees selbst angemeldeten Mitgliedern (Baumeinstellung SHOW_LIVING_NAMES,
# Voreinstellung "Mitglieder") - nur die Daten bleiben verborgen. Gaeste sehen auch den Namen nicht.
NAME_PRIVAT = "Markername"
GEHEIMBAUM = "Markerbaum"      # einzige Person im privaten Baum "geheim"

VERBOTEN = {
    None: [LEBEND, BERUF, KONFIDENZ, NAME_PRIVAT, GEHEIMBAUM],
    "mitglied": [KONFIDENZ, GEHEIMBAUM],
    "bearbeiter": [KONFIDENZ, GEHEIMBAUM],
}


def setUpModule():
    global U
    U = umgebung.Umgebung().aufbauen()


def tearDownModule():
    if U:
        U.abbauen()


def leseaufrufe(baum="testbaum"):
    """Alle Leserouten mit allen Personen und Familien des Testbaums."""
    personen = ["I1", "I2", "I3", "I4", "I5"]
    aufrufe = [("Info", None, {})]
    for q in ["", LEBEND, KONFIDENZ, NAME_PRIVAT, BERUF, GEHEIMBAUM, "Offen"]:
        aufrufe.append(("Individuals", baum, {"q": q}))
        aufrufe.append(("Individuals", baum, {"q": q, "scope": "all"}))
    for x in personen:
        aufrufe += [
            ("Individual", baum, {"xref": x}),
            ("Pedigree", baum, {"xref": x, "generations": 5, "siblings": 1}),
            ("Descendants", baum, {"xref": x, "generations": 5}),
            ("Relationship", baum, {"xref1": "I1", "xref2": x}),
            ("Relationship", baum, {"xref1": x, "xref2": "I2"}),
        ]
    for f in ["F1", "F2"]:
        aufrufe.append(("Family", baum, {"xref": f}))
    aufrufe.append(("Sources", baum, {}))
    aufrufe.append(("Repositories", baum, {}))
    for q in ["S1", "S2"]:
        aufrufe.append(("Source", baum, {"xref": q}))
    aufrufe += [
        ("Export", baum, {"page": 1}),
        ("MediaList", baum, {}),
        ("Bookmarks", baum, {}),
        ("Anniversaries", baum, {"days": 60}),
        ("Pending", baum, {}),
        ("Tags", baum, {"type": "INDI"}),
        ("Tags", baum, {"type": "FAM"}),
        ("Places", baum, {"q": ""}),
        ("Places", baum, {"q": "Marker"}),
        ("Places", baum, {"list": 1}),
        ("Merges", baum, {}),
        ("Tasks", baum, {}),
        ("Tasks", baum, {"open": 1}),
        ("Changes", baum, {"limit": 20}),
        ("Changes", baum, {"xref": "I1"}),
    ]
    for ort in ["Offenbach", "Bieber, Offenbach", "Markerlebendort", "Markerkonfidenzort", "Gibtesnicht"]:
        aufrufe.append(("Place", baum, {"name": ort}))
    return aufrufe


def api_modul_turned_crop(crop, breite, hoehe, vierteldrehungen):
    """WriteActions::turnedCrop() ueber php -r - die Rechnung des Moduls selbst, ohne Server."""
    code = (
        f"require '{umgebung.MODUL}/src/WriteActions.php'; "
        "final class T { use \\Api4Webtrees\\WriteActions; } "
        f"echo json_encode(T::turnedCrop(json_decode('{json.dumps(crop)}', true), {breite}, {hoehe}, {vierteldrehungen}));")
    return json.loads(subprocess.run(["php", "-r", code], capture_output=True, text=True, check=True).stdout)


class Lecktest(unittest.TestCase):
    def pruefen(self, benutzer):
        s = U.sitzung(benutzer)
        verboten = VERBOTEN[benutzer]
        for baum in ("testbaum", "geheim"):
            for aktion, b, params in leseaufrufe(baum):
                a = s.get(aktion, b, **params)
                self.assertLess(a.status, 500, f"{benutzer or 'Gast'}: {aktion} {b} {params} -> {a}")
                # Jede Antwort muss zum Manifest passen (docs/openapi.json, neu erzeugen: tests/manifest.py).
                if a.json is not None and a.json.get("ok") is not False:
                    abweichung = manifest.pruefen(a.json, manifest.schema_fuer("get", aktion))
                    self.assertEqual([], abweichung[:5], f"Manifest veraltet? {aktion} {params} (tests/manifest.py)")
                # Die Suche nennt das Suchwort in der Antwort - das ist kein Leck.
                text = a.text.replace(json.dumps(params.get("q", "")), '""')
                # Place nennt den erfragten Namen nicht, wenn es den Ort nicht zu sehen gibt (not-found).
                for wort in verboten:
                    # Ortsnamen schuetzt webtrees nicht (Ortsliste, Ortsvorschlaege: SearchService::searchPlaces) -
                    # die Route Places nutzt dieselbe Funktion, nur fuer Bearbeiter. Kein Leck des Moduls.
                    if aktion == "Places" and "q" in params and wort == KONFIDENZ:
                        continue
                    self.assertNotIn(wort, text, f"LECK fuer {benutzer or 'Gast'}: {aktion} {b} {params} zeigt {wort}")

    def test_gast(self):
        self.pruefen(None)

    def test_mitglied(self):
        self.pruefen("mitglied")

    def test_bearbeiter(self):
        self.pruefen("bearbeiter")

    def test_verwalter_sieht_alles(self):
        """Gegenprobe: Die Markierungen stehen wirklich in den Daten - sonst waere der Lecktest wertlos."""
        s = U.sitzung("verwalter")
        self.assertIn(LEBEND, s.get("Individual", "testbaum", xref="I3").text)
        self.assertIn(KONFIDENZ, s.get("Individual", "testbaum", xref="I5").text)
        self.assertIn(BERUF, s.get("Individual", "testbaum", xref="I1").text)
        m = U.sitzung("mitglied")
        self.assertIn(LEBEND, m.get("Individual", "testbaum", xref="I3").text)
        self.assertIn(BERUF, m.get("Individual", "testbaum", xref="I1").text)

    def test_geheimer_baum_fuer_gast_unsichtbar(self):
        info = U.sitzung().info()
        self.assertEqual(["testbaum"], [t["name"] for t in info["trees"]])


class Paten(unittest.TestCase):
    """Stufe 19: Paten und Trauzeugen lesen (docs/spec-paten-quellen.md, Abschnitt 1, 3, 5, 6)."""

    def fakt(self, benutzer, xref, tag, art="Individual"):
        fakten = U.sitzung(benutzer).get(art, "testbaum", xref=xref).json["facts"]
        return next(f for f in fakten if f["tag"] == tag)

    def test_verlinkte_paten_mit_notiz_und_quelle(self):
        taufe = self.fakt("verwalter", "I1", "CHR")
        paten = {a["xref"]: a for a in taufe["associates"]}
        self.assertEqual(["I2", "I3", "I5", "I4"], list(paten), "drei 2 _ASSO, dann der 1 ASSO der Person (level1)")
        anna = paten["I2"]
        self.assertEqual(("Anna Offen", "F", "godparent", "godparent", False, False), (anna["name"], anna["sex"], anna["rela"], anna["role"], anna["private"], anna["level1"]))
        self.assertIn(anna["label"], ("Patin", "Godmother"), "Beschriftung nach dem Geschlecht der verknuepften Person")
        self.assertEqual(["Schwester der Mutter"], anna["notes"])
        self.assertEqual(("S1", "Taufen 1800, Nr. 4"), (anna["sources"][0]["xref"], anna["sources"][0]["page"]))
        # Gross-/Kleinschreibung und aeltere Werte: Godparent, godfather -> role godparent, rela bleibt roh
        self.assertEqual(("Godparent", "godparent"), (paten["I3"]["rela"], paten["I3"]["role"]))
        self.assertEqual(("godfather", "godparent"), (paten["I5"]["rela"], paten["I5"]["role"]))
        self.assertIn(paten["I5"]["label"], ("Pate", "Godfather"))
        # 1 ASSO an der Person: in der Taufe mit level1, der Fakt ASSO bleibt daneben stehen (aeltere Clients)
        self.assertEqual(("Godfather", "godparent", True), (paten["I4"]["rela"], paten["I4"]["role"], paten["I4"]["level1"]))
        asso = self.fakt("verwalter", "I1", "ASSO")
        self.assertEqual(["I4"], [a["xref"] for a in asso["associates"]])
        self.assertTrue(asso["associates"][0]["level1"])
        # Taufen-Datum und -Ort bleiben wie bisher
        self.assertEqual(1800, taufe["date"]["year"])

    def test_asso_ohne_taufe_bleibt_eigener_fakt(self):
        asso = self.fakt("verwalter", "I2", "ASSO")
        a = asso["associates"][0]
        self.assertEqual(("I1", "friend", "other", True), (a["xref"], a["rela"], a["role"], a["level1"]))
        self.assertIn(a["label"], ("Freund", "Friend"))

    def test_freie_paten_neu_und_alt(self):
        taufe = self.fakt("verwalter", "I1", "CHR")
        self.assertEqual(["associates", "note"], taufe["noteKinds"])
        self.assertEqual(2, len(taufe["notes"]), "die Patennotiz bleibt in notes")
        self.assertEqual([("godparent", "Friedrich Plate", "Anbauer zu Celle", "Friedrich Plate, Anbauer zu Celle"),
                          ("godparent", "Marie Offen", "Witwe", "Marie Offen, Witwe")],
                         [(p["role"], p["name"], p["detail"], p["text"]) for p in taufe["freeAssociates"]])
        heirat = self.fakt("verwalter", "F1", "MARR", "Family")
        self.assertEqual([("witness", "Fritz Krause", "Schneider", "Fritz Krause, Schneider"), ("witness", None, None, "Hans Müller, Bauer, Offenbach")],
                         [(p["role"], p["name"], p["detail"], p["text"]) for p in heirat["freeAssociates"]],
                         "erst _WITN, dann die Notiz; alte Form ohne ';': nicht raten")

    def test_godp_zeilen(self):
        # GEDCOM-L: "2 _GODP <Text>" unter CHR/BAPM, je Zeile eine Person (Name bis zum ersten Komma); mit ";" eine Liste
        taufe = self.fakt("verwalter", "I4", "CHR")
        self.assertEqual([], taufe["notes"])
        self.assertEqual([("godparent", "Hans Meier", "Bauer"), ("godparent", "Grete Meier", "Witwe"), ("godparent", "Peter Schulz und Paul Schulz", None)],
                         [(p["role"], p["name"], p["detail"]) for p in taufe["freeAssociates"]])
        self.assertEqual("Peter Schulz und Paul Schulz", taufe["freeAssociates"][2]["text"])

    def test_heiratsart_und_trauzeuge(self):
        heirat = self.fakt("verwalter", "F1", "MARR", "Family")
        self.assertEqual("CIVIL", heirat["type"], "type wie bisher: webtrees' kanonische Form (Fact::attribute)")
        self.assertIn(heirat["typeLabel"], ("Standesamtliche Heirat", "Civil marriage"))
        zeuge = heirat["associates"][0]
        self.assertEqual(("I4", "witness", "witness"), (zeuge["xref"], zeuge["rela"], zeuge["role"]))
        self.assertIn(zeuge["label"], ("Zeuge", "Witness"))
        self.assertIsNone(self.fakt("verwalter", "I1", "BIRT")["typeLabel"])

    def test_cont_und_conc(self):
        # Beim Import fuegt webtrees CONC schon zusammen ...
        heirat = self.fakt("verwalter", "F1", "MARR", "Family")
        self.assertIn("Trauung in der Stadtkirche\nzweite Zeile", heirat["notes"])
        # ... eine ausstehende Aenderung mit CONC liest die API selbst richtig (Bearbeiter sehen ihre eigenen)
        s = U.sitzung("bearbeiter")
        a = s.post("Fact", "testbaum", {"gedcom": "1 EVEN\n2 TYPE Conctest\n2 NOTE Erste Zei\n3 CONC le\n3 CONT zweite Zeile"}, xref="I4")
        self.assertEqual(True, a.json["ok"], a)
        even = next(f for f in s.get("Individual", "testbaum", xref="I4").json["facts"] if f.get("type") == "Conctest")
        self.assertEqual(["Erste Zeile\nzweite Zeile"], even["notes"])
        self.assertEqual("Conctest", even["typeLabel"])

    def test_gegenrichtung(self):
        wo = U.sitzung("verwalter").get("Individual", "testbaum", xref="I1").json["associatedIn"]
        self.assertEqual([("I5", "INDI", "CHR", "godparent", False), ("I3", "INDI", "CHR", "godparent", False), ("I2", "INDI", "ASSO", "other", True)],
                         [(e["record"], e["recordType"], e["tag"], e["role"], e["level1"]) for e in wo], "nach Datum, ohne Datum zuletzt")
        self.assertEqual(("godmother", 1990), (wo[1]["rela"], wo[1]["date"]["year"]))
        self.assertIn(wo[0]["label2"], ("Pate", "Godfather"), "godparent: Beschriftung nach dem Geschlecht des Paten selbst")
        self.assertIn(wo[1]["label2"], ("Patin", "Godmother"), "godmother: webtrees kennt den Wert, er bleibt wie er ist")
        # Karl: Pate ueber 1 ASSO (-> die Taufe von I1) und Trauzeuge der Eltern (Familie)
        wo = U.sitzung("verwalter").get("Individual", "testbaum", xref="I4").json["associatedIn"]
        self.assertEqual([("I1", "INDI", "CHR", True, 1800), ("F1", "FAM", "MARR", False, 1828)],
                         [(e["record"], e["recordType"], e["tag"], e["level1"], e["date"]["year"]) for e in wo])
        self.assertEqual([(None, None), ("I1", "I2")], [(e["husband"], e["wife"]) for e in wo], "Partner der Familie zum Oeffnen")
        self.assertIn("Theodor", wo[0]["name"])
        self.assertIn("Anna", wo[1]["name"])

    def test_datenschutz_paten(self):
        # Gast: lebender und vertraulicher Pate ganz weg (webtrees zeigt Gaesten nicht einmal den Namen -
        # SHOW_LIVING_NAMES steht auf "Mitglieder"); Gegenrichtung ohne verborgene Datensaetze
        taufe = self.fakt(None, "I1", "CHR")
        self.assertEqual(["I2", "I4"], [a["xref"] for a in taufe["associates"]])
        self.assertEqual([False, False], [a["private"] for a in taufe["associates"]])
        wo = U.sitzung().get("Individual", "testbaum", xref="I1").json["associatedIn"]
        self.assertEqual(["I2"], [e["record"] for e in wo])
        # Mitglied: sieht Lebende, den Vertraulichen nur als Verweis ohne Namen
        taufe = self.fakt("mitglied", "I1", "CHR")
        paten = {a["xref"]: a for a in taufe["associates"]}
        self.assertEqual(["I2", "I3", "I5", "I4"], list(paten))
        self.assertEqual("Lebhart Markerlebend", paten["I3"]["name"])
        self.assertEqual((None, None, True), (paten["I5"]["name"], paten["I5"]["sex"], paten["I5"]["private"]))
        wo = U.sitzung("mitglied").get("Individual", "testbaum", xref="I1").json["associatedIn"]
        self.assertEqual(["I3", "I2"], [e["record"] for e in wo])


class Orte(unittest.TestCase):
    """Stufe 21: Ortsliste und ein Ort (PlaceActions)."""

    def test_ortsliste(self):
        orte = {o["name"]: o for o in U.sitzung("verwalter").get("Places", "testbaum", list=1).json["places"]}
        self.assertEqual(["Bieber, Gelnhausen", "Bieber, Offenbach", "Hof Nr. 1, Offenbach", "Hof Nr. 2, Offenbach", "Markerkonfidenzort", "Markerlebendort", "Offenbach"], sorted(orte))
        off = orte["Offenbach"]
        self.assertEqual((3, 2, 0, "L1", "OFFACHJO40BC", "location", None), (off["events"], off["individuals"], off["families"], off["location"], off["gov"], off["coordSource"], off["type"]))
        self.assertAlmostEqual(50.1, off["lat"])
        self.assertIsNone(orte["Bieber, Offenbach"]["location"], "Blattname Bieber hat keinen _LOC")
        self.assertEqual("L2", orte["Markerkonfidenzort"]["location"], "Blattname eindeutig: _LOC gefunden")
        # Stufe 27: Hof mit Bewohner ueber 3 _LOC, Hof ohne Ereignis nur ueber die _LOC-Hierarchie
        self.assertEqual((1, 1, "L3", "Hof", 50.11), tuple(orte["Hof Nr. 1, Offenbach"][k] for k in ("events", "individuals", "location", "type", "lat")))
        self.assertEqual((0, 0, "L4", "farm", None), tuple(orte["Hof Nr. 2, Offenbach"][k] for k in ("events", "individuals", "location", "type", "lat")))
        gast = [o["name"] for o in U.sitzung().get("Places", "testbaum", list=1).json["places"]]
        self.assertEqual(["Bieber, Offenbach", "Hof Nr. 1, Offenbach", "Hof Nr. 2, Offenbach", "Offenbach"], gast, "Orte verborgener Personen fehlen (F2 hat ein lebendes Kind)")

    def test_ein_ort(self):
        o = U.sitzung("verwalter").get("Place", "testbaum", name="offenbach").json
        self.assertEqual(("Offenbach", ["Offenbach"], None, 3), (o["name"], o["levels"], o["parent"], o["events"]))
        self.assertEqual([("Bieber, Offenbach", None, None, None), ("Hof Nr. 1, Offenbach", "L3", "Hof", "24"), ("Hof Nr. 2, Offenbach", "L4", "farm", "24")],
                         [(c["name"], c["location"], c["type"], c["govType"]) for c in o["children"]], "Unterorte aus Ortstabelle und _LOC-Hierarchie, Art mit GOV-Typnummer")
        self.assertEqual({"birth": 1, "marriage": 0, "death": 0, "other": 2}, o["eventCounts"], "Geburt I1; Taufe I1 und Wohnort I2")
        self.assertEqual(["I2", "I1"], [p["xref"] for p in o["individuals"]], "nach Namen: Anna vor Theodor")
        self.assertEqual(["BIRT", "CHR"], [f["tag"] for f in o["individuals"][1]["facts"]])
        self.assertEqual(1800, o["individuals"][1]["facts"][0]["date"]["year"])
        loc = o["location"]
        self.assertEqual(("L1", "Offenbach", "OFFACHJO40BC", ["Stadt am Main"]), (loc["xref"], loc["name"], loc["gov"], loc["notes"]))
        self.assertEqual([(None, "https://de.wikipedia.org/wiki/Offenbach_am_Main"), ("S1", "Ortsbeschreibung")], [(q["xref"], q["page"] or q["title"]) for q in loc["sources"]])
        self.assertEqual(("63065", "Hessen", None), (loc["postalCode"], loc["region"], loc["country"]))
        self.assertAlmostEqual(8.766667, loc["lng"], places=5)
        b = U.sitzung("verwalter").get("Place", "testbaum", name="Bieber, Offenbach").json
        self.assertEqual(("Offenbach", ["I4"], None), (b["parent"], [p["xref"] for p in b["individuals"]], b["location"]))

    def test_hof(self):
        """Stufe 27: _LOC-Hierarchie (1 _LOC), Art (TYPE) und Ereignisse am Ort (EVEN) - Hoefe fuer Ortsfamilienbuecher."""
        h = U.sitzung("verwalter").get("Place", "testbaum", name="Hof Nr. 1, Offenbach").json
        self.assertEqual(("Offenbach", 1, ["I4"], "L3"), (h["parent"], h["events"], [p["xref"] for p in h["individuals"]], h["location"]["xref"]))
        loc = h["location"]
        # zwei Arten (GEDCOM-L: 1 TYPE mehrfach mit Datum): die letzte gilt, dazu die GOV-Typnummer aus 2 _GOVTYPE (24 = Hof)
        self.assertEqual(("Hof", "24"), (loc["type"], loc["govType"]))
        self.assertEqual([("L1", "Offenbach", "Offenbach", "POLI", 1800)], [(p["xref"], p["name"], p["fullName"], p["type"], p["date"]["year"]) for p in loc["parents"]])
        self.assertEqual(1, len(loc["events"]))
        e = loc["events"][0]
        self.assertEqual(("Brand", 1734, ["Scheune abgebrannt"], [("S1", "Ortschronik S. 12")]), (e["type"], e["date"]["year"], e["notes"], [(q["xref"], q["page"]) for q in e["sources"]]))
        self.assertAlmostEqual(50.11, h["lat"])
        # Hof ohne Ereignis: nur ueber die Hierarchie, Ereignisse 0, kein not-found
        h2 = U.sitzung().get("Place", "testbaum", name="hof nr. 2, offenbach").json
        self.assertEqual(("Hof Nr. 2, Offenbach", 0, [], "L4", "farm", "24", []), (h2["name"], h2["events"], h2["individuals"], h2["location"]["xref"], h2["location"]["type"], h2["location"]["govType"], h2["location"]["events"]))
        liste = {o["name"]: o for o in U.sitzung("verwalter").get("Places", "testbaum", list=1).json["places"]}
        self.assertEqual([("Hof", "24"), ("farm", "24"), (None, None)], [(liste[n]["type"], liste[n]["govType"]) for n in ("Hof Nr. 1, Offenbach", "Hof Nr. 2, Offenbach", "Bieber, Offenbach")])
        self.assertEqual([], h2["children"])

    def test_unbekannt_und_verborgen(self):
        self.assertEqual("not-found", U.sitzung().get("Place", "testbaum", name="Markerlebendort").json["error"])
        self.assertEqual("not-found", U.sitzung("bearbeiter").get("Place", "testbaum", name="Markerkonfidenzort").json["error"])
        self.assertEqual("not-found", U.sitzung("verwalter").get("Place", "testbaum", name="Gibtesnicht").json["error"])
        self.assertEqual("name-missing", U.sitzung("verwalter").get("Place", "testbaum").json["error"])

    def test_zz_ortsdaten_schreiben(self):
        """Stufe 22: POST Place - zuletzt, weil es den Testbaum aendert."""
        s = U.sitzung("admin")
        # vorhandener _LOC: nur die GOV-Kennung aendern, Notiz/Quelle/Koordinaten bleiben
        a = s.post("Place", "testbaum", {"name": "Offenbach", "gov": "OFFACHJO40BD"})
        self.assertEqual((True, "L1", 0), (a.json["ok"], a.json["xref"], a.json["linked"]), a)
        loc = s.get("Place", "testbaum", name="Offenbach").json["location"]
        self.assertEqual(("OFFACHJO40BD", ["Stadt am Main"], 2, 50.1), (loc["gov"], loc["notes"], len(loc["sources"]), loc["lat"]))
        # Art aendern: Unterzeilen der juengsten Art (2 _GOVTYPE, 2 SOUR) und die aeltere Art mit Datum bleiben (1.18.1)
        a = s.post("Place", "testbaum", {"name": "Hof Nr. 1, Offenbach", "type": "Bauernhof"})
        self.assertEqual((True, "L3"), (a.json["ok"], a.json["xref"]), a)
        g = umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = 'L3'")[0][0]
        self.assertIn("\n1 TYPE Mühle\n2 DATE TO 1799\n1 TYPE Bauernhof\n2 _GOVTYPE 24\n2 SOUR @S1@\n3 PAGE Ortschronik S. 3", g)
        self.assertEqual(1, g.count("1 TYPE Bauernhof"))
        loc = s.get("Place", "testbaum", name="Hof Nr. 1, Offenbach").json["location"]
        self.assertEqual(("Bauernhof", "24"), (loc["type"], loc["govType"]))
        # leer entfernt alle Arten; dann neu setzen ohne Unterzeilen
        s.post("Place", "testbaum", {"name": "Hof Nr. 1, Offenbach", "type": ""})
        self.assertNotIn("1 TYPE", umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = 'L3'")[0][0])
        s.post("Place", "testbaum", {"name": "Hof Nr. 1, Offenbach", "type": "Hof"})
        loc = s.get("Place", "testbaum", name="Hof Nr. 1, Offenbach").json["location"]
        self.assertEqual(("Hof", None), (loc["type"], loc["govType"]))
        # Koordinaten und Notiz ersetzen, auch in die Geografischen Daten (Admin)
        a = s.post("Place", "testbaum", {"name": "Offenbach", "lat": 50.104444, "lng": -8.766, "note": "Stadt am Main\nzweite Zeile", "mapData": True})
        self.assertEqual((True, True), (a.json["ok"], a.json["mapData"]), a)
        loc = s.get("Place", "testbaum", name="Offenbach").json["location"]
        self.assertEqual((50.104444, -8.766, ["Stadt am Main\nzweite Zeile"]), (loc["lat"], loc["lng"], loc["notes"]))
        self.assertIn("2 LONG W8.766", umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = 'L1'")[0][0])
        self.assertEqual([(50.104444, -8.766)], [(float(a), float(b)) for a, b in umgebung.sql("SELECT latitude, longitude FROM wt_place_location WHERE place = 'Offenbach'")])
        # Medien am _LOC: hochladen (Route Media mit der Kennung des _LOC), dann ueber die Liste loesen
        rumpf, art = manifest.multipart({"title": "Ortsansicht", "link": "true"}, ("ansicht.png", manifest.PNG, "image/png"))
        req = urllib.request.Request(s.url("/module/_api4webtrees_/Media/testbaum", xref="L1"), data=rumpf, method="POST")
        req.add_header("Content-Type", art); req.add_header("X-CSRF-TOKEN", s.csrf)
        m = s._senden(req).json
        self.assertEqual(True, m["ok"], m)
        self.assertEqual(["Ortsansicht"], [x["title"] for x in s.get("Place", "testbaum", name="Offenbach").json["location"]["media"]])
        orts_medium = s.get("Place", "testbaum", name="Offenbach").json["location"]["media"][0]
        self.assertEqual(("photo", "png"), (orts_medium["type"], orts_medium["format"].lower()))
        self.assertTrue(any("1" in i for i in orts_medium["info"]), orts_medium["info"])
        # Titel und Art aendern (Route MediaObject)
        self.assertEqual(True, s.post("MediaObject", "testbaum", {"title": "Ortsansicht 1926", "type": "card"}, xref=m["media"]).json["ok"])
        orts_medium = s.get("Place", "testbaum", name="Offenbach").json["location"]["media"][0]
        self.assertEqual(("Ortsansicht 1926", "card"), (orts_medium["title"], orts_medium["type"]))
        self.assertEqual("invalid-type", s.post("MediaObject", "testbaum", {"type": "foto"}, xref=m["media"]).json["error"])
        self.assertEqual("not-editable", U.sitzung("mitglied").post("MediaObject", "testbaum", {"title": "x"}, xref=m["media"]).json["error"])
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Offenbach", "media": []}).json["ok"])
        self.assertEqual([], s.get("Place", "testbaum", name="Offenbach").json["location"]["media"])
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Offenbach", "media": [m["media"]]}).json["ok"])
        self.assertEqual(1, len(s.get("Place", "testbaum", name="Offenbach").json["location"]["media"]))
        # allgemeiner Quellenverweis am _LOC (Route Citation ohne factId)
        a = s.post("Citation", "testbaum", {"source": "S1", "page": "Ortschronik S. 3"}, xref="L1")
        self.assertEqual(True, a.json["ok"], a)
        self.assertIn(("S1", "Ortschronik S. 3"), [(q["xref"], q["page"]) for q in s.get("Place", "testbaum", name="Offenbach").json["location"]["sources"]])
        # Kurzname als "2 ABBR" unter dem NAME, auch in der Ortsliste
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Offenbach", "shortName": "Offb."}).json["ok"])
        self.assertEqual("Offb.", s.get("Place", "testbaum", name="Offenbach").json["location"]["shortName"])
        self.assertIn("1 NAME Offenbach\n2 ABBR Offb.", umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = 'L1'")[0][0])
        self.assertEqual("Offb.", next(o for o in s.get("Places", "testbaum", list=1).json["places"] if o["name"] == "Offenbach")["shortName"])
        s.post("Place", "testbaum", {"name": "Offenbach", "shortName": ""})
        self.assertIsNone(s.get("Place", "testbaum", name="Offenbach").json["location"]["shortName"])
        # Postleitzahl (vorhandene Schreibweise POST bleibt), Region, Land
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Offenbach", "postalCode": "63067", "country": "Deutschland"}).json["ok"])
        loc = s.get("Place", "testbaum", name="Offenbach").json["location"]
        self.assertEqual(("63067", "Hessen", "Deutschland"), (loc["postalCode"], loc["region"], loc["country"]))
        ged = umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = 'L1'")[0][0]
        self.assertIn("1 POST 63067", ged); self.assertIn("1 _CTRY Deutschland", ged); self.assertNotIn("_POST", ged)
        # Koordinaten entfernen
        s.post("Place", "testbaum", {"name": "Offenbach", "lat": None, "lng": None})
        self.assertIsNone(s.get("Place", "testbaum", name="Offenbach").json["location"]["lat"])
        # neuer _LOC; Blattname "Bieber" gibt es zweimal -> Verweis an die Ereignisse
        a = s.post("Place", "testbaum", {"name": "Bieber, Offenbach", "gov": "BIEBERJO40BC"})
        self.assertEqual((True, 1), (a.json["ok"], a.json["linked"]), a)
        neu = a.json["xref"]
        self.assertIn("3 _LOC @" + neu + "@", umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = 'I4'")[0][0])
        o = s.get("Place", "testbaum", name="Bieber, Offenbach").json
        self.assertEqual((neu, "Bieber", "BIEBERJO40BC"), (o["location"]["xref"], o["location"]["name"], o["location"]["gov"]))
        self.assertIsNone(s.get("Place", "testbaum", name="Bieber, Gelnhausen").json["location"], "der andere Bieber bekommt ihn nicht")
        # Bearbeiter ohne Sofortfreigabe: ausstehender _LOC, zweites Speichern trifft denselben
        b = U.sitzung("bearbeiter")
        a = b.post("Place", "testbaum", {"name": "Bieber, Gelnhausen", "note": "Dorf im Kinzigtal"})
        self.assertEqual((True, True), (a.json["ok"], a.json["pending"]), a)
        a2 = b.post("Place", "testbaum", {"name": "Bieber, Gelnhausen", "gov": "BIEBERJO40AA"})
        self.assertEqual(a.json["xref"], a2.json["xref"], "kein zweiter _LOC vor der Freigabe")
        # Stufe 27: Art und uebergeordneter Ort; neuer Hof ohne Ereignis nur mit parent
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Hof Nr. 2, Offenbach", "type": "Haus", "note": "Altenteil"}).json["ok"])
        self.assertEqual("1 TYPE Haus", [z for z in umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = 'L4'")[0][0].split("\n") if z.startswith("1 TYPE")][0])
        self.assertEqual(("Haus", ["Altenteil"]), tuple(s.get("Place", "testbaum", name="Hof Nr. 2, Offenbach").json["location"][k] for k in ("type", "notes")))
        neu = s.post("Place", "testbaum", {"name": "Hof Nr. 3, Offenbach", "type": "Hof", "parent": "L1"})
        self.assertEqual((True, 201), (neu.json["ok"], neu.status), neu)
        h3 = s.get("Place", "testbaum", name="Hof Nr. 3, Offenbach").json
        self.assertEqual(("Hof", ["L1"], 0), (h3["location"]["type"], [p["xref"] for p in h3["location"]["parents"]], h3["events"]))
        self.assertIn("Hof Nr. 3, Offenbach", [c["name"] for c in s.get("Place", "testbaum", name="Offenbach").json["children"]])
        self.assertEqual("not-found", s.post("Place", "testbaum", {"name": "Hof Nr. 4, Offenbach", "type": "Hof"}).json["error"], "ohne parent kein Ort ohne Ereignis")
        self.assertEqual("invalid-parent", s.post("Place", "testbaum", {"name": "Hof Nr. 3, Offenbach", "parent": "L99"}).json["error"])
        self.assertEqual("invalid-parent", s.post("Place", "testbaum", {"name": "Hof Nr. 3, Offenbach", "parent": neu.json["xref"]}).json["error"], "nicht sein eigener Oberort")
        s.post("Place", "testbaum", {"name": "Hof Nr. 3, Offenbach", "parent": None})
        self.assertNotIn("1 _LOC", umgebung.sql("SELECT o_gedcom FROM wt_other WHERE o_id = ?", neu.json["xref"])[0][0])
        # Fehler und Rechte
        self.assertEqual("not-editable", U.sitzung("mitglied").post("Place", "testbaum", {"name": "Offenbach", "gov": "X"}).json["error"])
        self.assertEqual("invalid-coordinates", s.post("Place", "testbaum", {"name": "Offenbach", "lat": 95, "lng": 1}).json["error"])
        self.assertEqual("invalid-coordinates", s.post("Place", "testbaum", {"name": "Offenbach", "lat": 50}).json["error"])
        self.assertEqual("invalid-gov", s.post("Place", "testbaum", {"name": "Offenbach", "gov": "a b"}).json["error"])
        self.assertEqual("not-found", b.post("Place", "testbaum", {"name": "Markerkonfidenzort", "gov": "X"}).json["error"])

    def test_zz_stufe32_verweise_art_oberort(self):
        """Stufe 32: Verweis 3 _LOC beim Schreiben, linkEvents, unlinked, govType, nur der aktuelle Oberort aendert sich."""
        s = U.sitzung("admin")
        ged = lambda t, x: umgebung.sql(f"SELECT {t[0]}_gedcom FROM wt_{t} WHERE {t[0]}_id = ?", x)[0][0]  # noqa: E731
        # 1. Neue Person mit Geburt am Hof: den Verweis tragen schon andere Ereignisse dort (I4: 3 _LOC @L3@)
        p = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Verweis", "surname": "Probe", "sex": "M", "birthPlace": "Hof Nr. 1, Offenbach"}).json["xref"]
        self.assertIn("2 PLAC Hof Nr. 1, Offenbach\n3 _LOC @L3@", ged("individuals", p))
        # Hof ohne Bewohner: gefunden ueber den Namen entlang der _LOC-Hierarchie (L4 "Hof Nr. 2" unter L1 "Offenbach")
        s.post("Fact", "testbaum", {"tag": "RESI", "place": "Hof Nr. 2, Offenbach"}, xref=p)
        self.assertIn("2 PLAC Hof Nr. 2, Offenbach\n3 _LOC @L4@", ged("individuals", p))
        # unbekannter Ort: kein Verweis; geaenderter Ort: der alte Verweis faellt weg, der neue kommt
        s.post("Fact", "testbaum", {"tag": "OCCU", "value": "Bauer", "place": "Gibtsnicht, Offenbach"}, xref=p)
        self.assertIn("2 PLAC Gibtsnicht, Offenbach\n", ged("individuals", p) + "\n")
        self.assertNotIn("Gibtsnicht, Offenbach\n3 _LOC", ged("individuals", p))
        resi = next(f["id"] for f in s.get("Individual", "testbaum", xref=p).json["facts"] if f["tag"] == "RESI")
        s.post("Fact", "testbaum", {"factId": resi, "tag": "RESI", "place": "Hof Nr. 1, Offenbach"}, xref=p)
        g = ged("individuals", p)
        self.assertNotIn("_LOC @L4@", g)
        self.assertEqual(2, g.count("3 _LOC @L3@"))
        # 2. unlinked: Offenbach hat 3 Ereignisse, eines mit Verweis; linkEvents verknuepft den Rest, der _LOC bleibt unveraendert
        liste = {o["name"]: o for o in s.get("Places", "testbaum", list=1).json["places"]}
        self.assertEqual((3, 2), (liste["Offenbach"]["events"], liste["Offenbach"]["unlinked"]))
        self.assertEqual(0, liste["Hof Nr. 1, Offenbach"]["unlinked"])
        vorher = ged("other", "L1")
        a = s.post("Place", "testbaum", {"name": "Offenbach", "linkEvents": True}).json
        self.assertEqual((True, 2), (a["ok"], a["linked"]), a)
        self.assertEqual(vorher, ged("other", "L1"))
        self.assertEqual(0, s.get("Place", "testbaum", name="Offenbach").json["unlinked"])
        # 3. govType setzt und entfernt 2 _GOVTYPE der juengsten Art; ohne govType bleibt sie
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Hof Nr. 2, Offenbach", "type": "Mühle", "govType": "87"}).json["ok"])
        self.assertIn("1 TYPE Mühle\n2 _GOVTYPE 87", ged("other", "L4"))
        self.assertEqual(("Mühle", "87"), tuple(s.get("Place", "testbaum", name="Hof Nr. 2, Offenbach").json["location"][k] for k in ("type", "govType")))
        s.post("Place", "testbaum", {"name": "Hof Nr. 2, Offenbach", "govType": None})
        self.assertNotIn("_GOVTYPE", ged("other", "L4"))
        self.assertIn("1 TYPE Mühle", ged("other", "L4"))
        self.assertEqual("invalid-gov-type", s.post("Place", "testbaum", {"name": "Hof Nr. 2, Offenbach", "govType": "Hof"}).json["error"])
        s.post("Place", "testbaum", {"name": "Hof Nr. 2, Offenbach", "type": "Haus", "govType": "24"})
        self.assertIn("1 TYPE Haus\n2 _GOVTYPE 24", ged("other", "L4"))
        # 4. Oberort: nur der aktuelle politische Zeiger aendert sich, fruehere und kirchliche bleiben
        alt = ged("other", "L3")
        umgebung.sql("UPDATE wt_other SET o_gedcom = ? WHERE o_id = 'L3'", alt + "\n1 _LOC @L4@\n2 TYPE POLI\n2 DATE FROM 1700 TO 1799\n1 _LOC @L4@\n2 TYPE RELI")
        self.assertEqual(True, s.post("Place", "testbaum", {"name": "Hof Nr. 1, Offenbach", "parent": "L4"}).json["ok"])
        g = ged("other", "L3")
        self.assertIn("1 _LOC @L4@\n2 TYPE POLI\n2 DATE FROM 1800", g, "aktueller Zeiger: neue Kennung, Unterzeilen bleiben")
        self.assertIn("1 _LOC @L4@\n2 TYPE POLI\n2 DATE FROM 1700 TO 1799", g)
        self.assertIn("1 _LOC @L4@\n2 TYPE RELI", g)
        self.assertNotIn("_LOC @L1@", g)
        s.post("Place", "testbaum", {"name": "Hof Nr. 1, Offenbach", "parent": None})
        g = ged("other", "L3")
        self.assertEqual((2, 0), (g.count("1 _LOC @L4@"), g.count("2 DATE FROM 1800")), "leer loest nur den aktuellen")
        s.post("Place", "testbaum", {"name": "Hof Nr. 1, Offenbach", "parent": "L1"})
        self.assertIn("1 _LOC @L1@\n2 TYPE POLI", ged("other", "L3"), "neuer Zeiger bekommt 2 TYPE POLI")
        umgebung.sql("UPDATE wt_other SET o_gedcom = ? WHERE o_id = 'L3'", alt)
        # aufraeumen: die Probeperson weg, damit die folgenden Tests die alten Zahlen sehen
        self.assertEqual(True, s.post("DeleteRecord", "testbaum", {}, xref=p).json["ok"])

    def test_zzz_umbenennen_und_zusammenfuehren(self):
        """Stufe 23: POST PlaceRename - nach dem Schreibtest, weil es den Testbaum aendert."""
        s = U.sitzung("admin")
        # Vorschau: Offenbach -> Offenbach am Main, mit dem Ort darunter; aendert nichts
        v = s.post("PlaceRename", "testbaum", {"from": "Offenbach", "to": "Offenbach am Main", "preview": True}).json
        self.assertEqual((True, False, 3, 5, 2, 0, "L1"), (v["preview"], v["merge"], v["records"], v["events"], v["subPlaces"], v["skipped"], v["location"]["from"]), v)
        self.assertEqual(True, s.get("Place", "testbaum", name="Offenbach").json.get("ok", True))
        # Ausfuehren
        a = s.post("PlaceRename", "testbaum", {"from": "Offenbach", "to": "Offenbach am Main"}).json
        self.assertEqual((True, False, 5), (a["ok"], a["preview"], a["events"]), a)
        self.assertEqual("not-found", s.get("Place", "testbaum", name="Offenbach").json["error"])
        o = s.get("Place", "testbaum", name="Offenbach am Main").json
        self.assertEqual(("L1", "Offenbach am Main", 3), (o["location"]["xref"], o["location"]["name"], o["events"]))
        self.assertEqual(["Bieber, Offenbach am Main", "Hof Nr. 1, Offenbach am Main", "Hof Nr. 2, Offenbach am Main"], [c["name"] for c in o["children"]], "Hoefe wandern ueber die _LOC-Hierarchie mit")
        ged = umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = 'I1'")[0][0]
        self.assertEqual(2, ged.count("2 PLAC Offenbach am Main\n3 _LOC @L1@"), "Geburt und Taufe zeigen auf den _LOC")
        # Hin und zurueck: der alte Name steht noch in der Ortstabelle von webtrees, ist aber kein Zusammenfuehren
        self.assertEqual(False, s.post("PlaceRename", "testbaum", {"from": "Offenbach am Main", "to": "Offenbach", "preview": True}).json["merge"])
        # Bearbeiter: das gesperrte Ereignis (RESI von I2) bleibt
        v = U.sitzung("bearbeiter").post("PlaceRename", "testbaum", {"from": "Offenbach am Main", "to": "Offenbach a. M.", "preview": True}).json
        self.assertEqual((4, 1), (v["events"], v["skipped"]), v)
        # Zusammenfuehren: Bieber unter Offenbach am Main -> Bieber, Gelnhausen (beide mit _LOC, GOV weicht ab)
        von = s.get("Place", "testbaum", name="Bieber, Offenbach am Main").json["location"]["xref"]
        nach = s.get("Place", "testbaum", name="Bieber, Gelnhausen").json["location"]["xref"]
        v = s.post("PlaceRename", "testbaum", {"from": "Bieber, Offenbach am Main", "to": "Bieber, Gelnhausen", "preview": True}).json
        self.assertEqual((True, von, nach, ["gov"]), (v["merge"], v["location"]["from"], v["location"]["to"], v["location"]["conflicts"]), v)
        a = s.post("PlaceRename", "testbaum", {"from": "Bieber, Offenbach am Main", "to": "Bieber, Gelnhausen"}).json
        self.assertEqual(True, a["ok"], a)
        b = s.get("Place", "testbaum", name="Bieber, Gelnhausen").json
        self.assertEqual((nach, ["I4"], ["F2"]), (b["location"]["xref"], [p["xref"] for p in b["individuals"]], [f["xref"] for f in b["families"]]))
        self.assertIn("Dorf im Kinzigtal", b["location"]["notes"], "Notiz des Ziels bleibt")
        self.assertEqual([], umgebung.sql("SELECT o_id FROM wt_other WHERE o_id = ?", von), "der alte _LOC ist weg")
        self.assertIn("3 _LOC @" + nach + "@", umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = 'I4'")[0][0])
        # Fehler und Rechte
        self.assertEqual("not-editable", U.sitzung("mitglied").post("PlaceRename", "testbaum", {"from": "Offenbach am Main", "to": "X"}).json["error"])
        self.assertEqual("not-found", U.sitzung("bearbeiter").post("PlaceRename", "testbaum", {"from": "Markerkonfidenzort", "to": "X"}).json["error"])
        self.assertEqual("name-missing", s.post("PlaceRename", "testbaum", {"from": "Offenbach am Main", "to": " , "}).json["error"])

class Spitznamen(unittest.TestCase):
    """1.18.2: Spitzname im Namen behaelt die Anfuehrungszeichen, "2 NICK" kommt hinter die Vornamen."""

    def test_spitznamen(self):
        g = U.sitzung()
        name = lambda x: g.get("Individual", "testbaum", xref=x).json["person"]["name"]
        self.assertEqual('Johannes "Jan" de Vries', name("I20"))
        self.assertEqual('Pieter "Piet" Bakker', name("I21"))
        self.assertEqual('… "Klaas" Smit', name("I22"), "ohne Vornamen hinter den Auslassungspunkten")
        treffer = g.get("Individuals", "testbaum", q="Bakker").json["data"]
        self.assertEqual(['Pieter "Piet" Bakker'], [p["name"] for p in treffer])

class StufeDreissig(unittest.TestCase):
    """Stufe 30: Merkliste in den Favoriten, Forschungsaufgaben (_TODO), Reihenfolge, Aenderungsverlauf."""

    def test_merkliste_in_favoriten(self):
        m = U.sitzung("mitglied")
        uid = umgebung.sql("SELECT user_id FROM wt_user WHERE user_name = 'mitglied'")[0][0]
        gid = umgebung.sql("SELECT gedcom_id FROM wt_gedcom WHERE gedcom_name = 'testbaum'")[0][0]
        umgebung.sql("DELETE FROM wt_favorite WHERE gedcom_id = ? AND user_id = ?", gid, uid)
        # Alte Einstellung (bis Stufe 29) wird einmalig uebernommen
        umgebung.sql("INSERT OR REPLACE INTO wt_user_gedcom_setting (user_id, gedcom_id, setting_name, setting_value) VALUES (?, ?, 'api4webtrees_bookmarks', 'I1,I2')", uid, gid)
        b = m.get("Bookmarks", "testbaum").json
        self.assertEqual(["I1", "I2"], [p["xref"] for p in b["data"]], b)
        self.assertEqual("", umgebung.sql("SELECT setting_value FROM wt_user_gedcom_setting WHERE user_id = ? AND gedcom_id = ? AND setting_name = 'api4webtrees_bookmarks'", uid, gid)[0][0], "Einstellung geleert")
        self.assertEqual(2, umgebung.sql("SELECT COUNT(*) FROM wt_favorite WHERE gedcom_id = ? AND user_id = ? AND favorite_type = 'INDI'", gid, uid)[0][0])
        # Hinzufuegen mit Notiz, Entfernen
        b = m.post("Bookmarks", "testbaum", {"xref": "I4", "add": True, "note": "Taufe pruefen"}).json
        self.assertEqual(("I4", "Taufe pruefen"), (b["data"][-1]["xref"], b["data"][-1]["note"]))
        b = m.post("Bookmarks", "testbaum", {"xref": "I1", "add": False}).json
        self.assertEqual(["I2", "I4"], [p["xref"] for p in b["data"]])
        # Favoriten des Stammbaums: nur Verwalter
        self.assertEqual("not-manager", m.post("Bookmarks", "testbaum", {"xref": "I1", "add": True, "forTree": True}).json["error"])
        v = U.sitzung("verwalter")
        b = v.post("Bookmarks", "testbaum", {"xref": "I1", "add": True, "forTree": True, "note": "Stammvater"}).json
        self.assertEqual([("I1", "Stammvater")], [(p["xref"], p["note"]) for p in b["treeFavorites"]])
        self.assertEqual(["I1"], [p["xref"] for p in m.get("Bookmarks", "testbaum").json["treeFavorites"]], "Mitglied sieht die Stammbaum-Favoriten")
        v.post("Bookmarks", "testbaum", {"xref": "I1", "add": False, "forTree": True})
        self.assertEqual("not-logged-in", U.sitzung().get("Bookmarks", "testbaum").json["error"])

    def test_aufgaben(self):
        s = U.sitzung("admin")
        a = s.post("Task", "testbaum", {"text": "Taufe in Offenbach suchen", "note": "Kirchenbuch\nzweite Zeile"}, xref="I4").json
        self.assertEqual((True, False), (a["ok"], a["pending"]), a)
        t = s.get("Tasks", "testbaum").json
        meine = [x for x in t["tasks"] if x["record"] == "I4"]
        self.assertEqual(1, len(meine), t)
        self.assertEqual(("Taufe in Offenbach suchen", "admin", "Kirchenbuch\nzweite Zeile", a["factId"]), (meine[0]["text"], meine[0]["user"], meine[0]["note"], meine[0]["factId"]))
        self.assertIsNotNone(meine[0]["date"], "ohne Datum wird heute eingetragen")
        self.assertEqual(1, len(s.get("Individual", "testbaum", xref="I4").json["tasks"]), "auch in der Personenantwort")
        self.assertEqual(0, len([f for f in s.get("Individual", "testbaum", xref="I4").json["facts"] if f["tag"] == "_TODO"]), "nicht in den Ereignissen")
        # Aendern mit Wiedervorlage in der Zukunft: faellt aus ?open=1 heraus
        b = s.post("Task", "testbaum", {"factId": a["factId"], "text": "Taufe suchen", "date": "1 JAN 2099"}, xref="I4").json
        self.assertEqual(True, b["ok"], b)
        self.assertEqual([], [x for x in s.get("Tasks", "testbaum", open=1).json["tasks"] if x["record"] == "I4"])
        self.assertEqual(["Taufe suchen"], [x["text"] for x in s.get("Tasks", "testbaum").json["tasks"] if x["record"] == "I4"])
        # Fehler, dann erledigt = geloescht
        self.assertEqual("text-missing", s.post("Task", "testbaum", {"text": "  "}, xref="I4").json["error"])
        self.assertEqual("not-editable", U.sitzung("mitglied").post("Task", "testbaum", {"text": "x"}, xref="I4").json["error"])
        self.assertEqual(True, s.post("DeleteFact", "testbaum", {"factId": b["factId"]}, xref="I4").json["ok"])
        self.assertEqual([], [x for x in s.get("Tasks", "testbaum").json["tasks"] if x["record"] == "I4"])
        # Bearbeiter ohne Sofortfreigabe: ausstehend
        e = U.sitzung("bearbeiter").post("Task", "testbaum", {"text": "Wartet"}, xref="I4").json
        try:
            self.assertEqual(True, e["pending"], e)
        finally:
            s.post("Reject", "testbaum", {}, xref="I4")

    def test_reihenfolge(self):
        s = U.sitzung("admin")
        vorher = [c["xref"] for c in s.get("Family", "testbaum", xref="F1").json["children"]]
        self.assertEqual(["I4", "I5"], vorher)
        r = s.post("Reorder", "testbaum", {"type": "children", "order": ["I5"]}, xref="F1").json
        self.assertEqual((True, ["I5", "I4"]), (r["ok"], r["order"]), r)
        self.assertEqual(["I5", "I4"], [c["xref"] for c in s.get("Family", "testbaum", xref="F1").json["children"]])
        ged = umgebung.sql("SELECT f_gedcom FROM wt_families WHERE f_id = 'F1'")[0][0]
        self.assertIn("1 CHIL @I5@\n1 CHIL @I4@", ged, "nur die Reihenfolge der Zeilen aendert sich")
        self.assertIn("1 HUSB @I1@", ged)
        s.post("Reorder", "testbaum", {"type": "children", "order": ["I4", "I5"]}, xref="F1")
        self.assertEqual("invalid-value", s.post("Reorder", "testbaum", {"type": "children", "order": []}, xref="I1").json["error"], "Kinder nur an der Familie")
        self.assertIn(U.sitzung("mitglied").post("Reorder", "testbaum", {"type": "children", "order": []}, xref="F1").json["error"], ("not-editable", "private"))

    def test_aenderungsverlauf(self):
        s = U.sitzung("admin")
        s.post("Fact", "testbaum", {"tag": "OCCU", "value": "Verlaufsberuf"}, xref="I4")
        c = s.get("Changes", "testbaum", limit=5).json
        self.assertEqual(True, c["ok"], c)
        self.assertTrue(1 <= len(c["changes"]) <= 5)
        e = c["changes"][0]
        self.assertEqual(("I4", "INDI", "updated", False), (e["xref"], e["type"], e["action"], e["pending"]), e)
        self.assertTrue(e["time"].startswith("20") and e["user"])
        nur = s.get("Changes", "testbaum", xref="I4").json["changes"]
        self.assertTrue(all(x["xref"] == "I4" for x in nur) and nur)
        lc = s.get("Individual", "testbaum", xref="I4").json["lastChange"]
        self.assertTrue(lc and lc["time"].startswith("20"), lc)
        self.assertEqual("not-logged-in", U.sitzung().get("Changes", "testbaum").json["error"])
        self.assertTrue(len(U.sitzung("mitglied").get("Changes", "testbaum").json["changes"]) >= 1, "Mitglieder sehen den Verlauf sichtbarer Datensaetze")


class Zusammenfuehren(unittest.TestCase):
    """Stufe 29: POST Merge mit Vorschau, GET Merges, POST MergeUndo - nur Verwalter."""

    def person(self, s, **felder):
        a = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Doppel", "surname": "Testfall", "sex": "M", "dead": True} | felder)
        self.assertEqual(True, a.json["ok"], a)
        return a.json["xref"]

    def test_zzz_zusammenfuehren_und_rueckgaengig(self):
        s = U.sitzung("admin")
        p1 = self.person(s, birthDate="1850")
        p2 = self.person(s, birthDate="1850", deathDate="1900")
        s.post("Fact", "testbaum", {"tag": "OCCU", "value": "Schmied"}, xref=p1)
        s.post("Fact", "testbaum", {"tag": "OCCU", "value": "Schmied"}, xref=p2)
        s.post("Fact", "testbaum", {"tag": "RESI", "place": "Offenbach am Main"}, xref=p2)
        # Geburt von p2 um den Ort ergaenzen: die Geburt von p1 (nur Datum) ist darin enthalten
        geb2 = [f for f in s.get("Individual", "testbaum", xref=p2).json["facts"] if f["tag"] == "BIRT"][0]["id"]
        s.post("Fact", "testbaum", {"factId": geb2, "tag": "BIRT", "date": "1850", "place": "Offenbach am Main"}, xref=p2)
        # p2 ist Partner von I4 (neue Familie) und wird an einer Quelle zitiert
        fam = s.post("Link", "testbaum", {"individual": p2, "relation": "spouse", "relativeTo": "I4"}).json["family"]
        # Vorschau: alle Fakten von p1, von p2 nur Tod und Wohnort (Name, Geschlecht, Geburt, Beruf sind wortgleich)
        v = s.post("Merge", "testbaum", {"xref1": p1, "xref2": p2, "preview": True}).json
        self.assertEqual((True, True, p1, p2), (v["ok"], v["preview"], v["person1"]["xref"], v["person2"]["xref"]), v)
        # p1 hat "1 DEAT Y" (verstorben ohne Datum) - der Tod mit Datum von p2 ersetzt ihn
        # p1 hat "1 DEAT Y" (verstorben ohne Datum) und eine Geburt nur mit Datum - beides ist in p2 vollstaendiger enthalten
        self.assertEqual({"NAME": True, "SEX": True, "BIRT": False, "OCCU": True, "DEAT": False}, {f["tag"]: f["keep"] for f in v["facts1"]}, v["facts1"])
        behalten2 = {f["tag"]: f["keep"] for f in v["facts2"] if not f["link"]}
        self.assertEqual({"NAME": False, "SEX": False, "BIRT": True, "OCCU": False, "DEAT": True, "RESI": True}, behalten2, v["facts2"])
        self.assertTrue([f for f in v["facts1"] if f["tag"] == "BIRT"][0]["same"], "die Geburt von p1 gilt als enthalten")
        self.assertEqual([("FAMS", True)], [(f["tag"], f["keep"]) for f in v["facts2"] if f["link"]], "die Familie bleibt immer")
        self.assertEqual([fam], [l["xref"] for l in v["links"]], v["links"])
        self.assertEqual([], v["suggestions"])
        self.assertEqual(p2, s.get("Individual", "testbaum", xref=p2).json["person"]["xref"], "die Vorschau aendert nichts")
        # Zusammenfuehren mit dem Vorschlag
        a = s.post("Merge", "testbaum", {"xref1": p1, "xref2": p2}).json
        self.assertEqual((True, False, p1, p2, False), (a["ok"], a["preview"], a["xref"], a["removed"], a["pending"]), a)
        self.assertTrue(a["mergeId"])
        self.assertEqual("not-found", s.get("Individual", "testbaum", xref=p2).json["error"], "p2 ist weg")
        rest = s.get("Individual", "testbaum", xref=p1).json
        tags = sorted(f["tag"] for f in rest["facts"])
        self.assertEqual(["BIRT", "DEAT", "NAME", "OCCU", "RESI", "SEX"], tags, "Tod und Wohnort kamen dazu, nichts doppelt")
        self.assertEqual("Offenbach am Main", [f for f in rest["facts"] if f["tag"] == "BIRT"][0]["place"]["name"], "die vollstaendigere Geburt blieb")
        self.assertEqual([fam], [f["xref"] for f in rest["spouseFamilies"]], "p1 steht jetzt in der Familie")
        paar = s.get("Family", "testbaum", xref=fam).json
        self.assertEqual({"I4", p1}, {paar["husband"]["xref"], paar["wife"]["xref"]}, "die Familie zeigt auf p1")
        # Protokoll
        m = s.get("Merges", "testbaum").json["merges"]
        self.assertEqual((a["mergeId"], p1, p2, None), (m[0]["id"], m[0]["xref"], m[0]["removed"], m[0]["undone"]), m[0])
        self.assertEqual("not-manager", U.sitzung("bearbeiter").get("Merges", "testbaum").json["error"])
        # Rueckgaengig: Vorschau, dann wirklich
        u = s.post("MergeUndo", "testbaum", {"id": a["mergeId"], "preview": True}).json
        self.assertEqual((True, True, p2), (u["ok"], u["preview"], u["removed"]), u)
        u = s.post("MergeUndo", "testbaum", {"id": a["mergeId"]}).json
        self.assertEqual((True, False), (u["ok"], u["preview"]), u)
        zurueck = s.get("Individual", "testbaum", xref=p2).json
        self.assertEqual(["BIRT", "DEAT", "NAME", "OCCU", "RESI", "SEX"], sorted(f["tag"] for f in zurueck["facts"]), "p2 ist wieder da, mit allem")
        self.assertEqual([fam], [f["xref"] for f in zurueck["spouseFamilies"]])
        self.assertEqual(["BIRT", "DEAT", "NAME", "OCCU", "SEX"], sorted(f["tag"] for f in s.get("Individual", "testbaum", xref=p1).json["facts"]), "p1 wie vorher (mit DEAT Y)")
        paar = s.get("Family", "testbaum", xref=fam).json
        self.assertEqual({"I4", p2}, {paar["husband"]["xref"], paar["wife"]["xref"]}, "die Familie zeigt wieder auf p2")
        self.assertIsNotNone(s.get("Merges", "testbaum").json["merges"][0]["undone"])
        self.assertEqual("already-undone", s.post("MergeUndo", "testbaum", {"id": a["mergeId"]}).json["error"])
        # Noch einmal zusammenfuehren, dann p1 aendern: Rueckgaengig lehnt ab und aendert nichts
        b = s.post("Merge", "testbaum", {"xref1": p1, "xref2": p2}).json
        self.assertEqual(True, b["ok"], b)
        s.post("Fact", "testbaum", {"tag": "OCCU", "value": "Baecker"}, xref=p1)
        u = s.post("MergeUndo", "testbaum", {"id": b["mergeId"]}).json
        self.assertEqual(("changed-since", [p1]), (u["error"], [c["xref"] for c in u["changed"]]), u)
        self.assertEqual("not-found", s.get("Individual", "testbaum", xref=p2).json["error"], "nichts zurueckgedreht")
        # Fehler und Rechte
        self.assertEqual("same-record", s.post("Merge", "testbaum", {"xref1": p1, "xref2": p1}).json["error"])
        self.assertEqual("not-found", s.post("Merge", "testbaum", {"xref1": p1, "xref2": "I999"}).json["error"])
        self.assertEqual("xref-missing", s.post("Merge", "testbaum", {"xref1": p1}).json["error"])
        self.assertEqual("not-manager", U.sitzung("bearbeiter").post("Merge", "testbaum", {"xref1": "I1", "xref2": "I2", "preview": True}).json["error"])
        self.assertEqual("not-manager", U.sitzung("mitglied").post("MergeUndo", "testbaum", {"id": b["mergeId"]}).json["error"])
        self.assertEqual("not-found", s.post("MergeUndo", "testbaum", {"id": "gibtesnicht"}).json["error"])

    def test_zzz_verwalter_ohne_sofortfreigabe(self):
        """Ein Verwalter ohne auto_accept: Zusammenfuehren und Rueckgaengig sind ausstehende Aenderungen."""
        adm = U.sitzung("admin")
        p1 = self.person(adm, given="Ausstehend")
        p2 = self.person(adm, given="Ausstehend", deathDate="1901")
        v = U.sitzung("verwalter")
        try:
            a = v.post("Merge", "testbaum", {"xref1": p1, "xref2": p2}).json
            self.assertEqual((True, True), (a["ok"], a["pending"]), a)
            self.assertEqual(True, adm.get("Individual", "testbaum", xref=p2).json.get("ok", True), "p2 noch da (Loeschung wartet)")
            u = v.post("MergeUndo", "testbaum", {"id": a["mergeId"]}).json
            self.assertEqual(True, u["ok"], u)
            self.assertEqual([], umgebung.sql("SELECT change_id FROM wt_change WHERE status = 'pending' AND xref IN (?, ?)", p1, p2), "alles verworfen")
            self.assertEqual(True, adm.get("Individual", "testbaum", xref=p2).json.get("ok", True), "p2 bleibt")
            self.assertEqual("already-undone", v.post("MergeUndo", "testbaum", {"id": a["mergeId"]}).json["error"])
        finally:
            adm.post("Reject", "testbaum", {})


GEDCOML = ("_RUFNAME", "_UID", "_GOV", "_POST", "_MAIDENHEAD", "_NAME", "_GODP", "_ASSO", "_WITN", "_PRIM", "_TODO", "_CAT",
           "_PRTY", "_CDATE", "_RDATE", "_STAT", "_LOC", "_AIDN", "_DMGD", "_GOVTYPE")
GEDCOML_TABELLEN = {"I": ("wt_individuals", "i"), "F": ("wt_families", "f"), "S": ("wt_sources", "s"), "M": ("wt_media", "m")}


def gedcoml_ged(xref):
    """Der rohe Datensatz im Baum gedcoml (andere Baeume haben dieselben Kennungen)."""
    baum = umgebung.sql("SELECT gedcom_id FROM wt_gedcom WHERE gedcom_name = 'gedcoml'")[0][0]
    tabelle, p = GEDCOML_TABELLEN.get(xref[0], ("wt_other", "o"))
    return umgebung.sql(f"SELECT {p}_gedcom FROM {tabelle} WHERE {p}_id = ? AND {p}_file = ?", xref, baum)[0][0]


def gedcoml_pfade(gedcom):
    """Jede Zeile mit einem GEDCOM-L-Tag oder unter einem: Pfad aus den Tags der Eltern plus eigene Zeile (ohne Ebene).
    So bleibt der Vergleich gueltig, wenn sich Werte der Eltern aendern (anderes Datum am Ereignis, anderer Aufgabentext)."""
    pfade, stapel = set(), []
    for zeile in gedcom.split("\n"):
        teile = zeile.split(" ", 2)
        if len(teile) < 2 or not teile[0].isdigit():
            continue
        ebene, tag, wert = int(teile[0]), teile[1], teile[2] if len(teile) > 2 else ""
        del stapel[ebene:]
        stapel.append(tag)
        if ebene > 0 and any(t in GEDCOML for t in stapel):
            pfade.add("/".join(stapel[:-1]) + "|" + tag + " " + wert)
    return pfade


class GedcomL(unittest.TestCase):
    """Stufe 34: alle Angaben des GEDCOM-L-Addendums lesen - und nichts davon verlieren, wenn die App schreibt."""

    def test_a_lesen(self):
        s = U.sitzung("admin")
        p = s.get("Individual", "gedcoml", xref="I1").json
        self.assertEqual(["2B7A6D3E1F904C5A8E1D2C3B4A5F6E7D8C9B"], p["uids"])
        birt = next(f for f in p["facts"] if f["tag"] == "BIRT")
        self.assertEqual([{"tag": "PLAC:_GOV", "value": "HOFNR3JO53PC", "date": None},
                          {"tag": "PLAC:_POST", "value": "29553", "date": "FROM 1993"},
                          {"tag": "PLAC:_MAIDENHEAD", "value": "JO53PC", "date": None},
                          {"tag": "ADDR:_NAME", "value": "Familie Mohwinkel", "date": None}], birt["extras"])
        self.assertEqual([], next(f for f in p["facts"] if f["tag"] == "OCCU")["extras"])
        self.assertEqual(True, p["media"][0]["highlighted"])
        aufgabe = p["tasks"][0]
        self.assertEqual(("Taufeintrag im Kirchenbuch prüfen", "Kirchenbuch", "1", "0", "open", "Eintrag gefunden auf Seite 19"),
                         (aufgabe["description"], aufgabe["category"], aufgabe["priority"], aufgabe["taskType"], aufgabe["status"], aufgabe["solution"]))
        self.assertEqual(("R1", "Pfarramt Bienenbüttel"), (aufgabe["repository"]["xref"], aufgabe["repository"]["name"]))
        self.assertEqual((2026, 2026), (aufgabe["completed"]["year"], aufgabe["reminder"]["year"]))
        fam = s.get("Family", "gedcoml", xref="F1").json
        self.assertEqual(["AAAABBBBCCCCDDDDEEEEFFFF00001111"], fam["uids"])
        # _STAT kommt als Fakt mit uebersetztem Wert (wie webtrees ihn zeigt)
        self.assertNotEqual("", next(f for f in fam["facts"] if f["tag"] == "_STAT")["value"])
        self.assertEqual(("6", None), (fam["tasks"][0]["priority"], fam["tasks"][0]["description"]))
        loc = s.get("Place", "gedcoml", name="Bienenbüttel").json["location"]
        self.assertEqual([{"value": "Bienenbüttel", "date": "FROM 1900", "lang": None, "abbr": "Bbüttel"},
                          {"value": "Bienenbuettel", "date": "BEF 1900", "lang": "German", "abbr": None}], loc["names"])
        self.assertEqual([{"value": "Dorf", "date": "BEF 1972", "govType": "55"},
                          {"value": "Gemeinde", "date": "FROM 1972", "govType": "54"}], loc["types"])
        self.assertEqual(("Gemeinde", "54"), (loc["type"], loc["govType"]))
        self.assertEqual([{"value": "03360004", "date": "FROM 1972", "type": "AGS"}], loc["adminIds"])
        self.assertEqual(["1200", "6500"], [d["value"] for d in loc["demographics"]])
        self.assertEqual([{"value": "29553", "date": "FROM 1993"}], loc["postalCodes"])
        self.assertEqual(("JO53PC", "evangelisch-lutherisch", ["8888DDDD8888DDDD8888DDDD8888DDDD"]),
                         (loc["maidenhead"], loc["religion"], loc["uids"]))
        # Export traegt die Zusatzangaben ebenso
        ex = next(i for i in s.get("Export", "gedcoml", page=1).json["individuals"] if i["xref"] == "I1")
        self.assertEqual(4, len(next(f for f in ex["facts"] if f["tag"] == "BIRT")["extras"]))

    def test_b_schreiben_verliert_nichts(self):
        s = U.sitzung("admin")
        xrefs = ["I1", "I2", "F1", "M1", "M2", "S1", "R1", "N1", "L1", "L2"]
        vorher = {x: gedcoml_pfade(gedcoml_ged(x)) for x in xrefs}
        self.assertGreater(sum(len(v) for v in vorher.values()), 60)
        fakten = {f["tag"]: f for f in s.get("Individual", "gedcoml", xref="I1").json["facts"]}
        ok = lambda r: self.assertEqual(True, r.json.get("ok"), r)
        # Ereignisse aendern: Datum, Beruf, Name
        ok(s.post("Fact", "gedcoml", {"factId": fakten["BIRT"]["id"], "tag": "BIRT", "date": "4 MAR 1850",
                                      "place": "Hof Nr. 3, Bienenbüttel"}, xref="I1"))
        ok(s.post("Fact", "gedcoml", {"factId": fakten["OCCU"]["id"], "tag": "OCCU", "value": "Hofbauer"}, xref="I1"))
        ok(s.post("Fact", "gedcoml", {"factId": fakten["NAME"]["id"], "value": "Johann Hinrich /Mohwinkel/"}, xref="I1"))
        # Paten unveraendert zurueckschreiben, wie der Dialog es tut, wenn man nur speichert
        bapm = next(f for f in s.get("Individual", "gedcoml", xref="I1").json["facts"] if f["tag"] == "BAPM")
        ok(s.post("Association", "gedcoml", {"factId": bapm["id"],
            # wie der Paten-Dialog: rela nur bei "other", die Notiz nur, wenn sie geaendert wurde
            "linked": [{"xref": a["xref"], "role": a["role"], **({"rela": a["rela"]} if a["role"] == "other" else {})} for a in bapm["associates"]],
            "free": [{"text": a["text"], "role": a["role"]} for a in bapm["freeAssociates"]]}, xref="I1"))
        # Quelle zitieren, Aufgabe aendern, Medium umbenennen
        birt = next(f for f in s.get("Individual", "gedcoml", xref="I1").json["facts"] if f["tag"] == "BIRT")
        ok(s.post("Citation", "gedcoml", {"factId": birt["id"], "source": "S1", "page": "S. 19"}, xref="I1"))
        aufgabe = s.get("Individual", "gedcoml", xref="I1").json["tasks"][0]
        ok(s.post("Task", "gedcoml", {"factId": aufgabe["factId"], "text": "Taufeintrag und Paten prüfen", "date": "2 OCT 2026",
                                      "note": "Pfarramt angeschrieben"}, xref="I1"))
        ok(s.post("MediaObject", "gedcoml", {"title": "Hof Mohwinkel um 1900"}, xref="M1"))
        # Familie: Heirat und Aufgabe
        fam = s.get("Family", "gedcoml", xref="F1").json
        marr = next(f for f in fam["facts"] if f["tag"] == "MARR")
        ok(s.post("Fact", "gedcoml", {"factId": marr["id"], "tag": "MARR", "date": "12 MAY 1875", "place": "Bienenbüttel"}, xref="F1"))
        ok(s.post("Task", "gedcoml", {"factId": fam["tasks"][0]["factId"], "text": "Heiratseintrag suchen (Standesamt)"}, xref="F1"))
        # Quelle, Archiv, Orte
        ok(s.post("Source", "gedcoml", {"author": "Pfarramt"}, xref="S1"))
        ok(s.post("Repository", "gedcoml", {"name": "Pfarramt Bienenbüttel (Archiv)"}, xref="R1"))
        ok(s.post("Place", "gedcoml", {"name": "Bienenbüttel", "lat": 53.15, "lng": 10.49, "type": "Samtgemeinde"}))
        ok(s.post("Place", "gedcoml", {"name": "Hof Nr. 3, Bienenbüttel", "type": "Bauernhof"}))
        # Umbenennen hin und zurueck
        ok(s.post("PlaceRename", "gedcoml", {"from": "Hof Nr. 3, Bienenbüttel", "to": "Hof Nr. 3a, Bienenbüttel"}))
        ok(s.post("PlaceRename", "gedcoml", {"from": "Hof Nr. 3a, Bienenbüttel", "to": "Hof Nr. 3, Bienenbüttel"}))
        # Text, Datum und Notiz der Aufgaben hat der Test selbst geaendert
        geaendert = re.compile(r"^@[^@]+@(\|_TODO |/_TODO\|(DATE|NOTE) )")
        verloren = {x: sorted(p for p in vorher[x] - gedcoml_pfade(gedcoml_ged(x)) if not geaendert.match(p)) for x in xrefs}
        self.assertEqual({x: [] for x in xrefs}, verloren)

    def test_c_schreiben_stufe35(self):
        """Stufe 35: Familienstand ueber Fact (roher Wert in gedcomValue), Hauptbild mit _PRIM, Aufgabenfelder."""
        s = U.sitzung("admin")
        ok = lambda r: self.assertEqual(True, r.json.get("ok"), r)
        stat = next(f for f in s.get("Family", "gedcoml", xref="F1").json["facts"] if f["tag"] == "_STAT")
        self.assertEqual("NOT MARRIED", stat["gedcomValue"])
        ok(s.post("Fact", "gedcoml", {"factId": stat["id"], "value": "NEVER MARRIED"}, xref="F1"))
        self.assertIn("\n1 _STAT NEVER MARRIED", gedcoml_ged("F1"))
        self.assertIsNone(next(f for f in s.get("Family", "gedcoml", xref="F1").json["facts"] if f["tag"] == "MARR")["gedcomValue"])
        # Hauptbild: zweites Medium anlegen und zum Hauptbild machen - _PRIM wandert mit
        medien = s.get("Individual", "gedcoml", xref="I1").json["media"]
        self.assertEqual([("M1", True), ("M2", False)], [(m["xref"], m["highlighted"]) for m in medien])
        zweit = next(m for m in medien if m["xref"] == "M2")
        ok(s.post("PrimaryMedia", "gedcoml", {"media": zweit["xref"]}, xref="I1"))
        g = gedcoml_ged("I1")
        self.assertIn("1 OBJE @" + zweit["xref"] + "@\n2 _PRIM Y", g)
        self.assertNotIn("1 OBJE @M1@\n2 _PRIM Y", g)
        medien = s.get("Individual", "gedcoml", xref="I1").json["media"]
        self.assertEqual([("M2", True), ("M1", True)], [(m["xref"], m["highlighted"]) for m in medien])  # M1 traegt _PRIM noch an der Datei
        # Aufgabe mit allen Feldern anlegen, dann nur den Status aendern
        a = s.post("Task", "gedcoml", {"text": "Grabstein fotografieren", "description": "Friedhof Bienenbüttel\nzweite Reihe",
            "category": "Friedhof", "priority": "3", "taskType": "2", "status": "open", "reminder": "1 MAY 2027", "repository": "R1"}, xref="I2").json
        self.assertEqual(True, a.get("ok"), a)
        g = gedcoml_ged("I2")
        for zeile in ("2 DESC Friedhof Bienenbüttel\n3 CONT zweite Reihe", "2 _CAT Friedhof", "2 _PRTY 3", "2 TYPE 2", "2 STAT open", "2 _RDATE 1 MAY 2027", "2 REPO @R1@"):
            self.assertIn(zeile, g)
        t = s.get("Individual", "gedcoml", xref="I2").json["tasks"][0]
        ok(s.post("Task", "gedcoml", {"factId": t["factId"], "text": t["text"], "status": "completed", "completed": "2 MAY 2027",
                                      "solution": "Foto im Archiv", "reminder": ""}, xref="I2"))
        t = s.get("Individual", "gedcoml", xref="I2").json["tasks"][0]
        self.assertEqual(("completed", 2027, "Foto im Archiv", None, "Friedhof", "3"),
                         (t["status"], t["completed"]["year"], t["solution"], t["reminder"], t["category"], t["priority"]))


class Ausschnitt(unittest.TestCase):
    """Stufe 38: Ausschnitt eines Medienverweises (GEDCOM 7 CROP) - jede Person ihr Teil desselben Bildes."""

    def test_ausschnitt(self):
        s = U.sitzung("admin")
        ausschnitt = {"top": 10, "left": 20, "height": 30, "width": 40}

        def medien():
            return {m["xref"]: m["crop"] for m in s.get("Individual", "gedcoml", xref="I2").json["media"]}

        # Noch nicht verknuepft: MediaCrop verknuepft mit Ausschnitt
        self.assertNotIn("M1", medien())
        a = s.post("MediaCrop", "gedcoml", {"media": "M1", "crop": ausschnitt}, xref="I2").json
        self.assertEqual((True, "M1"), (a.get("ok"), a.get("media")), a)
        self.assertIn("1 OBJE @M1@\n2 CROP\n3 TOP 10\n3 LEFT 20\n3 HEIGHT 30\n3 WIDTH 40", gedcoml_ged("I2"))
        self.assertEqual(ausschnitt, medien()["M1"])
        # Aendern ersetzt den Ausschnitt, die Verknuepfung bleibt eine
        ausschnitt = {"top": 0, "left": 5, "height": 100, "width": 80}
        self.assertEqual(True, s.post("MediaCrop", "gedcoml", {"media": "M1", "crop": ausschnitt}, xref="I2").json.get("ok"))
        self.assertEqual(1, gedcoml_ged("I2").count("1 OBJE @M1@"))
        self.assertEqual(1, gedcoml_ged("I2").count("2 CROP"))
        self.assertEqual(ausschnitt, medien()["M1"])
        # Die andere Person mit demselben Bild behaelt ihr Bild ohne Ausschnitt
        self.assertIsNone(next(m for m in s.get("Individual", "gedcoml", xref="I1").json["media"] if m["xref"] == "M1")["crop"])
        # Ungueltig
        for falsch in ({"top": -1, "left": 0, "height": 1, "width": 1}, {"top": 0, "left": 0, "height": 0, "width": 1},
                       {"top": 0, "left": 0, "height": 1}, {"top": "1", "left": 0, "height": 1, "width": 1}, "10,20,30,40"):
            r = s.post("MediaCrop", "gedcoml", {"media": "M1", "crop": falsch}, xref="I2")
            self.assertEqual((400, "invalid-crop"), (r.json.get("status"), r.json.get("error")), falsch)
        self.assertEqual("media-not-found", s.post("MediaCrop", "gedcoml", {"media": "M99", "crop": None}, xref="I2").json.get("error"))
        # webtrees kennt CROP als Tag des Moduls: das Bearbeitungsformular markiert es nicht als unbekannt (rot)
        fakt = next(m for m in s.get("Individual", "gedcoml", xref="I2").json["media"] if m["xref"] == "M1")["factId"]
        with s.oeffner.open(s.url(f"/tree/gedcoml/edit-fact/I2/{fakt}"), timeout=60) as r:
            formular = r.read().decode("utf-8", "replace")
        self.assertIn("CROP", formular)
        self.assertNotIn('<span class="error"', formular)
        # null entfernt den Ausschnitt, die Verknuepfung bleibt
        self.assertEqual(True, s.post("MediaCrop", "gedcoml", {"media": "M1", "crop": None}, xref="I2").json.get("ok"))
        self.assertNotIn("CROP", gedcoml_ged("I2"))
        self.assertIsNone(medien()["M1"])
        # Gaeste und Mitglieder duerfen nicht
        for rolle in (None, "mitglied"):
            r = U.sitzung(rolle).post("MediaCrop", "gedcoml", {"media": "M1", "crop": None}, xref="I2")
            self.assertNotEqual(True, (r.json or {}).get("ok"), rolle)
        s.post("UnlinkMedia", "gedcoml", {"media": "M1"}, xref="I2")

    def test_medienobjekt(self):
        """Stufe 40: wer auf einem Bild markiert ist - alle verknuepften Personen mit ihrem Ausschnitt."""
        s = U.sitzung("admin")
        ausschnitt = {"top": 1, "left": 2, "height": 3, "width": 4}
        s.post("MediaCrop", "gedcoml", {"media": "M1", "crop": ausschnitt}, xref="I2")
        m = s.get("MediaObject", "gedcoml", xref="M1").json
        self.assertEqual(("M1", True), (m["xref"], m["canEdit"]))
        self.assertTrue(m["files"])
        personen = {p["xref"]: p["crop"] for p in m["people"]}
        self.assertEqual(ausschnitt, personen["I2"])
        self.assertIsNone(personen["I1"])
        self.assertTrue(all(p["factId"] for p in m["people"]))
        self.assertEqual("not-found", s.get("MediaObject", "gedcoml", xref="M99").json.get("error"))
        s.post("UnlinkMedia", "gedcoml", {"media": "M1"}, xref="I2")

    def test_portrait(self):
        """Stufe 39: thumb zeigt den Ausschnitt des Hauptfotos (Route Portrait)."""
        import struct
        import zlib
        s = U.sitzung("admin")
        # 100 x 60: links rot, rechts blau
        zeile = b"\x00" + (b"\xff\x00\x00" * 50) + (b"\x00\x00\xff" * 50)
        def block(art, daten):
            return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xffffffff)
        png = (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", 100, 60, 8, 2, 0, 0, 0))
               + block(b"IDAT", zlib.compress(zeile * 60)) + block(b"IEND", b""))
        rumpf, art = manifest.multipart({"title": "Rot und Blau"}, ("rotblau.png", png, "image/png"))
        req = urllib.request.Request(s.url("/module/_api4webtrees_/Media/gedcoml", xref="I2"), data=rumpf, method="POST")
        req.add_header("Content-Type", art)
        req.add_header("X-CSRF-TOKEN", s.csrf)
        m = s._senden(req).json["media"]
        self.assertEqual(True, s.post("PrimaryMedia", "gedcoml", {"media": m}, xref="I2").json.get("ok"))

        def thumb():
            return s.get("Individual", "gedcoml", xref="I2").json["person"]["thumb"]

        def bild(url):
            with s.oeffner.open(url, timeout=60) as r:
                return r.headers.get("Content-Type"), r.read()

        self.assertNotIn("Portrait", thumb())
        s.post("MediaCrop", "gedcoml", {"media": m, "crop": {"top": 0, "left": 0, "height": 60, "width": 50}}, xref="I2")
        rot_url = thumb()
        self.assertIn("Portrait", rot_url)
        art, rot = bild(rot_url)
        self.assertEqual("image/png", art)
        self.assertEqual((200, 200), struct.unpack(">II", rot[16:24]))
        s.post("MediaCrop", "gedcoml", {"media": m, "crop": {"top": 0, "left": 50, "height": 60, "width": 50}}, xref="I2")
        blau_url = thumb()
        self.assertNotEqual(rot_url, blau_url, "neuer Ausschnitt, neue Adresse")
        self.assertNotEqual(rot, bild(blau_url)[1])
        # Ueber den Rand: endet am Rand statt zu scheitern
        s.post("MediaCrop", "gedcoml", {"media": m, "crop": {"top": 50, "left": 90, "height": 500, "width": 500}}, xref="I2")
        self.assertEqual("image/png", bild(thumb())[0])
        # Ohne Ausschnitt wieder die Vorschau von webtrees; Portrait meldet no-portrait
        s.post("MediaCrop", "gedcoml", {"media": m, "crop": None}, xref="I2")
        self.assertNotIn("Portrait", thumb())
        self.assertEqual("no-portrait", s.get("Portrait", "gedcoml", xref="I2").json.get("error"))
        s.post("UnlinkMedia", "gedcoml", {"media": m}, xref="I2")


    def test_drehen(self):
        """Stufe n: Bild drehen - die Datei dreht sich, die Ausschnitte aller Verweise drehen mit."""
        import struct
        import zlib
        s = U.sitzung("admin")
        # 100 x 60: links rot, rechts blau
        zeile = b"\x00" + (b"\xff\x00\x00" * 50) + (b"\x00\x00\xff" * 50)
        def block(art, daten):
            return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xffffffff)
        png = (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", 100, 60, 8, 2, 0, 0, 0))
               + block(b"IDAT", zlib.compress(zeile * 60)) + block(b"IEND", b""))
        rumpf, art = manifest.multipart({"title": "Rot und Blau"}, ("drehen.png", png, "image/png"))
        req = urllib.request.Request(s.url("/module/_api4webtrees_/Media/gedcoml", xref="I2"), data=rumpf, method="POST")
        req.add_header("Content-Type", art)
        req.add_header("X-CSRF-TOKEN", s.csrf)
        m = s._senden(req).json["media"]
        # Rahmen um die linke (rote) Haelfte
        s.post("MediaCrop", "gedcoml", {"media": m, "crop": {"top": 0, "left": 0, "height": 60, "width": 50}}, xref="I2")

        for falsch in (0, 4, "1", None):
            r = s.post("MediaRotate", "gedcoml", {"quarterTurns": falsch}, xref=m)
            self.assertEqual("invalid-turns", r.json.get("error"), falsch)
        self.assertNotEqual(True, (U.sitzung(None).post("MediaRotate", "gedcoml", {"quarterTurns": 1}, xref=m).json or {}).get("ok"))

        a = s.post("MediaRotate", "gedcoml", {"quarterTurns": 1}, xref=m).json
        self.assertEqual((True, 1), (a.get("ok"), a.get("turnedCrops")), a)
        # Im Uhrzeigersinn: 60 x 100, die rote linke Haelfte liegt jetzt oben - und der Rahmen mit ihr
        pfad = s.get("MediaObject", "gedcoml", xref=m).json["files"][0]["path"]
        datei = os.path.join(umgebung.WT, "data", "media", pfad)
        bild = subprocess.run(["php", "-r", f"$i = imagecreatefrompng('{datei}'); echo imagesx($i), ' ', imagesy($i), ' ', "
                               "dechex(imagecolorat($i, 30, 10)), ' ', dechex(imagecolorat($i, 30, 90));"],
                              capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(["60", "100", "ff0000", "ff"], bild)
        rahmen = next(x for x in s.get("Individual", "gedcoml", xref="I2").json["media"] if x["xref"] == m)["crop"]
        self.assertEqual({"top": 0, "left": 0, "height": 50, "width": 60}, rahmen)
        s.post("UnlinkMedia", "gedcoml", {"media": m}, xref="I2")

    def test_ausschnitt_drehen(self):
        """Stufe n: Ausschnitte drehen rechnerisch richtig, auch mit fehlenden Angaben und mehreren Vierteldrehungen."""
        dreh = api_modul_turned_crop
        self.assertEqual({"TOP": 0, "LEFT": 0, "HEIGHT": 50, "WIDTH": 60}, dreh({"top": 0, "left": 0, "height": 60, "width": 50}, 100, 60, 1))
        self.assertEqual({"TOP": 0, "LEFT": 50, "HEIGHT": 60, "WIDTH": 50}, dreh({"top": 0, "left": 0, "height": 60, "width": 50}, 100, 60, 2))
        self.assertEqual({"TOP": 50, "LEFT": 0, "HEIGHT": 50, "WIDTH": 60}, dreh({"top": 0, "left": 0, "height": 60, "width": 50}, 100, 60, 3))
        self.assertEqual({"TOP": 0, "LEFT": 0, "HEIGHT": 100, "WIDTH": 60}, dreh({"top": None, "left": None, "height": None, "width": None}, 100, 60, 1))

class Zugang(unittest.TestCase):
    """Stufe 36: wer ein Ereignis, eine Person, eine Familie sehen darf - fuer Buecher, die weitergegeben werden (Issue 21)."""

    def test_zugang(self):
        s = U.sitzung("admin")
        i1 = s.get("Individual", "testbaum", xref="I1").json
        self.assertEqual("visitor", i1["person"]["access"])
        berufe = {f["value"]: f["access"] for f in i1["facts"] if f["tag"] == "OCCU"}
        # andere Tests legen weitere Berufe an I1 an - geprueft werden die beiden aus dem Testbaum
        self.assertEqual(("visitor", "member"), (berufe["Schmied"], berufe["Markerberuf"]))
        # RESN locked sperrt nur das Bearbeiten, nicht das Ansehen
        self.assertEqual("visitor", next(f for f in i1["facts"] if f["tag"] == "RELI")["access"])
        self.assertEqual("visitor", next(f for f in i1["facts"] if f["tag"] == "NAME")["access"])
        self.assertIn("OCCU", i1["person"]["restricted"])
        # Paten tragen ihre Sichtbarkeit: an der Taufe von I1 ist der lebende I3 Pate, I5 vertraulich
        paten = {a["xref"]: a["access"] for f in i1["facts"] if f["tag"] == "CHR" for a in f["associates"]}
        self.assertEqual(("member", "manager"), (paten["I3"], paten["I5"]))
        self.assertEqual("manager", s.get("Individual", "testbaum", xref="I5").json["person"]["access"])
        # I3 lebt; der Testbaum verbirgt Lebende vor Besuchern
        lebend = s.get("Individual", "testbaum", xref="I3").json["person"]
        self.assertEqual(("member", "member"), (lebend["access"], lebend["nameAccess"]))
        self.assertEqual("visitor", i1["person"]["nameAccess"])
        # webtrees verbirgt eine Familie, sobald ein Mitglied privat ist: F1 hat das vertrauliche Kind I5
        self.assertEqual("manager", s.get("Family", "testbaum", xref="F1").json["access"])
        # F2 hat das lebende Kind I3: fuer Besucher verborgen, fuer Mitglieder sichtbar
        self.assertEqual("member", s.get("Family", "testbaum", xref="F2").json["access"])
        ex = s.get("Export", "testbaum", page=1).json
        self.assertTrue(all("access" in f for f in ex["families"]))
        self.assertIn("member", [f["access"] for f in next(i for i in ex["individuals"] if i["xref"] == "I1")["facts"] if f["tag"] == "OCCU"])


class Schreiben(unittest.TestCase):
    def fakten(self, s, xref):
        return s.get("Individual", "testbaum", xref=xref).json["facts"]

    def beruf_anlegen(self, s, wert, **extra):
        return s.post("Fact", "testbaum", {"tag": "OCCU", "value": wert}, xref="I1", **extra)

    def test_adresse_am_ereignis(self):
        """Stufe 33: Adresse lesen und schreiben; Ort aendern und Adresse leeren laesst den Rest des Ereignisses stehen."""
        s = U.sitzung("admin")
        p = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Adresse", "surname": "Probe", "sex": "F", "dead": True}).json["xref"]
        s.post("Fact", "testbaum", {"tag": "RESI", "date": "FROM 1850 TO 1870", "place": "Offenbach", "address": "Klosterstraße 3\nHinterhaus", "note": "Mieter"}, xref=p)
        resi = next(f for f in self.fakten(s, p) if f["tag"] == "RESI")
        self.assertEqual("Klosterstraße 3\nHinterhaus", resi["address"])
        self.assertIsNone(next(f for f in self.fakten(s, p) if f["tag"] == "NAME")["address"])
        g = umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = ?", p)[0][0]
        self.assertIn("\n2 ADDR Klosterstraße 3\n3 CONT Hinterhaus\n2 PLAC Offenbach", g)
        # Ort auf den Hof, Adresse weg: Datum und Notiz bleiben
        s.post("Fact", "testbaum", {"factId": resi["id"], "tag": "RESI", "place": "Klosterstraße 3, Offenbach", "address": ""}, xref=p)
        resi = next(f for f in self.fakten(s, p) if f["tag"] == "RESI")
        self.assertIsNone(resi["address"])
        self.assertEqual("Klosterstraße 3, Offenbach", resi["place"]["name"])
        g = umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = ?", p)[0][0]
        self.assertNotIn("ADDR", g)
        self.assertIn("2 DATE FROM 1850 TO 1870", g)
        self.assertIn("2 NOTE Mieter", g)
        # Export liefert das Feld ebenso
        ex = next(i for i in s.get("Export", "testbaum", page=1).json["individuals"] if i["xref"] == p)
        self.assertIn("address", next(f for f in ex["facts"] if f["tag"] == "RESI"))
        s.post("DeleteRecord", "testbaum", {}, xref=p)

    def test_gast_darf_nicht_schreiben(self):
        a = self.beruf_anlegen(U.sitzung(), "Gastberuf")
        self.assertEqual(False, a.json and a.json.get("ok"), a)
        self.assertNotIn("Gastberuf", U.sitzung("verwalter").get("Individual", "testbaum", xref="I1").text)

    def test_mitglied_darf_nicht_schreiben(self):
        a = self.beruf_anlegen(U.sitzung("mitglied"), "Mitgliedberuf")
        self.assertEqual({"ok": False, "error": "not-editable", "status": 403}, a.json)

    def test_ohne_csrf_wird_nichts_ausgefuehrt(self):
        s = U.sitzung("admin")
        a = self.beruf_anlegen(s, "Ohnecsrf", csrf=False)
        self.assertIn(a.status, (302, 303), a)  # webtrees CheckCsrf: Umleitung statt Ausfuehrung
        self.assertNotIn("Ohnecsrf", U.sitzung("verwalter").get("Individual", "testbaum", xref="I1").text)

    def test_bearbeiter_schreibt_als_ausstehende_aenderung(self):
        s = U.sitzung("bearbeiter")
        a = self.beruf_anlegen(s, "Bearbeiterberuf")
        self.assertEqual(True, a.json["ok"], a)
        self.assertEqual(True, a.json["pending"], "ohne auto_accept muss die Aenderung auf Freigabe warten")
        # Gaeste sehen ausstehende Aenderungen nicht
        self.assertNotIn("Bearbeiterberuf", U.sitzung().get("Individual", "testbaum", xref="I1").text)
        pending = U.sitzung("verwalter").get("Pending", "testbaum")
        self.assertIn("I1", pending.text)

    def test_admin_mit_auto_accept_schreibt_sofort(self):
        s = U.sitzung("admin")
        a = self.beruf_anlegen(s, "Adminberuf")
        self.assertEqual((True, False), (a.json["ok"], a.json["pending"]), a)
        self.assertIn("Adminberuf", U.sitzung().get("Individual", "testbaum", xref="I1").text)

    def test_vertrauliche_person_fuer_bearbeiter_nicht_bearbeitbar(self):
        a = U.sitzung("bearbeiter").post("Fact", "testbaum", {"tag": "OCCU", "value": "x"}, xref="I5")
        self.assertEqual("private", a.json["error"], a)

    def test_gesperrtes_ereignis_nicht_loeschbar(self):
        s = U.sitzung("bearbeiter")
        reli = [f for f in self.fakten(s, "I1") if f.get("tag") == "RELI"]
        self.assertEqual(1, len(reli), "RELI mit RESN locked muss sichtbar sein")
        a = s.post("DeleteFact", "testbaum", {"factId": reli[0]["id"]}, xref="I1")
        self.assertEqual("fact-locked", a.json["error"], a)

    def test_details(self):
        """Stufe 37: Angaben ohne eigenes Feld kommen als details - Bezeichnung und Wert, wie webtrees sie zeigt."""
        s = U.sitzung("admin")
        a = s.post("Fact", "testbaum", {"gedcom": "1 DEAT\n2 DATE 1900\n2 AGE 72y\n2 CAUS Fieber\n2 AGNC Standesamt"}, xref="I3")
        self.assertEqual(True, a.json["ok"], a)
        tod = next(f for f in s.get("Individual", "testbaum", xref="I3").json["facts"] if f["tag"] == "DEAT" and f["date"] and f["date"]["year"] == 1900)
        self.assertEqual([("AGE", 0), ("CAUS", 0), ("AGNC", 0)], [(d["tag"], d["level"]) for d in tod["details"]])
        self.assertTrue(all(d["label"] for d in tod["details"]), tod["details"])
        self.assertIn("72", tod["details"][0]["value"])
        self.assertEqual(("Fieber", "Standesamt"), (tod["details"][1]["value"], tod["details"][2]["value"]))
        self.assertEqual(True, s.post("DeleteFact", "testbaum", {"factId": tod["id"]}, xref="I3").json["ok"])

    def test_person_anlegen(self):
        s = U.sitzung("admin")
        a = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Neue", "surname": "Testperson", "sex": "F",
                                                  "dead": True, "birthDate": "1850"})
        self.assertEqual(True, a.json["ok"], a)
        self.assertIn("Testperson", s.get("Individual", "testbaum", xref=a.json["xref"]).text)

    def test_quellenverweis_mit_seite(self):
        # Die Seitenangabe (3 PAGE, mehrzeilig) kommt mit; eine vertrauliche Quelle nur fuer den Verwalter.
        # Gaesten zeigt webtrees in diesem Baum keine Quellen - dann auch keine Seite (das deckt der Lecktest ab).
        for benutzer, sieht_konfidenz in (("verwalter", True), ("bearbeiter", False), ("mitglied", False)):
            fakten = U.sitzung(benutzer).get("Individual", "testbaum", xref="I1").json["facts"]
            geburt = next(f for f in fakten if f.get("tag") == "BIRT")
            q = geburt["sources"]
            self.assertEqual((1, "S1", "Kirchenbuch Offenbach", "Taufen 1800,\nNr. 4"), (len(q), q[0]["xref"], q[0]["title"], q[0]["page"]), benutzer)
            tod = next(f for f in fakten if f.get("tag") == "DEAT")
            self.assertEqual(sieht_konfidenz, any(q.get("page") == "Markerkonfidenz-Seite" for q in tod["sources"]), benutzer)
        gast = U.sitzung().get("Individual", "testbaum", xref="I1").json["facts"]
        self.assertNotIn("Taufen 1800", json.dumps(gast, ensure_ascii=False))

    def test_quellenverweis_vollstaendig(self):
        # Stufe 18: Qualitaet, Datum und Text der Fundstelle, Notiz - und Text-Quellen ohne Datensatz
        fakten = U.sitzung("bearbeiter").get("Individual", "testbaum", xref="I1").json["facts"]
        q = next(f for f in fakten if f.get("tag") == "BIRT")["sources"][0]
        self.assertEqual(3, q["quality"])
        self.assertEqual("2 MAR 1800", q["date"]["gedcom"])
        self.assertEqual("Theodor, Sohn des\nSchmieds, getauft", q["text"])
        self.assertEqual(["Eintrag gut lesbar"], q["notes"])
        beruf = next(f for f in fakten if f.get("tag") == "OCCU" and f.get("value") == "Schmied")
        self.assertEqual([("", "laut Martha Meier", ["mündlich 1950"])], [(x["xref"], x["title"], x["notes"]) for x in beruf["sources"]])

    def test_quellen_liste_und_einzeln(self):
        s = U.sitzung("bearbeiter")
        liste = s.get("Sources", "testbaum").json
        self.assertEqual(["S1"], [x["xref"] for x in liste["sources"]])  # S2 ist vertraulich
        s1 = liste["sources"][0]
        self.assertEqual(("Pfarramt Offenbach", "Offenbach, 1790–1830", "Bistumsarchiv Mainz", "KB 12", 4),
                         (s1["author"], s1["publication"], s1["repository"], s1["callNumber"], s1["uses"]))
        einzeln = s.get("Source", "testbaum", xref="S1").json
        self.assertEqual("Taufen, Trauungen, Begräbnisse", einzeln["text"])
        self.assertEqual(["Digitalisat im Archiv"], einzeln["notes"])
        self.assertEqual({("I1", ("Geburt", "Kindstaufe")), ("I2", ("Tod",))}, {(p["xref"], tuple(p["facts"])) for p in einzeln["individuals"]})
        self.assertEqual("private", s.get("Source", "testbaum", xref="S2").json["error"])
        verwalter = U.sitzung("verwalter").get("Sources", "testbaum").json
        self.assertEqual(["Kirchenbuch Offenbach", "Markerkonfidenz-Quelle"], [x["title"] for x in verwalter["sources"]])

    def test_quellenverweis_schreiben(self):
        # Verweis anlegen, gezielt aendern, verschieben, loeschen - das Ereignis und die anderen Teile bleiben unberuehrt.
        # Die Kennung des Ereignisses aendert sich mit jedem Schreiben (Hash); die Antwort liefert die neue.
        s = U.sitzung("admin")
        a = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Zita", "surname": "Quell", "sex": "F", "dead": True,
                                                  "birthDate": "1 MAY 1850", "birthPlace": "Zitadorf"})
        xref = a.json["xref"]
        fid = next(f for f in self.fakten(s, xref) if f.get("tag") == "BIRT")["id"]

        def zitat(**rumpf):
            nonlocal fid
            a = s.post("Citation", "testbaum", {"factId": fid, **rumpf}, xref=xref)
            self.assertEqual(True, a.json["ok"], a)
            fid = a.json["factId"]
            return next(f for f in self.fakten(s, xref) if f.get("tag") == "BIRT")

        # 1. anlegen: Quelle S1 mit Seite, Qualitaet, Datum, Zitat, Notiz; 2. eine Text-Quelle dazu
        zitat(source="S1", page="Taufen 1850, Nr. 7", quality=3, date="3 MAY 1850", text="Zita, Tochter\ndes Quell", note="gut lesbar")
        geburt = zitat(source="laut Oma Quell", quality=1)
        self.assertEqual(fid, geburt["id"])
        q = geburt["sources"]
        self.assertEqual([("S1", "Taufen 1850, Nr. 7", 3, "Zita, Tochter\ndes Quell", ["gut lesbar"]), ("", "", 1, "", [])],
                         [(x["xref"], x["page"], x["quality"], x["text"], x["notes"]) for x in q])
        self.assertEqual("laut Oma Quell", q[1]["title"])
        # 3. nur die Seite aendern: Qualitaet, Datum, Zitat, Notiz bleiben
        q0 = zitat(index=0, page="Taufen 1850, Nr. 8")["sources"][0]
        self.assertEqual(("Taufen 1850, Nr. 8", 3, "3 MAY 1850", "Zita, Tochter\ndes Quell", ["gut lesbar"]),
                         (q0["page"], q0["quality"], q0["date"]["gedcom"], q0["text"], q0["notes"]))
        # 4. verschieben: die Text-Quelle nach vorn
        self.assertEqual(["", "S1"], [x["xref"] for x in zitat(index=1, moveTo=0)["sources"]])
        # Ort und Datum des Ereignisses selbst sind unveraendert, DATA ist richtig aufgebaut
        ged = umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = ?", xref)[0][0]
        self.assertIn("2 DATE 1 MAY 1850\n2 PLAC Zitadorf", ged)
        self.assertIn("3 DATA\n4 DATE 3 MAY 1850\n4 TEXT Zita, Tochter\n5 CONT des Quell", ged)
        # 5. loeschen
        self.assertEqual(["S1"], [x["xref"] for x in zitat(index=0, delete=True)["sources"]])
        # 6. allgemeiner Verweis am Datensatz und Fehler
        a = s.post("Citation", "testbaum", {"source": "S1", "page": "Familienbogen"}, xref=xref)
        self.assertEqual(True, a.json["ok"], a)
        allgemein = [f for f in self.fakten(s, xref) if f.get("tag") == "SOUR"]
        self.assertEqual([("S1", "Familienbogen")], [(x["sources"][0]["xref"], x["sources"][0]["page"]) for x in allgemein])
        self.assertEqual("source-not-found", s.post("Citation", "testbaum", {"factId": fid, "source": "@S999@"}, xref=xref).json["error"])
        self.assertEqual("invalid-quality", s.post("Citation", "testbaum", {"factId": fid, "index": 0, "quality": 7}, xref=xref).json["error"])
        self.assertEqual("not-editable", U.sitzung("mitglied").post("Citation", "testbaum", {"factId": fid, "source": "S1"}, xref=xref).json["error"])

    def test_paten_schreiben(self):
        # Stufe 20: Paten verknuepft und frei schreiben, Unterzeilen und Schreibweise bleiben, 1 ASSO in die Taufe
        s = U.sitzung("admin")
        a = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Pia", "surname": "Pate", "sex": "F", "dead": True,
                                                  "birthDate": "1 MAY 1850"})
        xref = a.json["xref"]
        taufe_alt = ("1 CHR\n2 DATE 3 MAY 1850\n2 PLAC Patendorf\n2 _ASSO @I2@\n3 RELA Godmother\n3 SOUR @S1@\n4 PAGE Taufen 1850"
                     "\n2 _ASSO @I5@\n3 RELA godfather\n2 NOTE Paten: Hans Alt, Bauer; Grete Alt\n2 NOTE Alte Randnotiz\n2 SOUR @S1@\n3 PAGE Taufen 1850, Nr. 3")
        self.assertEqual(True, s.post("Fact", "testbaum", {"gedcom": taufe_alt}, xref=xref).json["ok"])
        self.assertEqual(True, s.post("Fact", "testbaum", {"gedcom": "1 ASSO @I4@\n2 RELA Godfather\n2 NOTE in Abwesenheit"}, xref=xref).json["ok"])

        def taufe():
            return next(f for f in self.fakten(s, xref) if f.get("tag") == "CHR")

        def ged():
            return umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = ?", xref)[0][0]

        # Die gewoehnliche Notiz aendern: die Patenliste bleibt (sie ist nicht "die erste Notiz")
        a = s.post("Fact", "testbaum", {"factId": taufe()["id"], "note": "Randnotiz"}, xref=xref)
        self.assertEqual(True, a.json["ok"], a)
        self.assertEqual(["Paten: Hans Alt, Bauer; Grete Alt", "Randnotiz"], taufe()["notes"])
        fid = taufe()["id"]
        # Reihenfolge tauschen, I3 neu als Pate mit Notiz, I5 (fuer Mitglieder privat) bleibt, I4 aus 1 ASSO hereinholen,
        # freie Paten neu - die alte Notiz "Paten: ..." geht in _GODP ueber, die Randnotiz bleibt
        a = s.post("Association", "testbaum", {"factId": fid, "convertLevel1": True,
                                               "linked": [{"xref": "I5", "role": "godparent"}, {"xref": "I2", "role": "godparent"},
                                                          {"xref": "I3", "role": "godparent", "note": "Bruder des Vaters"},
                                                          {"xref": "I4", "role": "godparent"}],
                                               "free": [{"text": "Hans Alt, Bauer", "role": "godparent"}, {"text": "Fritz Neu, Schmied", "role": "godparent"}]},
                   xref=xref)
        self.assertEqual(True, a.json["ok"], a)
        t = taufe()
        self.assertEqual(a.json["factId"], t["id"])
        self.assertEqual(["I5", "I2", "I3", "I4"], [x["xref"] for x in t["associates"]])
        self.assertEqual([False] * 4, [x["level1"] for x in t["associates"]], "I4 steht jetzt an der Taufe selbst")
        self.assertEqual([("Hans Alt", "Bauer"), ("Fritz Neu", "Schmied")], [(x["name"], x["detail"]) for x in t["freeAssociates"]])
        self.assertEqual(["Randnotiz"], t["notes"])
        g = ged()
        self.assertIn("2 _ASSO @I5@\n3 RELA godfather\n2 _ASSO @I2@\n3 RELA Godmother\n3 SOUR @S1@\n4 PAGE Taufen 1850"
                      "\n2 _ASSO @I3@\n3 RELA godparent\n3 NOTE Bruder des Vaters\n2 _ASSO @I4@\n3 RELA godparent\n3 NOTE in Abwesenheit"
                      "\n2 _GODP Hans Alt, Bauer\n2 _GODP Fritz Neu, Schmied\n2 SOUR @S1@\n3 PAGE Taufen 1850, Nr. 3\n2 NOTE Randnotiz", g)
        self.assertNotIn("1 ASSO", g, "der 1 ASSO ist in die Taufe gewandert")
        self.assertIn("2 DATE 3 MAY 1850\n2 PLAC Patendorf", g)
        # Nur die freien aendern: verknuepfte bleiben; einen verknuepften entfernen: die freien bleiben
        fid = s.post("Association", "testbaum", {"factId": t["id"], "free": []}, xref=xref).json["factId"]
        self.assertEqual(([], 4), (taufe()["freeAssociates"], len(taufe()["associates"])))
        a = s.post("Association", "testbaum", {"factId": fid, "linked": [{"xref": "I2", "role": "godparent"}]}, xref=xref)
        self.assertEqual(["I2"], [x["xref"] for x in taufe()["associates"]])
        self.assertIn("3 SOUR @S1@", ged(), "Quelle am Paten bleibt")
        # Fehler
        fid = a.json["factId"]
        self.assertEqual("invalid-role", s.post("Association", "testbaum", {"factId": fid, "linked": [{"xref": "I3", "role": "nachbar"}]}, xref=xref).json["error"])
        self.assertEqual("individual-not-found", s.post("Association", "testbaum", {"factId": fid, "linked": [{"xref": "I999", "role": "godparent"}]}, xref=xref).json["error"])
        self.assertEqual("invalid-value", s.post("Association", "testbaum", {"factId": fid, "free": [{"text": "@I1@", "role": "godparent"}]}, xref=xref).json["error"])
        self.assertEqual("not-editable", U.sitzung("mitglied").post("Association", "testbaum", {"factId": fid, "free": []}, xref=xref).json["error"])
        # Andere Beteiligte mit freier Rolle
        s.post("Association", "testbaum", {"factId": fid, "linked": [{"xref": "I2", "role": "godparent"}, {"xref": "I3", "role": "other", "rela": "Hebamme"}]}, xref=xref)
        self.assertEqual([("godparent", "Godmother"), ("other", "Hebamme")], [(x["role"], x["rela"]) for x in taufe()["associates"]])
        # Aufraeumen: die Testperson zitiert S1 und wuerde die Zaehlung anderer Tests verfaelschen
        self.assertEqual(True, s.post("DeleteRecord", "testbaum", {}, xref=xref).json["ok"])

    def test_heiratsart_und_trauzeugen_schreiben(self):
        s = U.sitzung("admin")
        a = s.post("Fact", "testbaum", {"tag": "MARR", "date": "5 JUN 1830", "type": "religious"}, xref="F1")
        self.assertEqual(True, a.json["ok"], a)
        heiraten = [f for f in s.get("Family", "testbaum", xref="F1").json["facts"] if f["tag"] == "MARR"]
        kirchlich = next(f for f in heiraten if f["type"] == "RELIGIOUS")
        self.assertTrue(kirchlich["typeLabel"])
        a = s.post("Association", "testbaum", {"factId": kirchlich["id"], "linked": [{"xref": "I4", "role": "witness"}],
                                               "free": [{"text": "Otto Zeuge, Kuester", "role": "witness"}]}, xref="F1")
        self.assertEqual(True, a.json["ok"], a)
        g = umgebung.sql("SELECT f_gedcom FROM wt_families WHERE f_id = 'F1'")[0][0]
        self.assertIn("1 MARR\n2 TYPE RELIGIOUS\n2 DATE 5 JUN 1830\n2 _ASSO @I4@\n3 RELA witness\n2 _WITN Otto Zeuge, Kuester", g)
        # Art aendern und wieder aufraeumen; die erste Heirat (civil) bleibt unberuehrt
        a = s.post("Fact", "testbaum", {"factId": a.json["factId"], "type": "Civil"}, xref="F1")
        self.assertIn("2 TYPE CIVIL\n2 DATE 5 JUN 1830", umgebung.sql("SELECT f_gedcom FROM wt_families WHERE f_id = 'F1'")[0][0])
        neu = [f for f in s.get("Family", "testbaum", xref="F1").json["facts"] if f["tag"] == "MARR" and f["date"] and f["date"]["year"] == 1830 and f["date"]["gedcom"] == "5 JUN 1830"]
        self.assertEqual(True, s.post("DeleteFact", "testbaum", {"factId": neu[0]["id"]}, xref="F1").json["ok"])
        self.assertIn("2 DATE 1828\n2 TYPE civil", umgebung.sql("SELECT f_gedcom FROM wt_families WHERE f_id = 'F1'")[0][0])

    def test_quelle_und_archiv_pflegen(self):
        s = U.sitzung("admin")
        # Archiv anlegen, Quelle anlegen mit Archiv und Signatur
        r = s.post("Repository", "testbaum", {"name": "Stadtarchiv Zitadorf"})
        self.assertEqual(True, r.json["ok"], r); repo = r.json["xref"]
        a = s.post("Source", "testbaum", {"title": "Adressbuch Zitadorf 1900", "author": "Magistrat", "publication": "Zitadorf, 1900",
                                           "text": "Seite 1\nSeite 2", "note": "Digitalisat", "repository": repo, "callNumber": "AB 1900"})
        self.assertEqual(True, a.json["ok"], a); quelle = a.json["xref"]
        q = s.get("Source", "testbaum", xref=quelle).json
        self.assertEqual(("Adressbuch Zitadorf 1900", "Magistrat", "Zitadorf, 1900", "Seite 1\nSeite 2", ["Digitalisat"], "Stadtarchiv Zitadorf", "AB 1900"),
                         (q["title"], q["author"], q["publication"], q["text"], q["notes"], q["repository"], q["callNumber"]))
        # nur den Autor aendern: alles andere bleibt
        a = s.post("Source", "testbaum", {"author": "Magistrat der Stadt"}, xref=quelle)
        self.assertEqual(True, a.json["ok"], a)
        q = s.get("Source", "testbaum", xref=quelle).json
        self.assertEqual(("Adressbuch Zitadorf 1900", "Magistrat der Stadt", "Zitadorf, 1900", "AB 1900"), (q["title"], q["author"], q["publication"], q["callNumber"]))
        # nur die Signatur aendern, dann das Archiv entfernen
        s.post("Source", "testbaum", {"callNumber": "AB 1900/2"}, xref=quelle)
        self.assertEqual(("Stadtarchiv Zitadorf", "AB 1900/2"), (lambda q: (q["repository"], q["callNumber"]))(s.get("Source", "testbaum", xref=quelle).json))
        s.post("Source", "testbaum", {"repository": ""}, xref=quelle)
        self.assertEqual(("", ""), (lambda q: (q["repository"], q["callNumber"]))(s.get("Source", "testbaum", xref=quelle).json))
        # Archiv umbenennen, Liste
        self.assertEqual(True, s.post("Repository", "testbaum", {"name": "Stadtarchiv Zitadorf (neu)"}, xref=repo).json["ok"])
        repos = s.get("Repositories", "testbaum").json["repositories"]
        self.assertIn(("Stadtarchiv Zitadorf (neu)", 0), [(x["name"], x["uses"]) for x in repos])
        # Fehler und Rechte
        self.assertEqual("title-missing", s.post("Source", "testbaum", {"author": "x"}).json["error"])
        self.assertEqual("repository-not-found", s.post("Source", "testbaum", {"title": "x", "repository": "R999"}).json["error"])
        self.assertEqual("not-editable", U.sitzung("mitglied").post("Source", "testbaum", {"title": "x"}).json["error"])
        # Medium ohne Verknuepfung hochladen (link=false), dann an die Quelle und an einen Verweis haengen
        rumpf, art = manifest.multipart({"title": "Scan", "type": "document", "link": "false"}, ("scan.png", manifest.PNG, "image/png"))
        req = urllib.request.Request(s.url("/module/_api4webtrees_/Media/testbaum", xref="I1"), data=rumpf, method="POST")
        req.add_header("Content-Type", art); req.add_header("X-CSRF-TOKEN", s.csrf)
        m = s._senden(req).json
        self.assertEqual(True, m["ok"], m)
        self.assertNotIn(m["media"], json.dumps(self.fakten(s, "I1")), "link=false darf die Person nicht verknuepfen")
        self.assertEqual(True, s.post("Source", "testbaum", {"media": [m["media"]]}, xref=quelle).json["ok"])
        self.assertEqual(["Scan"], [x["title"] for x in s.get("Source", "testbaum", xref=quelle).json["media"]])
        self.assertEqual(True, s.post("Source", "testbaum", {"media": []}, xref=quelle).json["ok"])
        self.assertEqual([], s.get("Source", "testbaum", xref=quelle).json["media"])
        geburt = next(f for f in self.fakten(s, "I2") if f.get("tag") == "DEAT")
        a = s.post("Citation", "testbaum", {"factId": geburt["id"], "index": 0, "media": [m["media"]]}, xref="I2")
        self.assertEqual(True, a.json["ok"], a)
        tod = next(f for f in self.fakten(s, "I2") if f.get("tag") == "DEAT")["sources"][0]
        self.assertEqual(("Begräbnisse 1880", ["Scan"]), (tod["page"], [x["title"] for x in tod["media"]]))
        # unbenutzte Quelle loeschen
        self.assertEqual(0, next(x["uses"] for x in s.get("Sources", "testbaum").json["sources"] if x["xref"] == quelle))
        self.assertEqual(True, s.post("DeleteRecord", "testbaum", {}, xref=quelle).json["ok"])
        self.assertEqual("not-found", s.get("Source", "testbaum", xref=quelle).json["error"])

    def test_medienobjekt_aus_archivdatei(self):
        # Eine Datei im Medienordner (Archiv) wird auf Wunsch ein Medienobjekt - einmal; ohne Verknuepfung an die Person
        s = U.sitzung("admin")
        ordner = os.path.join(umgebung.WT, "data", "media", "kirchenbuch")
        os.makedirs(ordner, exist_ok=True)
        open(os.path.join(ordner, "taufe_1833.png"), "wb").write(manifest.PNG)
        a = s.post("MediaFromFile", "testbaum", {"file": "kirchenbuch/taufe_1833.png", "title": "Taufe 1833"}, xref="I1")
        self.assertEqual((True, False), (a.json["ok"], a.json["existing"]), a)
        m = a.json["media"]
        b = s.post("MediaFromFile", "testbaum", {"file": "kirchenbuch/taufe_1833.png"}, xref="I1")
        self.assertEqual((m, True), (b.json["media"], b.json["existing"]), b)
        self.assertNotIn(m, json.dumps(self.fakten(s, "I1")), "darf die Person nicht verknuepfen")
        self.assertEqual("file-not-found", s.post("MediaFromFile", "testbaum", {"file": "kirchenbuch/gibtsnicht.png"}, xref="I1").json["error"])
        self.assertEqual("invalid-value", s.post("MediaFromFile", "testbaum", {"file": "../config.ini.php"}, xref="I1").json["error"])
        self.assertEqual("upload-not-allowed", U.sitzung("mitglied").post("MediaFromFile", "testbaum", {"file": "kirchenbuch/taufe_1833.png"}, xref="I1").json["error"])
        # Am Verweis haengt es dann wie jedes Medium
        tod = next(f for f in self.fakten(s, "I2") if f.get("tag") == "DEAT")
        self.assertEqual(True, s.post("Citation", "testbaum", {"factId": tod["id"], "index": 0, "media": [m]}, xref="I2").json["ok"])
        self.assertEqual(["Taufe 1833"], [x["title"] for x in next(f for f in self.fakten(s, "I2") if f.get("tag") == "DEAT")["sources"][0]["media"]])

    def test_notiz_und_vorhandenes_medium_an_person(self):
        """Stufe 23: Notizen an der Person (Route Fact, Tag NOTE), Verweis auf Notiz-Datensatz erkennbar (noteXref),
        vorhandenes Medium verknuepfen (Route Media mit media) und wieder loesen (UnlinkMedia)."""
        s = U.sitzung("admin")
        a = s.post("Fact", "testbaum", {"tag": "NOTE", "value": "Erste Zeile\nzweite Zeile"}, xref="I2")
        self.assertEqual(True, a.json["ok"], a)
        notiz = next(f for f in self.fakten(s, "I2") if f.get("tag") == "NOTE")
        self.assertEqual(("Erste Zeile\nzweite Zeile", None), (notiz["value"], notiz["noteXref"]))
        # Medium an I1 hochladen, dann dasselbe Medium an I2 haengen, doppelt verknuepfen bleibt einfach
        rumpf, art = manifest.multipart({"title": "Gemeinsames Bild"}, ("bild.png", manifest.PNG, "image/png"))
        req = urllib.request.Request(s.url("/module/_api4webtrees_/Media/testbaum", xref="I1"), data=rumpf, method="POST")
        req.add_header("Content-Type", art); req.add_header("X-CSRF-TOKEN", s.csrf)
        mx = s._senden(req).json["media"]
        self.assertEqual(True, s.post("Media", "testbaum", {"media": mx}, xref="I2").json["ok"])
        self.assertEqual(True, s.post("Media", "testbaum", {"media": mx}, xref="I2").json["ok"])
        ged = umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = 'I2'")[0][0]
        self.assertEqual(1, ged.count("1 OBJE @" + mx + "@"))
        self.assertEqual("media-not-found", s.post("Media", "testbaum", {"media": "M999"}, xref="I2").json["error"])
        self.assertEqual(True, s.post("UnlinkMedia", "testbaum", {"media": mx}, xref="I2").json["ok"])
        self.assertNotIn("@" + mx + "@", umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = 'I2'")[0][0])

    def test_ort_in_zwei_schreibweisen(self):
        """SQLite: "Ort" und "ort" sind zwei Eintraege der Ortstabelle - Place zaehlt beide und nennt die Schreibweise
        der Mehrheit; PlaceRename nimmt beide mit."""
        s = U.sitzung("admin")
        liste = s.get("Places", "testbaum", list="1").json["places"]
        ort = max(liste, key=lambda o: o["events"])
        vorher = s.get("Place", "testbaum", name=ort["name"]).json
        a = s.post("Fact", "testbaum", {"tag": "RESI", "date": "1900", "place": ort["name"].lower()}, xref="I2")
        self.assertEqual(True, a.json["ok"], a)
        nachher = s.get("Place", "testbaum", name=ort["name"]).json
        self.assertEqual(vorher["events"] + 1, nachher["events"])
        self.assertEqual(vorher["events"] + 1, s.get("Place", "testbaum", name=ort["name"].lower()).json["events"])
        neu = [f for f in self.fakten(s, "I2") if f.get("tag") == "RESI" and (f.get("place") or {}).get("name") == ort["name"].lower()]
        self.assertEqual(True, s.post("DeleteFact", "testbaum", {"factId": neu[0]["id"]}, xref="I2").json["ok"])

    def test_verfuegbare_module(self):
        """Stufe 31: Info nennt je Baum die Module, die der Benutzer dort nutzen kann - ein abgeschaltetes fehlt."""
        def module(sitzung):
            return next(b for b in sitzung.info()["trees"] if b["name"] == "testbaum")["availableModules"]
        m = U.sitzung("mitglied")
        self.assertIn("privacy-policy", module(m))
        self.assertIn("_api4webtrees_", module(m))
        umgebung.sql("INSERT INTO wt_module (module_name, status) VALUES ('privacy-policy', 'disabled') "
                     "ON CONFLICT (module_name) DO UPDATE SET status = 'disabled'")
        try:
            self.assertNotIn("privacy-policy", module(U.sitzung("mitglied")))
        finally:
            umgebung.sql("UPDATE wt_module SET status = 'enabled' WHERE module_name = 'privacy-policy'")

    def test_startperson(self):
        """Stufe 24: Info nennt die Startperson wie webtrees; StartPerson setzt die eigene Standardperson, die des
        Stammbaums nur fuer Verwalter."""
        def baum(sitzung):
            return next(b for b in sitzung.info()["trees"] if b["name"] == "testbaum")
        m = U.sitzung("mitglied")
        self.assertTrue(baum(m)["startXref"])
        r = m.post("StartPerson", "testbaum", {"xref": "I2"})
        self.assertEqual((True, "I2", "I2"), (r.json["ok"], r.json["startXref"], r.json["defaultXref"]), r)
        self.assertEqual("I2", baum(m)["startXref"])
        self.assertEqual("not-manager", m.post("StartPerson", "testbaum", {"xref": "I2", "forTree": True}).json["error"])
        self.assertEqual("not-found", m.post("StartPerson", "testbaum", {"xref": "I999"}).json["error"])
        # Eigene Standardperson wieder entfernen: dann gilt die des Stammbaums
        v = U.sitzung("verwalter")
        self.assertEqual(True, v.post("StartPerson", "testbaum", {"xref": "I1", "forTree": True}).json["ok"])
        r = m.post("StartPerson", "testbaum", {"xref": ""})
        self.assertEqual(("", "I1"), (r.json["defaultXref"], r.json["treeDefaultXref"]))
        self.assertEqual("not-logged-in", U.sitzung().post("StartPerson", "testbaum", {"xref": "I1"}).json["error"])

    def test_mein_konto(self):
        """Stufe 25: MyAccount aendert den eigenen angezeigten Namen."""
        m = U.sitzung("mitglied")
        alt = m.info()["user"]["realName"]
        r = m.post("MyAccount", None, {"realName": "  Neuer Name  "})
        self.assertEqual((True, "Neuer Name"), (r.json["ok"], r.json["realName"]), r)
        self.assertEqual("Neuer Name", m.info()["user"]["realName"])
        self.assertEqual("missing-real-name", m.post("MyAccount", None, {"realName": " "}).json["error"])
        self.assertEqual("not-logged-in", U.sitzung().post("MyAccount", None, {"realName": "X"}).json["error"])
        # Spalte real_name: 64 Zeichen; Zeilenumbrueche werden zu Leerzeichen
        self.assertEqual("real-name-too-long", m.post("MyAccount", None, {"realName": "X" * 65}).json["error"])
        self.assertTrue(m.post("MyAccount", None, {"realName": "Ä" * 64}).json["ok"])
        self.assertEqual("Anna Bauer", m.post("MyAccount", None, {"realName": "Anna\nBauer"}).json["realName"])
        self.assertEqual("Anna Bauer", m.info()["user"]["realName"])
        m.post("MyAccount", None, {"realName": alt})

    def test_ausstehendes_geschlecht(self):
        """Stufe 28: Eine ausstehende Aenderung des Geschlechts zeigt der Bearbeiter in person.sex wie im SEX-Fakt und
        mit pending; Mitglieder sehen weiter den freigegebenen Wert, ohne pending."""
        ed, mi, adm = U.sitzung("bearbeiter"), U.sitzung("mitglied"), U.sitzung("admin")

        def ansicht(s):
            p = s.get("Individual", "testbaum", xref="I1").json
            return p["person"]["sex"], [(f["value"], f["pending"]) for f in p["facts"] if f["tag"] == "SEX"]

        self.assertEqual(("M", [("männlich", False)]), ansicht(ed))
        fid = next(f["id"] for f in ed.get("Individual", "testbaum", xref="I1").json["facts"] if f["tag"] == "SEX")
        try:
            r = ed.post("Fact", "testbaum", {"factId": fid, "tag": "SEX", "value": "F"}, xref="I1")
            self.assertEqual((True, True), (r.json["ok"], r.json["pending"]), r)
            self.assertEqual(("F", [("weiblich", True)]), ansicht(ed))
            self.assertEqual(("M", [("männlich", False)]), ansicht(mi))
            liste = ed.get("Individuals", "testbaum", q="Theodor").json["data"]
            self.assertEqual({"F"}, {e["sex"] for e in liste if e["xref"] == "I1"})
            alle = ed.get("Individual", "testbaum", xref="I1").json["facts"]
            self.assertEqual(1, sum(1 for f in alle if f["pending"]))
            self.assertFalse(any(f["pending"] for f in mi.get("Individual", "testbaum", xref="I1").json["facts"]))
        finally:
            adm.post("Reject", "testbaum", {}, xref="I1")
        self.assertEqual(("M", [("männlich", False)]), ansicht(ed))

    def test_anmeldeseite_in_info(self):
        """Stufe 26: Info.loginForm nennt Begruessungstext, Selbstregistrierung und Bedingungen - auch Gaesten."""
        def form():
            return U.sitzung().info()["loginForm"]
        f = form()
        self.assertEqual({"welcomeMessage", "isSelfRegistrationAllowed", "registrationTerms"}, set(f))
        self.assertTrue(f["welcomeMessage"])
        self.assertIsNone(f["registrationTerms"])
        self.assertGreaterEqual(U.sitzung().info()["api"], 26)
        try:
            umgebung.sql("INSERT OR REPLACE INTO wt_site_setting (setting_name, setting_value) VALUES ('SHOW_REGISTER_CAUTION', '1')")
            umgebung.sql("INSERT OR REPLACE INTO wt_site_setting (setting_name, setting_value) VALUES ('USE_REGISTRATION_MODULE', '0')")
            f = form()
            self.assertIn("<p>", f["registrationTerms"])
            self.assertFalse(f["isSelfRegistrationAllowed"])
        finally:
            umgebung.sql("DELETE FROM wt_site_setting WHERE setting_name = 'SHOW_REGISTER_CAUTION'")
            umgebung.sql("INSERT OR REPLACE INTO wt_site_setting (setting_name, setting_value) VALUES ('USE_REGISTRATION_MODULE', '1')")
        self.assertTrue(form()["isSelfRegistrationAllowed"])

    def test_name_aendern_behaelt_unterangaben(self):
        # Beim Aendern des Namens darf nichts verloren gehen: Praefix, Spitzname und Notiz bleiben,
        # GIVN/SURN/NSFX folgen dem neuen Namen.
        s = U.sitzung("admin")
        a = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Karl", "surname": "Muster", "sex": "M", "dead": True})
        xref = a.json["xref"]
        name = next(f for f in self.fakten(s, xref) if f.get("tag") == "NAME")
        voll = "1 NAME Dr. Karl /Muster/\n2 NPFX Dr.\n2 GIVN Karl\n2 SURN Muster\n2 NICK Kalle\n2 NOTE Namensnotiz"
        self.assertEqual(True, s.post("Fact", "testbaum", {"factId": name["id"], "gedcom": voll}, xref=xref).json["ok"])
        name = next(f for f in self.fakten(s, xref) if f.get("tag") == "NAME")
        a = s.post("Fact", "testbaum", {"factId": name["id"], "value": "Dr. Karl Heinz /Muster-Meier/ jun."}, xref=xref)
        self.assertEqual(True, a.json["ok"], a)
        ged = umgebung.sql("SELECT i_gedcom FROM wt_individuals WHERE i_id = ?", xref)[0][0]
        for zeile in ("1 NAME Dr. Karl Heinz /Muster-Meier/ jun.", "2 NPFX Dr.", "2 NICK Kalle", "2 NOTE Namensnotiz",
                      "2 GIVN Karl Heinz", "2 SURN Muster-Meier", "2 NSFX jun."):
            self.assertIn(zeile, ged)
        self.assertNotIn("2 SURN Muster\n", ged + "\n")

    def test_jahrestag_heute(self):
        heute = datetime.date.today()
        datum = f"{heute.day} {'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split()[heute.month - 1]} {heute.year - 100}"
        s = U.sitzung("admin")
        neu = s.post("AddIndividual", "testbaum", {"relation": "none", "given": "Hundert", "surname": "Jahre", "sex": "M",
                                                    "birthDate": datum, "dead": True})
        self.assertEqual(True, neu.json["ok"], neu)
        a = U.sitzung().get("Anniversaries", "testbaum", days=1)
        treffer = [e for e in a.json["data"] if e["xref"] == neu.json["xref"]]
        self.assertEqual(1, len(treffer), a)
        self.assertEqual((0, 100, "BIRT"), (treffer[0]["inDays"], treffer[0]["years"], treffer[0]["tag"]))
        self.assertEqual(neu.json["xref"], treffer[0]["person"]["xref"])
        self.assertIn("more", a.json)
        self.assertLessEqual(len(a.json["data"]), a.json["total"])

    def test_app_liste_gueltig(self):
        """src/Apps.php: jeder Eintrag vollstaendig, https, Schema eindeutig - die Pruefung, die ein Pull Request bestehen muss."""
        modul = os.path.join(umgebung.HIER, "..")
        code = 'require "src/Apps.php"; echo json_encode([Api4Webtrees\\Apps::check(), array_keys(Api4Webtrees\\Apps::ALL)]);'
        fehler, kennungen = json.loads(subprocess.check_output(["php", "-r", code], cwd=modul, text=True))
        self.assertEqual([], fehler)
        self.assertEqual(["wtwin", "wtand", "wttux", "wtmac"], kennungen[:4])

    def test_seite_app_je_geraet(self):
        """Die Seite App zeigt die Apps fuer das Geraet des Besuchers zuerst; ein Geraet ohne passende App bekommt den Browser-Hinweis."""
        s = U.sitzung("admin")

        def seite(user_agent):
            req = urllib.request.Request(s.url("/module/_api4webtrees_/App"))
            req.add_header("User-Agent", user_agent)
            return s._senden(req).text

        android = seite("Mozilla/5.0 (Linux; Android 14) Mobile")
        self.assertLess(android.index("wtAnd"), android.index("wtWin"))
        # Logo und Satz des Modulautors bei den eigenen Apps (Apps.php: icon, forDevice: own)
        self.assertIn("wt-apps.png", android)
        self.assertIn("App und Schnittstelle werden gemeinsam entwickelt", android)
        self.assertLess(android.index("wtWin"), android.index("wtTux"))
        self.assertIn("webtreesand://connect?", android)
        self.assertIn("github.com/thobgg/api4webtrees", android)
        self.assertNotIn("noch kein eigenes Programm", android)

        windows = seite("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        self.assertLess(windows.index("wtWin"), windows.index("wtAnd"))
        self.assertIn("wtwin://connect?", windows)

        # iPhone: die App aus der Liste fuer iOS steht oben, mit Store-Badge und Koppel-Link, kein Browser-Hinweis
        iphone = seite("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")
        self.assertNotIn("noch kein eigenes Programm", iphone)
        self.assertIn("webtreesmobile://connect?", iphone)
        self.assertIn("app-store.svg", iphone)
        self.assertLess(iphone.index("webtrees mobile"), iphone.index("wtAnd"))
        # Die fremde App hat kein Logo und nicht den Satz des Modulautors - der steht erst bei wtAnd darunter
        self.assertLess(iphone.index("webtrees mobile"), iphone.index("gemeinsam entwickelt"))

        mac = seite("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")
        self.assertNotIn("noch kein eigenes Programm", mac)
        self.assertLess(mac.index("wtMac"), mac.index("wtWin"))
        self.assertIn("wtmac://connect?", mac)

        # Verbinden-Seite (Ziel des QR-Codes): ein Knopf je Handy-App mit Schema, Download je App.
        verbinden = s._senden(urllib.request.Request(s.url("/module/_api4webtrees_/Connect"))).text
        self.assertIn('data-scheme="webtreesand"', verbinden)
        self.assertIn("wt-apps.png", verbinden)
        self.assertIn("wtAnd herunterladen", verbinden)
        self.assertNotIn("wtWin", verbinden)
        self.assertIn("github.com/thobgg/api4webtrees", verbinden)

    def test_app_abschalten(self):
        """Einstellungen: eine abgehakte App verschwindet von der Seite App und aus der Fusszeile."""
        s = U.sitzung("admin")
        admin_url = s.url("/module/_api4webtrees_/Admin")
        formular = s._senden(urllib.request.Request(admin_url)).text
        alle = re.findall(r'name="apps\[\]" value="([^"]+)"', formular)
        self.assertIn("wttux", alle)
        csrf = re.search(r'name="_csrf" value="([^"]+)"', formular).group(1)

        def speichern(apps):
            daten = urllib.parse.urlencode([("_csrf", csrf), ("trees[]", "testbaum"), ("trees[]", "geheim")] + [("apps[]", a) for a in apps]).encode()
            req = urllib.request.Request(admin_url, data=daten, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            return s._senden(req)

        try:
            self.assertIn(speichern(["wtand", "wtwin"]).status, (302, 303))
            seite = s._senden(urllib.request.Request(s.url("/module/_api4webtrees_/App/testbaum"))).text
            self.assertNotIn("wtTux", seite)
            self.assertIn("wtWin", seite)
        finally:
            speichern(alle)

        seite = s._senden(urllib.request.Request(s.url("/module/_api4webtrees_/App/testbaum"))).text
        self.assertIn("wtTux", seite)

    def test_ungueltiger_koppelcode(self):
        s = U.sitzung()
        a = s.post("Pair", None, {"code": "0" * 48})
        self.assertEqual(False, a.json["ok"], a)
        self.assertFalse(s.info()["user"]["loggedIn"])


if __name__ == "__main__":
    sys.path.insert(0, umgebung.HIER)
    unittest.main(verbosity=2)
