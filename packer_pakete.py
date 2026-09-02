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

from packer_python import _KEIN_PAKET, _log, _site_packages, \
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
    # packages_distributions nennt auch __pycache__: Python sieht es
    # als Namensraum-Paket ueber alle site-packages hinweg. Wer das
    # glaubt, kopiert fremde .pyc ins Paket und macht damit den Import
    # beim naechsten Lauf kaputt. Gemessen am 22.08.2026.
    # Gemessen, nicht geraten. Frueher stand hier _ist_intern -
    # das verwarf auch echte Dateien, deren Name mit einer Ziffer
    # beginnt, und der Empfaenger stand ohne sie da.
    # Gemessen am 23.08.2026 an schreiber.
    treffer = [t for t in treffer if _mitnehmbar(t)]
    return treffer or [verteilung]


def _version_gilt(bedingung):
    """
    Prueft die python_version-Bedingung einer Abhaengigkeit gegen die
    laufende Python-Version. Was nicht sicher zu lesen ist, gilt - eine
    falsch verworfene Abhaengigkeit faellt erst beim Empfaenger auf.
    """
    import re
    hier = sys.version_info[:2]
    for zeichen, zahl in re.findall(
            r"python_version\s*(<=|>=|==|!=|<|>)\s*[\"']([0-9.]+)[\"']",
            bedingung):
        try:
            teile = tuple(int(t) for t in zahl.split(".")[:2])
        except ValueError:
            continue
        while len(teile) < 2:
            teile = teile + (0,)
        if zeichen == "<" and not (hier < teile):
            return False
        if zeichen == "<=" and not (hier <= teile):
            return False
        if zeichen == ">" and not (hier > teile):
            return False
        if zeichen == ">=" and not (hier >= teile):
            return False
        if zeichen == "==" and not (hier == teile):
            return False
        if zeichen == "!=" and not (hier != teile):
            return False
    return True


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
            # Versionsbedingungen gelten fuer die Version, gegen die
            # gebaut wird. aiohttp nennt async-timeout nur fuer
            # python_version < "3.11" - unter 3.13 gibt es das Paket
            # gar nicht, und die Warnung war jedesmal grundlos.
            if "python_version" in bedingung and not _version_gilt(bedingung):
                continue
        name = zeile.split(";")[0].strip()
        for trenner in ("<", ">", "=", "!", "~", "[", " ", "("):
            name = name.split(trenner)[0]
        name = name.strip()
        if name:
            raus.append(name)
    return raus


def _mitnehmbar(name):
    """Gehoert dieser Name ins Paket?

    Gemessen, nicht nach dem Namen beurteilt: Was find_spec mit
    einer echten Datei findet, wird mitgenommen. Namensraum-Pakete
    ohne eigene Datei fallen weg - darunter __pycache__, das
    packages_distributions faelschlich als Paket meldet.

    Der Unterschied zu _ist_intern: Dort geht es um die Frage, ob
    ein Name in einer import-Zeile stehen KANN. Hier geht es darum,
    ob eine Datei existiert, die der Empfaenger braucht. Das ist
    nicht dasselbe. 81d243bd2c585b0f4821__mypyc laesst sich nie
    schreiben, wird aber zur Laufzeit von yarl nachgeladen und muss
    darum mit.
    """
    n = (name or "").strip()
    if not n:
        return False
    # Pfade statt Modulnamen, etwa PySide6/Qt3DCore.
    if "/" in n or "\\" in n:
        return False
    import importlib.util
    import importlib.util
    try:
        spec = importlib.util.find_spec(n)
    except (ImportError, ValueError, AttributeError):
        # Nur was beim Suchen selbst schiefgehen KANN. Ein
        # breites except verschluckt Tippfehler und fehlende
        # Importe - dann meldet der Bau still, es gebe nichts
        # mitzunehmen. Gemessen am 23.08.2026.
        return False
    # origin ist None bei Namensraum-Paketen: kein Ordner, keine
    # Datei, nichts zu kopieren.
    return bool(spec and spec.origin)


