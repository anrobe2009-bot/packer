"""
Fremdpakete mitliefern.

Alles, was das Programm ausser der Standardbibliothek braucht, wandert in
den Unterordner pakete. Der Starter haengt diesen Ordner vorn an den
Suchpfad - damit findet jedes Python die Pakete, ohne sie zu besitzen.

Auf dem fremden Rechner wird nichts installiert, nichts veraendert, nichts
nachgeladen. Beim Deinstallieren ist alles wieder weg.

Ein Vorbehalt: Pakete mit uebersetzten Teilen wie Pillow oder PySide6 sind
an eine Python-Version gebunden. Das steht in anforderung.json, damit der
Installierer es pruefen kann, bevor er ein fremdes Python auswaehlt.
"""

import json
import os
import shutil
import sys

from paketbau_python import _KEIN_PAKET, _log, _site_packages, \
    _verteilungsordner

# Diese Endungen sind uebersetzt und passen nur zu einer Python-Version.
_BINAER = (".pyd", ".dll", ".so")


def _ist_binaer(ordner):
    for wurzel, _, dateien in os.walk(ordner):
        for d in dateien:
            if d.lower().endswith(_BINAER):
                return True
    return False


def _groesse(ordner):
    return sum(os.path.getsize(os.path.join(w, f))
               for w, _, fs in os.walk(ordner) for f in fs)


def _verteilungsname(modul):
    """
    Vom Modulnamen zum Namen der Paketverwaltung. PIL gehoert zu pillow,
    dotenv zu python-dotenv. Ohne diese Zuordnung findet die Abfrage der
    Abhaengigkeiten nichts.
    """
    try:
        import importlib.metadata as meta
    except Exception:
        return modul
    try:
        tabelle = meta.packages_distributions()
        namen = tabelle.get(modul) or tabelle.get(modul.lower())
        if namen:
            return namen[0]
    except Exception:
        pass
    return modul


def _module_von(verteilung):
    """Umgekehrt: welche Module bringt ein Paket mit."""
    try:
        import importlib.metadata as meta
    except Exception:
        return [verteilung]
    treffer = []
    try:
        for modul, namen in meta.packages_distributions().items():
            for n in namen:
                if n.lower().replace("-", "_") == \
                        verteilung.lower().replace("-", "_"):
                    treffer.append(modul)
    except Exception:
        pass
    return treffer or [verteilung]


def _pflicht_abhaengigkeiten(verteilung):
    """
    Die Pakete, ohne die das genannte nicht laeuft.

    Ausgelassen wird alles mit extra == im Bedingungsteil: das sind
    Zusaetze fuer besondere Verwendungen, die niemand braucht, der das
    Paket normal benutzt.
    """
    try:
        import importlib.metadata as meta
    except Exception:
        return []
    try:
        angaben = meta.requires(verteilung) or []
    except Exception:
        return []
    raus = []
    for zeile in angaben:
        if "extra ==" in zeile:
            continue
        # Bedingungen auswerten statt ueberlesen. pynput nennt evdev fuer
        # Linux, webview nennt pyobjc fuer macOS - beides gilt hier nie.
        if ";" in zeile:
            bedingung = zeile.split(";", 1)[1].lower()
            if "sys_platform" in bedingung or "platform_system" in bedingung:
                if not ("win32" in bedingung or "windows" in bedingung):
                    continue
        name = zeile.split(";")[0].strip()
        for trenner in ("<", ">", "=", "!", "~", "[", " ", "("):
            name = name.split(trenner)[0]
        name = name.strip()
        if name:
            raus.append(name)
    return raus


def _ist_intern(name):
    """
    Hilfsmodule, die zu einem Paket gehoeren, aber nie eingebunden
    werden. Etwa 81d243bd2c585b0f4821__mypyc von charset_normalizer.
    """
    n = name.strip()
    if not n:
        return True
    if n.endswith("__mypyc"):
        return True
    if n[0].isdigit():
        return True
    if n.startswith("_") and not n.startswith("_cffi"):
        return True
    return False


