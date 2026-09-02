"""
Python zum Mitliefern.

Holt das offizielle embeddable-Paket von python.org, entpackt es in den
Programmordner, ruestet tkinter nach und kopiert die Fremdpakete hinein,
die das Programm braucht.

Das Ergebnis ist ein Ordner python\\ neben dem Quellkode. Darin liegt eine
von der Python Software Foundation signierte python.exe. Es entsteht keine
unsignierte Binaerdatei, also auch keine Warnung des Virenscanners.

Ohne Python auf dem Zielrechner, ohne Adminrechte, ohne Registry.
"""

import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile

# Reihenfolge der Versuche. Zuerst die Fassung, unter der wir selbst laufen -
# die Fremdpakete stammen aus ihrem site-packages und muessen zur ABI passen.
_RUECKFALL = ("3.13.7", "3.13.5", "3.12.10", "3.11.9")

_URL = ("https://www.python.org/ftp/python/{v}/"
        "python-{v}-embed-amd64.zip")

# Alles, was tkinter zum Laufen braucht. Das embeddable-Paket bringt es
# nicht mit, eine normale Installation schon.
_TK_DLLS = ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")

# Diese Namen sind Teil der Standardbibliothek oder stecken schon im
# embeddable-Paket. Sie werden nie aus site-packages kopiert.
_KEIN_PAKET = {
    "os", "sys", "re", "json", "time", "math", "random", "shutil", "subprocess",
    "threading", "pathlib", "datetime", "typing", "tkinter", "sqlite3",
    "logging", "argparse", "configparser", "traceback", "zipfile", "urllib",
    "socket", "struct", "hashlib", "base64", "csv", "io", "glob", "ctypes",
    "winsound", "winreg", "queue", "collections", "itertools", "functools",
    "webbrowser", "tempfile", "platform", "textwrap", "unicodedata", "uuid",
}


def _log(fn, text):
    if fn:
        fn(text)


def _eigene_version():
    return "{}.{}.{}".format(*sys.version_info[:3])


def _versionsliste():
    liste = [_eigene_version()]
    for v in _RUECKFALL:
        if v not in liste:
            liste.append(v)
    return liste


def _kurz(version):
    """3.13.7 wird zu 313 - so heissen die Dateien im Paket."""
    teile = version.split(".")
    return teile[0] + teile[1]


# ------------------------------------------------------------- Herunterladen

def _lade_zip(version, log=None):
    """Holt das embeddable-ZIP. Gibt den Pfad zurueck oder None."""
    url = _URL.format(v=version)
    ziel = os.path.join(tempfile.gettempdir(),
                        "python-{}-embed-amd64.zip".format(version))
    if os.path.exists(ziel) and os.path.getsize(ziel) > 5_000_000:
        _log(log, "Python {} liegt schon im Zwischenspeicher.".format(version))
        return ziel
    _log(log, "Lade Python {} von python.org ...".format(version))
    try:
        with urllib.request.urlopen(url, timeout=60) as antwort:
            daten = antwort.read()
        if len(daten) < 5_000_000:
            _log(log, "Antwort zu klein - Version {} gibt es nicht."
                 .format(version))
            return None
        with open(ziel, "wb") as f:
            f.write(daten)
        _log(log, "Geladen: {:.1f} MB".format(len(daten) / 1_048_576))
        return ziel
    except Exception as e:
        _log(log, "Fehlgeschlagen fuer {}: {}".format(version, e))
        return None


def hole_embeddable(log=None):
    """Versucht die Versionen der Reihe nach. Gibt (zip_pfad, version)."""
    for v in _versionsliste():
        pfad = _lade_zip(v, log)
        if pfad:
            return pfad, v
    return None, None


# ---------------------------------------------------------------- Entpacken