def _ist_intern(name):
    """
    Hilfsmodule, die zu einem Paket gehoeren, aber nie eingebunden
    werden. Etwa 81d243bd2c585b0f4821__mypyc von charset_normalizer.
    """
    n = name.strip()
    if not n:
        return True
    # packages_distributions nennt auch Pfade statt Modulnamen -
    # PySide6/Qt3DCore etwa. find_spec kann so etwas nie finden, und
    # der Bau meldete jedesmal eine Luecke, die keine war.
    if "/" in n or "\\" in n:
        return True
    if n.endswith("__mypyc"):
        return True
    if n[0].isdigit():
        return True
    if n.startswith("_") and not n.startswith("_cffi"):
        return True
    return False


def _ohne_zwillinge(namen):
    """
    Entfernt Namen, die nur eine andere Schreibweise desselben Pakets
    sind. packages_distributions nennt Shiboken und shiboken6 als
    getrennte Eintraege, obwohl die C-Erweiterung im Ordner shiboken6
    liegt und mit ihm kommt. Gesucht wurde sie bisher einzeln - und
    jeder Bau meldete eine Luecke, die keine war.

    Wegfallen darf ein Name nur, wenn ein anderer mit ihm beginnt und
    danach ausschliesslich Ziffern folgen. So trifft die Regel
    shiboken6, aber niemals attr neben attrs.

    Zurueck kommen die verbliebenen Namen und die entfernten.
    """
    weg = []
    behalten = []
    for name in namen:
        klein = name.lower()
        zwilling = False
        for anderer in namen:
            k2 = anderer.lower()
            if k2 == klein or not k2.startswith(klein):
                continue
            if k2[len(klein):].isdigit():
                zwilling = True
                break
        if zwilling:
            weg.append(name)
        else:
            behalten.append(name)
    return behalten, weg


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

    fertig, weg = _ohne_zwillinge(fertig)
    if weg:
        dazu = [d for d in dazu if d not in weg]
    if dazu:
        _log(log, "Zusaetzlich noetig, weil andere Pakete sie brauchen: "
             + ", ".join(sorted(set(dazu))))
    return fertig


# --- pywin32 ---------------------------------------------------------
# Kein normales Paket. Am 28.08.2026 gemessen: pythoncom.py und der
# Ordner win32com lagen im Paket, luden aber nicht - es fehlte
# pywintypes aus win32\lib und die DLL aus pywin32_system32.

PYWIN32_MODULE = ("pythoncom", "pywintypes", "win32com", "win32api",
                  "win32gui", "win32con", "win32file", "win32event",
                  "win32clipboard", "win32process", "win32security",
                  "win32service", "win32serviceutil", "win32print",
                  "win32ui", "winerror", "servicemanager")

PYWIN32_ORDNER = ("win32", "pywin32_system32", "win32comext",
                  "pythonwin")


def _pywin32_orte():
    r"""Die Ordner, ohne die pywin32 beim Empfaenger nicht laedt.

    win32 bringt win32\lib mit, dort liegt pywintypes.py.
    pywin32_system32 haelt pywintypes313.dll und pythoncom313.dll -
    ohne sie meldet jeder Import einen ImportError, obwohl der
    Quelltext danebenliegt.
    """
    import importlib.util
    orte = []
    wurzel = ""
    for probe in ("pythoncom", "win32com", "pywintypes"):
        try:
            spec = importlib.util.find_spec(probe)
        except Exception:
            spec = None
        if spec is None:
            continue
        if spec.submodule_search_locations:
            pfad = list(spec.submodule_search_locations)[0]
            wurzel = os.path.dirname(pfad)
        elif spec.origin:
            wurzel = os.path.dirname(spec.origin)
        # pywintypes liegt in win32\lib - zwei Ebenen hoeher.
        if os.path.basename(wurzel).lower() == "lib":
            wurzel = os.path.dirname(os.path.dirname(wurzel))
        if wurzel and os.path.isdir(wurzel):
            break
    if not wurzel or not os.path.isdir(wurzel):
        return orte
    for name in PYWIN32_ORDNER:
        pfad = os.path.join(wurzel, name)
        if os.path.isdir(pfad):
            orte.append(pfad)
    return orte


