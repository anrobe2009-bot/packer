"""
Zwei Wege nach dem Bauen.

  Bei mir installieren     entpackt das Paket und richtet es ein
  Fuer die Webseite        legt es in die Ablage der Webseite

Beide unabhaengig. Ein Werkzeug kann nur bei Robert landen, nur auf der
Webseite, oder beides.

Der Umweg ueber das Hochladen im Browser entfaellt: Webserver und
Packer laufen auf demselben Rechner. Die Webseite liest den
Ablageordner bei jedem Aufruf selbst aus - was dort liegt, erscheint.
Nichts wird von Hand gepflegt, nichts muss angemeldet werden.

Eine Startdatei wird bewusst nicht mehr gebaut. Der Server erzeugt sie
bei jedem Abruf selbst, mit einer persoenlichen Kennung, die nach sieben
Tagen verfaellt - eine mitgelieferte waere sofort veraltet.
"""

import json
import os
import re
import shutil
import subprocess
import zipfile

from packer_python import _log

# Der Ablageordner der Webseite steht in der Marke unter webseite.
#
# Frueher stand er hier fest im Kode. Das ging nur so lange gut, wie
# niemand ausser Robert den Packer benutzt - ein Fremder hat weder
# diese Webseite noch diesen Ordner.
#
# Fehlt der Eintrag, gibt es keine Webseite. Der Packer zeigt den Haken
# dafuer dann gar nicht erst an.
ABLAGE_WEBSEITE = ""


def ablage_webseite():
    """Wohin die Werkzeuge fuer die Webseite gehen - oder leer."""
    try:
        import packer
        return str(packer.lade_marke().get("webseite", "")).strip()
    except Exception:
        return ""


MAX_LAENGE = 50


def netzname(name):
    """
    Aus dem Werkzeugnamen den Ordnernamen im Netz machen.

    Der Ordnername ist zugleich die Adresse. Erlaubt sind deshalb nur
    Kleinbuchstaben, Ziffern, Bindestrich und Unterstrich - hoechstens
    fuenfzig Zeichen.

    Am 21.08.2026 entstand ein Ordner namens
    KI-Stammtisch-Cologne_AI Terminal Bridge. Die Webseite zeigte das
    Werkzeug nicht an. Seither setzt der Packager selbst um, statt es
    dem Nutzer aufzubuerden.
    """
    n = str(name).strip().lower()
    for umlaut, ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                           ("ß", "ss")):
        n = n.replace(umlaut, ersatz)
    n = re.sub(r"[^a-z0-9_-]+", "-", n).strip("-_")

    # Das Kuerzel der Marke abschneiden, falls es vorn klebt. Aus
    # ki-stammtisch-cologne_schreiber wird schreiber. Genommen wird das
    # eingetragene Kuerzel, nicht irgendein Praefix - sonst
    # verstuemmelt es Namen, die zufaellig aehnlich beginnen.
    try:
        import packer
        kuerzel = re.sub(r"[^a-z0-9_-]+", "-",
                         str(packer.KUERZEL).lower()).strip("-_")
    except Exception:
        kuerzel = ""
    if kuerzel:
        for trenner in ("_", "-"):
            vorn = kuerzel + trenner
            if n.startswith(vorn) and len(n) > len(vorn):
                n = n[len(vorn):]
                break

    # Mehrere Bindestriche hintereinander zu einem, aussen weg.
    n = re.sub(r"-{2,}", "-", n).strip("-_")

    # Nicht laenger als noetig. Am Bindestrich kuerzen, damit kein
    # halbes Wort stehen bleibt.
    if len(n) > MAX_LAENGE:
        n = n[:MAX_LAENGE]
        stelle = n.rfind("-")
        if stelle > MAX_LAENGE // 2:
            n = n[:stelle]
        n = n.strip("-_")

    return n or "werkzeug"


# ------------------------------------------------------ paket.json ---

