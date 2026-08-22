"""
Die Einfuehrung beim ersten Start.

Eine LIESMICH-Datei nuetzt nichts, wenn man sie erst suchen und oeffnen
muss. Wer nicht sieht, bekommt sie nie zu Gesicht. Also kommt sie von
selbst: beim allerersten Start ein Fenster mit wenigen Saetzen, und es
liest sie vor.

Der Text wird beim Bauen erzeugt und liegt als einfuehrung.txt im Paket.
Gesucht wird er in dieser Reihenfolge:

  1. einfuehrung.txt im Projektordner - Roberts eigener Text, unveraendert
  2. das Feld einfuehrung in paket.json
  3. die ersten Absaetze aus LIESMICH.md oder README.md
  4. der Docstring der Startdatei

Gezeigt wird er von starter.py, nicht vom Programm selbst. Damit bekommt
jedes verpackte Werkzeug die Einfuehrung, ohne dass eine Zeile daran
geaendert werden muss.
"""

import json
import os
import re

from packer_python import _log

# Mehr hoert niemand geduldig an. Bei etwa hundertsechzig Zeichen je
# Sekunde sind das gut vierzig Sekunden - lang genug fuer den Zweck,
# kurz genug, um es abzuwarten.
MAX_ZEICHEN = 700


def _lies(pfad):
    try:
        with open(pfad, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _kuerzen(text, grenze=MAX_ZEICHEN):
    """Am Satzende abschneiden, nicht mitten im Wort."""
    text = text.strip()
    if len(text) <= grenze:
        return text
    stueck = text[:grenze]
    for zeichen in (". ", "! ", "? ", ".\n"):
        stelle = stueck.rfind(zeichen)
        if stelle > grenze // 2:
            return stueck[:stelle + 1].strip()
    stelle = stueck.rfind(" ")
    return (stueck[:stelle] if stelle > 0 else stueck).strip()


def _aus_markdown(text):
    """
    Die ersten sinnvollen Absaetze aus einer Markdown-Datei.

    Ueberschriften, Auszeichnungen und Kodebloecke fliegen raus -
    vorgelesen ergaeben Rauten und Sternchen nur Geraeusch.
    """
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    absaetze = []
    for roh in text.split("\n\n"):
        zeile = roh.strip()
        if not zeile or zeile.startswith("#"):
            continue
        if zeile.startswith(("|", ">", "---", "===")):
            continue
        zeile = re.sub(r"[*_`]", "", zeile)
        zeile = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", zeile)
        zeile = re.sub(r"^\s*[-*]\s+", "", zeile, flags=re.M)
        zeile = " ".join(zeile.split())
        if len(zeile) > 40:
            absaetze.append(zeile)
        if sum(len(a) for a in absaetze) > MAX_ZEICHEN:
            break
    return "\n\n".join(absaetze)


def _aus_docstring(pfad):
    text = _lies(pfad)
    treffer = re.search(r'^\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
    return " ".join(treffer.group(1).split()) if treffer else ""


def sammle_text(src_dir, name, log=None):
    """Findet den besten verfuegbaren Einfuehrungstext."""
    eigen = os.path.join(src_dir, "einfuehrung.txt")
    if os.path.exists(eigen):
        text = _lies(eigen).strip()
        if text:
            _log(log, "Einfuehrung: eigener Text aus einfuehrung.txt.")
            return text

    paket = os.path.join(src_dir, "paket.json")
    if os.path.exists(paket):
        try:
            with open(paket, "r", encoding="utf-8") as f:
                wert = str(json.load(f).get("einfuehrung", "")).strip()
            if wert:
                _log(log, "Einfuehrung: Text aus paket.json.")
                return wert
        except Exception:
            pass

    for datei in ("LIESMICH.md", "README.md", "BEDIENUNG.md"):
        pfad = os.path.join(src_dir, datei)
        if os.path.exists(pfad):
            text = _aus_markdown(_lies(pfad))
            if len(text) > 80:
                _log(log, "Einfuehrung: aus " + datei + " zusammengefasst.")
                return _kuerzen(text)

    for datei in sorted(os.listdir(src_dir)):
        if datei.lower().endswith(".py"):
            text = _aus_docstring(os.path.join(src_dir, datei))
            if len(text) > 80:
                _log(log, "Einfuehrung: aus dem Docstring von " + datei + ".")
                return _kuerzen(text)

    _log(log, "Einfuehrung: kein Text gefunden.")
    return ""


STANDARD_STIMME = "de-DE-KatjaNeural"


def _stimme_aus_paket(src_dir):
    """Je Projekt aenderbar ueber das Feld stimme in paket.json."""
    pfad = os.path.join(src_dir, "paket.json")
    if not os.path.exists(pfad):
        return STANDARD_STIMME
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            wert = str(json.load(f).get("stimme", "")).strip()
        return wert or STANDARD_STIMME
    except Exception:
        return STANDARD_STIMME


def sprich_datei(app_dir, text, src_dir, log=None):
    """
    Spricht den Text mit edge-tts in eine MP3 im Paket.

    Beim Bauen, nicht beim Abspielen: Der Empfaenger braucht weder
    edge-tts noch Internet, nur eine Datei.

    Scheitert es - kein Paket, keine Leitung -, bleibt es beim
    gesprochenen Rueckfall des Anzeigers. Kein Grund abzubrechen.
    """
    stimme = _stimme_aus_paket(src_dir)
    pfad = os.path.join(app_dir, "einfuehrung.mp3")
    try:
        import asyncio
        import edge_tts
    except Exception as fehler:
        # Frueher stand hier eine feste Meldung: edge-tts nicht
        # vorhanden. Sie stimmte selten. Ein ImportError tief in einer
        # Abhaengigkeit sieht von aussen genauso aus wie ein fehlendes
        # Paket - und schickt die Suche in die Irre.
        fehlend = getattr(fehler, "name", "") or ""
        if fehlend in ("edge_tts", "asyncio"):
            _log(log, "edge-tts nicht vorhanden - die Einfuehrung wird "
                      "beim Empfaenger von der Windows-Stimme gelesen.")
        else:
            _log(log, "edge-tts liess sich nicht laden: "
                      + type(fehler).__name__ + ": " + str(fehler)
                      + (" - es fehlt " + fehlend if fehlend else "")
                      + ". Die Einfuehrung wird beim Empfaenger von der "
                        "Windows-Stimme gelesen.")
        return ""

    # Der Screenreader stolpert ueber Striche und Rauten, die Stimme
    # ebenso. Vorgelesen wird der Fliesstext.
    sauber = " ".join(text.replace("-", " ").split())
    try:
        asyncio.run(edge_tts.Communicate(sauber, stimme).save(pfad))
    except Exception as e:
        _log(log, "Stimme nicht erzeugt: " + str(e))
        return ""

    if not os.path.exists(pfad) or os.path.getsize(pfad) < 2000:
        _log(log, "Stimme erzeugt, aber die Datei ist unbrauchbar.")
        try:
            os.remove(pfad)
        except Exception:
            pass
        return ""

    _log(log, "einfuehrung.mp3 gesprochen von {}, {:.0f} KB."
         .format(stimme, os.path.getsize(pfad) / 1024))
    return pfad


def schreibe(app_dir, name, src_dir, log=None):
    """Legt einfuehrung.txt ins Paket."""
    text = sammle_text(src_dir, name, log)
    if not text:
        text = ("Zu diesem Programm liegt keine Beschreibung bei. "
                "Naeheres steht in der Datei LIESMICH.md im Programmordner.")

    kopf = name + "\n" + "-" * len(name) + "\n\n"

    # Der Hinweis wird mitgesprochen. Wer nichts sieht, weiss sonst nicht,
    # wie es weitergeht - und nicht, wie er die Stimme abstellt.
    hinweis = ("Dieses Fenster erscheint nur beim ersten Start. "
               "Mit der Eingabetaste geht es weiter. "
               "Mit der Leertaste hoert die Stimme auf.")
    fuss = "\n\n" + hinweis + "\n"

    kurz = _kuerzen(text)
    pfad = os.path.join(app_dir, "einfuehrung.txt")
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(kopf + kurz + fuss)
    _log(log, "einfuehrung.txt geschrieben, {} Zeichen.".format(len(kurz)))

    # Gesprochen wird der Text und der Bedienhinweis, nicht aber die
    # Ueberschrift - der Programmname allein vorgelesen sagt nichts.
    #
    # Vorweg die Bestaetigung. Seit der Installation mit einem Klick
    # ist diese Stimme das einzige, was dem Empfaenger sagt, dass es
    # geklappt hat - der Installierer schweigt bewusst, sonst reden
    # zwei zugleich. Nur gesprochen, nicht in der Datei: wer sie
    # spaeter liest, ist laengst installiert.
    ansage = (name + " ist installiert und startet jetzt. ")
    sprich_datei(app_dir, ansage + kurz + " " + hinweis, src_dir, log)
    return True


# --------------------------------------------------------------- Anzeiger
# Wird als einfuehrung_zeigen.py ins Paket gelegt. Bewusst ohne
# Escape-Sequenzen geschrieben: der Text durchlaeuft zwei Ebenen, und
# ein zerlegtes Steuerzeichen faellt erst beim Empfaenger auf.

ZEIGER = '''# Zeigt die Einfuehrung beim allerersten Start und liest sie vor.
#
# Aufgerufen von starter.py, bevor das eigentliche Programm beginnt.
#
# Die Stimme kommt aus System.Speech, das zu Windows gehoert - kein
# Zusatzpaket noetig, keine Internetverbindung. Vorgelesen wird von
# selbst, weil man sich auf einen Screenreader nicht verlassen kann:
# nicht jeder benutzt einen, und eine verrutschte Maus laesst ihn mitten
# im Text neu ansetzen.
#
# Der Fokus liegt auf einem Knopf, nicht auf dem Text. Laege er auf dem
# Text, laese ein vorhandener Screenreader mit - und zwei Stimmen
# gleichzeitig sind schlimmer als keine.

import os
import subprocess


def _merkzettel(name):
    wurzel = os.environ.get("APPDATA") or os.path.expanduser("~")
    ordner = os.path.join(wurzel, "KI-Stammtisch")
    try:
        os.makedirs(ordner, exist_ok=True)
    except Exception:
        return None
    return os.path.join(ordner, "einfuehrung_" + name + ".txt")


def schon_gesehen(name):
    pfad = _merkzettel(name)
    return bool(pfad) and os.path.exists(pfad)


def vermerken(name):
    pfad = _merkzettel(name)
    if not pfad:
        return
    try:
        with open(pfad, "w", encoding="utf-8") as f:
            f.write("gesehen")
    except Exception:
        pass


class Stimme:
    """Windows-Sprachausgabe in einem eigenen Vorgang, damit sie sich
    jederzeit beenden laesst, ohne das Fenster mitzureissen."""

    def __init__(self):
        self._lauf = None

    def sprich(self, text, ordner=None):
        """Erst die mitgelieferte Aufnahme, sonst die Windows-Stimme."""
        self.still()

        if ordner:
            mp3 = os.path.join(ordner, "einfuehrung.mp3")
            if os.path.exists(mp3):
                befehl = ("Add-Type -AssemblyName presentationCore; "
                          "$p = New-Object System.Windows.Media.MediaPlayer; "
                          "$p.Open([uri]'" + mp3.replace("'", "") + "'); "
                          "Start-Sleep -Milliseconds 600; "
                          "$p.Play(); "
                          "Start-Sleep -Seconds 300")
                try:
                    self._lauf = subprocess.Popen(
                        ["powershell", "-NoProfile", "-Command", befehl],
                        creationflags=getattr(subprocess,
                                              "CREATE_NO_WINDOW", 0))
                    return
                except Exception:
                    self._lauf = None

        sauber = " ".join(str(text).split())
        for zeichen in ("=", "-", "'", '"'):
            sauber = sauber.replace(zeichen, " ")
        sauber = " ".join(sauber.split())
        if not sauber:
            return
        befehl = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$s.Speak('" + sauber + "')")
        try:
            self._lauf = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", befehl],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            self._lauf = None

    def still(self):
        if self._lauf is not None and self._lauf.poll() is None:
            try:
                self._lauf.kill()
            except Exception:
                pass
        self._lauf = None


def zeigen(text, name, ordner=None):
    try:
        import tkinter as tk
    except Exception:
        print(text)
        return

    stimme = Stimme()

    fenster = tk.Tk()
    fenster.title("Einfuehrung - " + name)
    fenster.geometry("760x560")

    rahmen = tk.Frame(fenster, padx=20, pady=16)
    rahmen.pack(fill="both", expand=True)

    feld = tk.Text(rahmen, wrap="word", font=("Segoe UI", 13),
                   relief="flat", padx=14, pady=14)
    feld.insert("1.0", text)
    feld.config(state="disabled")
    feld.pack(fill="both", expand=True)

    unten = tk.Frame(fenster, pady=12)
    unten.pack(fill="x")

    nicht_mehr = tk.BooleanVar(value=True)
    tk.Checkbutton(unten, variable=nicht_mehr, text="Nicht mehr zeigen",
                   font=("Segoe UI", 11)).pack(side="left", padx=18)

    def schliessen(*_):
        stimme.still()
        if nicht_mehr.get():
            vermerken(name)
        fenster.destroy()

    def erneut(*_):
        stimme.sprich(text, ordner)

    def stoppen(*_):
        stimme.still()

    tk.Button(unten, text="Weiter", command=schliessen,
              font=("Segoe UI", 12), width=12).pack(side="right", padx=18)
    tk.Button(unten, text="Stopp", command=stoppen,
              font=("Segoe UI", 12), width=10).pack(side="right", padx=6)
    knopf_lesen = tk.Button(unten, text="Noch einmal", command=erneut,
                            font=("Segoe UI", 12), width=14)
    knopf_lesen.pack(side="right", padx=6)

    fenster.bind("<Return>", schliessen)
    fenster.bind("<Escape>", schliessen)
    fenster.bind("<space>", stoppen)
    fenster.protocol("WM_DELETE_WINDOW", schliessen)

    fenster.after(80, knopf_lesen.focus_set)
    fenster.after(400, lambda: stimme.sprich(text, ordner))

    fenster.mainloop()
    stimme.still()


def einmalig(ordner, name):
    """Zeigt die Einfuehrung, falls sie noch nie gezeigt wurde."""
    pfad = os.path.join(ordner, "einfuehrung.txt")
    if not os.path.exists(pfad):
        return
    if schon_gesehen(name):
        return
    try:
        with open(pfad, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return
    try:
        zeigen(text, name, ordner)
    except Exception:
        pass
'''


def schreibe_zeiger(app_dir, log=None):
    """Legt einfuehrung_zeigen.py ins Paket."""
    pfad = os.path.join(app_dir, "einfuehrung_zeigen.py")
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(ZEIGER)
    _log(log, "einfuehrung_zeigen.py geschrieben.")
    return pfad
