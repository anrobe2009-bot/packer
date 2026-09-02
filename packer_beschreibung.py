"""Laesst eine KI ansehen, was ein Programm tut, und beschreiben.

Warum ueberhaupt: Bis zum 29.08.2026 suchte der Packer den
Sprechtext in Dateien - erst einfuehrung.txt, dann paket.json, dann
eine Beschreibungsdatei, zuletzt ein Docstring. Suchen ist nicht
Verstehen. Beim Memory Hub kam so der Docstring heraus, eine Notiz
an den Entwickler: der Empfaenger hoert Roberts Namen und versteht
kein Wort.

Dieses Modul misst statt zu raten. Es liest die Struktur des
Projekts - Dateien, Importe, oeffentliche Funktionen, vorhandene
Beschreibung - und laesst Gemini daraus drei bis fuenf Saetze
schreiben, die ein Fremder versteht.

Der Schluessel kommt aus der gemeinsamen Verwaltung unter
start\\gemeinsam, abgelegt mit DPAPI unter APPDATA. Im Projektordner
liegt nie ein Schluessel.

Bewusst ohne Fremdpakete: nur urllib aus der Standardbibliothek.
Wuerde hier requests oder google.generativeai stehen, wanderte es
beim Selbstbau des Packers als Abhaengigkeit mit ins Paket.
"""

import json
import os
import re
import ssl
import urllib.error
import urllib.request

# Der Ordner, in dem Roberts gemeinsame Module liegen. Fest benannt,
# weil er nicht im Suchpfad steht - am 29.08.2026 gemessen.
GEMEINSAM = r"C:\Users\Entwickler\Desktop\start\gemeinsam"

MODELL = "gemini-2.5-flash"
ADRESSE = ("https://generativelanguage.googleapis.com/v1beta/"
           "models/{modell}:generateContent")

# Wie viel die KI zu sehen bekommt. Nicht der ganze Quellkode: bei
# 32 Dateien waere das sinnlos teuer, und die Struktur sagt mehr
# ueber den Zweck als jede einzelne Zeile.
MAX_ZEICHEN_GESAMT = 24000
MAX_ZEICHEN_DATEI = 3500
MAX_DATEIEN = 40

ZIEL_ZEICHEN = 700

# Ordner, die ueber den Zweck nichts sagen.
RAUS_ORDNER = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    "build", "dist", "werkzeug", "pakete", "python", "logs",
    "data", "daten", "cache", "temp", "_archiv", "_verworfen",
}


def _log(funktion, text):
    if funktion:
        try:
            funktion(text)
        except Exception:
            pass


# ------------------------------------------------------ Schluessel ---

def _schluessel(anbieter="gemini"):
    """Holt den Schluessel aus der gemeinsamen Verwaltung.

    Geladen wird ueber den Dateipfad, nicht mit import schluessel.
    Grund: Ein gewoehnlicher Import stuende im Quelltext und wuerde
    beim Selbstbau des Packers als Fremdpaket erkannt und mitgepackt.
    So sieht ihn keine Textsuche, und beim Empfaenger fehlt er
    einfach - dann greift die alte Dateisuche.
    """
    pfad = os.path.join(GEMEINSAM, "schluessel.py")
    if not os.path.exists(pfad):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ki_schluessel", pfad)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        return modul.holen(anbieter)
    except Exception:
        return None


# ------------------------------------------------- Projekt ansehen ---

def _oeffentliche_namen(quelle):
    """Funktionen und Klassen, die nicht mit Unterstrich beginnen."""
    namen = []
    try:
        import ast
        baum = ast.parse(quelle)
        for knoten in baum.body:
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                if not knoten.name.startswith("_"):
                    namen.append(knoten.name)
    except Exception:
        pass
    return namen[:25]


def _importe(quelle):
    """Welche Fremdpakete das Programm benutzt - sie verraten viel.

    tkinter und PySide6 heisst Fenster, sounddevice heisst Ton,
    requests heisst Netz. Das sagt oft mehr als jeder Kommentar.
    """
    treffer = set()
    for zeile in quelle.splitlines():
        z = zeile.strip()
        gefunden = re.match(r"^(?:import|from)\s+([A-Za-z_][\w.]*)", z)
        if gefunden:
            treffer.add(gefunden.group(1).split(".")[0])
    return sorted(treffer)[:30]


def _lies(pfad, grenze=MAX_ZEICHEN_DATEI):
    try:
        with open(pfad, "r", encoding="utf-8", errors="replace") as f:
            return f.read(grenze)
    except Exception:
        return ""