def mit_abhaengigkeiten(module, log=None):
    """
    Aus der Liste der eingebundenen Module die vollstaendige Liste der
    mitzunehmenden Module machen - samt allem, was sie ihrerseits
    brauchen.
    """
    offen = [m for m in module if m.lower() not in _KEIN_PAKET]
    fertig = []
    gesehen = set()
    dazu = []

    while offen:
        modul = offen.pop(0)
        if modul.lower() in gesehen or modul.lower() in _KEIN_PAKET:
            continue
        gesehen.add(modul.lower())
        fertig.append(modul)

        verteilung = _verteilungsname(modul)
        for abhaengig in _pflicht_abhaengigkeiten(verteilung):
            for weiteres in _module_von(abhaengig):
                if weiteres.lower() in gesehen:
                    continue
                if weiteres.lower() in _KEIN_PAKET:
                    continue
                offen.append(weiteres)
                dazu.append(weiteres)

    if dazu:
        _log(log, "Zusaetzlich noetig, weil andere Pakete sie brauchen: "
             + ", ".join(sorted(set(dazu))))
    return fertig


def _wo_liegt(modul):
    """
    Wo liegt ein Modul auf dieser Platte?

    Python weiss es selbst. find_spec kennt jeden Ordner im Suchpfad,
    auch die aus pth-Dateien - kis_toene liegt ausserhalb aller
    site-packages und wird nur so gefunden.

    Zurueck kommt eine Liste von Pfaden: der Ordner oder die Datei des
    Moduls, dazu die zugehoerigen Angaben der Paketverwaltung, falls das
    Programm sie zur Laufzeit abfragt.
    """
    import importlib.util

    orte = []
    try:
        spec = importlib.util.find_spec(modul)
    except Exception:
        spec = None

    if spec is not None:
        if spec.submodule_search_locations:
            for ort in list(spec.submodule_search_locations):
                if os.path.isdir(ort):
                    orte.append(ort)
                    break
        elif spec.origin and os.path.isfile(spec.origin):
            orte.append(spec.origin)

    # Nachbarn und Angaben daneben.
    #
    # numpy legt seine uebersetzten Bibliotheken nicht in den eigenen
    # Ordner, sondern in numpy.libs daneben. Ohne ihn bricht numpy beim
    # ersten Aufruf ab mit DLL load failed. soundfile und sounddevice
    # halten es ebenso mit _soundfile_data und _sounddevice_data.
    #
    # Die dist-info kommt mit, damit importlib.metadata beim Empfaenger
    # dieselben Auskuenfte geben kann wie hier.
    if orte:
        elternteil = os.path.dirname(orte[0])
        klein = modul.lower().replace("-", "_")
        nachbarn = (klein + ".libs", klein + ".dlls",
                    "_" + klein + "_data", klein + "_libs")
        try:
            for eintrag in os.listdir(elternteil):
                e = eintrag.lower().replace("-", "_")
                voll = os.path.join(elternteil, eintrag)
                if voll in orte:
                    continue
                if e in nachbarn:
                    orte.append(voll)
                elif e.startswith(klein + "-") and e.endswith(
                        (".dist-info", ".egg-info")):
                    orte.append(voll)
        except Exception:
            pass
        return orte

    # Rueckfall: die alte Suche ueber site-packages, fuer Namen die sich
    # nicht einbinden lassen.
    sp = _site_packages()
    if sp:
        for eintrag in _verteilungsordner(sp, modul):
            orte.append(os.path.join(sp, eintrag))
    return orte