# --- Module, die allen Werkzeugen gehoeren ---------------------------
# Nach Regel 651 liegen vorlesen.py, umlaute.py und Geschwister in
# start\gemeinsam. Dieser Ordner steht in keinem Suchpfad, also
# findet find_spec sie nicht.
#
# Am 01.09.2026 kostete das die halbe AI Terminal Bridge: vorlesen
# wurde nicht gefunden, damit auch nicht dessen edge_tts, damit auch
# nicht aiohttp, certifi und elf weitere.

EIGENE_ORDNER = (
    r"C:\Users\Entwickler\Desktop\start\gemeinsam",
    r"C:\Users\Entwickler\Desktop\start\assistenz",
    r"C:\Users\Entwickler\Desktop\start\sound",
)


def _eigenes_modul(modul):
    """Sucht ein Modul in Roberts gemeinsamen Ordnern.

    Zurueck kommt eine Liste mit dem Pfad oder eine leere Liste.
    Gesucht wird NAME.py und der Ordner NAME mit __init__.py.
    """
    for ordner in EIGENE_ORDNER:
        if not os.path.isdir(ordner):
            continue
        datei = os.path.join(ordner, modul + ".py")
        if os.path.isfile(datei):
            return [datei]
        paket = os.path.join(ordner, modul)
        if os.path.isfile(os.path.join(paket, "__init__.py")):
            return [paket]
    return []


def _importe_von(pfad):
    """Welche Fremdpakete ein einzelnes Modul selbst braucht.

    Ohne diesen Schritt kaeme vorlesen.py mit, aber edge_tts nicht -
    und das Programm stuerbe beim ersten Sprechen.
    """
    namen = set()
    try:
        with open(pfad, "r", encoding="utf-8", errors="replace") as f:
            baum = ast.parse(f.read())
    except Exception:
        return namen
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for a in knoten.names:
                namen.add(a.name.split(".")[0])
        elif isinstance(knoten, ast.ImportFrom):
            if not knoten.level and knoten.module:
                namen.add(knoten.module.split(".")[0])
    return namen


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

    # pywin32 verteilt sich auf mehrere Ordner und wird von einer
    # eigenen pth-Datei zusammengehalten. Keine Namensregel trifft
    # das, deshalb benannt - wie ffmpeg in ERLAUBTE_PROGRAMME.
    #
    # pythoncom und win32com melden beide, es fehle pywintypes. Das
    # liegt in win32\\lib, die zugehoerige DLL in pywin32_system32.
    #
    # Diese Zeilen muessen VOR dem return im Nachbarn-Block stehen -
    # am 28.08.2026 standen sie dahinter und liefen nie.
    if modul.lower() in PYWIN32_MODULE:
        for _ort in _pywin32_orte():
            if _ort not in orte:
                orte.append(_ort)

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
        # _NAME.py neben NAME.py: sounddevice und soundfile halten
        # ihren Kern dort. Am 28.08.2026 gemessen - ohne diese Zeile
        # laedt sounddevice beim Empfaenger nicht, obwohl die Datei
        # sounddevice.py im Paket liegt.
        nachbarn = (klein + ".libs", klein + ".dlls",
                    "_" + klein + "_data", klein + "_libs",
                    "_" + klein, "_" + klein + ".py")
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

    # Roberts gemeinsame Module. Sie liegen ausserhalb aller
    # site-packages und werden von find_spec nie gefunden.
    eigen = _eigenes_modul(modul)
    if eigen:
        return eigen

    # Rueckfall: die alte Suche ueber site-packages, fuer Namen die sich
    # nicht einbinden lassen.
    sp = _site_packages()
    if sp:
        for eintrag in _verteilungsordner(sp, modul):
            orte.append(os.path.join(sp, eintrag))
    return orte