def _pth_oeffnen(ziel, version, log=None):
    """
    Im embeddable-Paket liegt python313._pth. Diese Datei ist die gesamte
    Modulsuche - was nicht darin steht, findet Python nicht. Ab Werk fehlen
    zwei Dinge: der Ordner Lib, in den tkinter nachgeruestet wird, und die
    freigegebene Zeile import site fuer Lib\\site-packages.
    """
    name = "python{}._pth".format(_kurz(version))
    pfad = os.path.join(ziel, name)
    if not os.path.exists(pfad):
        _log(log, "WARNUNG: {} nicht gefunden.".format(name))
        return False
    with open(pfad, "r", encoding="utf-8") as f:
        zeilen = [z.rstrip() for z in f.read().splitlines()]

    neu = []
    hat_site = False
    for z in zeilen:
        if z.strip() in ("#import site", "import site"):
            hat_site = True
            continue
        if z.strip():
            neu.append(z)

    # Reihenfolge zaehlt: eigener Ordner, dann Lib, dann site-packages.
    # Reihenfolge nach dem Einfuegen: ., Lib, Lib\\site-packages, .., ..\\pakete
    # .. und ..\\pakete sind noetig, weil das eingebettete Python PYTHONPATH
    # ignoriert - ein Kindprozess ueber sys.executable faende sonst weder den
    # Quellkode noch die Fremdpakete im Ordner pakete.
    for eintrag in ("..\\pakete", "..\\pakete\\win32", "..\\pakete\\win32\\lib", "..", "Lib\\site-packages", "Lib", "."):
        if eintrag not in neu:
            neu.insert(0, eintrag)

    neu.append("import site")
    if not hat_site:
        _log(log, "Zeile import site war nicht vorhanden, ergaenzt.")

    with open(pfad, "w", encoding="utf-8") as f:
        f.write("\n".join(neu) + "\n")
    _log(log, "{} eingerichtet: {}".format(name, ", ".join(neu)))
    return True


def entpacke(zip_pfad, ziel, version, log=None):
    """Entpackt das embeddable-Paket nach ziel und macht es aufnahmefaehig."""
    os.makedirs(ziel, exist_ok=True)
    with zipfile.ZipFile(zip_pfad) as zf:
        zf.extractall(ziel)
    # Die Standardbibliothek steckt als python313.zip darin. Sie bleibt
    # gepackt - Python liest sie direkt, das spart Platz und Dateien.
    os.makedirs(os.path.join(ziel, "Lib", "site-packages"), exist_ok=True)
    _pth_oeffnen(ziel, version, log)
    _log(log, "Python entpackt nach {}".format(ziel))
    return True


# ------------------------------------------------------------------ tkinter

def tkinter_nachruesten(ziel, log=None):
    """
    Kopiert tkinter aus der laufenden Installation. Ohne das laesst sich
    kein Fenster oeffnen - und alle Werkzeuge hier haben eine Oberflaeche.
    """
    quelle = os.path.dirname(sys.executable)
    lib = sysconfig.get_paths()["stdlib"]

    tk_paket = os.path.join(lib, "tkinter")
    if not os.path.isdir(tk_paket):
        _log(log, "FEHLER: tkinter nicht gefunden in {}".format(lib))
        return False
    shutil.copytree(tk_paket, os.path.join(ziel, "Lib", "tkinter"),
                    dirs_exist_ok=True)

    dlls = os.path.join(quelle, "DLLs")
    kopiert = 0
    for name in _TK_DLLS:
        for ordner in (dlls, quelle):
            p = os.path.join(ordner, name)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(ziel, name))
                kopiert += 1
                break

    tcl = os.path.join(quelle, "tcl")
    if os.path.isdir(tcl):
        shutil.copytree(tcl, os.path.join(ziel, "tcl"), dirs_exist_ok=True)
    else:
        _log(log, "WARNUNG: Ordner tcl nicht gefunden.")

    _log(log, "tkinter nachgeruestet, {} Bibliotheken kopiert.".format(kopiert))
    return True


# ------------------------------------------------------------- Fremdpakete

def _site_packages():
    p = sysconfig.get_paths().get("purelib")
    if p and os.path.isdir(p):
        return p
    for pfad in sys.path:
        if pfad.endswith("site-packages") and os.path.isdir(pfad):
            return pfad
    return None


def _verteilungsordner(sp, name):
    """Findet zu einem Modulnamen alle Ordner und Dateien in site-packages."""
    treffer = []
    klein = name.lower().replace("-", "_")
    for eintrag in os.listdir(sp):
        e = eintrag.lower().replace("-", "_")
        if e == klein or e == klein + ".py" or e.startswith(klein + "-"):
            treffer.append(eintrag)
        elif e.startswith(klein + ".") and e.endswith(".pyd"):
            treffer.append(eintrag)
    return treffer