def sammle(app_dir, pakete, log=None):
    """
    Kopiert die genannten Pakete nach app_dir\\pakete und schreibt daneben
    anforderung.json. Gibt die Angaben als dict zurueck.
    """
    ziel = os.path.join(app_dir, "pakete")
    if os.path.isdir(ziel):
        shutil.rmtree(ziel, ignore_errors=True)

    gebraucht = mit_abhaengigkeiten(pakete, log)
    angaben = {
        "python": "{}.{}".format(*sys.version_info[:2]),
        "binaer": False,
        "pakete": [],
        "fehlend": [],
    }

    if not gebraucht:
        _log(log, "Keine Fremdpakete noetig - kein Ordner pakete angelegt.")
        _schreibe_anforderung(app_dir, angaben)
        return angaben

    os.makedirs(ziel, exist_ok=True)
    for paket in gebraucht:
        orte = _wo_liegt(paket)
        if not orte:
            _log(log, "NICHT GEFUNDEN: " + paket
                 + " - das Programm koennte beim Empfaenger scheitern.")
            angaben["fehlend"].append(paket)
            continue
        for quelle in orte:
            zielp = os.path.join(ziel, os.path.basename(quelle))
            try:
                if os.path.isdir(quelle):
                    shutil.copytree(
                        quelle, zielp, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", "tests", "test"))
                else:
                    shutil.copy2(quelle, zielp)
            except Exception as fehler:
                _log(log, "Fehler bei " + os.path.basename(quelle)
                     + ": " + str(fehler))
        angaben["pakete"].append(paket)
        _log(log, "Mitgenommen: " + paket)

    angaben["binaer"] = _ist_binaer(ziel)
    _log(log, "Ordner pakete: {:.1f} MB, {}".format(
        _groesse(ziel) / 1_048_576,
        "uebersetzte Teile enthalten - an Python "
        + angaben["python"] + " gebunden" if angaben["binaer"]
        else "reines Python, versionsunabhaengig"))

    _schreibe_anforderung(app_dir, angaben)
    return angaben


def _schreibe_anforderung(app_dir, angaben):
    """
    Sagt dem Installierer, was das Programm braucht. Ohne diese Datei
    muesste er raten, ob ein gefundenes Python passt.
    """
    pfad = os.path.join(app_dir, "anforderung.json")
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(angaben, f, ensure_ascii=True, indent=2)
    return pfad


STARTER = '''"""
Startet {name}.

Diese Datei ist der Einstieg. Sie haengt den Ordner pakete vorn an den
Suchpfad, damit die mitgelieferten Fremdpakete gefunden werden, und
uebergibt danach an das eigentliche Programm.

Nicht loeschen. Die Verknuepfung auf dem Desktop zeigt hierher.
"""

import os
import runpy
import sys

_hier = os.path.dirname(os.path.abspath(__file__))

# Dem Programm sagen, dass es die ausgelieferte Fassung ist. Diese beiden
# Angaben setzte frueher PyInstaller; Programme fragen sie ab, um private
# Entwicklerpfade nur waehrend der Entwicklung zu verwenden. Ohne sie
# greift das verschenkte Programm auf Ordner zu, die es beim Empfaenger
# nicht gibt.
sys.frozen = True
sys._MEIPASS = _hier

_pakete = os.path.join(_hier, "pakete")
if os.path.isdir(_pakete) and _pakete not in sys.path:
    sys.path.insert(0, _pakete)

if _hier not in sys.path:
    sys.path.insert(0, _hier)

os.chdir(_hier)

# Die Einfuehrung beim allerersten Start. Sie laeuft vor dem Programm
# und merkt sich unter APPDATA, dass sie gezeigt wurde. Scheitert sie,
# startet das Programm trotzdem - sie darf nie im Weg stehen.
try:
    import einfuehrung_zeigen
    einfuehrung_zeigen.einmalig(_hier, "{name}")
except Exception:
    pass

_ziel = os.path.join(_hier, "{einstieg}")
sys.argv = [_ziel] + sys.argv[1:]
runpy.run_path(_ziel, run_name="__main__")
'''


def schreibe_starter(app_dir, name, einstieg, log=None):
    """Legt starter.py an. Darauf zeigt spaeter die Verknuepfung."""
    pfad = os.path.join(app_dir, "starter.py")
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(STARTER.format(name=name, einstieg=einstieg))
    _log(log, "starter.py geschrieben, Einstieg: " + einstieg)
    return pfad


def probelauf(app_dir, python_exe=None, log=None):
    """
    Startet den Starter einmal mit abgeschalteter Oberflaeche und prueft,
    ob alle Pakete geladen werden koennen. Findet Fehler beim Bauen statt
    beim Empfaenger.
    """
    import subprocess

    if not python_exe:
        eigen = os.path.join(app_dir, "python", "python.exe")
        python_exe = eigen if os.path.exists(eigen) else sys.executable

    anf = os.path.join(app_dir, "anforderung.json")
    try:
        with open(anf, "r", encoding="utf-8") as f:
            pakete = json.load(f).get("pakete", [])
    except Exception:
        pakete = []
    pakete = [p for p in pakete if not _ist_intern(p)]
    if not pakete:
        _log(log, "Keine Pakete zu pruefen.")
        return True, ""

    kode = ("import sys, os; sys.path.insert(0, os.path.join(r'"
            + app_dir + "', 'pakete'));"
            + "".join("import " + p + ";" for p in pakete)
            + "print('ok')")
    try:
        p = subprocess.run([python_exe, "-c", kode], capture_output=True,
                           text=True, timeout=60,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        if "ok" in (p.stdout or ""):
            _log(log, "Paketpruefung bestanden: " + ", ".join(pakete))
            return True, ""
        zeilen = (p.stderr or p.stdout or "").strip().splitlines()
        grund = zeilen[-1] if zeilen else "unbekannt"
        _log(log, "Paketpruefung gescheitert: " + grund)
        return False, grund
    except Exception as e:
        _log(log, "Paketpruefung nicht moeglich: " + str(e))
        return False, str(e)