# --- Qt ausduennen ---------------------------------------------------
# PySide6 bringt 634 MB mit, darunter einen vollstaendigen Browser
# (Qt6WebEngineCore.dll, 195 MB). Ein Widgets-Programm nutzt davon
# nichts. Was bleiben muss, wird nicht aufgeschrieben, sondern aus den
# Dateikoepfen gelesen - siehe _pe_importe.

QT_IMMER = ("QtCore", "QtGui")

# Ordner, die zur Laufzeit nie geoeffnet werden.
QT_ORDNER_RAUS = (
    "include", "typesystems", "glue", "doc", "scripts",
    "metatypes", "qml", "resources", "translations",
)

# Qt oeffnet diese Ordner erst zur Laufzeit; sie stehen in keiner
# Importtabelle. Ohne platforms erscheint kein Fenster.
QT_PLUGINS_BLEIBEN = (
    "platforms", "styles", "imageformats", "iconengines",
    "platforminputcontexts", "generic",
)

# Grafik-Rueckfall und Laufzeitbibliotheken. Sie haengen an keinem
# Modul, fehlen aber auf fremden Rechnern schnell.
QT_IMMER_DATEIEN = (
    "opengl32sw.dll", "d3dcompiler_47.dll",
    "libegl.dll", "libglesv2.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll",
)


def _pe_importe(pfad):
    """
    Liest aus dem Dateikopf einer DLL, EXE oder PYD, welche anderen
    Dateien sie braucht. Gibt die Namen klein zurueck, bei Unlesbarem
    eine leere Menge - eine unlesbare Datei fuehrt nie zum Loeschen.
    """
    import struct
    try:
        with open(pfad, "rb") as f:
            roh = f.read()
    except Exception:
        return set()
    try:
        if roh[:2] != b"MZ":
            return set()
        pe = struct.unpack_from("<I", roh, 0x3C)[0]
        if roh[pe:pe + 4] != b"PE\0\0":
            return set()
        opt_groesse = struct.unpack_from("<H", roh, pe + 20)[0]
        magie = struct.unpack_from("<H", roh, pe + 24)[0]
        verzeichnis = pe + 24 + (96 if magie == 0x10B else 112)
        import_rva = struct.unpack_from("<I", roh, verzeichnis + 8)[0]
        if not import_rva:
            return set()
        abschnitte = []
        start = pe + 24 + opt_groesse
        anzahl = struct.unpack_from("<H", roh, pe + 6)[0]
        for i in range(anzahl):
            s = start + i * 40
            v_adr = struct.unpack_from("<I", roh, s + 12)[0]
            v_gr = struct.unpack_from("<I", roh, s + 8)[0]
            r_zeiger = struct.unpack_from("<I", roh, s + 20)[0]
            abschnitte.append((v_adr, v_gr, r_zeiger))

        def dateilage(rva):
            for v_adr, v_gr, r_zeiger in abschnitte:
                if v_adr <= rva < v_adr + max(v_gr, 1):
                    return r_zeiger + (rva - v_adr)
            return None

        lage = dateilage(import_rva)
        if lage is None:
            return set()
        namen = set()
        while True:
            block = roh[lage:lage + 20]
            if len(block) < 20 or block == b"\0" * 20:
                break
            name_rva = struct.unpack_from("<I", block, 12)[0]
            if not name_rva:
                break
            nl = dateilage(name_rva)
            if nl is not None:
                ende = roh.find(b"\0", nl)
                if ende > nl:
                    namen.add(roh[nl:ende].decode("ascii",
                                                  "ignore").lower())
            lage += 20
        return namen
    except Exception:
        return set()