def projekt_ansehen(ordner, einstieg=""):
    """Sammelt, was ueber den Zweck eines Projekts Auskunft gibt.

    Zurueck kommt Text, der an die KI geht - keine Datei wird dabei
    veraendert. Kein vollstaendiger Quellkode: Struktur, Importe,
    oeffentliche Namen, der Anfang der Startdatei und eine
    vorhandene Beschreibung.
    """
    teile = []
    dateien = []
    alle_importe = set()

    if not os.path.isdir(ordner):
        return ""

    for wurzel, unter, namen in os.walk(ordner):
        unter[:] = [u for u in unter
                    if u.lower() not in RAUS_ORDNER
                    and not u.startswith(".")]
        for name in sorted(namen):
            rel = os.path.relpath(os.path.join(wurzel, name), ordner)
            dateien.append(rel)
            if len(dateien) > 300:
                break

    teile.append("DATEIEN IM PROJEKT:")
    teile.append(", ".join(dateien[:120]))

    # Die Startdatei zuerst, sie sagt am meisten.
    reihenfolge = []
    if einstieg:
        reihenfolge.append(einstieg)
    for rel in dateien:
        if rel.lower().endswith(".py") and rel not in reihenfolge:
            reihenfolge.append(rel)

    gelesen = 0
    for rel in reihenfolge[:MAX_DATEIEN]:
        pfad = os.path.join(ordner, rel)
        if not os.path.isfile(pfad):
            continue
        quelle = _lies(pfad)
        if not quelle:
            continue
        alle_importe.update(_importe(quelle))
        namen = _oeffentliche_namen(quelle)
        stueck = ["", "DATEI: " + rel]
        if namen:
            stueck.append("  Funktionen und Klassen: " + ", ".join(namen))
        # Vom Anfang jeder Datei ein Stueck - dort stehen Docstring
        # und Kommentare, die den Zweck nennen.
        kopf = "\n".join(quelle.splitlines()[:30])
        stueck.append("  Anfang:")
        stueck.append(kopf)
        text = "\n".join(stueck)
        if gelesen + len(text) > MAX_ZEICHEN_GESAMT:
            break
        teile.append(text)
        gelesen += len(text)

    if alle_importe:
        teile.insert(1, "BENUTZTE MODULE: " + ", ".join(sorted(alle_importe)))

    return "\n".join(teile)


def vorhandene_beschreibung(ordner):
    """Was schon da ist - die KI soll es sehen, nicht ignorieren.

    Ein guter Text bleibt so stehen. Ein schlechter wird ersetzt.
    Ohne diesen Schritt waere jede Korrektur beim naechsten Bau weg.
    """
    stuecke = []
    eigen = os.path.join(ordner, "einfuehrung.txt")
    if os.path.exists(eigen):
        stuecke.append("BISHERIGER EINFUEHRUNGSTEXT:\n" + _lies(eigen, 2000))
    try:
        for name in sorted(os.listdir(ordner)):
            wurzel = os.path.splitext(name.lower())[0]
            if any(wurzel.endswith(w) or wurzel == w
                   for w in ("readme", "liesmich", "bedienung",
                             "anleitung", "handbuch")):
                stuecke.append("BESCHREIBUNGSDATEI " + name + ":\n"
                               + _lies(os.path.join(ordner, name), 4000))
                break
    except Exception:
        pass
    return "\n\n".join(stuecke)


# --------------------------------------------------------- Auftrag ---

AUFTRAG = """Du beschreibst ein Programm fuer Menschen, die es
geschenkt bekommen und noch nie davon gehoert haben.

Der Text wird VORGELESEN. Er muss beim Hoeren verstaendlich sein.

Regeln:
- Deutsch, mit echten Umlauten.
- Drei bis fuenf Saetze, hoechstens {ziel} Zeichen.
- Beginne damit, WAS das Programm tut und WOFUER man es braucht.
- Danach ein Satz, fuer wen es gedacht ist, falls erkennbar.
- Danach hoechstens ein Satz zur Bedienung, falls sie erkennbar ist.
- Keine Aufzaehlung, keine Ueberschrift, keine Sternchen, keine
  Rauten, keine Klammern mit Dateinamen.
- Keine Fachbegriffe aus dem Quellkode. Kein Modulname, kein
  Funktionsname, kein Dateiname.
- Schreibe NICHT, dass etwas installiert wurde oder startet.
- Nenne KEINE Tastenkuerzel zum Weiterklicken.
- Erfinde nichts. Was du nicht erkennst, laesst du weg.
- Antworte NUR mit dem Text, ohne Vorwort und ohne Anfuehrungszeichen.

Gibt es bereits einen Einfuehrungstext: Pruefe ihn gegen das, was
das Programm wirklich tut. Ist er richtig und verstaendlich, gib
ihn unveraendert zurueck. Ist er falsch, an Entwickler gerichtet
oder unverstaendlich, schreibe einen neuen.

Das Programm heisst: {name}

{beschreibung}

{struktur}
"""