def kopiere_pakete(ziel, pakete, log=None):
    """
    Kopiert die genannten Fremdpakete aus dem eigenen site-packages in das
    mitgelieferte Python. Kein pip, kein Netz - was hier laeuft, laeuft dort.
    """
    sp = _site_packages()
    if not sp:
        _log(log, "FEHLER: site-packages nicht gefunden.")
        return []
    nach = os.path.join(ziel, "Lib", "site-packages")
    os.makedirs(nach, exist_ok=True)

    fertig = []
    for paket in pakete:
        if paket.lower() in _KEIN_PAKET:
            continue
        eintraege = _verteilungsordner(sp, paket)
        if not eintraege:
            _log(log, "NICHT GEFUNDEN: {} - bitte pruefen.".format(paket))
            continue
        for e in eintraege:
            quelle = os.path.join(sp, e)
            zielp = os.path.join(nach, e)
            try:
                if os.path.isdir(quelle):
                    shutil.copytree(quelle, zielp, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(
                                        "__pycache__", "*.pyc", "tests",
                                        "test", "*.dist-info.bak"))
                else:
                    shutil.copy2(quelle, zielp)
            except Exception as e2:
                _log(log, "Fehler bei {}: {}".format(e, e2))
        fertig.append(paket)
        _log(log, "Mitgenommen: {}".format(paket))
    return fertig


# ---------------------------------------------------------------- Starter

def schreibe_starter(programmordner, name, einstieg, log=None):
    """
    Eine Zeile, die pythonw.exe aus dem mitgelieferten Ordner aufruft.
    pythonw statt python: kein schwarzes Fenster, kein Aufblitzen.
    """
    pfad = os.path.join(programmordner, "START_" + name + ".bat")
    inhalt = ("@echo off\r\n"
              "cd /d \"%~dp0\"\r\n"
              "start \"\" \"%~dp0python\\pythonw.exe\" \"%~dp0" + einstieg + "\"\r\n")
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(inhalt)
    _log(log, "Starter geschrieben: " + os.path.basename(pfad))
    return pfad


# -------------------------------------------------------------- Gesamtlauf

def python_mitliefern(programmordner, pakete=(), log=None):
    """
    Der ganze Weg in einem Aufruf. Legt programmordner\\python an.
    Gibt True zurueck, wenn das mitgelieferte Python benutzbar ist.
    """
    ziel = os.path.join(programmordner, "python")
    if os.path.isdir(ziel):
        shutil.rmtree(ziel, ignore_errors=True)

    zip_pfad, version = hole_embeddable(log)
    if not zip_pfad:
        _log(log, "ABBRUCH: Kein Python zum Mitliefern erhalten. "
                  "Das Paket sucht dann Python auf dem Zielrechner.")
        return False

    entpacke(zip_pfad, ziel, version, log)
    tkinter_nachruesten(ziel, log)
    if pakete:
        kopiere_pakete(ziel, pakete, log)

    exe = os.path.join(ziel, "pythonw.exe")
    if not os.path.exists(exe):
        _log(log, "ABBRUCH: pythonw.exe fehlt im entpackten Paket.")
        return False

    groesse = sum(os.path.getsize(os.path.join(w, f))
                  for w, _, fs in os.walk(ziel) for f in fs)
    _log(log, "Python {} liegt bei, {:.1f} MB."
         .format(version, groesse / 1_048_576))
    return True


def probelauf(programmordner, log=None):
    """
    Startet das mitgelieferte Python einmal und laesst es tkinter laden.
    Findet Fehler beim Bauen statt beim Empfaenger.
    """
    exe = os.path.join(programmordner, "python", "python.exe")
    if not os.path.exists(exe):
        return False, "python.exe fehlt"
    try:
        p = subprocess.run(
            [exe, "-c", "import tkinter; tkinter.Tk().destroy(); print('ok')"],
            capture_output=True, text=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if "ok" in (p.stdout or ""):
            _log(log, "Probelauf bestanden - tkinter laeuft.")
            return True, ""
        fehler = (p.stderr or p.stdout or "").strip().splitlines()
        grund = fehler[-1] if fehler else "unbekannt"
        _log(log, "Probelauf gescheitert: " + grund)
        return False, grund
    except Exception as e:
        _log(log, "Probelauf nicht moeglich: {}".format(e))
        return False, str(e)