def _qt_module_im_projekt(quell_dir):
    """
    Liest alle .py des Projekts und gibt die benutzten PySide6-Module
    zurueck. Findet er keine, gibt er None zurueck - dann bleibt
    PySide6 unangetastet.
    """
    if not quell_dir or not os.path.isdir(quell_dir):
        return None
    import re
    muster = re.compile(r"PySide6\.(Qt\w+)")
    gefunden = set()
    dateien = 0
    for wurzel, ordner, namen in os.walk(quell_dir):
        ordner[:] = [o for o in ordner
                     if o not in ("__pycache__", "werkzeug", ".git")]
        for name in namen:
            if not name.endswith(".py"):
                continue
            dateien += 1
            try:
                with open(os.path.join(wurzel, name), "r",
                          encoding="utf-8", errors="ignore") as f:
                    for treffer in muster.findall(f.read()):
                        gefunden.add(treffer)
            except Exception:
                pass
    if not dateien or not gefunden:
        return None
    gefunden.update(QT_IMMER)
    return gefunden


def _qt_kette(qt, module):
    """
    Verfolgt von den benutzten Modulen aus die Importtabellen, bis
    nichts Neues mehr dazukommt. Gibt die Namen aller Dateien zurueck,
    die im Ordner PySide6 bleiben muessen, klein geschrieben.
    """
    vorhanden = {}
    for eintrag in os.listdir(qt):
        if os.path.isfile(os.path.join(qt, eintrag)):
            vorhanden[eintrag.lower()] = eintrag

    behalten = set()
    offen = []

    def aufnehmen(name):
        klein = name.lower()
        if klein in vorhanden and klein not in behalten:
            behalten.add(klein)
            offen.append(vorhanden[klein])

    for modul in module:
        aufnehmen(modul + ".pyd")
        aufnehmen(modul + ".abi3.pyd")
        aufnehmen("Qt6" + modul[2:] + ".dll")
    for name in QT_IMMER_DATEIEN:
        aufnehmen(name)
    for klein in list(vorhanden):
        if klein.startswith("pyside6") or klein.startswith("shiboken"):
            aufnehmen(klein)

    # Plugins stehen in keiner Importtabelle - Qt laedt sie zur
    # Laufzeit. Ihre eigenen Abhaengigkeiten zaehlen aber mit.
    plugins = os.path.join(qt, "plugins")
    if os.path.isdir(plugins):
        for unter in QT_PLUGINS_BLEIBEN:
            pfad = os.path.join(plugins, unter)
            if not os.path.isdir(pfad):
                continue
            for datei in os.listdir(pfad):
                for gebraucht in _pe_importe(os.path.join(pfad, datei)):
                    aufnehmen(gebraucht)

    while offen:
        datei = offen.pop()
        for gebraucht in _pe_importe(os.path.join(qt, datei)):
            aufnehmen(gebraucht)

    return behalten


def _qt_probelauf(ziel, module, log=None):
    """
    Startet das ausgeduennte PySide6 mit einem echten QApplication und
    einem Fenster. Der Paketordner steht vorn im Suchpfad, damit nicht
    das vollstaendige PySide6 des Baurechners einspringt. Gibt True
    zurueck, wenn es traegt.
    """
    import subprocess
    zeilen = ["import sys"]
    for modul in sorted(module):
        zeilen.append("import PySide6." + modul)
    zeilen.append("from PySide6.QtWidgets import QApplication, QWidget")
    zeilen.append("a = QApplication([])")
    zeilen.append("w = QWidget()")
    zeilen.append("w.show()")
    zeilen.append("a.processEvents()")
    zeilen.append("print('QT-OK')")
    kode = "\n".join(zeilen)

    umgebung = dict(os.environ)
    umgebung["PYTHONPATH"] = ziel
    umgebung["QT_QPA_PLATFORM"] = "offscreen"
    try:
        lauf = subprocess.run(
            [sys.executable, "-c", kode],
            capture_output=True, text=True, timeout=90,
            env=umgebung, cwd=ziel)
    except Exception as fehler:
        _log(log, "Probelauf nicht moeglich: " + str(fehler))
        return False
    if lauf.returncode == 0 and "QT-OK" in (lauf.stdout or ""):
        return True
    meldung = (lauf.stderr or lauf.stdout or "").strip().splitlines()
    _log(log, "Probelauf gescheitert: "
         + (meldung[-1] if meldung else "ohne Meldung"))
    return False