def _text_aus(app_dir, name):
    """
    Kurztext und langer Text fuer die Karte auf der Webseite.

    Genommen wird, was beim Bauen ohnehin entstanden ist: die
    Einfuehrung. Sie stammt entweder aus einer eigenen einfuehrung.txt
    des Projekts oder wurde aus LIESMICH.md zusammengefasst.
    """
    pfad = os.path.join(app_dir, "einfuehrung.txt")
    if not os.path.exists(pfad):
        return "", ""
    try:
        with open(pfad, "r", encoding="utf-8", errors="replace") as f:
            roh = f.read()
    except Exception:
        return "", ""

    # Ueberschrift und Bedienhinweis gehoeren nicht in die Beschreibung.
    zeilen = []
    for zeile in roh.splitlines():
        z = zeile.strip()
        if not z or set(z) <= set("-="):
            continue
        if z == name or z.lower() == name.lower():
            continue
        if z.startswith("Dieses Fenster erscheint"):
            continue
        zeilen.append(z)
    lang = "\n".join(zeilen).strip()
    if not lang:
        return "", ""

    # Der Kurztext sind die ersten ein bis zwei Saetze.
    kurz = ""
    for satz in re.split(r"(?<=[.!?])\s+", lang.replace("\n", " ")):
        if not satz.strip():
            continue
        kurz = (kurz + " " + satz).strip()
        if len(kurz) > 90:
            break
    return kurz[:200], lang


def schreibe_paketjson(ordner, name, app_dir, marke, log=None):
    """Legt paket.json an - der Steckbrief fuer die Webseite."""
    kurz, lang = _text_aus(app_dir, name)
    angaben = {
        "titel": name[:1].upper() + name[1:],
        "version": str(marke.get("version", "")).strip() or "1.0",
        "kurz": kurz or ("Werkzeug von " + str(marke.get("autor", ""))),
        "installer": "INSTALLIEREN.bat",
    }
    if lang:
        angaben["beschreibung"] = lang

    pfad = os.path.join(ordner, "paket.json")
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(angaben, f, indent=2, ensure_ascii=False)

    if not kurz:
        _log(log, "ACHTUNG: Keine Beschreibung gefunden. Auf der Seite "
                  "steht dann nur der Name. Eine einfuehrung.txt im "
                  "Projektordner behebt das.")
    return pfad, angaben


# -------------------------------------------------- Fuer die Webseite ---

def bereitstellen(app_dir, name, zip_pfad, marke, log=None,
                  ablage=None):
    """
    Legt das Werkzeug in die Ablage der Webseite.

    Aufbau je Werkzeug:
        pakete\\NAME\\paket\\NAME.zip
        pakete\\NAME\\audio\\einfuehrung.mp3
        pakete\\NAME\\paket.json

    Der Server sucht rekursiv nach der ersten ZIP- und der ersten
    MP3-Datei. Wichtig ist nur: genau ein Werkzeug je Ordner.
    """
    if not zip_pfad or not os.path.exists(zip_pfad):
        _log(log, "Kein Archiv vorhanden - nichts bereitzustellen.")
        return None, "Kein Archiv"

    if not ablage:
        ablage = str(marke.get("webseite", "")).strip() or ablage_webseite()
    if not ablage:
        _log(log, "In den Einstellungen fehlt der Ordner der Webseite.")
        return None, "Kein Ordner fuer die Webseite eingetragen"
    if not os.path.isdir(ablage):
        _log(log, "Die Ablage der Webseite gibt es nicht: " + ablage)
        return None, "Ablage fehlt"

    kurz = netzname(name)
    ordner = os.path.join(ablage, kurz)

    # Leeren, damit keine alte Fassung neben der neuen liegt. Der
    # Server nimmt die erste ZIP, die er findet - bei zweien raet er.
    if os.path.isdir(ordner):
        shutil.rmtree(ordner, ignore_errors=True)
    os.makedirs(os.path.join(ordner, "paket"), exist_ok=True)

    ziel_zip = os.path.join(ordner, "paket", kurz + ".zip")
    shutil.copy2(zip_pfad, ziel_zip)
    groesse = os.path.getsize(ziel_zip) / 1_048_576
    _log(log, "Archiv abgelegt: {} ({:.0f} MB)".format(ziel_zip, groesse))

    mp3_da = False
    quelle_mp3 = os.path.join(app_dir, "einfuehrung.mp3")
    if os.path.exists(quelle_mp3):
        os.makedirs(os.path.join(ordner, "audio"), exist_ok=True)
        shutil.copy2(quelle_mp3, os.path.join(ordner, "audio",
                                              "einfuehrung.mp3"))
        mp3_da = True
        _log(log, "Gesprochene Vorstellung abgelegt.")
    else:
        _log(log, "Keine MP3 gefunden - auf der Seite fehlt nur der "
                  "Anhoeren-Knopf.")

    _pfad, angaben = schreibe_paketjson(ordner, kurz, app_dir, marke, log)
    _log(log, "paket.json geschrieben, Titel: " + angaben["titel"])

    bericht = ("{} steht auf der Webseite bereit. {:.0f} Megabyte. "
               "{} Ein Neustart ist nicht noetig."
               .format(angaben["titel"], groesse,
                       "Mit gesprochener Vorstellung."
                       if mp3_da else "Ohne gesprochene Vorstellung."))
    return ordner, bericht