def _fragen(schluessel, frage, log=None):
    """Schickt die Frage an Gemini und gibt die Antwort zurueck."""
    adresse = ADRESSE.format(modell=MODELL)
    # thinkingBudget 0: Gemini 2.5 denkt sonst vor dem Antworten,
    # und die Denk-Tokens zaehlen gegen maxOutputTokens. Am
    # 29.08.2026 brach die Antwort deshalb nach 164 Zeichen mitten
    # im Satz ab. Fuer drei Saetze Beschreibung braucht es keine
    # Denkphase.
    daten = json.dumps({
        "contents": [{"parts": [{"text": frage}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2000,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode("utf-8")

    anfrage = urllib.request.Request(
        adresse,
        data=daten,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": schluessel,
        },
        method="POST",
    )
    zusammenhang = ssl.create_default_context()
    with urllib.request.urlopen(anfrage, timeout=90,
                                context=zusammenhang) as antwort:
        roh = json.loads(antwort.read().decode("utf-8"))

    kandidaten = roh.get("candidates") or []
    if not kandidaten:
        raise ValueError("Keine Antwort erhalten: " + str(roh)[:200])

    # Der Abbruchgrund entscheidet, nicht die Anwesenheit von Text.
    # MAX_TOKENS heisst: der Satz ist nicht zu Ende. Ein halber Satz
    # klingt vorgelesen schlimmer als gar keiner - dann lieber die
    # alte Dateisuche.
    grund = str(kandidaten[0].get("finishReason", "")).upper()
    teile = kandidaten[0].get("content", {}).get("parts") or []
    text = "".join(t.get("text", "") for t in teile).strip()

    if grund and grund not in ("STOP", "FINISH_REASON_STOP"):
        raise ValueError("Antwort unvollstaendig, Grund " + grund
                         + ", " + str(len(text)) + " Zeichen")
    if not text:
        raise ValueError("Leere Antwort")
    return text


def _saeubern(text):
    """Nimmt heraus, was beim Vorlesen stoert."""
    text = re.sub(r"[*#`]+", "", text)
    text = re.sub(r"^\s*[-–]\s*", "", text, flags=re.MULTILINE)
    text = " ".join(text.split())
    if len(text) > ZIEL_ZEICHEN + 200:
        schnitt = text.rfind(". ", 0, ZIEL_ZEICHEN + 200)
        if schnitt > 200:
            text = text[:schnitt + 1]
    return text.strip()


def erzeugen(name, ordner, einstieg="", log=None):
    """Laesst die KI den Einfuehrungstext schreiben.

    Zurueck kommt der Text oder eine leere Zeichenkette. Leer heisst:
    kein Schluessel, kein Netz, oder die Antwort war unbrauchbar -
    dann greift die alte Dateisuche. Ein Bau bricht deswegen nie ab.
    """
    schluessel = _schluessel("gemini")
    if not schluessel:
        _log(log, "Kein Gemini-Schluessel hinterlegt - der "
                  "Einfuehrungstext wird wie bisher aus den Dateien "
                  "gesucht.")
        return ""

    struktur = projekt_ansehen(ordner, einstieg)
    if not struktur:
        _log(log, "Nichts zu lesen im Projektordner.")
        return ""

    frage = AUFTRAG.format(
        ziel=ZIEL_ZEICHEN,
        name=name,
        beschreibung=vorhandene_beschreibung(ordner),
        struktur=struktur,
    )

    _log(log, "Einfuehrung: Gemini sieht sich das Programm an "
              "({} Zeichen Struktur) ...".format(len(struktur)))
    try:
        antwort = _fragen(schluessel, frage, log)
    except urllib.error.HTTPError as fehler:
        _log(log, "Gemini antwortet nicht ({}) - der Text wird wie "
                  "bisher aus den Dateien gesucht."
                  .format(fehler.code))
        return ""
    except Exception as fehler:
        _log(log, "Gemini nicht erreichbar ({}) - der Text wird wie "
                  "bisher aus den Dateien gesucht."
                  .format(type(fehler).__name__))
        return ""

    text = _saeubern(antwort)
    if len(text) < 80:
        _log(log, "Die Antwort war zu kurz - der Text wird wie bisher "
                  "aus den Dateien gesucht.")
        return ""

    _log(log, "Einfuehrung: von Gemini geschrieben, {} Zeichen."
         .format(len(text)))
    return text


def ablegen(ordner, text, log=None):
    """Schreibt den Text als einfuehrung.txt in den Projektordner.

    Damit steht er fest und ist aenderbar. Vorher wird gesichert -
    Endung .bak nach Regel 613, niemals ein eigener Name.
    """
    if not text:
        return ""
    pfad = os.path.join(ordner, "einfuehrung.txt")
    try:
        if os.path.exists(pfad):
            alt = _lies(pfad, 100000)
            if alt.strip() == text.strip():
                _log(log, "einfuehrung.txt ist unveraendert.")
                return pfad
            import shutil
            shutil.copy2(pfad, pfad + ".bak")
        with open(pfad, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        _log(log, "einfuehrung.txt im Projektordner geschrieben.")
        return pfad
    except Exception as fehler:
        _log(log, "einfuehrung.txt nicht schreibbar: " + str(fehler))
        return ""