def _qt_ausduennen(ziel, quell_dir, log=None):
    """
    Duennt PySide6 aus. Aussortiertes geht zuerst nach _verworfen;
    geloescht wird erst, wenn der Probelauf bestanden ist.
    """
    qt = os.path.join(ziel, "PySide6")
    if not os.path.isdir(qt):
        return
    module = _qt_module_im_projekt(quell_dir)
    if not module:
        _log(log, "PySide6 bleibt vollstaendig - keine Importe gemessen.")
        return

    vorher = _groesse(qt)
    _log(log, "Qt-Module benutzt: " + ", ".join(sorted(module)))
    behalten = _qt_kette(qt, module)
    _log(log, "Gemessen ueber die Dateikoepfe: {} Dateien noetig.".format(
        len(behalten)))

    verworfen = os.path.join(ziel, "_verworfen")
    shutil.rmtree(verworfen, ignore_errors=True)
    os.makedirs(verworfen, exist_ok=True)

    for eintrag in os.listdir(qt):
        pfad = os.path.join(qt, eintrag)
        if os.path.isdir(pfad):
            if eintrag == "plugins":
                for unter in os.listdir(pfad):
                    if unter not in QT_PLUGINS_BLEIBEN:
                        shutil.move(os.path.join(pfad, unter),
                                    os.path.join(verworfen,
                                                 "plugins_" + unter))
                continue
            if eintrag in QT_ORDNER_RAUS or eintrag == "__pycache__":
                shutil.move(pfad, os.path.join(verworfen, eintrag))
            continue
        klein = eintrag.lower()
        if klein in behalten:
            continue
        if klein.endswith((".dll", ".exe", ".pyd", ".pyi", ".lib")):
            shutil.move(pfad, os.path.join(verworfen, eintrag))

    if _qt_probelauf(ziel, module, log):
        shutil.rmtree(verworfen, ignore_errors=True)
        nachher = _groesse(qt)
        _log(log, "PySide6 ausgeduennt: {:.1f} MB statt {:.1f} MB - "
                  "Probelauf bestanden.".format(
                      nachher / 1_048_576, vorher / 1_048_576))
        return

    # Zurueck auf Anfang. Ein grosses Paket ist besser als ein totes.
    for eintrag in os.listdir(verworfen):
        quelle = os.path.join(verworfen, eintrag)
        if eintrag.startswith("plugins_"):
            zurueck = os.path.join(qt, "plugins", eintrag[8:])
        else:
            zurueck = os.path.join(qt, eintrag)
        try:
            shutil.move(quelle, zurueck)
        except Exception:
            pass
    shutil.rmtree(verworfen, ignore_errors=True)
    _log(log, "ACHTUNG: Ausduennung zurueckgenommen, PySide6 bleibt "
              "vollstaendig ({:.1f} MB).".format(vorher / 1_048_576))