# ------------------------------------------------ Bei mir installieren ---

def oertlich_installieren(zip_pfad, name, log=None):
    """
    Das eben gebaute Paket bei Robert einrichten.

    Entpackt in TEMP und ruft _install.ps1 auf. Kein Netz, kein
    Herunterladen - das Paket liegt ja schon da.
    """
    if not zip_pfad or not os.path.exists(zip_pfad):
        _log(log, "Kein Archiv vorhanden - nichts zu installieren.")
        return False, "Kein Archiv"

    arbeit = os.path.join(os.environ.get("TEMP", "."),
                          name + "_einrichten")
    shutil.rmtree(arbeit, ignore_errors=True)
    os.makedirs(arbeit, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_pfad) as zf:
            zf.extractall(arbeit)
    except Exception as e:
        _log(log, "Entpacken gescheitert: " + str(e))
        return False, str(e)

    # Die Installationsroutine kann eine Ebene tiefer liegen.
    skript = None
    for wurzel, _ordner, dateien in os.walk(arbeit):
        if "_install.ps1" in dateien:
            skript = os.path.join(wurzel, "_install.ps1")
            break
    if not skript:
        _log(log, "Im Archiv fehlt _install.ps1.")
        shutil.rmtree(arbeit, ignore_errors=True)
        return False, "Installationsroutine fehlt"

    _log(log, "Installation wird gestartet ...")
    try:
        subprocess.Popen(["powershell", "-NoProfile",
                          "-ExecutionPolicy", "Bypass",
                          "-File", skript])
    except Exception as e:
        _log(log, "Start gescheitert: " + str(e))
        shutil.rmtree(arbeit, ignore_errors=True)
        return False, str(e)

    # Der Arbeitsordner bleibt liegen, bis die Installation ihn nicht
    # mehr braucht - sie kopiert daraus. Beim naechsten Mal wird er
    # ohnehin geleert.
    return True, "Die Installation laeuft. Bitte dem Fenster folgen."


# ---------------------------------------------------------- Ansage ---

def sag(text, log=None):
    """
    Gesprochene Rueckmeldung. Robert ist blind - was geschehen ist,
    muss er hoeren koennen, nicht nur im Protokoll nachlesen.
    """
    sauber = " ".join(str(text).split()).replace("'", "")
    if not sauber:
        return
    befehl = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.Speak('" + sauber + "')")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-Command", befehl],
                         creationflags=getattr(subprocess,
                                               "CREATE_NO_WINDOW", 0))
    except Exception as e:
        _log(log, "Ansage nicht moeglich: " + str(e))