def sammle(app_dir, pakete, log=None, quell_dir=None, weiche=None):
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
    # Was ein eigenes Modul seinerseits braucht, gehoert dazu. Ohne
    # diesen Durchgang kaeme vorlesen.py mit, edge_tts aber nicht.
    nachgezogen = []
    for paket in list(gebraucht):
        for ort in _wo_liegt(paket):
            if not ort.lower().endswith(".py"):
                continue
            if not any(ort.lower().startswith(o.lower())
                       for o in EIGENE_ORDNER):
                continue
            for weiterer in sorted(_importe_von(ort)):
                if weiterer in gebraucht or weiterer in nachgezogen:
                    continue
                if weiterer in sys.stdlib_module_names:
                    continue
                if not _wo_liegt(weiterer):
                    continue
                nachgezogen.append(weiterer)
    if nachgezogen:
        _log(log, "Aus eigenen Modulen nachgezogen: "
             + ", ".join(nachgezogen))
        gebraucht = list(gebraucht) + mit_abhaengigkeiten(nachgezogen, log)
        gesehen = []
        for name in gebraucht:
            if name not in gesehen:
                gesehen.append(name)
        gebraucht = gesehen

    for paket in gebraucht:
        orte = _wo_liegt(paket)
        if not orte:
            _log(log, "NICHT GEFUNDEN: " + paket)
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

    _qt_ausduennen(ziel, quell_dir, log)
    angaben["binaer"] = _ist_binaer(ziel)
    _log(log, "Ordner pakete: {:.1f} MB, {}".format(
        _groesse(ziel) / 1_048_576,
        "uebersetzte Teile enthalten - an Python "
        + angaben["python"] + " gebunden" if angaben["binaer"]
        else "reines Python, versionsunabhaengig"))

    # Ein Paket, dessen Kern fehlt, geht nicht hinaus. Bis zum
    # 01.09.2026 wurde nur gemeldet und weitergebaut - die AI
    # Terminal Bridge lag danach stumm auf der Webseite.
    #
    # Robert kann beim Empfaenger nicht nachsehen. Also bricht der
    # Bau ab und nennt jedes fehlende Modul einzeln.
    # Pflicht und Kuer trennen. Ein Import im try ist als
    # verzichtbar gekennzeichnet - das Programm hat einen Rueckfall
    # und laeuft ohne. Ein Import auf oberster Ebene ist Pflicht:
    # fehlt er, stuerzt das Programm beim Start ab.
    weich = set(weiche or ())
    pflicht = [n for n in angaben["fehlend"] if n not in weich]
    kuer = [n for n in angaben["fehlend"] if n in weich]

    # Der Abbruch nennt seine Grundlage. Ohne diese Zeilen sagt er
    # nur, WAS fehlt, nicht WORAUF er sich stuetzt - und die Suche
    # nach der Ursache beginnt jedes Mal von vorn. Am 02.09.2026
    # kostete das vier falsche Vermutungen.
    if angaben["fehlend"]:
        _log(log, "Als abgefangen bekannt: {} Modul(e){}".format(
            len(weich),
            " - " + ", ".join(sorted(weich)) if weich else " (KEINE)"))

    if kuer:
        _log(log, "Nicht gefunden, aber im Kode abgefangen - das "
                  "Programm laeuft ohne sie:")
        for name in kuer:
            _log(log, "   " + name)

    if pflicht:
        _log(log, "ABBRUCH - diese Module werden beim Start gebraucht "
                  "und fehlen dem Empfaenger:")
        for name in pflicht:
            _log(log, "   " + name)
        _log(log, "Sie stehen nicht in einem try, das Programm faengt "
                  "ihr Fehlen also nicht ab.")
        _log(log, "Liegt ein Modul in einem eigenen Ordner, gehoert "
                  "dieser in EIGENE_ORDNER in packer_pakete.py.")
        raise RuntimeError(
            "Nicht gefunden: " + ", ".join(pflicht))

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


def pruefe_archiv(zip_pfad, log=None):
    """
    Packt das fertige Archiv aus und laesst die Paketpruefung darauf los.

    Der Unterschied zur Pruefung im Bauordner ist klein, aber
    entscheidend: Geprueft wird, was tatsaechlich hinausgeht. Was beim
    Packen verlorengeht oder beschaedigt wird, faellt sonst erst beim
    Empfaenger auf - und der kann nichts dagegen tun.

    Gibt (True, Meldung) zurueck, wenn das Paket laedt.
    """
    import tempfile
    import zipfile

    if not zip_pfad or not os.path.exists(zip_pfad):
        return True, "kein Archiv"

    arbeit = tempfile.mkdtemp(prefix="probelauf_archiv_")
    try:
        try:
            with zipfile.ZipFile(zip_pfad) as zf:
                zf.extractall(arbeit)
        except Exception as e:
            _log(log, "Das Archiv liess sich nicht auspacken: " + str(e))
            return False, "Archiv nicht lesbar"

        # Der Programmordner ist der, in dem anforderung.json liegt.
        ziel = None
        for wurzel, _ordner, dateien in os.walk(arbeit):
            if "anforderung.json" in dateien:
                ziel = wurzel
                break
        if not ziel:
            _log(log, "Probelauf im Archiv entfaellt - keine "
                      "anforderung.json im Paket.")
            return True, "nichts zu pruefen"

        _log(log, "Probelauf im ausgepackten Archiv ...")
        return probelauf(ziel, log=log)
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)


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

    # Der entscheidende Unterschied: Mit dem mitgelieferten Python wird
    # NICHTS gesetzt. Die Pakete muessen allein ueber python313._pth
    # gefunden werden - dem Weg, den der Empfaenger geht. Wer hier
    # sys.path ergaenzt, repariert den Fehler waehrend der Messung und
    # bekommt ein bestanden, das nichts wert ist.
    eigenes_python = os.path.normcase(os.path.abspath(python_exe)).startswith(
        os.path.normcase(os.path.abspath(os.path.join(app_dir, "python"))))

    vorspann = ""
    if not eigenes_python:
        # Rueckfall auf das Python des Baurechners. Es hat keine _pth des
        # Pakets, also muss der Ordner hier eingehaengt werden.
        vorspann = ("sys.path.insert(0, os.path.join(r'" + app_dir
                    + "', 'pakete'));")

    kode = ("import sys, os, importlib;" + vorspann
            + "fehlt=[]\n"
            "for n in " + repr(pakete) + ":\n"
            "    try:\n"
            "        importlib.import_module(n)\n"
            "    except BaseException as e:\n"
            "        fehlt.append(n + ' (' + e.__class__.__name__ + ')')\n"
            "print('FEHLT ' + ', '.join(fehlt)) if fehlt"
            " else print('PAKETE_OK')\n"
            "sys.exit(1 if fehlt else 0)\n")
    try:
        # -s und -E sind entscheidend. Ohne sie zieht das mitgelieferte
        # Python das Benutzerverzeichnis des Baurechners herein - bei
        # Robert AppData\Roaming\Python\Python313\site-packages - und
        # findet dort alles, was im Paket fehlt. Gemessen am 25.08.2026:
        # Ein Paket ohne die Zeile ..\pakete in python313._pth bestand
        # die Pruefung trotzdem. Beim Empfaenger gibt es diesen Ordner
        # nicht, und das Programm startet nicht.
        p = subprocess.run([python_exe, "-s", "-E", "-c", kode],
                           capture_output=True,
                           text=True, timeout=120,
                           cwd=app_dir,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        # Auf eine eindeutige Marke pruefen, nicht auf ok irgendwo im
        # Text: shiboken6 enthaelt die Buchstaben ok, und damit bestand
        # am 25.08.2026 ein Paket, in dem alle fuenf Pakete fehlten.
        zeilen_aus = [z.strip() for z in (p.stdout or "").splitlines()]
        if "PAKETE_OK" in zeilen_aus and p.returncode == 0:
            _log(log, "Paketpruefung bestanden ({}): {}".format(
                "eigenes Python" if eigenes_python else "fremdes Python",
                ", ".join(pakete)))
            return True, ""
        zeilen = (p.stdout or "").strip().splitlines()
        fehlend = [z[6:] for z in zeilen if z.startswith("FEHLT ")]
        if fehlend:
            grund = fehlend[0]
        else:
            zeilen = (p.stderr or p.stdout or "").strip().splitlines()
            grund = zeilen[-1] if zeilen else "unbekannt"
        _log(log, "PAKETPRUEFUNG GESCHEITERT - laesst sich nicht laden:")
        _log(log, "   " + grund)
        return False, grund
    except Exception as e:
        _log(log, "Paketpruefung nicht moeglich: " + str(e))
        return False, str(e)
