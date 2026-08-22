# Packer 2.0 - Einzeldateien UND ganze Projekte verpacken
# Neu: Projekt-Modus (Ordner + Startdatei), EXE-Modus via PyInstaller,
#      automatischer Ausschluss ungenutzter Qt-Module, Deinstallation

import os, sys, io, re, json, shutil, subprocess, threading, ast, zipfile, datetime
try: import winsound
except ImportError: winsound = None

# Signaltoene kommen ausschliesslich aus kis_toene. winsound bleibt nur
# als stiller Rueckfall - ein Programm darf nicht abbrechen, weil ein
# Ton nicht kommt.
try:
    import kis_toene
except Exception:
    kis_toene = None


def _ton(art):
    """erfolg, fehler, hinweis oder fertig - zentral geregelt."""
    if kis_toene is not None:
        try:
            getattr(kis_toene, art)()
            return
        except Exception:
            pass
    if winsound:
        try:
            hoehe = {"fehler": 300, "hinweis": 700}.get(art, 1200)
            winsound.Beep(hoehe, 200)
        except Exception:
            pass
import tkinter as tk
from tkinter import filedialog, messagebox

import packer_python as pyb
import packer_pakete as pk
import packer_einfuehrung as pe
import packer_hochladen as ph


def _sucht_eingaben(src_dir):
    """Sucht input()-Aufrufe im Quellcode.

    Ein Programm mit input() bricht als fensterlose EXE mit
    RuntimeError lost sys.stdin ab - es gibt keine Tastatureingabe.
    Der Fehler faellt erst nach dem Verpacken auf und kostet einen
    kompletten Baulauf. Deshalb vorher pruefen.
    """
    import re
    fundstellen = []
    muster = re.compile(r"(?<![\w.])input\s*\(")
    for wurzel, ordner, dateien in os.walk(src_dir):
        ordner[:] = [o for o in ordner
                     if o not in ("__pycache__", "werkzeug", ".git")]
        for name in dateien:
            if not name.lower().endswith(".py"):
                continue
            pfad = os.path.join(wurzel, name)
            try:
                with open(pfad, encoding="utf-8", errors="replace") as f:
                    for nr, zeile in enumerate(f, 1):
                        blank = zeile.strip()
                        if blank.startswith("#"):
                            continue
                        if muster.search(zeile):
                            fundstellen.append((name, nr, blank[:70]))
            except Exception:
                pass
    return fundstellen




def _base():
    return os.path.dirname(sys.executable if getattr(sys,"frozen",False) else os.path.abspath(__file__))

FLAG = os.path.join(_base(), "packer_accepted.flag")
ZIEL_ORDNER = os.path.join(os.environ["USERPROFILE"], "Desktop", "KI Tools Stammtisch")
# Die Marke. Sie steht in packer_marke.json und wird auf jeden Text
# angewendet, der das Haus verlaesst - Installierer, Deinstallierer,
# Dokumentation, Name des Archivs.
#
# Fehlen die Angaben, bleibt alles wie bisher. Wer den Packager uebernimmt,
# traegt hier seine eigenen ein und verschenkt nicht fremde Namen.

VORGABE_FIRMA = "KI Stammtisch Cologne"
VORGABE_KUERZEL = "KI-Stammtisch"


def _marke_lesen():
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "packer_marke.json")
    firma, kuerzel = VORGABE_FIRMA, VORGABE_KUERZEL
    try:
        with open(pfad, "r", encoding="utf-8-sig") as fh:
            angaben = json.load(fh)
        firma = str(angaben.get("firma", "")).strip() or firma
        kuerzel = str(angaben.get("kuerzel", "")).strip() or kuerzel
    except Exception:
        pass
    return firma, kuerzel


FIRMA, KUERZEL = _marke_lesen()


def gepraegt(text):
    """
    Setzt die eigene Marke ein, kurz bevor der Text hinausgeht.

    Der laengere Name zuerst - sonst bliebe von KI Stammtisch Cologne
    nach dem Ersetzen des Kuerzels ein Rest stehen.
    """
    if not text:
        return text
    text = text.replace(VORGABE_FIRMA, FIRMA)
    text = text.replace(VORGABE_KUERZEL, KUERZEL)
    return text


ZIP_PREFIX  = KUERZEL + "_"

LOGO_MEMO = os.path.join(_base(), "packer_logo.txt")
BUILD_LOG = os.path.join(_base(), "packer_build.log")


def _remember_logo_dir(path):
    """Merkt sich, wo die Logos liegen - damit sie nach einem Umzug
    des Packagers nicht wieder gesucht werden muessen."""
    try:
        with open(LOGO_MEMO, "w", encoding="utf-8") as f:
            f.write(os.path.dirname(path))
    except Exception:
        pass


def _remembered_logo_dir():
    try:
        if os.path.exists(LOGO_MEMO):
            d = open(LOGO_MEMO, encoding="utf-8").read().strip()
            if d and os.path.isdir(d):
                return d
    except Exception:
        pass
    return None


def _find_logos():
    """Sucht logo*.png im Packager-Ordner und im zuletzt gemerkten Ordner."""
    found = []
    ordner = [_base()]
    d = _remembered_logo_dir()
    if d and os.path.normcase(d) != os.path.normcase(_base()):
        ordner.append(d)
    for o in ordner:
        try:
            for fn in sorted(os.listdir(o)):
                if fn.lower().startswith("logo") and fn.lower().endswith(".png"):
                    p = os.path.join(o, fn)
                    if p not in found:
                        found.append(p)
        except Exception:
            pass
    found.sort(key=lambda p: os.path.getsize(p))
    return found

def _default_icon_path(logos):
    """Waehlt das kleinste gefundene Logo als Fenster-Icon (typischerweise logo_64.png)."""
    return logos[0] if logos else None

def _logo_bytes(path):
    try:
        with open(path, "rb") as f: return f.read()
    except Exception: return None

def _img(path, size=None):
    from PIL import Image, ImageTk
    img = Image.open(path)
    if size: img = img.resize((size,size), Image.LANCZOS)
    return ImageTk.PhotoImage(img)

def _normalize_name(name):
    """
    Aus einem versehentlich uebernommenen Dateinamen einen Programmnamen
    machen. waechter.py.bak wird zu waechter.

    Abgeschnitten wird wiederholt, weil doppelte Endungen vorkommen. Der
    Name steht spaeter auf dem Splash, im Fenstertitel, auf der
    Verknuepfung und im Ordnernamen - dort hat eine Dateiendung nichts
    zu suchen.
    """
    name = (name or "").strip().strip('"').strip()
    endungen = (".bak", ".orig", ".py", ".pyw", ".pyc", ".tmp", ".txt")
    weiter = True
    while weiter and name:
        weiter = False
        for e in endungen:
            if name.lower().endswith(e) and len(name) > len(e):
                name = name[:-len(e)]
                weiter = True
    # Zeichen, die Windows in Datei- und Ordnernamen verbietet.
    for z in '<>:"/\\|?*':
        name = name.replace(z, "")
    return name.strip(" .")

# ── Splash (Packager selbst, nur beim allerersten Start) ─────────────────────
def show_splash():
    root = tk.Tk()
    root.title("KI Stammtisch Cologne")
    root.configure(bg="#1a2332")
    root.resizable(False, False)
    w,h = 480,620
    sw,sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    accepted=[False]
    logos = _find_logos()
    icon_path = _default_icon_path(logos)
    hero_path = logos[-1] if logos else None
    try:
        if icon_path: ic=_img(icon_path,64); root.iconphoto(True,ic)
    except Exception: pass
    try:
        if not hero_path: raise Exception("kein Logo")
        lg=_img(hero_path,160)
        lb=tk.Label(root,image=lg,bg="#1a2332",bd=0); lb.image=lg; lb.pack(pady=(24,6))
    except Exception:
        tk.Label(root,text="KI STAMMTISCH COLOGNE",bg="#1a2332",fg="#e8edf5",
                 font=("Segoe UI",16,"bold")).pack(pady=(24,6))
    tk.Label(root,text="KI Stammtisch Cologne",bg="#1a2332",fg="#8fa8c8",font=("Segoe UI",11)).pack()
    tk.Frame(root,bg="#2e4060",height=1,width=420).pack(pady=12)
    tk.Label(root,text="Packer",bg="#1a2332",fg="#00e5c8",font=("Segoe UI",20,"bold")).pack()
    tk.Label(root,text="Build-Tool fuer KI Stammtisch Software",bg="#1a2332",fg="#8fa8c8",
             font=("Segoe UI",9)).pack(pady=(2,12))
    tk.Frame(root,bg="#2e4060",height=1,width=420).pack()
    msg=("Dieses Tool ist ausschliesslich fuer die Erstellung von KI Stammtisch Software. "
         "Es wird ohne Gewaehrleistung bereitgestellt. Nutzung auf eigene Gefahr.")
    tk.Label(root,text=msg,bg="#1a2332",fg="#8fa8c8",font=("Segoe UI",9),
             justify="center",wraplength=400).pack(pady=12)
    var=tk.BooleanVar(value=False)
    cb=tk.Checkbutton(root,
        text="Ich habe den Hinweis gelesen und nutze das Tool auf eigene Gefahr.",
        variable=var,bg="#1a2332",fg="#e8edf5",selectcolor="#2e4060",
        activebackground="#1a2332",activeforeground="#00e5c8",
        font=("Segoe UI",10),wraplength=400,justify="left",cursor="hand2")
    cb.pack(padx=20)
    btn=tk.Button(root,text="  Starten  ",
        bg="#0d3b66",fg="#e8edf5",disabledforeground="#4a5a70",
        activebackground="#00e5c8",activeforeground="#1a2332",
        font=("Segoe UI",13,"bold"),bd=0,padx=24,pady=14,
        cursor="hand2",relief="flat",state="disabled")
    btn.pack(pady=(16,24))
    def _cb(*_): btn.config(state="normal" if var.get() else "disabled")
    def _go():
        accepted[0]=True
        try: open(FLAG,"w").write("ok")
        except Exception: pass
        root.destroy()
    def _close(): root.destroy(); sys.exit(0)
    cb.config(command=_cb); btn.config(command=_go)
    root.protocol("WM_DELETE_WINDOW",_close)
    root.mainloop()
    return accepted[0]


# ── Marke und Lizenz ─────────────────────────────────────────────────────────
MARKE_DATEI = os.path.join(_base(), "packer_marke.json")
GPL_CACHE = os.path.join(_base(), "gpl-3.0.txt")
GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.txt"

MARKE_VORGABE = {
    "autor": "KI Stammtisch Cologne",
    "web": "https://ki-stammtisch-cologne.de",
    "lizenz": "GPL-3.0",
}

MIT_TEXT = """MIT License

Copyright (c) ~JAHR~ ~AUTOR~

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

GPL_KURZHINWEIS = """~APP~ - ~KURZ~
Copyright (C) ~JAHR~ ~AUTOR~

Dieses Programm ist freie Software. Sie duerfen es weitergeben und
veraendern, unter den Bedingungen der GNU General Public License,
Version 3, wie von der Free Software Foundation veroeffentlicht.

Die Weitergabe erfolgt in der Hoffnung, dass es nuetzlich ist, aber OHNE
JEDE GEWAEHRLEISTUNG - sogar ohne die implizite Gewaehrleistung der
MARKTREIFE oder der EIGNUNG FUER EINEN BESTIMMTEN ZWECK. Einzelheiten
stehen in der GNU General Public License.

Der vollstaendige Lizenztext liegt in der Datei LICENSE.txt bei und ist
abrufbar unter ~GPLURL~
"""


def lade_marke():
    m = dict(MARKE_VORGABE)
    try:
        if os.path.exists(MARKE_DATEI):
            import json
            m.update(json.load(open(MARKE_DATEI, encoding="utf-8")))
    except Exception:
        pass
    return m


def sichere_marke(m):
    try:
        import json
        with open(MARKE_DATEI, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _gpl_text(log=None):
    """Vollen GPL-Text besorgen. Einmal geholt, danach aus dem Ordner."""
    if os.path.exists(GPL_CACHE):
        try:
            t = open(GPL_CACHE, encoding="utf-8").read()
            if len(t) > 20000:
                return t
        except Exception:
            pass
    try:
        import urllib.request
        with urllib.request.urlopen(GPL_URL, timeout=20) as r:
            t = r.read().decode("utf-8", "replace")
        if len(t) > 20000:
            with open(GPL_CACHE, "w", encoding="utf-8") as f:
                f.write(t)
            if log:
                log("GPL-Text geladen und gesichert: " + GPL_CACHE)
            return t
    except Exception as e:
        if log:
            log(f"GPL-Text konnte nicht geladen werden: {e}")
    return None


def _lizenz_vorhanden(ordner):
    """
    Liegt im Ordner schon eine Lizenzdatei?

    Roberts Projekte bringen eine eigene LICENSE mit, gepflegt und mit
    dem richtigen Urhebervermerk. Der Packager soll sie nicht durch eine
    zweite Datei mit anderem Namen ergaenzen.
    """
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LIZENZ",
                 "LIZENZ.txt", "COPYING"):
        pfad = os.path.join(ordner, name)
        if os.path.exists(pfad) and os.path.getsize(pfad) > 200:
            return name
    return ""


def schreibe_lizenz(ordner, name, marke, kurz, log=None):
    """LICENSE.txt und NOTICE.txt ins Paket legen."""
    jahr = str(datetime.date.today().year)
    autor = marke.get("autor") or MARKE_VORGABE["autor"]
    web = marke.get("web") or ""
    schon_da = _lizenz_vorhanden(ordner)
    if schon_da:
        if log:
            log("Lizenz: " + schon_da + " liegt bereits im Projekt und "
                "bleibt unberuehrt.")
        return

    art = marke.get("lizenz", "MIT")

    if art == "keine":
        if log:
            log("Keine Lizenzdatei gewuenscht.")
        return

    if art == "MIT":
        text = MIT_TEXT.replace("~JAHR~", jahr).replace("~AUTOR~", autor)
        kopf = (f"{name} - {kurz}\n"
                f"Copyright (c) {jahr} {autor}\n\n"
                "Weitergabe und Veraenderung sind erlaubt, solange dieser\n"
                "Urheberhinweis erhalten bleibt. Einzelheiten in LICENSE.txt.\n")
    else:
        voll = _gpl_text(log)
        if voll:
            text = voll
        else:
            text = ("GNU GENERAL PUBLIC LICENSE Version 3\n\n"
                    "Der vollstaendige Lizenztext konnte beim Bauen nicht\n"
                    "geladen werden. Er ist abrufbar unter:\n"
                    + GPL_URL + "\n")
            if log:
                log("ACHTUNG: LICENSE.txt enthaelt nur den Verweis, "
                    "nicht den vollen Text.")
        kopf = (GPL_KURZHINWEIS.replace("~APP~", name)
                .replace("~KURZ~", kurz).replace("~JAHR~", jahr)
                .replace("~AUTOR~", autor).replace("~GPLURL~", GPL_URL))

    with open(os.path.join(ordner, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(text)

    notiz = kopf + "\n"
    if web:
        notiz += f"Original und weitere Werkzeuge: {web}\n"
    notiz += ("\nDieses Programm stammt aus der Werkzeugsammlung des\n"
              f"{autor}. Wer es weitergibt, gibt es bitte vollstaendig\n"
              "weiter - mit dieser Datei und mit LICENSE.txt.\n")
    with open(os.path.join(ordner, "NOTICE.txt"), "w", encoding="utf-8") as f:
        f.write(notiz)
    if log:
        log(f"Lizenz: {art} - LICENSE.txt und NOTICE.txt beigelegt.")


# ── Wrapper-Template (Splash fuer die gebaute App, Logo als Datei mitkopiert) ─
WRAPPER_TPL = r"""
import os, sys
import tkinter as tk
APP_NAME = "~APPNAME~"
def _base():
    return os.path.dirname(sys.executable if getattr(sys,"frozen",False) else os.path.abspath(__file__))
LOGO512_PATH = os.path.join(_base(), "logo512.png")
LOGO64_PATH  = os.path.join(_base(), "logo64.png")
FLAG = os.path.join(_base(), APP_NAME+"_accepted.flag")
def _img(path, size=None):
    from PIL import Image, ImageTk
    img = Image.open(path)
    if size: img = img.resize((size,size), Image.LANCZOS)
    return ImageTk.PhotoImage(img)
def show_splash():
    root = tk.Tk()
    root.title("KI Stammtisch Cologne - "+APP_NAME)
    root.configure(bg="#1a2332")
    root.resizable(False, False)
    w,h = 480,620
    sw,sh = root.winfo_screenwidth(),root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    accepted = [False]
    try:
        ic=_img(LOGO64_PATH,64); root.iconphoto(True,ic)
    except Exception: pass
    try:
        lg=_img(LOGO512_PATH,160)
        lb=tk.Label(root,image=lg,bg="#1a2332",bd=0)
        lb.image=lg; lb.pack(pady=(24,6))
    except Exception:
        tk.Label(root,text="KI STAMMTISCH COLOGNE",bg="#1a2332",fg="#e8edf5",
                 font=("Segoe UI",16,"bold")).pack(pady=(24,6))
    tk.Label(root,text="KI Stammtisch Cologne",bg="#1a2332",fg="#8fa8c8",font=("Segoe UI",11)).pack()
    tk.Frame(root,bg="#2e4060",height=1,width=420).pack(pady=12)
    tk.Label(root,text=APP_NAME,bg="#1a2332",fg="#00e5c8",font=("Segoe UI",20,"bold")).pack()
    tk.Label(root,text="~AUTOR~",bg="#1a2332",fg="#e8edf5",font=("Segoe UI",10)).pack(pady=(4,0))
    tk.Label(root,text="Lizenz: ~LIZENZ~",bg="#1a2332",fg="#8fa8c8",font=("Segoe UI",9)).pack()
    tk.Label(root,text="~WEB~",bg="#1a2332",fg="#00e5c8",font=("Segoe UI",9)).pack()
    tk.Frame(root,bg="#2e4060",height=1,width=420).pack(pady=12)
    msg=("Dieses Tool wird ohne Gewaehrleistung bereitgestellt. Nutzung auf eigene Gefahr. "
         "Weitergabe erwuenscht - bitte vollstaendig, mit LICENSE.txt und NOTICE.txt.")
    tk.Label(root,text=msg,bg="#1a2332",fg="#8fa8c8",font=("Segoe UI",9),
             justify="center",wraplength=400).pack(pady=8)
    var=tk.BooleanVar(value=False)
    cb=tk.Checkbutton(root,
        text="Ich habe den Hinweis gelesen und nutze das Tool auf eigene Gefahr.",
        variable=var,bg="#1a2332",fg="#e8edf5",selectcolor="#2e4060",
        activebackground="#1a2332",activeforeground="#00e5c8",
        font=("Segoe UI",10),wraplength=400,justify="left",cursor="hand2")
    cb.pack(padx=20)
    btn=tk.Button(root,text="  Starten  ",
        bg="#0d3b66",fg="#e8edf5",disabledforeground="#4a5a70",
        activebackground="#00e5c8",activeforeground="#1a2332",
        font=("Segoe UI",13,"bold"),bd=0,padx=24,pady=14,
        cursor="hand2",relief="flat",state="disabled")
    btn.pack(pady=(16,24))
    def _cb(*_): btn.config(state="normal" if var.get() else "disabled")
    def _go():
        accepted[0]=True
        try: open(FLAG,"w").write("ok")
        except Exception: pass
        root.destroy()
    def _close(): root.destroy(); sys.exit(0)
    cb.config(command=_cb)
    btn.config(command=_go)
    root.protocol("WM_DELETE_WINDOW",_close)
    root.mainloop()
    return accepted[0]
if not os.path.exists(FLAG):
    if not show_splash(): sys.exit(0)
"""

# ── Doku-Vorlagen ─────────────────────────────────────────────────────────────
DOKU_HTML_TPL = """<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>~APPNAME~ - Dokumentation</title>
<link rel="stylesheet" href="doku.css"></head><body>
<header><div class="header-inner">
<div class="logo-area"><div class="logo-circle">KI</div>
<div><div class="header-title">~APPNAME~</div>
<div class="header-sub">KI Stammtisch Cologne</div></div></div>
<div class="version">~DATE~</div></div></header>
<main>
<section class="intro"><h1>Was ist ~APPNAME~?</h1>
<p>~BESCHREIBUNG~</p></section>
<section><h2>Installation</h2><ol>
<li>ZIP-Datei entpacken</li>
<li>INSTALLIEREN.bat doppelklicken</li>
<li>Verknuepfung auf dem Desktop erscheint</li>
<li>Rechtsklick darauf -&gt; An Taskleiste anheften</li>
</ol></section>
<section><h2>Funktionen</h2>~FUNKTIONEN~</section>
<section><h2>Erste Nutzung</h2>
<p>Beim ersten Start erscheint ein Hinweisfenster mit Nutzungsbedingungen. 
Checkbox anhaken, Starten klicken. Danach startet ~APPNAME~ immer direkt.</p></section>
<footer><p>KI Stammtisch Cologne</p>
<p>Dieses Tool wird ohne Gewaehrleistung bereitgestellt. Nutzung auf eigene Gefahr.</p>
</footer></main></body></html>
"""

DOKU_CSS = """* { box-sizing: border-box; margin:0; padding:0; }
body { font-family:"Segoe UI",Arial,sans-serif; background:#0f1923; color:#c8d8e8; line-height:1.7; }
header { background:#0d1b2a; border-bottom:2px solid #00e5c8; padding:20px 0; }
.header-inner { max-width:860px; margin:0 auto; padding:0 32px; display:flex; align-items:center; justify-content:space-between; }
.logo-area { display:flex; align-items:center; gap:16px; }
.logo-circle { width:48px; height:48px; border-radius:50%; background:#00e5c8; color:#0d1b2a; font-weight:900; display:flex; align-items:center; justify-content:center; }
.header-title { font-size:20px; font-weight:700; color:#e8edf5; }
.header-sub { font-size:12px; color:#8fa8c8; }
.version { font-size:12px; color:#4a6a8a; }
main { max-width:860px; margin:0 auto; padding:40px 32px 80px; }
section { margin-bottom:48px; }
h1 { font-size:28px; color:#e8edf5; margin-bottom:16px; }
h2 { font-size:20px; color:#00e5c8; margin-bottom:16px; padding-bottom:8px; border-bottom:1px solid #1e3a5a; }
p { margin-bottom:12px; color:#a8bdd0; }
ol, ul { padding-left:24px; margin-bottom:16px; color:#a8bdd0; }
li { margin-bottom:8px; }
.intro { background:#0d1b2a; border-radius:12px; padding:28px 32px; margin-bottom:48px; }
footer { margin-top:64px; padding-top:24px; border-top:1px solid #1e3a5a; text-align:center; color:#4a6a8a; font-size:13px; }
"""

PACKAGER_VERSION = "2.1"

# Import-Name -> Name auf PyPI, wo sie sich unterscheiden
PIP_NAMES = {
    "PIL": "pillow", "cv2": "opencv-python", "bs4": "beautifulsoup4",
    "yaml": "pyyaml", "dotenv": "python-dotenv", "serial": "pyserial",
    "dateutil": "python-dateutil", "sklearn": "scikit-learn",
    "fitz": "pymupdf", "docx": "python-docx", "pptx": "python-pptx",
    "OpenGL": "PyOpenGL", "speech_recognition": "SpeechRecognition",
    "win32com": "pywin32", "win32api": "pywin32", "win32gui": "pywin32",
    "win32con": "pywin32", "pythoncom": "pywin32",
    "sentence_transformers": "sentence-transformers",
    "google": "google-genai", "genai": "google-genai",
    "Crypto": "pycryptodome", "usb": "pyusb", "gi": "PyGObject",
    "edge_tts": "edge-tts", "pyttsx3": "pyttsx3",
    "dateparser": "dateparser", "tzlocal": "tzlocal",
}

# Ordner und Dateien, die nie mit ins Paket wandern
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "build", "dist",
             "venv", ".venv", "env", "node_modules", "release", "build_tmp",
             ".pytest_cache", ".mypy_cache", "backup", "backups", "sicherung",
             "werkzeug"}
SKIP_EXT = {".pyc", ".pyo", ".bak", ".orig", ".log", ".db", ".db-wal",
            ".db-shm", ".spec", ".zip", ".tmp", ".sqlite", ".sqlite3",
            ".session", ".crdownload", ".part"}
SKIP_NAMES = {"thumbs.db", "desktop.ini", ".gitignore",
              "packer_zoom.txt"}

# Ordner, in die das Programm zur Laufzeit schreibt. Ihr Inhalt gehoert
# dem Nutzer, nicht dem Paket - und beim Empfaenger entstehen sie von
# selbst neu. Am 19.08.2026 lagen sonst Zugangsschluessel und drei
# Sprachaufnahmen im verschenkten Paket.
BETRIEB_DIRS = {"data", "daten", "logs", "log", "protokoll", "protokolle",
                "cache", "caches", "temp", "tmp", "failed_recordings",
                "aufnahmen", "recordings", "verlauf", "history",
                "instance", "instanz", "userdata", "benutzerdaten"}

# Endungen, die es nur im Betrieb gibt.
BETRIEB_EXT = {".dat", ".lock", ".pid", ".wav", ".mp3", ".ogg", ".m4a",
               ".webm", ".cache", ".state", ".hist", ".index"}

# Wortteile, die auf Zugangsdaten hindeuten - in jeder Schreibweise und
# an jeder Stelle des Namens.
GEHEIM_WORTTEILE = ("schluessel", "schlussel", "passwor", "kennwort",
                    "credential", "zugangsdaten", "secret", "geheim")

# Wortteile, die nur mit passender Endung verdaechtig sind. token und key
# stehen sonst auch in tokens.css oder hotkeys.py, die harmlos sind.
GEHEIM_MIT_ENDUNG = ("token", "apikey", "api_key", "auth")
GEHEIM_MIT_ENDUNG_EXT = {".json", ".txt", ".dat", ".ini", ".cfg", ".yaml"}

# Programmdateien. Ihr Name sagt nichts ueber ihren Inhalt aus.
QUELLTEXT_EXT = {".py", ".pyw", ".js", ".ts", ".css", ".html",
                 ".htm", ".md", ".c", ".h", ".cpp", ".cs", ".java"}

# Alles, was nach Zugangsdaten riecht. Diese Dateien duerfen unter keinen
# Umstaenden in ein Paket, das weitergegeben wird.
GEHEIM_NAMEN = {
    "config.json", "settings.json", "credentials.json", "credential.json",
    "token.json", "tokens.json", "secret.json", "secrets.json",
    "auth.json", "account.json", "accounts.json", "konten.json",
    "config.ini", "settings.ini", "secrets.ini", "config.cfg",
    ".env", ".env.local", ".env.production", ".netrc",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "apikey.txt", "api_key.txt", "schluessel.txt", "passwort.txt",
    "password.txt", "passwoerter.txt", "zugangsdaten.txt",
    "client_secret.json", "service_account.json",
}
GEHEIM_ENDUNGEN = {".key", ".pem", ".pfx", ".p12", ".keystore", ".jks",
                   ".ppk", ".asc", ".gpg", ".kdbx"}
GEHEIM_TEILE = ("_config.json", "-config.json", "_settings.json",
                "_secret", "_token", "_credentials", "_zugangsdaten",
                "client_secret", "service_account", "apikey", "api_key")

# Dateiarten, in die hineingeschaut wird. Quelltext wird bewusst
# ausgelassen - dort steht das Wort "password" voellig zu Recht.
DATEN_ENDUNGEN = {".json", ".txt", ".ini", ".cfg", ".conf", ".yaml", ".yml",
                  ".xml", ".csv", ".env", ".properties", ".toml"}
# Ein Name, hinter dem ein Wert stehen koennte. Ob der Wert wirklich
# ein Schluessel ist, entscheidet _echter_schluessel - der Name allein
# sagt gar nichts.
GEHEIM_WORTE = re.compile(
    r'["\']?(password|passwort|passwd|api[_-]?key|apikey|secret|'
    r'client_secret|access_token|refresh_token|private_key|app_password|'
    r'zugangsdaten|schluessel|token)["\']?\s*[:=]\s*'
    r'["\']?([^\s"\',}\]]{4,})',
    re.I)

# Formen, die es nur bei echten Schluesseln gibt.
SCHLUESSEL_ANFANG = ("sk-", "sk_live_", "sk_test_", "pk_live_", "rk_live_",
                     "aiza", "ya29.", "ghp_", "gho_", "ghu_", "ghs_",
                     "github_pat_", "xoxb-", "xoxp-", "xoxa-", "xoxs-",
                     "gsk_", "akia", "asia", "glpat-", "dop_v1_",
                     "shpat_", "shpss_", "sq0atp-", "eyj")

# Woerter, die einen Platzhalter verraten. Wer sie schreibt, meint
# ausdruecklich keinen echten Schluessel.
PLATZHALTER = ("dein", "deine", "ihr", "ihre", "your", "hier", "here",
               "xxx", "yyy", "zzz", "abc123", "changeme", "change_me",
               "beispiel", "example", "sample", "todo", "none", "null",
               "leer", "empty", "platzhalter", "placeholder", "test",
               "insert", "einfuegen", "unset", "notset", "fixme")


def _echter_schluessel(wert):
    """
    Ist das wirklich ein Zugangsschluessel - oder nur sein Name?

    Der Unterschied entscheidet, ob eine Warnung berechtigt ist. Am
    21.08.2026 meldete der Packager OPENAI_API_KEY als Zugangsdaten,
    obwohl das nur der Name einer Umgebungsvariablen ist.
    """
    w = str(wert).strip().strip('"\'`,;')
    if not w:
        return False

    klein = w.lower()

    # Bekannte Formen. Da braucht es keine weitere Ueberlegung.
    for anfang in SCHLUESSEL_ANFANG:
        if klein.startswith(anfang) and len(w) >= 12:
            return True

    # Ein Verweis auf etwas anderes, kein Wert.
    if w[0] in "$%<{(" or w.startswith("&"):
        return False
    if "\\" in w or "/" in w:
        return False

    # Der Name einer Umgebungsvariablen: nur Grossbuchstaben, Ziffern
    # und Unterstriche. So sieht kein Schluessel aus.
    if re.fullmatch(r"[A-Z0-9_]+", w):
        return False

    # Ausdruecklich als Platzhalter gemeint.
    for wort in PLATZHALTER:
        if wort in klein:
            return False

    # Zu kurz, um ein Schluessel zu sein.
    if len(w) < 20:
        return False

    # Bleibt: eine lange Zeichenfolge. Ein echter Schluessel mischt
    # Buchstaben und Ziffern und wiederholt sich nicht.
    hat_ziffer = any(z.isdigit() for z in w)
    hat_buchstabe = any(z.isalpha() for z in w)
    vielfalt = len(set(w)) / len(w)
    if hat_ziffer and hat_buchstabe and vielfalt > 0.35:
        return True

    return False


DEINSTALL_PS1 = r"""
param(
    [string]$Ziel = "",
    [int]$Eltern = 0,
    [switch]$Kopie
)

try { Add-Type -AssemblyName System.Windows.Forms } catch {}
$Name  = "~NAME~"
$Daten = "~DATEN~"

function Sag($Text, $Titel, $Fehler) {
    try {
        if ($Fehler) { [System.Media.SystemSounds]::Hand.Play() }
        else { [System.Media.SystemSounds]::Asterisk.Play() }
    } catch {}
    try {
        if ($Fehler) { $i = [System.Windows.Forms.MessageBoxIcon]::Warning }
        else { $i = [System.Windows.Forms.MessageBoxIcon]::Information }
        [System.Windows.Forms.MessageBox]::Show($Text, $Titel,
            [System.Windows.Forms.MessageBoxButtons]::OK, $i) | Out-Null
    } catch { Write-Host $Text }
}

# ---------------------------------------------------------- Erster Lauf ---
# Fragen, dann ausziehen. Wer im Ordner steht, den er loeschen soll,
# haelt ihn selbst fest - und die Prozessschleife weiter unten wuerde
# ihn ausserdem erschlagen.
if (-not $Kopie) {
    $Ziel = $PSScriptRoot

    $frage = "$Name wirklich entfernen?`r`n`r`nOrdner: $Ziel"
    if ($Daten) {
        $frage += "`r`n`r`nEntfernt werden ausserdem Ihre Einstellungen und, falls vorhanden, Ihre Zugangsschluessel."
    }
    $ja = $false
    try {
        $a = [System.Windows.Forms.MessageBox]::Show($frage, "$Name entfernen",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        $ja = ($a -eq [System.Windows.Forms.DialogResult]::Yes)
    } catch {
        Write-Host $frage
        $ja = ((Read-Host "Entfernen? (j/N)") -match "^[jJyY]")
    }
    if (-not $ja) { return }

    $kennung = [guid]::NewGuid().ToString("N").Substring(0, 8)
    $kopiepfad = Join-Path $env:TEMP ("entfernen_" + $Name + "_" + $kennung + ".ps1")
    try {
        Copy-Item -LiteralPath $PSCommandPath -Destination $kopiepfad -Force
    } catch {
        Sag "Die Deinstallation konnte nicht vorbereitet werden.`r`n`r`n$($_.Exception.Message)" "Fehler" $true
        return
    }
    # Anfuehrungszeichen sind Pflicht: Start-Process fuegt die Liste
    # ungeschuetzt zusammen, und C:\Stammtisch Tools enthaelt ein
    # Leerzeichen. Ohne sie kommt nur C:\Stammtisch an.
    Start-Process powershell -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $kopiepfad + '"'),
        "-Ziel", ('"' + $Ziel + '"'),
        "-Eltern", $PID, "-Kopie")
    return
}

# ------------------------------------------------------- Zweiter Lauf ---
# Ab hier laeuft die Kopie in TEMP. Erst warten, bis der Aufrufer samt
# seiner cmd.exe beendet ist - sonst haelt er den Ordner noch fest.
# Ohne brauchbaren Zielpfad wird nichts geloescht und nichts behauptet.
if (-not $Ziel -or -not (Test-Path -LiteralPath $Ziel)) {
    Sag ("Die Deinstallation konnte den Programmordner nicht finden." +
         "`r`n`r`nUebergeben wurde: " + $Ziel +
         "`r`n`r`nEs wurde nichts geloescht. Bitte den Ordner von Hand entfernen.") `
        "Nicht entfernt" $true
    return
}

if ($Eltern -gt 0) {
    try { Wait-Process -Id $Eltern -Timeout 30 -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Milliseconds 800

# Alles beenden, was aus dem Zielordner laeuft. Die eigene Kennung wird
# uebersprungen: der Zielordner steht auch in unserer Kommandozeile.
foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    if ($p.ProcessId -eq $PID) { continue }
    if ($p.CommandLine -and $p.CommandLine -like "*$Ziel*") {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
    }
}
Get-Process -Name $Name -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 900

# Loeschen und nachsehen. Dreimal versuchen - Windows gibt manche Datei
# erst nach einem Augenblick frei.
function Weg($Pfad) {
    # Sechs Versuche mit wachsenden Pausen. Windows gibt manche Datei
    # erst frei, wenn der aufrufende Vorgang beendet ist - bei der
    # Deinstallation ist das die cmd.exe der DEINSTALLIEREN.bat.
    if (-not $Pfad) { return $true }
    if (-not (Test-Path -LiteralPath $Pfad)) { return $true }
    for ($i = 1; $i -le 6; $i++) {
        try {
            Remove-Item -LiteralPath $Pfad -Recurse -Force -ErrorAction Stop
        } catch {}
        if (-not (Test-Path -LiteralPath $Pfad)) { return $true }
        Start-Sleep -Milliseconds (300 * $i)
    }
    return $false
}

function IstLeer($Pfad) {
    # Ein Ordner ohne Inhalt. Darin liegt nichts, was jemanden angeht.
    if (-not (Test-Path -LiteralPath $Pfad)) { return $false }
    try {
        $inhalt = @(Get-ChildItem -LiteralPath $Pfad -Force -Recurse `
                    -ErrorAction SilentlyContinue)
        return ($inhalt.Count -eq 0)
    } catch {
        return $false
    }
}

Set-Location $env:TEMP
$Rest = @()

# Das Buch der Installation. Rueckwaerts abarbeiten: Verknuepfungen
# zuerst, Ordner zuletzt - die Liste selbst liegt im Programmordner.
$Buch = Join-Path $Ziel "installiert.txt"
$Ordner = @()
if (Test-Path -LiteralPath $Buch) {
    $zeilen = @(Get-Content -LiteralPath $Buch |
                Where-Object { $_ -and -not $_.StartsWith("#") })
    [array]::Reverse($zeilen)
    foreach ($z in $zeilen) {
        $teile = $z -split "\|", 2
        if ($teile.Count -lt 2) { continue }
        $art = $teile[0]
        $pfad = $teile[1]
        if ($art -eq "Ordner") { $Ordner += $pfad; continue }
        if ($art -eq "Gruppe") { continue }
        if ($art -eq "Registry") {
            Remove-Item -LiteralPath $pfad -Recurse -Force -ErrorAction SilentlyContinue
            continue
        }
        if (-not (Weg $pfad)) { $Rest += $pfad }
    }
}

# Die bekannten Orte immer pruefen, auch wenn ein Buch vorliegt. Eine
# Zeile kann fehlen, wenn die Installation abgebrochen wurde.
# Der Eintrag in Apps und Features. Bleibt er stehen, bietet Windows
# das Deinstallieren eines Programms an, das es nicht mehr gibt.
$Schluessel = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$Name"
if (Test-Path -LiteralPath $Schluessel) {
    Remove-Item -LiteralPath $Schluessel -Recurse -Force -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $Schluessel) {
    $Rest += "Eintrag in Apps und Features"
}

if (-not (Weg "$env:USERPROFILE\Desktop\$Name.lnk")) {
    $Rest += "$env:USERPROFILE\Desktop\$Name.lnk"
}
$Gruppe = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\KI Stammtisch Cologne"
if (-not (Weg (Join-Path $Gruppe "$Name.lnk"))) {
    $Rest += (Join-Path $Gruppe "$Name.lnk")
}
# Die Gruppe verschwindet nur, wenn kein anderes Werkzeug mehr darin steht.
if ((Test-Path -LiteralPath $Gruppe) -and
    -not (Get-ChildItem -LiteralPath $Gruppe -Force -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath $Gruppe -Force -ErrorAction SilentlyContinue
}

# Der Datenordner. Was das Programm im Betrieb selbst angelegt hat,
# steht in keiner Liste.
if ($Daten) {
    foreach ($w in @($env:APPDATA, $env:LOCALAPPDATA)) {
        $d = Join-Path $w $Daten
        if (-not (Weg $d)) { $Rest += $d }
    }
}

# Der Programmordner, immer zuletzt.
foreach ($o in $Ordner) { if (-not (Weg $o)) { $Rest += $o } }
if (-not (Weg $Ziel)) { $Rest += $Ziel }

# Noch einmal nachsehen, ohne Doppelnennung.
$Rest = @($Rest | Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
          Select-Object -Unique)

# Ein leerer Ordner ist kein Rest. Darin liegt nichts - er haengt nur
# noch an der cmd.exe, die diese Deinstallation gestartet hat, und
# verschwindet, sobald die beendet ist. Ein Nachzuegler im Hintergrund
# holt ihn.
$Huelle = @($Rest | Where-Object { IstLeer $_ })
if ($Huelle.Count -gt 0) {
    $Rest = @($Rest | Where-Object { -not (IstLeer $_) })
    foreach ($h in $Huelle) {
        try {
            $nach = "for (`$i = 0; `$i -lt 30; `$i++) { " +
                    "Start-Sleep -Seconds 2; " +
                    "Remove-Item -LiteralPath '" + $h + "' -Recurse -Force " +
                    "-ErrorAction SilentlyContinue; " +
                    "if (-not (Test-Path -LiteralPath '" + $h + "')) { break } }"
            Start-Process powershell -WindowStyle Hidden `
                -ArgumentList @("-NoProfile", "-Command", $nach)
        } catch {}
    }
}

if ($Rest.Count -eq 0) {
    $text = "$Name wurde vollstaendig entfernt.`r`n`r`nEs bleibt nichts zurueck: kein Programm, keine Verknuepfung, keine Einstellungen, keine Zugangsschluessel."
    if ($Huelle.Count -gt 0) {
        $text += "`r`n`r`nDer leere Programmordner verschwindet in den naechsten Sekunden von selbst. Es liegt nichts mehr darin."
    }
    Sag $text "Fertig" $false
} else {
    $t = "$Name wurde NICHT vollstaendig entfernt.`r`n`r`nStehen geblieben ist:`r`n"
    foreach ($r in $Rest) { $t += "  " + $r + "`r`n" }
    $t += "`r`nMeist haelt ein noch laufendes Programm eine Datei fest. Bitte den Rechner neu starten und diese Deinstallation noch einmal aufrufen."
    Sag $t "Nicht vollstaendig entfernt" $true
}

# Die Kopie raeumt sich selbst weg. Sie kann sich nicht loeschen, solange
# sie laeuft - also uebernimmt das ein kurzlebiger zweiter Aufruf.
try {
    $selbst = $PSCommandPath
    Start-Process powershell -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-Command",
        "Start-Sleep -Seconds 3; Remove-Item -LiteralPath '$selbst' -Force -ErrorAction SilentlyContinue")
} catch {}
"""


def _geheim_grund(dateiname):
    """Gibt den Grund zurueck, warum eine Datei draussen bleibt - oder None."""
    n = dateiname.lower()
    if n in GEHEIM_NAMEN:
        return "Zugangsdaten"
    if os.path.splitext(n)[1] in GEHEIM_ENDUNGEN:
        return "Schluesseldatei"
    for teil in GEHEIM_TEILE:
        if teil in n:
            return "Zugangsdaten"
    if n.endswith(".flag"):
        return "Merker"
    if os.path.splitext(n)[1] in SKIP_EXT:
        return "Arbeitsdatei"
    if n in SKIP_NAMES:
        return "Systemdatei"
    return None


def _geheimnis_verdacht(dateien):
    """Sucht in den Dateien, die ins Paket wandern, nach Zugangsdaten.
    Der Guertel zum Hosentraeger: falls doch etwas durchgerutscht ist."""
    treffer = []
    for p in dateien:
        if os.path.splitext(p)[1].lower() not in DATEN_ENDUNGEN:
            continue
        try:
            if os.path.getsize(p) > 2_000_000:
                continue
            txt = open(p, "r", encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for nr, zeile in enumerate(txt.splitlines(), 1):
            m = GEHEIM_WORTE.search(zeile)
            if not m:
                continue
            if not _echter_schluessel(m.group(2)):
                continue
            wert = m.group(2)
            gekuerzt = wert[:6] + "..." if len(wert) > 10 else wert
            treffer.append((os.path.basename(p),
                            m.group(1) + " Zeile " + str(nr)
                            + ": " + gekuerzt))
            break
    return treffer

# Qt-Zusatzmodule, die PyInstaller sonst blind mitschleppt.
# Ausgeschlossen wird nur, was im Quelltext nachweislich nicht vorkommt.
QT_BALLAST = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick",
    "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQml", "QtQmlModels",
    "Qt3DCore", "Qt3DRender", "Qt3DAnimation", "Qt3DExtras", "Qt3DInput",
    "Qt3DLogic", "QtCharts", "QtDataVisualization", "QtGraphs",
    "QtMultimedia", "QtMultimediaWidgets", "QtSpatialAudio",
    "QtDesigner", "QtUiTools", "QtHelp", "QtTest",
    "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors",
    "QtSerialPort", "QtSerialBus", "QtWebSockets", "QtWebChannel",
    "QtRemoteObjects", "QtScxml", "QtStateMachine", "QtTextToSpeech",
    "QtPdf", "QtPdfWidgets", "QtSql", "QtNetworkAuth", "QtHttpServer",
]


def _scan_requirements(pyfiles):
    """Ermittelt, welche Fremdpakete das Projekt braucht.
    Standardbibliothek und projekteigene Module fallen raus."""
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    stdlib |= {"tkinter", "sqlite3", "email", "html", "http", "xml",
               "urllib", "json", "os", "sys", "re", "io"}
    local = {os.path.splitext(os.path.basename(p))[0] for p in pyfiles}
    found = {}
    for p in pyfiles:
        try:
            tree = ast.parse(open(p, "r", encoding="utf-8",
                                  errors="replace").read())
        except Exception:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:          # relativer Import - projekteigen
                    continue
                names = [node.module or ""]
            for full in names:
                top = full.split(".")[0]
                if not top or top in stdlib or top in local:
                    continue
                if top.startswith("_"):
                    continue
                found[top] = PIP_NAMES.get(top, top)
    return dict(sorted(found.items()))


def _human(n):
    """Groesse lesbar ausgeben - auch unterhalb von einem Megabyte."""
    if n >= 10 * 1048576:
        return f"{n / 1048576:.0f} MB"
    if n >= 1048576:
        return f"{n / 1048576:.1f} MB".replace(".", ",")
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} Byte"


def _python_exe():
    """Echte python.exe finden - auch wenn der Packager selbst als EXE laeuft."""
    cands = [
        r"C:\Program Files\Python313\python.exe",
        r"C:\Program Files\Python312\python.exe",
        r"C:\Program Files\Python311\python.exe",
    ]
    if not getattr(sys, "frozen", False):
        cands.insert(0, sys.executable)
    for c in cands:
        if c and os.path.exists(c):
            return c
    found = shutil.which("python") or shutil.which("py")
    return found or "python"



# Pakete, die erst zur Laufzeit nachladen. PyInstaller sieht das nicht und
# muss ausdruecklich darauf gestossen werden.
NACHLADER = {
    "webview": ["webview.platforms.edgechromium",
                "webview.platforms.winforms"],
    "pystray": ["pystray._win32"],
    "PIL": ["PIL._tkinter_finder"],
    "sounddevice": ["_sounddevice_data"],
}


def _wird_gebraucht(src_dir, modul):
    """Kommt das Modul irgendwo im Quellordner vor?

    Frueher wurden nur die Dateien auf oberster Ebene durchsucht. Bei
    einem Projekt mit Unterordnern steht dort meist nur die
    Startdatei, und alles Uebrige galt faelschlich als ungenutzt.
    """
    for wurzel, ordner, dateien_ in os.walk(src_dir):
        ordner[:] = [d for d in ordner if d.lower() not in SKIP_DIRS]
        for fn in dateien_:
            if not fn.lower().endswith(".py"):
                continue
            try:
                with open(os.path.join(wurzel, fn), "r",
                          encoding="utf-8", errors="replace") as fh:
                    if modul in fh.read():
                        return True
            except Exception:
                pass
    return False


def _datenordner(src_dir):
    """Der Ordner unter %APPDATA%, in den das Programm schreibt.

    Steht in paket.json unter "datenordner". Ohne diese Angabe gibt
    es keine Moeglichkeit zu wissen, wohin ein Programm seine
    Einstellungen legt - dann bleibt der Ordner beim Deinstallieren
    stehen, wie bisher.
    """
    pfad = os.path.join(src_dir, "paket.json")
    if not os.path.exists(pfad):
        return ""
    try:
        with open(pfad, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("datenordner", "")).strip()
    except Exception:
        return ""


def _paket_zusatz(src_dir):
    """Liest paket.json und ergaenzt, was PyInstaller sonst uebersieht.

    Rueckgabe: (Liste der Mitnahme-Angaben, Liste der Importe)

    Die Mitnahme-Angaben haben die Form "Quelle;Ziel". Quelle ist ein
    Pfad im gespiegelten Quellordner, Ziel der Ort im fertigen Paket -
    beide gleich, damit relative Pfade im Programm weiter stimmen.
    """
    mitnehmen, importe = [], []

    pfad = os.path.join(src_dir, "paket.json")
    if os.path.exists(pfad):
        try:
            with open(pfad, "r", encoding="utf-8") as fh:
                angaben = json.load(fh)
        except Exception as exc:
            return [], ["# paket.json unlesbar: %s" % exc]
        for eintrag in angaben.get("mitnehmen", []):
            quelle = os.path.join(src_dir, eintrag.replace("/", os.sep))
            if os.path.exists(quelle):
                mitnehmen.append("%s%s%s" % (quelle, os.pathsep,
                                             eintrag.replace("/", os.sep)))
        importe.extend(angaben.get("importe", []))

    # Ordner ohne Python-Dateien wandern von selbst mit. Wer eine
    # Oberflaeche aus HTML und JavaScript mitliefert, soll nicht erst eine
    # Datei anlegen muessen, um sie im Paket wiederzufinden.
    if not mitnehmen:
        for name in sorted(os.listdir(src_dir)):
            voll = os.path.join(src_dir, name)
            if not os.path.isdir(voll) or name.lower() in SKIP_DIRS:
                continue
            hat_daten = False
            for wurzel, _, dateien_ in os.walk(voll):
                for fn in dateien_:
                    if not fn.lower().endswith((".py", ".pyc")):
                        hat_daten = True
                        break
                if hat_daten:
                    break
            if hat_daten:
                mitnehmen.append("%s%s%s" % (voll, os.pathsep, name))

    # Nachlader anhand der tatsaechlichen Importe erkennen.
    quelltext = ""
    for wurzel, _, dateien_ in os.walk(src_dir):
        for fn in dateien_:
            if fn.lower().endswith(".py"):
                try:
                    with open(os.path.join(wurzel, fn), "r",
                              encoding="utf-8", errors="replace") as fh:
                        quelltext += fh.read()
                except Exception:
                    pass
    for paket, module in NACHLADER.items():
        if ("import " + paket) in quelltext or ("from " + paket) in quelltext:
            importe.extend(module)

    return mitnehmen, sorted(set(importe))


# Begleitdateien der Projektverwaltung. Sie beschreiben Roberts
# Arbeitsweise und gehen niemanden an, der ein Werkzeug geschenkt bekommt.
BEGLEIT_ENDEN = ("_aufbau.txt", "_sessions.txt", "_heute.txt",
                 "_entwicklung.txt", "_technik.md", "_spec.md")

# Unterlagen der Entwicklung. Sie sind fuer Robert und die KI bestimmt,
# nicht fuer den Beschenkten - der braucht LIESMICH.md und BEDIENUNG.md.
# Wer am Kode arbeiten will, findet alles auf GitHub.
#
# Frueher wurden nur Namen mit Unterstrich erkannt, also
# SCHREIBER_AUFBAU.txt. Hiess die Datei schlicht AUFBAU.txt, ging sie
# durch - am 20.08.2026 lag deshalb ENTWICKLUNG.txt im Paket, mit dem
# Pfad zu Roberts .env darin.
BEGLEIT_NAMEN = {
    "projektverzeichnis.txt", "projektverzeichnis",
    "aufbau.txt", "sessions.txt", "heute.txt", "entwicklung.txt",
    "technik.md", "spec.md", "todo.md", "todo.txt",
    "changelog.md", "notizen.txt", "notizen.md",
}

# Textarten, in die hineingesehen wird. Quelltext bleibt aussen vor:
# dort steht ein Pfad manchmal zu Recht.
PRUEF_ARTEN = (".txt", ".md", ".json", ".ini", ".cfg", ".yaml", ".yml",
               ".html", ".csv", ".log")

# Was im Inhalt nichts zu suchen hat. Der Name einer Datei sagt nichts
# ueber ihren Inhalt - eine Namensliste kennt nur, woran jemand dachte.
INHALT_MUSTER = (
    ("Pfad zum Benutzerkonto", r"[A-Za-z]:[\\/]Users[\\/][^\s\"'<>|)\]]+"),
    ("Zugangsschluessel", r"\b(?:sk-[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_\-]{20,}"
                          r"|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"),
)


def _inhalt_pruefen(dateien, log=None):
    """
    Sieht in die Dateien hinein. Gibt eine Liste der Fundstellen zurueck -
    leer heisst sauber.
    """
    import re
    funde = []
    for pfad in dateien:
        if not pfad.lower().endswith(PRUEF_ARTEN):
            continue
        try:
            with open(pfad, "r", encoding="utf-8", errors="replace") as fh:
                zeilen = fh.read().splitlines()
        except Exception:
            continue
        for nr, zeile in enumerate(zeilen, 1):
            for was, muster in INHALT_MUSTER:
                if re.search(muster, zeile):
                    funde.append((pfad, nr, was))
                    break
    return funde


# Was der Packager selbst ins Paket legt. Diese Dateien duerfen die
# Endkontrolle passieren, auch wenn ihre Endung sonst verboten waere -
# einfuehrung.mp3 zum Beispiel entsteht beim Bauen, nicht im Betrieb.
EIGENE_DATEIEN = {
    "einfuehrung.txt", "einfuehrung.mp3", "einfuehrung_zeigen.py",
    "starter.py", "anforderung.json", "installiert.txt",
    "app_icon.ico", "logo512.png", "logo64.png",
    "license", "license.txt", "notice.txt", "liesmich.md",
    "bedienung.md", "doku.html", "doku.css",
    "installieren.bat", "deinstallieren.bat",
    "_install.ps1", "_deinstall.ps1",
}


def _skip_grund(dateiname):
    """
    Warum eine Datei nicht ins Paket gehoert - oder leer, wenn sie darf.

    Bis zum 19.08.2026 wurden SKIP_EXT und SKIP_NAMES zwar gepflegt, aber
    von keiner Zeile abgefragt. Diese Funktion schliesst die Luecke.
    """
    n = dateiname.lower()
    endung = os.path.splitext(n)[1]

    if n in EIGENE_DATEIEN:
        return ""

    # Quelltext wird nie nach Wortteilen beurteilt. In schluessel.py
    # steht das Wort voellig zu Recht - es ist der Kode, der die
    # Schluessel verwaltet, und ohne ihn startet das Programm nicht.
    if endung not in QUELLTEXT_EXT:
        for wort in GEHEIM_WORTTEILE:
            if wort in n:
                return "Zugangsdaten"
        if endung in GEHEIM_MIT_ENDUNG_EXT:
            for wort in GEHEIM_MIT_ENDUNG:
                if wort in n:
                    return "Zugangsdaten"

    if endung in SKIP_EXT:
        if endung in (".bak", ".orig"):
            return "Sicherungsdatei"
        return "Endung " + endung
    if endung in BETRIEB_EXT:
        return "entsteht im Betrieb"
    if n in SKIP_NAMES:
        return "Systemdatei"
    if n in BEGLEIT_NAMEN or n.endswith(BEGLEIT_ENDEN):
        return "Begleitdatei, nur fuer Claude"

    # Eigene Einstellungen, nach der Form beurteilt statt nach einer
    # Liste von Namen. In einer Markendatei stehen Ablageorte, Pfade
    # und Namen - beim Empfaenger sind sie falsch und gehen ihn nichts
    # an. Am 21.08.2026 waere packer_marke.json mitgewandert,
    # weil der Filter nur settings.json und config.json kannte.
    stamm = os.path.splitext(n)[0]
    if endung in (".json", ".ini", ".cfg", ".conf", ".toml", ".yaml",
                  ".yml"):
        for teil in ("marke", "einstellung", "settings", "setting",
                     "config", "konfig", "prefs", "preference",
                     "optionen", "options", "profil", "profile"):
            if teil in stamm:
                return "eigene Einstellungen"

    # Patchskripte gehoeren zur Entwicklung. Der Ordner werkzeug ist
    # ausgeschlossen, aber sie liegen nicht immer dort.
    if stamm.startswith("patch_") or stamm.startswith("fix_"):
        return "Patchskript der Entwicklung"

    return ""


def _project_files(folder, mit_grund=False):
    """Alle Dateien des Projekts, die ins Paket gehoeren.

    Mit mit_grund=True kommt zusaetzlich die Liste der ausgelassenen
    Dateien samt Begruendung zurueck - die steht dann im Log, damit
    nachvollziehbar bleibt, was NICHT weitergegeben wird."""
    out, weg = [], []
    for root_, dirs, files in os.walk(folder):
        raus = [d for d in dirs if d.lower() in SKIP_DIRS]
        for d in raus:
            weg.append((d + "\\", "Ordner uebersprungen"))
        betrieb = [d for d in dirs if d.lower() in BETRIEB_DIRS]
        for d in betrieb:
            weg.append((d + "\\", "Betriebsordner, entsteht neu"))
        dirs[:] = [d for d in dirs
                   if d.lower() not in SKIP_DIRS
                   and d.lower() not in BETRIEB_DIRS]
        for fn in files:
            grund = _geheim_grund(fn)
            if not grund:
                grund = _skip_grund(fn)
            if grund:
                weg.append((fn, grund))
                continue
            out.append(os.path.join(root_, fn))
    return (out, weg) if mit_grund else out


def _alle_py_files(folder):
    """
    Alle Python-Dateien des Projekts, auch in Unterordnern.

    Fuer die Paketerkennung. Die oberste Ebene genuegt nicht: beim
    Schreiber liegen dort nur run.py und stopp_instanzen.py, der gesamte
    Kode steckt in app und lib.

    Uebersprungen wird, was auch der Paketfilter auslaesst - in
    __pycache__ oder .git steht nichts, was das Programm braucht.
    """
    treffer = []
    for wurzel, ordner, dateien in os.walk(folder):
        ordner[:] = [d for d in ordner
                     if d.lower() not in SKIP_DIRS
                     and d.lower() not in BETRIEB_DIRS]
        for d in dateien:
            if d.lower().endswith((".py", ".pyw")):
                treffer.append(os.path.join(wurzel, d))
    return sorted(treffer)


def _py_files(folder):
    """Nur die Python-Dateien auf oberster Ebene, sortiert."""
    try:
        return sorted(f for f in os.listdir(folder) if f.lower().endswith(".py"))
    except Exception:
        return []


def _guess_entry(folder):
    """Startdatei raten: bevorzugt eine Datei mit __main__-Block."""
    cands = _py_files(folder)
    if not cands:
        return ""
    liked = ("main.py", "app.py", "start.py",
             os.path.basename(folder).lower() + ".py")
    scored = []
    for fn in cands:
        score = 0
        try:
            txt = open(os.path.join(folder, fn), "r", encoding="utf-8",
                       errors="replace").read()
            if "__main__" in txt:
                score += 10
            if re.search(r"def\s+main\s*\(", txt):
                score += 4
        except Exception:
            pass
        if fn.lower() in liked:
            score += 6
        scored.append((score, fn))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][1]




def _uses_qt(pyfiles):
    for p in pyfiles:
        try:
            if "PySide6" in open(p, "r", encoding="utf-8", errors="replace").read():
                return True
        except Exception:
            pass
    return False

def _extract_info(pyfile):
    try:
        src = open(pyfile,"r",encoding="utf-8").read()
        tree = ast.parse(src)
        doc = ast.get_docstring(tree) or ""
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        return doc.strip(), funcs
    except Exception:
        return "", []

def _build_doku(out_dir, name, script):
    doc, funcs = _extract_info(script)
    beschreibung = doc if doc else (f"{name} ist ein Tool der KI Stammtisch Cologne Toolsammlung. "
        "Es wird ohne Gewaehrleistung bereitgestellt und dient der Unterstuetzung im Alltag der Community.")
    heute = datetime.date.today().strftime("%Y-%m-%d")
    if funcs:
        funk_html = "<ul>" + "".join(f"<li><code>{f}</code></li>" for f in funcs[:15]) + "</ul>"
        funk_text = "Das Programm stellt unter anderem folgende Funktionen bereit: " + ", ".join(funcs[:15]) + "."
    else:
        funk_html = "<p>Details zu den Funktionen folgen in einer spaeteren Version der Dokumentation.</p>"
        funk_text = "Details zu den einzelnen Funktionen folgen in einer spaeteren Version der Dokumentation."
    html = DOKU_HTML_TPL.replace("~APPNAME~", name).replace("~BESCHREIBUNG~", beschreibung)
    html = html.replace("~FUNKTIONEN~", funk_html).replace("~DATE~", heute)
    html = gepraegt(html)
    with open(os.path.join(out_dir,"doku.html"),"w",encoding="utf-8") as f: f.write(html)
    with open(os.path.join(out_dir,"doku.css"),"w",encoding="utf-8") as f: f.write(DOKU_CSS)
    doc_text = (
        f"Dokumentation: {name}\n"
        f"KI Stammtisch Cologne, Stand {heute}\n\n"
        f"Was ist {name}?\n{beschreibung}\n\n"
        f"Installation\n"
        f"Die ZIP-Datei wird zunaechst entpackt. Danach genuegt ein Doppelklick auf "
        f"die Datei INSTALLIEREN.bat. Das Programm installiert sich automatisch und "
        f"legt eine Verknuepfung auf dem Desktop an. Diese Verknuepfung kann per "
        f"Rechtsklick an die Taskleiste angeheftet werden.\n\n"
        f"Funktionen\n{funk_text}\n\n"
        f"Erste Nutzung\n"
        f"Beim allerersten Start von {name} erscheint ein Hinweisfenster mit den "
        f"Nutzungsbedingungen. Nach dem Anhaken der Checkbox und einem Klick auf "
        f"Starten oeffnet sich das Programm. Bei jedem weiteren Start entfaellt "
        f"dieser Hinweis, das Programm startet dann direkt.\n\n"
        f"Hinweis\n"
        f"{name} wird von der KI Stammtisch Cologne Community ohne Gewaehrleistung "
        f"bereitgestellt. Die Nutzung erfolgt auf eigene Gefahr.\n"
    )
    with open(os.path.join(out_dir,"doku.txt"),"w",encoding="utf-8") as f: f.write(doc_text)
    doc_md = (
        f"# {name}\n\n*KI Stammtisch Cologne — Stand {heute}*\n\n"
        f"## Was ist {name}?\n\n{beschreibung}\n\n"
        f"## Installation\n\n"
        f"1. ZIP-Datei entpacken\n"
        f"2. `INSTALLIEREN.bat` doppelklicken\n"
        f"3. Verknuepfung erscheint auf dem Desktop\n"
        f"4. Rechtsklick darauf -> An Taskleiste anheften\n\n"
        f"## Funktionen\n\n{funk_text}\n\n"
        f"## Erste Nutzung\n\n"
        f"Beim ersten Start erscheint ein Hinweisfenster mit den Nutzungsbedingungen. "
        f"Checkbox anhaken, Starten klicken. Danach startet {name} immer direkt.\n\n"
        f"## Hinweis\n\n"
        f"Dieses Tool wird ohne Gewaehrleistung bereitgestellt. Nutzung auf eigene Gefahr.\n"
    )
    with open(os.path.join(out_dir,"doku.md"),"w",encoding="utf-8") as f: f.write(doc_md)

# ── Bootstrap-Vorspann (holt fehlende Fremdpakete beim ersten Start) ─────────



# ── Installations-Skript (Fenster statt Konsole) ─────────────────────────────
INSTALL_PS1 = r"""$ErrorActionPreference = "Stop"
$Name    = "~NAME~"

Add-Type -AssemblyName System.Windows.Forms | Out-Null

$Daten   = "~DATEN~"
# Der Vorschlag beim allererstenmal. Nicht Program Files: dort braucht
# das Anlegen Adminrechte, und der Ordner bleibt schreibgeschuetzt -
# ein Programm koennte seine Einstellungen nicht neben sich ablegen.
# Was der Nutzer stattdessen waehlt, merkt sich installer.json.
$Sammel  = "$env:LOCALAPPDATA\Programs"
$Privat  = "$env:LOCALAPPDATA\KI-Stammtisch\$Name"
$Quelle  = Join-Path $PSScriptRoot "~NAME~"

# Was beim letzten Mal gewaehlt wurde. Gemerkt wird der uebergeordnete
# Ordner - der Name des Werkzeugs kommt jedesmal frisch dazu.
$Merker     = "$env:LOCALAPPDATA\KI-Stammtisch\installer.json"
$MerkOrdner = $Sammel
$MerkDesk   = $true
$MerkMenue  = $true
$Gemerkt    = $false
try {
    if (Test-Path -LiteralPath $Merker) {
        $m = Get-Content -LiteralPath $Merker -Raw | ConvertFrom-Json
        if ($m.ordner) { $MerkOrdner = [string]$m.ordner; $Gemerkt = $true }
        $felder = $m.PSObject.Properties.Name
        if ($felder -contains "desktop")    { $MerkDesk  = [bool]$m.desktop }
        if ($felder -contains "startmenue") { $MerkMenue = [bool]$m.startmenue }
    }
} catch { }
$Vorgabe = "$MerkOrdner\$Name"

function Merke($ordner, $desk, $menue) {
    try {
        $d = Split-Path $Merker -Parent
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        [pscustomobject]@{ ordner = $ordner; desktop = $desk;
                           startmenue = $menue } |
            ConvertTo-Json | Set-Content -LiteralPath $Merker -Encoding UTF8
    } catch { }
}

function Eltern-Ordner($pfad) {
    $t = $pfad.TrimEnd('\','/') -split '[\\/]'
    if ($t.Count -gt 1 -and $t[-1] -eq $Name) {
        return ($t[0..($t.Count - 2)] -join "\")
    }
    return $pfad
}
~PYTHONSUCHE~

function Ziel-Vervollstaendigen($p) {
    if ([string]::IsNullOrWhiteSpace($p)) { return $Vorgabe }
    $p = $p.Trim().Trim('"').TrimEnd('\','/')
    $teile = $p -split '[\\/]'
    if ($teile[-1] -ne $Name) { $p = $p + "\" + $Name }
    return $p
}

function Lege-Verknuepfung($Pfad, $Ziel) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($Pfad)
~ZIELSETZEN~
    $s.WorkingDirectory = $Ziel
    $s.Description = "$Name - KI Stammtisch Cologne"
~ICON~
    $s.Save()
}

$script:Liste = $null

function Notiere($Pfad, $Art) {
    # Ein Eintrag ins Buch. Schlaegt es fehl, geht die Installation
    # trotzdem weiter - eine fehlende Zeile ist besser als ein Abbruch.
    if (-not $Pfad) { return }
    if (-not $script:Liste) { return }
    try {
        Add-Content -LiteralPath $script:Liste -Encoding UTF8 `
                    -Value ($Art + "|" + $Pfad)
    } catch {}
}

function Installiere($Ziel, $Desktop, $Startmenue) {
    $hinweise = @()
    if ($Ziel -match '^[A-Za-z]:') {
        $lw = $Ziel.Substring(0,2) + "\"
        if (-not (Test-Path -LiteralPath $lw)) {
            throw "Das Laufwerk $lw gibt es auf diesem Rechner nicht."
        }
    }
    Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 300
    try {
        New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
    } catch {
        $Ziel = $Privat
        New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
        $hinweise += "Der Ordner liess sich nicht anlegen. Verwendet wurde $Privat"
    }
    $probe = $Ziel + "\schreibtest.tmp"
    try {
        Set-Content -LiteralPath $probe -Value "x" -ErrorAction Stop
        Remove-Item -LiteralPath $probe -Force
    } catch {
        $Ziel = $Privat
        New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
        $hinweise += "Dort besteht kein Schreibrecht. Verwendet wurde $Privat"
    }
    # Ab hier wird Buch gefuehrt. Jeder Schritt traegt sich sofort ein -
    # bricht die Installation ab, steht trotzdem da, was schon entstand.
    # Laeuft das Programm noch? Dann erst beenden - sonst verweigert
    # Windows das Ueberschreiben und die Installation bleibt halb
    # stecken, ohne dass jemand es merkt.
    $script:Nachher = @()
    foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($p.ProcessId -eq $PID) { continue }
        if (-not $p.CommandLine) { continue }
        if ($p.CommandLine -like "*$Ziel*") {
            $script:Nachher += $p.CommandLine
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            } catch {}
        }
    }
    if ($script:Nachher.Count -gt 0) {
        $hinweise += "Die laufende Fassung wurde beendet und nach der Installation neu gestartet."
        Start-Sleep -Milliseconds 1200
    }

    $script:Liste = Join-Path $Ziel "installiert.txt"
    try {
        Set-Content -LiteralPath $script:Liste -Encoding UTF8 -Value @(
            "# Diese Datei sagt der Deinstallation, was angelegt wurde.",
            "# Bitte nicht loeschen und nicht aendern.")
    } catch { $script:Liste = $null }
    Notiere $Ziel "Ordner"

    # Fehler beim Kopieren nicht verschlucken. Eine Installation, die
    # scheitert und Erfolg meldet, ist schlimmer als eine, die
    # abbricht.
    try {
        Copy-Item -Path (Join-Path $Quelle "*") -Destination $Ziel `
                  -Recurse -Force -ErrorAction Stop
    } catch {
        $hinweise += "Nicht alles liess sich kopieren: " + $_.Exception.Message
    }
    Copy-Item (Join-Path $PSScriptRoot "_deinstall.ps1") $Ziel -Force
    Copy-Item (Join-Path $PSScriptRoot "DEINSTALLIEREN.bat") $Ziel -Force
    if ($Desktop) {
        $Lnk = "$env:USERPROFILE\Desktop\$Name.lnk"
        Lege-Verknuepfung $Lnk $Ziel
        Notiere $Lnk "Verknuepfung"
    }
    if ($Startmenue) {
        $Gruppe = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\KI Stammtisch Cologne"
        New-Item -ItemType Directory -Force -Path $Gruppe | Out-Null
        Notiere $Gruppe "Gruppe"
        $Lnk2 = Join-Path $Gruppe "$Name.lnk"
        Lege-Verknuepfung $Lnk2 $Ziel
        Notiere $Lnk2 "Verknuepfung"
    }
    if ($Daten) {
        Notiere (Join-Path $env:APPDATA $Daten) "Daten"
        Notiere (Join-Path $env:LOCALAPPDATA $Daten) "Daten"
    }
    # Eintrag in Apps und Features. Ohne ihn muesste der Nutzer den
    # Programmordner suchen, um zu deinstallieren. HKCU genuegt - der
    # Eintrag gilt fuer diesen Benutzer und braucht keine Adminrechte.
    $Schluessel = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$Name"
    try {
        New-Item -Path $Schluessel -Force | Out-Null
        $entfernen = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' +
                     $Ziel + '\_deinstall.ps1"'
        New-ItemProperty -Path $Schluessel -Name DisplayName -Value $Name -Force | Out-Null
        New-ItemProperty -Path $Schluessel -Name Publisher -Value "KI Stammtisch Cologne" -Force | Out-Null
        New-ItemProperty -Path $Schluessel -Name InstallLocation -Value $Ziel -Force | Out-Null
        New-ItemProperty -Path $Schluessel -Name UninstallString -Value $entfernen -Force | Out-Null
        New-ItemProperty -Path $Schluessel -Name QuietUninstallString -Value $entfernen -Force | Out-Null
        New-ItemProperty -Path $Schluessel -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
        New-ItemProperty -Path $Schluessel -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
        $ico = Join-Path $Ziel "app_icon.ico"
        if (Test-Path -LiteralPath $ico) {
            New-ItemProperty -Path $Schluessel -Name DisplayIcon -Value $ico -Force | Out-Null
        }
        Notiere $Schluessel "Registry"
    } catch {
        $hinweise += "Der Eintrag in Apps und Features liess sich nicht anlegen."
    }

    # Was vorher lief, wieder starten. Wer ein Programm erneuert,
    # erwartet es danach an derselben Stelle wieder vor sich.
    foreach ($befehl in $script:Nachher) {
        try {
            Start-Process -FilePath "cmd.exe" `
                          -ArgumentList "/c", "start", "", $befehl `
                          -WindowStyle Hidden
        } catch {}
    }

    return @{ Ziel = $Ziel; Hinweise = $hinweise }
}

# ---------------------------------------------------------------- Fenster ---
$MitFenster = $true
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
} catch { $MitFenster = $false }

if ($MitFenster) {
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $schrift  = New-Object System.Drawing.Font("Segoe UI", 11)
    $fett     = New-Object System.Drawing.Font("Segoe UI", 15, [System.Drawing.FontStyle]::Bold)

    $f = New-Object System.Windows.Forms.Form
    $f.Text = "$Name installieren"
    $f.Size = New-Object System.Drawing.Size(760, 470)
    $f.StartPosition = "CenterScreen"
    $f.FormBorderStyle = "FixedDialog"
    $f.MaximizeBox = $false
    $f.MinimizeBox = $false
    $f.Font = $schrift

    $titel = New-Object System.Windows.Forms.Label
    $titel.Text = "$Name installieren"
    $titel.Font = $fett
    $titel.Location = New-Object System.Drawing.Point(24, 20)
    $titel.Size = New-Object System.Drawing.Size(700, 34)
    $titel.AccessibleName = "$Name installieren"
    $f.Controls.Add($titel)

    $unter = New-Object System.Windows.Forms.Label
    $unter.Text = "KI Stammtisch Cologne"
    $unter.Location = New-Object System.Drawing.Point(24, 54)
    $unter.Size = New-Object System.Drawing.Size(700, 26)
    $f.Controls.Add($unter)

    $lblZiel = New-Object System.Windows.Forms.Label
    $lblZiel.Text = "&Zielordner:"
    $lblZiel.Location = New-Object System.Drawing.Point(24, 100)
    $lblZiel.Size = New-Object System.Drawing.Size(700, 26)
    $lblZiel.TabIndex = 0
    $f.Controls.Add($lblZiel)

    $txtZiel = New-Object System.Windows.Forms.TextBox
    $txtZiel.Text = $Vorgabe
    $txtZiel.Location = New-Object System.Drawing.Point(24, 128)
    $txtZiel.Size = New-Object System.Drawing.Size(556, 32)
    $txtZiel.TabIndex = 1
    $txtZiel.AccessibleName = "Zielordner"
    $txtZiel.AccessibleDescription = "Ordner, in den das Programm installiert wird"
    $f.Controls.Add($txtZiel)

    $btnBlaettern = New-Object System.Windows.Forms.Button
    $btnBlaettern.Text = "&Durchsuchen"
    $btnBlaettern.Location = New-Object System.Drawing.Point(592, 126)
    $btnBlaettern.Size = New-Object System.Drawing.Size(140, 36)
    $btnBlaettern.TabIndex = 2
    $btnBlaettern.AccessibleName = "Ordner auswaehlen"
    $btnBlaettern.Add_Click({
        $d = New-Object System.Windows.Forms.FolderBrowserDialog
        $d.Description = "Ordner fuer die Installation"
        if ($d.ShowDialog() -eq "OK") { $txtZiel.Text = $d.SelectedPath }
    })
    $f.Controls.Add($btnBlaettern)

    $lblTipp = New-Object System.Windows.Forms.Label
    $lblTipp.Text = "Wird nur ein Sammelordner angegeben, legt die Installation darin einen Unterordner mit dem Programmnamen an."
    if ($Gemerkt) {
        $lblTipp.Text = "Vorbelegt mit der zuletzt verwendeten Einstellung. " + $lblTipp.Text
    }
    $lblTipp.Location = New-Object System.Drawing.Point(24, 166)
    $lblTipp.Size = New-Object System.Drawing.Size(708, 46)
    $f.Controls.Add($lblTipp)

    $chkDesk = New-Object System.Windows.Forms.CheckBox
    $chkDesk.Text = "Verknuepfung auf dem &Desktop anlegen"
    $chkDesk.Checked = $MerkDesk
    $chkDesk.Location = New-Object System.Drawing.Point(24, 220)
    $chkDesk.Size = New-Object System.Drawing.Size(708, 34)
    $chkDesk.TabIndex = 3
    $chkDesk.AccessibleName = "Verknuepfung auf dem Desktop anlegen"
    $f.Controls.Add($chkDesk)

    $chkMenue = New-Object System.Windows.Forms.CheckBox
    $chkMenue.Text = "Eintrag im &Startmenue anlegen, Gruppe KI Stammtisch Cologne"
    $chkMenue.Checked = $MerkMenue
    $chkMenue.Location = New-Object System.Drawing.Point(24, 256)
    $chkMenue.Size = New-Object System.Drawing.Size(708, 34)
    $chkMenue.TabIndex = 4
    $chkMenue.AccessibleName = "Eintrag im Startmenue anlegen"
    $f.Controls.Add($chkMenue)

    $lblStatus = New-Object System.Windows.Forms.Label
    $lblStatus.Text = "Bereit. Mit Eingabetaste installieren."
    $lblStatus.Location = New-Object System.Drawing.Point(24, 300)
    $lblStatus.Size = New-Object System.Drawing.Size(708, 60)
    $lblStatus.AccessibleName = "Status"
    $f.Controls.Add($lblStatus)

    $btnOk = New-Object System.Windows.Forms.Button
    $btnOk.Text = "&Installieren"
    $btnOk.Location = New-Object System.Drawing.Point(452, 372)
    $btnOk.Size = New-Object System.Drawing.Size(140, 42)
    $btnOk.TabIndex = 5
    $btnOk.AccessibleName = "Jetzt installieren"
    $f.Controls.Add($btnOk)

    $btnAbbruch = New-Object System.Windows.Forms.Button
    $btnAbbruch.Text = "A&bbrechen"
    $btnAbbruch.Location = New-Object System.Drawing.Point(600, 372)
    $btnAbbruch.Size = New-Object System.Drawing.Size(132, 42)
    $btnAbbruch.TabIndex = 6
    $btnAbbruch.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $btnAbbruch.AccessibleName = "Abbrechen"
    $f.Controls.Add($btnAbbruch)

    $f.AcceptButton = $btnOk
    $f.CancelButton = $btnAbbruch

    $btnOk.Add_Click({
        $btnOk.Enabled = $false
        $lblStatus.Text = "Wird installiert, bitte warten ..."
        $f.Refresh()
        try {
            $z = Ziel-Vervollstaendigen $txtZiel.Text
            $e = Installiere $z $chkDesk.Checked $chkMenue.Checked
            Merke (Eltern-Ordner $e.Ziel) $chkDesk.Checked $chkMenue.Checked
            $text = "Fertig. $Name wurde installiert." + [Environment]::NewLine
            $text += "Programmordner: " + $e.Ziel + [Environment]::NewLine
            foreach ($h in $e.Hinweise) { $text += $h + [Environment]::NewLine }
            $text += "Zum Entfernen: DEINSTALLIEREN.bat in diesem Ordner."
            $lblStatus.Text = "Fertig."
            [System.Windows.Forms.MessageBox]::Show($text, "$Name installiert",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
            $f.Close()
        } catch {
            $lblStatus.Text = "Fehlgeschlagen."
            [System.Windows.Forms.MessageBox]::Show(
                "Die Installation ist fehlgeschlagen." + [Environment]::NewLine +
                $_.Exception.Message, "Fehler",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
            $btnOk.Enabled = $true
        }
    })

    $f.Add_Shown({ $f.Activate(); $txtZiel.Focus(); $txtZiel.SelectAll() })
    [void]$f.ShowDialog()
}
else {
    Write-Host ""
    Write-Host "$Name installieren"
    Write-Host "Vorgeschlagener Ordner: $Vorgabe"
    if ($Gemerkt) { Write-Host "(vorbelegt mit der zuletzt verwendeten Einstellung)" }
    $eing = Read-Host "Anderer Ordner? Eingabetaste uebernimmt die Vorgabe"
    $z = Ziel-Vervollstaendigen $eing
    $d = Read-Host "Verknuepfung auf dem Desktop anlegen? (J/n)"
    $m = Read-Host "Eintrag im Startmenue anlegen? (J/n)"
    $desk = ($d -notmatch "^[nN]")
    $menue = ($m -notmatch "^[nN]")
    $e = Installiere $z $desk $menue
    Merke (Eltern-Ordner $e.Ziel) $desk $menue
    Write-Host ""
    Write-Host "Fertig. Programmordner: $($e.Ziel)"
    foreach ($h in $e.Hinweise) { Write-Host $h }
    Read-Host "Mit Eingabetaste schliessen"
}
"""

# ── Haupt-GUI ────────────────────────────────────────────────────────────────
# --------------------------------------------------------------- Zoom ---
# Strg-Plus, Strg-Minus, Strg-0. Siehe werkzeug\patch_zoom.py.

import tkinter.font as _tkfont

ZOOM_DATEI = os.path.join(_base(), "packer_zoom.txt")
_ZOOM = {"faktor": 1.0}
_ZOOM_BASIS = {}


def _zoom_lesen():
    """Die zuletzt gewaehlte Stufe holen. Fehlt sie, bleibt es bei 100."""
    try:
        with open(ZOOM_DATEI, "r", encoding="utf-8") as f:
            wert = float(f.read().strip())
        if 0.7 <= wert <= 3.0:
            _ZOOM["faktor"] = wert
    except Exception:
        pass


def _zoom_schreiben():
    try:
        with open(ZOOM_DATEI, "w", encoding="utf-8") as f:
            f.write("%.2f" % _ZOOM["faktor"])
    except Exception:
        pass


def _zoom_element(element, faktor):
    """Eine einzelne Schrift stellen, immer von der Ursprungsgroesse aus."""
    try:
        roh = element.cget("font")
    except Exception:
        return
    if not roh:
        return

    schluessel = str(element)
    if schluessel not in _ZOOM_BASIS:
        try:
            schrift = _tkfont.Font(root=element, font=roh)
            _ZOOM_BASIS[schluessel] = (
                schrift.actual("family"),
                abs(int(schrift.actual("size"))),
                schrift.actual("weight"),
                schrift.actual("slant"),
            )
        except Exception:
            return

    familie, groesse, gewicht, neigung = _ZOOM_BASIS[schluessel]
    neu = max(6, int(round(groesse * faktor)))
    stil = []
    if gewicht == "bold":
        stil.append("bold")
    if neigung == "italic":
        stil.append("italic")
    try:
        element.configure(font=tuple([familie, neu] + stil))
    except Exception:
        pass


def _zoom_anwenden(wurzel, faktor=None):
    """Den ganzen Baum durchgehen, einschliesslich offener Nebenfenster."""
    if faktor is not None:
        _ZOOM["faktor"] = max(0.7, min(3.0, round(faktor, 2)))
    jetzt = _ZOOM["faktor"]

    def lauf(element):
        _zoom_element(element, jetzt)
        try:
            kinder = element.winfo_children()
        except Exception:
            return
        for kind in kinder:
            lauf(kind)

    try:
        lauf(wurzel)
    except Exception:
        pass
    return jetzt


def _zoom_stufe(wurzel, richtung):
    """richtung 1 groesser, -1 kleiner, 0 zurueck auf normal."""
    if richtung == 0:
        neu = 1.0
    else:
        neu = _ZOOM["faktor"] + 0.1 * richtung
    vorher = _ZOOM["faktor"]
    _zoom_anwenden(wurzel, neu)
    _zoom_schreiben()
    try:
        _ton("hinweis" if _ZOOM["faktor"] != vorher else "fehler")
    except Exception:
        pass


def _zoom_einrichten(wurzel):
    """Tasten legen und eine gemerkte Stufe wiederherstellen."""
    _zoom_lesen()

    for taste in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
        wurzel.bind(taste, lambda _e, w=wurzel: _zoom_stufe(w, 1))
    for taste in ("<Control-minus>", "<Control-KP_Subtract>"):
        wurzel.bind(taste, lambda _e, w=wurzel: _zoom_stufe(w, -1))
    for taste in ("<Control-0>", "<Control-KP_0>"):
        wurzel.bind(taste, lambda _e, w=wurzel: _zoom_stufe(w, 0))

    # Das Einstellungsfenster entsteht erst spaeter. Nach F2 kurz warten,
    # dann noch einmal durchgehen - sonst steht es in Normalgroesse da.
    wurzel.bind("<F2>",
                lambda _e, w=wurzel: w.after(400, lambda: _zoom_anwenden(w)),
                add="+")

    if _ZOOM["faktor"] != 1.0:
        wurzel.after(200, lambda: _zoom_anwenden(wurzel))


class KIPackager:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Packer {PACKAGER_VERSION}")
        self.root.configure(bg="#1a2332")
        self.root.minsize(840, 620)
        try:
            logos = _find_logos()
            icon_path = _default_icon_path(logos)
            if icon_path:
                ic = _img(icon_path, 64)
                self.root.iconphoto(True, ic)
        except Exception:
            pass
        self._spinning = False
        self._build_ui()
        self.root.bind("<F2>", self._zeige_einstellungen)
        self.root.after(700, self._pruefe_erststart)
        self.root.bind("<F5>", lambda _e: self._build())
        _zoom_einrichten(self.root)
        self.root.mainloop()

    # ---------------------------------------------------------------- Aufbau
    def _build_ui(self):
        r = self.root
        hdr = tk.Frame(r, bg="#0d1b2a", pady=10)
        hdr.pack(fill="x")
        try:
            logos = _find_logos()
            hero_path = logos[-1] if logos else None
            if hero_path:
                lg = _img(hero_path, 48)
                lb = tk.Label(hdr, image=lg, bg="#0d1b2a", bd=0)
                lb.image = lg
                lb.pack(side="left", padx=16)
        except Exception:
            pass
        tk.Label(hdr, text="Packer", bg="#0d1b2a", fg="#00e5c8",
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Button(hdr, text="Einstellungen (F2)",
                  command=self._zeige_einstellungen,
                  bg="#2e4060", fg="#e8edf5", relief="flat",
                  font=("Segoe UI", 10), cursor="hand2").pack(
                      side="right", padx=16)
        tk.Label(hdr, text=FIRMA, bg="#0d1b2a", fg="#8fa8c8",
                 font=("Segoe UI", 10)).pack(side="right", padx=8)

        body = tk.Frame(r, bg="#1a2332", padx=24, pady=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        self.mode_var = tk.StringVar(value="datei")
        self.script_var = tk.StringVar()
        self.folder_var = tk.StringVar()
        self.entry_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.do_python = tk.BooleanVar(value=True)
        self.do_bat = tk.BooleanVar(value=True)
        self.do_doku = tk.BooleanVar(value=True)
        self.do_zip = tk.BooleanVar(value=True)
        self.do_selbst = tk.BooleanVar(value=False)
        self.do_webseite = tk.BooleanVar(value=False)
        self.do_logo = tk.BooleanVar(value=True)
        self.selected_logo = tk.StringVar(value="")

        self._build_mode_row(body, 0)
        self._src_row(body, 1)
        self._entry_row(body, 2)

        tk.Label(body, text="Tool-Name:", bg="#1a2332", fg="#e8edf5",
                 font=("Segoe UI", 11)).grid(row=3, column=0, sticky="w", pady=6)
        tk.Entry(body, textvariable=self.name_var, bg="#0d3b66", fg="#e8edf5",
                 insertbackground="#e8edf5", font=("Segoe UI", 11),
                 relief="flat", bd=4).grid(row=3, column=1, sticky="ew",
                                            padx=(8, 0), pady=6)

        self.preview_var = tk.StringVar(value="ZIP-Name: " + ZIP_PREFIX + "...")
        tk.Label(body, textvariable=self.preview_var, bg="#1a2332",
                 fg="#4a6a8a", font=("Segoe UI", 8)).grid(
                     row=4, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.name_var.trace_add("write", self._update_preview)

        # Alles Dauerhafte in ein eigenes Fenster. Es wird jetzt angelegt
        # und nur versteckt - so sind alle Angaben von Anfang an da,
        # auch wenn es nie geoeffnet wird.
        self._baue_einstellungen()

        self._build_progress_bar(body, row=5)

        cbf = tk.Frame(body, bg="#1a2332")
        cbf.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 8))

        # Der Bauen-Knopf gehoert hierher, nicht auf die
        # Einstellungsseite. Er stand frueher in der Logo-Reihe und
        # wanderte mit ihr mit.
        tk.Button(cbf, text="  Paket bauen  ",
                  bg="#00e5c8", fg="#0d1b2a",
                  activebackground="#00bfa5", activeforeground="#0d1b2a",
                  font=("Segoe UI", 13, "bold"), bd=0, padx=20, pady=12,
                  cursor="hand2", relief="flat",
                  command=self._build).pack(side="right", padx=(20, 8))
        opts = [(self.do_python,
                 "Python mitliefern (~30 MB, laeuft ohne Installation)"),
                (self.do_bat, "BAT/LNK"), (self.do_doku, "Doku"),
                (self.do_zip, "ZIP"),
                (self.do_selbst, "Bei mir installieren")]

        # Den Haken fuer die Webseite gibt es nur, wenn ein Ordner
        # dafuer eingetragen ist. Wer den Packer uebernimmt, hat keine -
        # dann soll ihn auch nichts danach fragen.
        if str(lade_marke().get("webseite", "")).strip():
            opts.append((self.do_webseite, "Fuer die Webseite"))
        for var, txt in opts:
            tk.Checkbutton(cbf, text=txt, variable=var,
                           bg="#1a2332", fg="#e8edf5", selectcolor="#0d3b66",
                           activebackground="#1a2332", activeforeground="#00e5c8",
                           font=("Segoe UI", 10), cursor="hand2").pack(side="left",
                                                                       padx=8)

        tk.Label(body, text="Log:", bg="#1a2332", fg="#8fa8c8",
                 font=("Segoe UI", 9)).grid(row=7, column=0, columnspan=2, sticky="w")
        logwrap = tk.Frame(body, bg="#1a2332")
        logwrap.grid(row=8, column=0, columnspan=2, sticky="nsew", pady=(2, 0))
        body.rowconfigure(8, weight=1)
        logwrap.columnconfigure(0, weight=1)
        logwrap.rowconfigure(0, weight=1)
        self.log = tk.Text(logwrap, bg="#0d1b2a", fg="#8fa8c8",
                           font=("Consolas", 9), height=12, relief="flat",
                           bd=4, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(logwrap, command=self.log.yview, bg="#1a2332")
        sb.grid(row=0, column=1, sticky="ns")
        self.log.config(yscrollcommand=sb.set)

    def _baue_einstellungen(self):
        """
        Das Fenster fuer alles Dauerhafte.

        Es wird beim Start angelegt und sofort versteckt. Grund: Die
        Eingabefelder darin gehoeren zu Angaben, die der Bauablauf
        braucht - gaebe es sie erst beim Oeffnen, scheiterte ein Bau,
        solange das Fenster nie aufgerufen wurde.
        """
        w = tk.Toplevel(self.root)
        w.title("Einstellungen - " + FIRMA)
        w.configure(bg="#1a2332")
        w.minsize(760, 520)
        w.withdraw()
        w.protocol("WM_DELETE_WINDOW", self._schliesse_einstellungen)
        w.bind("<Escape>", lambda _e: self._schliesse_einstellungen())
        self._einst_fenster = w

        kopf = tk.Frame(w, bg="#0d1b2a", pady=8)
        kopf.pack(fill="x")
        tk.Label(kopf, text="Einstellungen", bg="#0d1b2a", fg="#00e5c8",
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=16)
        tk.Label(kopf, text="Gilt fuer alle Pakete", bg="#0d1b2a",
                 fg="#8fa8c8", font=("Segoe UI", 9)).pack(side="right",
                                                          padx=16)

        innen = tk.Frame(w, bg="#1a2332", padx=20, pady=14)
        innen.pack(fill="both", expand=True)
        innen.columnconfigure(1, weight=1)

        # Wohin gebaut wird. Stand frueher fest im Kode und war nirgends
        # zu sehen - wer nicht auf den Bildschirm schauen kann, wusste
        # hinterher nicht, wo das Ergebnis liegt.
        tk.Label(innen, text="Ablage:", bg="#1a2332", fg="#e8edf5",
                 font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w",
                                             pady=6)
        gemerkt = str(lade_marke().get("ziel", "")).strip()
        self.ziel_var = tk.StringVar(value=gemerkt or ZIEL_ORDNER)
        tk.Entry(innen, textvariable=self.ziel_var, bg="#0d3b66",
                 fg="#e8edf5", insertbackground="#e8edf5",
                 font=("Segoe UI", 10), relief="flat", bd=4).grid(
                     row=0, column=1, sticky="ew", padx=(8, 8), pady=6)
        tk.Button(innen, text="Waehlen ...", command=self._pick_ziel,
                  bg="#2e4060", fg="#e8edf5", relief="flat",
                  font=("Segoe UI", 10), cursor="hand2").grid(
                      row=0, column=2, sticky="e", pady=6)

        # Der zweite Ablageort: wohin die Werkzeuge fuer die Webseite
        # gehen. Bleibt er leer, gibt es keine Webseite - dann fehlt im
        # Hauptfenster auch der Haken dafuer.
        tk.Label(innen, text="Webseite:", bg="#1a2332", fg="#e8edf5",
                 font=("Segoe UI", 11)).grid(row=1, column=0, sticky="w",
                                             pady=6)
        self.web_ablage_var = tk.StringVar(
            value=str(lade_marke().get("webseite", "")).strip())
        tk.Entry(innen, textvariable=self.web_ablage_var, bg="#0d3b66",
                 fg="#e8edf5", insertbackground="#e8edf5",
                 font=("Segoe UI", 10), relief="flat", bd=4).grid(
                     row=1, column=1, sticky="ew", padx=(8, 8), pady=6)
        tk.Button(innen, text="Waehlen ...", command=self._pick_webablage,
                  bg="#2e4060", fg="#e8edf5", relief="flat",
                  font=("Segoe UI", 10), cursor="hand2").grid(
                      row=1, column=2, sticky="e", pady=6)
        tk.Label(innen,
                 text="Leer lassen, wenn keine Webseite bestueckt wird.",
                 bg="#1a2332", fg="#4a6a8a",
                 font=("Segoe UI", 8)).grid(row=2, column=1, sticky="w",
                                            padx=(8, 0))

        self._build_marke_row(innen, row=3)
        self._build_logo_picker(innen, row=4)

        fuss = tk.Frame(w, bg="#1a2332", pady=10)
        fuss.pack(fill="x")
        tk.Button(fuss, text="Uebernehmen und schliessen",
                  command=self._schliesse_einstellungen,
                  bg="#00bfa5", fg="#0d1b2a", relief="flat",
                  font=("Segoe UI", 11, "bold"), cursor="hand2").pack(
                      side="right", padx=20)

    def _pruefe_erststart(self):
        """
        Steht der Packer ohne Marke da, ist es sein erster Start.

        Dann oeffnet er die Einstellungen von selbst und sagt, was er
        braucht. Wer ihn frisch installiert hat, stuende sonst vor
        einem Programm, das nichts von ihm weiss und nichts sagt.
        """
        try:
            m = lade_marke()
        except Exception:
            m = {}
        if not isinstance(m, dict):
            m = {}

        # Als eingerichtet gilt, wer einen Autor eingetragen hat. Der
        # Rest hat brauchbare Vorgaben, der Autor nicht - er steht in
        # jeder Lizenz, die der Packer schreibt.
        if str(m.get("autor", "")).strip():
            return

        text = ("Willkommen. Dieser Packer ist noch nicht eingerichtet. "
                "Bitte drei Angaben ergaenzen: die Ablage, also wohin "
                "die fertigen Pakete gehen sollen. Den Autor, der als "
                "Urheber in jeder Lizenz steht. Und die Lizenz selbst. "
                "Das Fenster dafuer ist jetzt offen. Es laesst sich "
                "spaeter jederzeit mit der Taste F 2 wieder aufrufen.")

        self._log("Erster Start - der Packer ist noch nicht eingerichtet.")
        self._log("Bitte Ablage, Autor und Lizenz eintragen. "
                  "Spaeter jederzeit ueber F2 erreichbar.")
        try:
            _ton("hinweis")
        except Exception:
            pass
        self._zeige_einstellungen()
        try:
            ph.sag(text)
        except Exception:
            pass

    def _zeige_einstellungen(self, *_):
        w = getattr(self, "_einst_fenster", None)
        if not w:
            return
        w.deiconify()
        w.lift()
        w.focus_force()

    def _schliesse_einstellungen(self, *_):
        """Schliessen heisst uebernehmen - die Angaben werden gesichert."""
        try:
            self._marke()
            self._log("Einstellungen uebernommen.")
        except Exception as fehler:
            self._log("Einstellungen nicht gesichert: " + str(fehler))
        w = getattr(self, "_einst_fenster", None)
        if w:
            w.withdraw()

    def _build_mode_row(self, body, row):
        wrap = tk.Frame(body, bg="#1a2332")
        wrap.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        tk.Label(wrap, text="Was soll verpackt werden?", bg="#1a2332",
                 fg="#e8edf5", font=("Segoe UI", 11)).pack(side="left", padx=(0, 12))
        for val, txt in (("datei", "Einzelne Datei"),
                         ("projekt", "Ganzes Projekt (Ordner)")):
            tk.Radiobutton(wrap, text=txt, variable=self.mode_var, value=val,
                           command=self._mode_changed,
                           bg="#1a2332", fg="#e8edf5", selectcolor="#0d3b66",
                           activebackground="#1a2332", activeforeground="#00e5c8",
                           font=("Segoe UI", 10), cursor="hand2").pack(side="left",
                                                                       padx=6)

    def _src_row(self, body, row):
        self.lbl_src = tk.Label(body, text="Python-Script (.py):", bg="#1a2332",
                                fg="#e8edf5", font=("Segoe UI", 11))
        self.lbl_src.grid(row=row, column=0, sticky="w", pady=6)
        f = tk.Frame(body, bg="#1a2332")
        f.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=6)
        f.columnconfigure(0, weight=1)
        self.src_entry = tk.Entry(f, textvariable=self.script_var, bg="#0d3b66",
                                  fg="#e8edf5", insertbackground="#e8edf5",
                                  font=("Segoe UI", 10), relief="flat", bd=4)
        self.src_entry.grid(row=0, column=0, sticky="ew")
        tk.Button(f, text="...", bg="#2e4060", fg="#e8edf5",
                  activebackground="#00e5c8", activeforeground="#0d1b2a",
                  font=("Segoe UI", 11, "bold"), bd=0, padx=12, pady=6,
                  cursor="hand2", relief="flat",
                  command=self._pick_source).grid(row=0, column=1, padx=(6, 0))

    def _entry_row(self, body, row):
        self.entry_frame = tk.Frame(body, bg="#1a2332")
        self.entry_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
        self.entry_frame.columnconfigure(1, weight=1)
        tk.Label(self.entry_frame, text="Startdatei:", bg="#1a2332", fg="#e8edf5",
                 font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w")
        self.entry_menu = tk.OptionMenu(self.entry_frame, self.entry_var, "")
        self.entry_menu.config(bg="#0d3b66", fg="#e8edf5", relief="flat",
                               activebackground="#00e5c8", activeforeground="#0d1b2a",
                               font=("Segoe UI", 10), bd=0, padx=10, pady=6,
                               highlightthickness=0, cursor="hand2")
        self.entry_menu["menu"].config(bg="#0d3b66", fg="#e8edf5",
                                       font=("Segoe UI", 10))
        self.entry_menu.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.lbl_count = tk.Label(self.entry_frame, text="", bg="#1a2332",
                                  fg="#4a6a8a", font=("Segoe UI", 9))
        self.lbl_count.grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.entry_frame.grid_remove()

    def _mode_changed(self):
        if self.mode_var.get() == "projekt":
            self.lbl_src.config(text="Projektordner:")
            self.src_entry.config(textvariable=self.folder_var)
            self.entry_frame.grid()
        else:
            self.lbl_src.config(text="Python-Script (.py):")
            self.src_entry.config(textvariable=self.script_var)
            self.entry_frame.grid_remove()

    def _build_marke_row(self, body, row):
        """Autor, Netzadresse und Lizenz. Wird einmal eingetragen und
        gemerkt - jedes gebaute Paket traegt es dann automatisch."""
        m = lade_marke()
        self.autor_var = tk.StringVar(value=m.get("autor", ""))
        self.web_var = tk.StringVar(value=m.get("web", ""))
        self.lizenz_var = tk.StringVar(value=m.get("lizenz", "GPL-3.0"))
        self.kurz_var = tk.StringVar(value="Werkzeug der KI Stammtisch Cologne Community")

        wrap = tk.Frame(body, bg="#1a2332")
        wrap.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 4))
        wrap.columnconfigure(1, weight=1)
        wrap.columnconfigure(3, weight=1)

        def feld(spalte, text, var, breite=1):
            tk.Label(wrap, text=text, bg="#1a2332", fg="#e8edf5",
                     font=("Segoe UI", 10)).grid(row=spalte[0], column=spalte[1],
                                                 sticky="w", pady=3)
            e = tk.Entry(wrap, textvariable=var, bg="#0d3b66", fg="#e8edf5",
                         insertbackground="#e8edf5", font=("Segoe UI", 10),
                         relief="flat", bd=4)
            e.grid(row=spalte[0], column=spalte[1] + 1, sticky="ew",
                   padx=(8, 16), pady=3, columnspan=breite)
            return e

        feld((0, 0), "Autor:", self.autor_var)
        feld((0, 2), "Netzadresse:", self.web_var)
        feld((1, 0), "Kurzbeschreibung:", self.kurz_var, breite=3)

        lz = tk.Frame(wrap, bg="#1a2332")
        lz.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        tk.Label(lz, text="Lizenz:", bg="#1a2332", fg="#e8edf5",
                 font=("Segoe UI", 10)).pack(side="left", padx=(0, 10))
        for wert, txt in (
                ("GPL-3.0", "GPL-3.0  (Abwandlungen muessen offen bleiben)"),
                ("MIT", "MIT  (frei, nur Namensnennung)"),
                ("keine", "keine Lizenzdatei")):
            tk.Radiobutton(lz, text=txt, variable=self.lizenz_var, value=wert,
                           bg="#1a2332", fg="#e8edf5", selectcolor="#0d3b66",
                           activebackground="#1a2332", activeforeground="#00e5c8",
                           font=("Segoe UI", 9), cursor="hand2").pack(side="left",
                                                                      padx=6)

    def _marke(self):
        """
        Die Marke speichern, ohne den Rest zu verlieren.

        Frueher wurde hier ein neues Woerterbuch mit drei Feldern
        gebaut und ueber die Datei geschrieben. firma, kuerzel und
        downloads verschwanden dabei jedesmal - der Fehler fiel erst
        auf, als das Archiv falsch hiess.
        """
        m = lade_marke()
        if not isinstance(m, dict):
            m = {}
        m["autor"] = self.autor_var.get().strip()
        m["web"] = self.web_var.get().strip()
        m["lizenz"] = self.lizenz_var.get()
        ziel = self.ziel_var.get().strip()
        if ziel:
            m["ziel"] = ziel
        if hasattr(self, "web_ablage_var"):
            m["webseite"] = self.web_ablage_var.get().strip()
        sichere_marke(m)
        return m

    def _build_logo_picker(self, body, row):
        wrap = tk.Frame(body, bg="#1a2332")
        wrap.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 4))
        tk.Checkbutton(wrap, text="Logo verwenden (Splash-Screen und Icon)",
                       variable=self.do_logo,
                       bg="#1a2332", fg="#e8edf5", selectcolor="#0d3b66",
                       activebackground="#1a2332", activeforeground="#00e5c8",
                       font=("Segoe UI", 10), cursor="hand2").pack(anchor="w")
        row2 = tk.Frame(wrap, bg="#1a2332")
        row2.pack(anchor="w", pady=(6, 0), fill="x")
        thumbs = tk.Frame(row2, bg="#1a2332")
        thumbs.pack(side="left")
        logos = _find_logos()
        self.logo_thumb_labels = {}
        self._logo_thumb_imgs = []
        if not logos:
            tk.Label(thumbs, text="Keine logo_*.png Dateien gefunden.",
                     bg="#1a2332", fg="#4a6a8a",
                     font=("Segoe UI", 9)).pack(side="left")
        for path in logos:
            try:
                th = _img(path, 56)
            except Exception:
                continue
            self._logo_thumb_imgs.append(th)
            cell = tk.Frame(thumbs, bg="#1a2332", padx=4, pady=4,
                            highlightthickness=2, highlightbackground="#1a2332")
            cell.pack(side="left", padx=4)
            lbl = tk.Label(cell, image=th, bg="#1a2332", bd=0, cursor="hand2")
            lbl.pack()
            name_lbl = tk.Label(cell, text=os.path.basename(path), bg="#1a2332",
                                fg="#8fa8c8", font=("Segoe UI", 7))
            name_lbl.pack()
            self.logo_thumb_labels[path] = cell
            for widget in (cell, lbl, name_lbl):
                widget.bind("<Button-1>", lambda e, p=path: self._select_logo(p))
        if logos:
            self._select_logo(logos[-1])
        tk.Button(row2, text="  Logo suchen ...  ", bg="#2e4060", fg="#e8edf5",
                  activebackground="#00e5c8", activeforeground="#0d1b2a",
                  font=("Segoe UI", 11), bd=0, padx=16, pady=12,
                  cursor="hand2", relief="flat",
                  command=self._pick_logo).pack(side="left", padx=(16, 0))
        self.logo_info = tk.Label(wrap, text="", bg="#1a2332", fg="#8fa8c8",
                                  font=("Segoe UI", 9), anchor="w")
        self.logo_info.pack(anchor="w", pady=(6, 0), fill="x")
        self._update_logo_info()

    def _pick_logo(self):
        start = _remembered_logo_dir() or _base()
        p = filedialog.askopenfilename(
            title="Logo waehlen (PNG, moeglichst quadratisch)",
            initialdir=start,
            filetypes=[("PNG-Bilder", "*.png"), ("Alle", "*.*")])
        if not p:
            return
        _remember_logo_dir(p)
        self.selected_logo.set(p)
        self.do_logo.set(True)
        self._update_logo_info()
        _ton("hinweis")
        if False:
            try:
                pass
            except Exception:
                pass
        messagebox.showinfo("Logo",
                            "Logo uebernommen:\n\n" + os.path.basename(p)
                            + "\n\nOrdner wird gemerkt, beim naechsten Start "
                              "sind die Logos wieder da.")

    def _update_logo_info(self):
        p = self.selected_logo.get()
        if p:
            self.logo_info.config(
                text="Gewaehltes Logo: " + os.path.basename(p)
                     + "     (" + os.path.dirname(p) + ")",
                fg="#8fa8c8")
        else:
            self.logo_info.config(
                text="KEIN LOGO GEFUNDEN - Knopf 'Logo suchen ...' benutzen, "
                     "sonst gibt es kein Symbol und keinen Splash.",
                fg="#f0b400")

    def _select_logo(self, path):
        self.selected_logo.set(path)
        for p, cell in self.logo_thumb_labels.items():
            cell.config(highlightbackground="#00e5c8" if p == path else "#1a2332")
        if hasattr(self, "logo_info"):
            self._update_logo_info()

    def _build_progress_bar(self, body, row):
        wrap = tk.Frame(body, bg="#1a2332")
        wrap.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.progress_var = tk.StringVar(value="Bereit")
        tk.Label(wrap, textvariable=self.progress_var, bg="#1a2332", fg="#8fa8c8",
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.progress_canvas = tk.Canvas(wrap, height=8, bg="#0d1b2a",
                                         highlightthickness=0)
        self.progress_canvas.pack(fill="x", pady=(4, 0))
        self._progress_x = 0

    # ------------------------------------------------------------- Auswahl
    def _pick_source(self):
        if self.mode_var.get() == "projekt":
            p = filedialog.askdirectory(title="Projektordner waehlen")
            if not p:
                return
            self.folder_var.set(p)
            files = _py_files(p)
            if not files:
                messagebox.showerror("Fehler",
                                     "In diesem Ordner liegt keine Python-Datei.")
                return
            menu = self.entry_menu["menu"]
            menu.delete(0, "end")
            for fn in files:
                menu.add_command(label=fn,
                                 command=lambda v=fn: self.entry_var.set(v))
            self.entry_var.set(_guess_entry(p))
            alle = _project_files(p)
            self.lbl_count.config(
                text=f"{len(files)} Python-Dateien, {len(alle)} Dateien gesamt")
            self.name_var.set(os.path.basename(p.rstrip("\\/")))
        else:
            p = filedialog.askopenfilename(title="Python-Script waehlen",
                                           filetypes=[("Python", "*.py"),
                                                      ("Alle", "*.*")])
            if p:
                self.script_var.set(p)
                self.name_var.set(os.path.splitext(os.path.basename(p))[0])

    def _pick_webablage(self):
        """Ordner der Webseite waehlen."""
        p = filedialog.askdirectory(
            title="Wohin sollen die Werkzeuge fuer die Webseite?",
            initialdir=self.web_ablage_var.get().strip() or "C:\\")
        if p:
            self.web_ablage_var.set(os.path.normpath(p))
            self._log("Ordner der Webseite: " + os.path.normpath(p))
            self._log("Der Haken dafuer erscheint nach einem Neustart.")

    def _pick_ziel(self):
        """Ordner waehlen, in den gebaut wird."""
        p = filedialog.askdirectory(title="Wohin soll gebaut werden?",
                                    initialdir=self.ziel_var.get().strip()
                                    or ZIEL_ORDNER)
        if p:
            self.ziel_var.set(os.path.normpath(p))
            self._log("Ablage: " + os.path.normpath(p))

    def _update_preview(self, *_):
        raw = self.name_var.get().strip() or "..."
        self.preview_var.set("ZIP-Name: " + ZIP_PREFIX + raw + ".zip")

    # ----------------------------------------------------------------- Log
    def _log(self, msg):
        # Zusaetzlich in eine Datei - das Log-Fenster ist mit Screenreader
        # kaum zu lesen, die Datei laesst sich vorlesen oder verschicken.
        try:
            with open(BUILD_LOG, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

        def _do():
            self.log.config(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.config(state="disabled")
        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def _start_spinner(self):
        self._spinning = True
        self._progress_x = 0
        self.progress_var.set("Arbeite ...")
        self._spin_tick()

    def _spin_tick(self):
        if not self._spinning:
            return
        c = self.progress_canvas
        c.delete("all")
        w = c.winfo_width() or 400
        block_w = max(40, w // 5)
        x = self._progress_x % (w + block_w) - block_w
        c.create_rectangle(x, 0, x + block_w, 8, fill="#00e5c8", outline="")
        self._progress_x += 12
        self.root.after(40, self._spin_tick)

    def _finish_build(self, success, note=""):
        self._spinning = False
        self.progress_canvas.delete("all")
        w = self.progress_canvas.winfo_width() or 400
        if success:
            self.progress_canvas.create_rectangle(0, 0, w, 8, fill="#00e5c8",
                                                  outline="")
            self.progress_var.set("Fertig")
            _ton("fertig")
            if False:
                try:
                    pass
                except Exception:
                    pass
            messagebox.showinfo("Packer", "Fertig gebaut!\n\n" + note)
        else:
            self.progress_canvas.create_rectangle(0, 0, w, 8, fill="#e53935",
                                                  outline="")
            self.progress_var.set("Fehler")
            _ton("fehler")
            if False:
                try:
                    pass
                except Exception:
                    pass
            messagebox.showerror("Packer",
                                 "Beim Bauen ist ein Fehler aufgetreten.\n\n"
                                 + note + "\n\nEinzelheiten im Log.")

    # --------------------------------------------------------------- Bauen
    def _build(self):
        mode = self.mode_var.get()
        roh = self.name_var.get().strip()
        name = _normalize_name(roh)
        if not name:
            messagebox.showerror("Fehler", "Kein Tool-Name angegeben.")
            return
        if name != roh:
            self.name_var.set(name)
            self._log("Tool-Name bereinigt: " + roh + "  ->  " + name)

        # Im Hauptfaden lesen, nicht spaeter im Baufaden - Tkinter mag
        # das nicht.
        self._ziel_basis = self.ziel_var.get().strip() or ZIEL_ORDNER
        if mode == "projekt":
            folder = self.folder_var.get().strip()
            entry = self.entry_var.get().strip()
            if not folder or not os.path.isdir(folder):
                messagebox.showerror("Fehler", "Kein gueltiger Projektordner.")
                return
            if not entry or not os.path.exists(os.path.join(folder, entry)):
                messagebox.showerror("Fehler", "Keine gueltige Startdatei.")
                return
        else:
            script = self.script_var.get().strip()
            if not script or not os.path.exists(script):
                messagebox.showerror("Fehler", "Kein gueltiges Script gewaehlt.")
                return
            folder, entry = os.path.dirname(script), os.path.basename(script)
        # Letzte Sicherung: sind in den zu packenden Dateien Zugangsdaten?
        if mode == "projekt":
            drin, _weg = _project_files(folder, mit_grund=True)
            verdacht = _geheimnis_verdacht(drin)
            if verdacht:
                _ton("hinweis")
                if False:
                    try:
                        pass
                    except Exception:
                        pass
                zeilen = "\n".join(f"   {n}:  {t}" for n, t in verdacht[:8])
                weiter = messagebox.askyesno(
                    "Achtung - moegliche Zugangsdaten",
                    "In diesen Dateien steht etwas, das nach Zugangsdaten "
                    "aussieht:\n\n" + zeilen +
                    ("\n   ..." if len(verdacht) > 8 else "") +
                    "\n\nSie wuerden mit ins Paket wandern und damit an "
                    "jeden weitergegeben, der es herunterlaedt.\n\n"
                    "Trotzdem bauen?\n\n"
                    "Nein = abbrechen und die Dateien vorher entfernen.")
                if not weiter:
                    return

        logo = self.selected_logo.get() if self.do_logo.get() else None
        if self.do_logo.get() and not logo:
            _ton("hinweis")
            if False:
                try:
                    pass
                except Exception:
                    pass
            weiter = messagebox.askyesno(
                "Kein Logo",
                "Es wurde kein Logo gefunden.\n\n"
                "Ohne Logo bekommt das Paket KEIN Symbol - Windows zeigt "
                "dann das Python-Zeichen - und der Splash erscheint nur "
                "als Text.\n\n"
                "Trotzdem jetzt bauen?\n\n"
                "Nein = abbrechen, dann 'Logo suchen ...' benutzen.")
            if not weiter:
                return
        self._start_spinner()
        threading.Thread(target=self._do_build,
                         args=(mode, folder, entry, name, logo),
                         daemon=True).start()

    def _copy_sources(self, mode, folder, entry, name, dest):
        """Quelltexte in den Arbeitsordner spiegeln und die Startdatei
        mit dem Splash-Vorspann versehen."""
        os.makedirs(dest, exist_ok=True)
        if mode == "projekt":
            files, weg = _project_files(folder, mit_grund=True)
            if weg:
                self._log(f"{len(weg)} Datei(en) bewusst NICHT ins Paket:")
                # Die Laufvariable darf nicht name heissen - sie
                # wuerde den Programmnamen ueberschreiben, der
                # weiter unten in den Splash eingesetzt wird.
                for datei, grund in sorted(set(weg))[:25]:
                    self._log(f"   {datei}   ({grund})")
                if len(set(weg)) > 25:
                    self._log(f"   ... und {len(set(weg)) - 25} weitere")
            for src in files:
                rel = os.path.relpath(src, folder)
                tgt = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(tgt), exist_ok=True)
                shutil.copy2(src, tgt)
            self._log(f"{len(files)} Projektdateien kopiert.")
        else:
            shutil.copy2(os.path.join(folder, entry), os.path.join(dest, entry))
            self._log("Script kopiert.")
        orig = open(os.path.join(dest, entry), "r", encoding="utf-8",
                    errors="replace").read()
        marke = lade_marke()
        wrap = (WRAPPER_TPL.replace("~APPNAME~", name)
                .replace("~AUTOR~", marke.get("autor", ""))
                .replace("~LIZENZ~", marke.get("lizenz", "GPL-3.0"))
                .replace("~WEB~", marke.get("web", "")))
        # Kein Nachladen mehr: die Pakete liegen im Ordner pakete,
        # starter.py haengt ihn vorn an den Suchpfad.
        vorspann = wrap
        wrapped = vorspann + "\n" + orig
        with open(os.path.join(dest, entry), "w", encoding="utf-8") as f:
            f.write(wrapped)
        self._log("Bootstrap und Splash in die Startdatei eingesetzt.")
        return [os.path.join(dest, f) for f in os.listdir(dest)
                if f.lower().endswith(".py")]

    def _make_ico(self, logo, target_dir):
        if not logo or not os.path.exists(logo):
            return None
        try:
            from PIL import Image
            ico = os.path.join(target_dir, "app_icon.ico")
            Image.open(logo).convert("RGBA").save(
                ico, format="ICO",
                sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
            self._log("Icon erzeugt (app_icon.ico).")
            return ico
        except Exception as e:
            self._log(f"Icon-Konvertierung fehlgeschlagen: {e}")
            return None

    def _copy_logos(self, logo, target_dir):
        if not logo or not os.path.exists(logo):
            self._log("Kein Logo eingebunden (Splash zeigt die Textvariante).")
            return
        shutil.copy2(logo, os.path.join(target_dir, "logo512.png"))
        small = _default_icon_path(_find_logos())
        if small:
            shutil.copy2(small, os.path.join(target_dir, "logo64.png"))
        self._log(f"Logo eingebunden: {os.path.basename(logo)}")

    @staticmethod
    def _dir_size(path):
        total = 0
        for root_, _, files in os.walk(path):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root_, fn))
                except Exception:
                    pass
        return total

    def _build_quelle(self, src_dir, entry, name, release):
        """
        Kein Uebersetzen, kein PyInstaller. Der Quellkode wandert als
        Klartext in den Programmordner, die Fremdpakete daneben in den
        Ordner pakete, und ein eigenes Python von python.org als Rueckfall.

        Auf dem Zielrechner wird nichts installiert. Ist dort ein
        passendes Python vorhanden, wird es benutzt - unveraendert.
        """
        app_dir = os.path.join(release, name)
        os.makedirs(app_dir, exist_ok=True)
        for eintrag in os.listdir(src_dir):
            q = os.path.join(src_dir, eintrag)
            z = os.path.join(app_dir, eintrag)
            try:
                if os.path.isdir(q):
                    shutil.copytree(q, z, dirs_exist_ok=True)
                else:
                    shutil.copy2(q, z)
            except Exception as e:
                self._log("Konnte nicht kopieren: " + eintrag + " - " + str(e))
        self._log("Quellkode als Klartext uebernommen.")

        # Hineinsehen, nicht nur den Namen lesen. Eine Datei kann harmlos
        # heissen und trotzdem einen Pfad zum Benutzerkonto enthalten.
        alle = []
        for wurzel, ordner, dateien in os.walk(app_dir):
            ordner[:] = [d for d in ordner
                         if d.lower() not in ("python", "pakete")]
            alle.extend(os.path.join(wurzel, d) for d in dateien)
        funde = _inhalt_pruefen(alle)
        if funde:
            self._log("ABBRUCH - private Angaben im Inhalt gefunden:")
            for pfad, nr, was in funde[:20]:
                self._log("   {}  Zeile {}  ({})".format(
                    os.path.relpath(pfad, app_dir), nr, was))
            if len(funde) > 20:
                self._log("   ... und {} weitere".format(len(funde) - 20))
            self._log("Diese Dateien aus dem Projekt entfernen oder die "
                      "Angaben herausnehmen, dann neu bauen.")
            return None, ("Inhaltspruefung: " + str(len(funde))
                          + " Fundstelle(n) mit privaten Angaben. "
                            "Siehe Log. Nichts wurde gepackt.")
        self._log("Inhaltspruefung bestanden - keine privaten Pfade "
                  "in den Dateien.")

        # Echte Zugangsschluessel. Eine Datendatei kann der Packager
        # weglassen - aus einer Programmdatei laesst sich nichts
        # herausschneiden, ohne das Programm zu beschaedigen.
        schlimm = []
        entfernt = []
        for wurzel, ordner, dateien in os.walk(app_dir):
            ordner[:] = [d for d in ordner
                         if d.lower() not in ("python", "pakete")]
            for d in dateien:
                voll = os.path.join(wurzel, d)
                if os.path.splitext(d)[1].lower() not in DATEN_ENDUNGEN:
                    continue
                for _n, fund in _geheimnis_verdacht([voll]):
                    if os.path.splitext(d)[1].lower() in DATEN_ENDUNGEN:
                        try:
                            os.remove(voll)
                            entfernt.append(os.path.relpath(voll, app_dir))
                            self._log("ENTFERNT: " + os.path.relpath(
                                voll, app_dir) + " - enthaelt " + fund)
                        except Exception as fehler:
                            schlimm.append(os.path.relpath(voll, app_dir)
                                           + " (" + str(fehler) + ")")
                    else:
                        schlimm.append(os.path.relpath(voll, app_dir)
                                       + " - " + fund)
        if schlimm:
            self._log("ABBRUCH - Zugangsschluessel im Quelltext:")
            for s in schlimm[:20]:
                self._log("   " + s)
            self._log("Diese Stellen im Projekt beheben, dann neu bauen.")
            _ton("fehler")
            try:
                ph.sag("Abbruch. Im Quelltext steht ein "
                       "Zugangsschluessel. Es wurde nichts gepackt.")
            except Exception:
                pass
            return None, ("Zugangsschluessel gefunden: "
                          + str(len(schlimm)) + " Stelle(n). Siehe Log.")

        if entfernt:
            # Hoerbar melden. Wer nicht mitliest, wuesste sonst nicht,
            # dass eine Datei fehlt.
            _ton("hinweis")
            if len(entfernt) == 1:
                ansage = ("Achtung. Eine Datei mit Zugangsdaten wurde aus "
                          "dem Paket entfernt: " + entfernt[0]
                          + ". Der Bau geht weiter.")
            else:
                ansage = ("Achtung. " + str(len(entfernt)) + " Dateien mit "
                          "Zugangsdaten wurden aus dem Paket entfernt. "
                          "Der Bau geht weiter.")
            self._log(ansage)
            try:
                ph.sag(ansage)
            except Exception:
                pass

        # Endkontrolle. Sie fragt nicht, sie bricht ab. Eine Rueckfrage
        # kann man versehentlich mit Ja beantworten - und was einmal
        # verschenkt ist, holt niemand zurueck.
        gefunden = []
        for wurzel, ordner, dateien in os.walk(app_dir):
            ordner[:] = [d for d in ordner
                         if d.lower() not in BETRIEB_DIRS
                         and d.lower() != "python"
                         and d.lower() != "pakete"]
            for d in dateien:
                grund = _geheim_grund(d) or _skip_grund(d)
                if grund in ("Zugangsdaten", "Sicherungsdatei",
                             "entsteht im Betrieb"):
                    gefunden.append(os.path.relpath(
                        os.path.join(wurzel, d), app_dir) + "  (" + grund + ")")
        if gefunden:
            self._log("ABBRUCH - im fertigen Paket liegt, was nicht "
                      "hinausgehen darf:")
            for g in gefunden[:20]:
                self._log("   " + g)
            return None, ("Endkontrolle: " + str(len(gefunden))
                          + " Datei(en) mit Zugangs- oder Betriebsdaten. "
                            "Siehe Log. Nichts wurde gepackt.")
        self._log("Endkontrolle bestanden - keine Zugangs- oder "
                  "Betriebsdaten im Paket.")

        pakete = []
        try:
            gefunden = _scan_requirements(_alle_py_files(src_dir))
            pakete = list(gefunden)
        except Exception as e:
            self._log("Paketsuche fehlgeschlagen: " + str(e))

        # Was erst zur Laufzeit geladen wird, findet keine Textsuche.
        # Diese Module stehen in paket.json unter importe.
        try:
            _mit, importe = _paket_zusatz(src_dir)
            for eintrag in importe:
                wurzel = str(eintrag).strip().split(".")[0]
                if wurzel and not wurzel.startswith("#"):
                    if wurzel not in pakete:
                        pakete.append(wurzel)
        except Exception as e:
            self._log("paket.json nicht auswertbar: " + str(e))

        # Was zum Projekt selbst gehoert, wird nicht als Fremdpaket
        # gesucht - es liegt ohnehin im Paket.
        eigen = set()
        for eintrag in os.listdir(src_dir):
            voll = os.path.join(src_dir, eintrag)
            if os.path.isdir(voll):
                eigen.add(eintrag.lower())
            elif eintrag.lower().endswith(".py"):
                eigen.add(os.path.splitext(eintrag)[0].lower())
        pakete = [p for p in pakete if p.lower() not in eigen]
        if pakete:
            self._log("Gebrauchte Fremdpakete: " + ", ".join(pakete))
        else:
            self._log("Keine Fremdpakete noetig.")

        angaben = pk.sammle(app_dir, pakete, log=self._log)
        if angaben.get("fehlend"):
            self._log("ACHTUNG: nicht gefunden - "
                      + ", ".join(angaben["fehlend"]))
        pk.schreibe_starter(app_dir, name, entry, log=self._log)

        # Die Einfuehrung fuer den ersten Start. Sie wird aus
        # dem Projekt zusammengetragen - eigener Text, paket.json,
        # LIESMICH.md oder Docstring, in dieser Reihenfolge.
        try:
            pe.schreibe(app_dir, name, src_dir, log=self._log)
            pe.schreibe_zeiger(app_dir, log=self._log)
        except Exception as fehler:
            self._log('Einfuehrung nicht erstellt: ' + str(fehler))

        if self.do_python.get():
            if not pyb.python_mitliefern(app_dir, (), log=self._log):
                return None, "Python konnte nicht mitgeliefert werden."
            gut, grund = pyb.probelauf(app_dir, log=self._log)
            if not gut:
                return None, "Probelauf gescheitert: " + grund
        else:
            self._log("Ohne eigenes Python - der Empfaenger braucht eines.")

        gut, grund = pk.probelauf(app_dir, log=self._log)
        if not gut:
            return None, "Paketpruefung gescheitert: " + grund
        return app_dir, ""



    def _write_installer(self, release, name, exe_path,
                         entry_pyc, has_ico, src_dir=None):
        """Erzeugt den Installer. Er zeigt ein Fenster mit beschrifteten
        Bedienelementen - fuer Screenreader deutlich besser als Abfragen
        in der Konsole. Ohne Windows-Formulare faellt er auf die
        Konsolenfassung zurueck."""
        pycname = os.path.basename(entry_pyc) if entry_pyc else name + ".pyc"

        if exe_path:
            suche = ""
            zielsetzen = ('    $s.TargetPath = "$Ziel\\' + name + '.exe"\r\n'
                          '    $s.Arguments = ""')
        else:
            suche = (
                'function Finde-Pythonw {\r\n'
                '  $c = @()\r\n'
                '  $g = Get-Command pythonw.exe -ErrorAction SilentlyContinue\r\n'
                '  if ($g) { $c += $g.Source }\r\n'
                '  foreach ($hive in @("HKCU:\\SOFTWARE\\Python\\PythonCore",'
                '"HKLM:\\SOFTWARE\\Python\\PythonCore")) {\r\n'
                '    Get-ChildItem $hive -ErrorAction SilentlyContinue | '
                'ForEach-Object {\r\n'
                '      $ip = (Get-ItemProperty "$($_.PSPath)\\InstallPath" '
                '-ErrorAction SilentlyContinue)."(default)"\r\n'
                '      if ($ip) { $c += (Join-Path $ip "pythonw.exe") }\r\n'
                '    }\r\n'
                '  }\r\n'
                '  foreach ($v in @("313","312","311","310")) {\r\n'
                '    $c += "$env:LOCALAPPDATA\\Programs\\Python\\Python$v\\pythonw.exe"\r\n'
                '    $c += "C:\\Program Files\\Python$v\\pythonw.exe"\r\n'
                '  }\r\n'
                '  foreach ($p in $c) { if ($p -and (Test-Path $p)) { return $p } }\r\n'
                '  return $null\r\n'
                '}\r\n'
                '$Pyw = Finde-Pythonw\r\n'
                'if (-not $Pyw) {\r\n'
                '  try {\r\n'
                '    Add-Type -AssemblyName System.Windows.Forms\r\n'
                '    [System.Windows.Forms.MessageBox]::Show('
                '"Python wurde auf diesem Rechner nicht gefunden. Bitte von '
                'python.org installieren und dabei die Einstellung '
                '\'Add Python to PATH\' ankreuzen. Danach diese Installation '
                'erneut starten.", "Python fehlt") | Out-Null\r\n'
                '  } catch {\r\n'
                '    Write-Host "Python wurde nicht gefunden. Bitte von '
                'python.org installieren."\r\n'
                '    Read-Host "Mit Eingabetaste beenden"\r\n'
                '  }\r\n'
                '  exit 1\r\n'
                '}\r\n')
            # Das mitgelieferte Python zuerst. Es hat garantiert alles,
            # weil der Packager es selbst bestueckt hat. Das Python des
            # Zielrechners bleibt unberuehrt - es wird nur nicht mehr
            # gebraucht.
            zielsetzen = ('    $Eig = Join-Path $Ziel "python\\pythonw.exe"\r\n'
                          '    if (Test-Path -LiteralPath $Eig) {\r\n'
                          '        $s.TargetPath = $Eig\r\n'
                          '    } else {\r\n'
                          '        $s.TargetPath = $Pyw\r\n'
                          '    }\r\n'
                          '    $s.Arguments = "`"$Ziel\\' + pycname + '`""')

        icon = ('    $s.IconLocation = "$Ziel\\app_icon.ico,0"'
                if has_ico else "")

        ps1 = (INSTALL_PS1
               .replace("~NAME~", name)
               .replace("~DATEN~",
                        _datenordner(src_dir) if src_dir else "")
               .replace("~PYTHONSUCHE~", suche)
               .replace("~ZIELSETZEN~", zielsetzen)
               .replace("~ICON~", icon))
        ps1 = gepraegt(ps1)
        with open(os.path.join(release, "_install.ps1"), "w",
                  encoding="utf-8-sig") as f:
            f.write(ps1)
        with open(os.path.join(release, "INSTALLIEREN.bat"), "w",
                  encoding="utf-8") as f:
            f.write("@echo off\r\nchcp 65001 >nul\r\n"
                    'powershell -NoProfile -ExecutionPolicy Bypass -File '
                    '"%~dp0_install.ps1"\r\n')

        # Deinstallation. Liegt spaeter IM Programmordner, daher ist
        # $PSScriptRoot genau der Ordner, der weg soll. Der Ordner
        # unter %APPDATA% kommt aus paket.json - ohne diese Angabe
        # bliebe er stehen, mitsamt allem, was darin liegt.
        datenordner = _datenordner(src_dir) if src_dir else ""
        if datenordner:
            self._log("Deinstallation raeumt zusaetzlich: "
                      "%APPDATA%\\" + datenordner)
        else:
            self._log("Kein datenordner in paket.json - beim "
                      "Deinstallieren bleiben Einstellungen stehen.")

        deinst = gepraegt(DEINSTALL_PS1
                          .replace("~NAME~", name)
                          .replace("~DATEN~", datenordner))
        with open(os.path.join(release, "_deinstall.ps1"), "w",
                  encoding="utf-8-sig") as f:
            f.write(deinst)
        with open(os.path.join(release, "DEINSTALLIEREN.bat"), "w",
                  encoding="utf-8") as f:
            f.write("@echo off\r\nchcp 65001 >nul\r\n"
                    'powershell -NoProfile -ExecutionPolicy Bypass -File '
                    '"%~dp0_deinstall.ps1"\r\n')
        self._log("INSTALLIEREN.bat erstellt - zeigt ein Fenster mit "
                  "beschrifteten Bedienelementen.")
        if not exe_path:
            self._log("Die Verknuepfung sucht pythonw.exe zur Laufzeit.")

    def _do_build(self, mode, folder, entry, name, logo):
        note = ""
        try:
            # In TEMP, nicht auf den Desktop. Dort raeumt Windows selbst
            # auf, und Robert sieht nicht, was sich sonst ansammelt.
            work_base = os.path.join(
                os.environ.get("TEMP") or os.environ["USERPROFILE"],
                name + "_build_tmp")
            shutil.rmtree(work_base, ignore_errors=True)
            release = os.path.join(work_base, "release")
            src_dir = os.path.join(work_base, "src")
            os.makedirs(release, exist_ok=True)
            ablage = getattr(self, "_ziel_basis", ZIEL_ORDNER)
            os.makedirs(ablage, exist_ok=True)
            self._log("Ablage: " + ablage)
            try:
                if os.path.exists(BUILD_LOG):
                    os.remove(BUILD_LOG)
            except Exception:
                pass
            self._log("=" * 52)
            self._log(f"Packer {PACKAGER_VERSION}")
            self._log(f"Packager-Ordner: {_base()}")
            self._log(f"Logo-Haken: {'an' if self.do_logo.get() else 'AUS'}")
            self._log(f"Gefundene Logos: {len(_find_logos())}")
            self._log(f"Gewaehltes Logo: {logo or '(keins)'}")
            try:
                import PIL
                self._log(f"Pillow: vorhanden ({PIL.__version__})")
            except Exception as e:
                self._log(f"Pillow FEHLT: {e}  ->  ohne Pillow kein Icon!")
            self._log("=" * 52)
            self._log(f"{name} - Modus: "
                      f"{'Projekt' if mode == 'projekt' else 'Einzeldatei'}")
            self._log(f"Arbeitsordner: {work_base}")

            self._copy_sources(mode, folder, entry, name, src_dir)
            ico = self._make_ico(logo, src_dir)

            exe_built = False
            entry_pyc = "starter.py"
            app_dir, err = self._build_quelle(src_dir, entry, name,
                                             release)
            if not app_dir:
                self._log("FEHLER: " + err)
                self.root.after(0, lambda: self._finish_build(False, err))
                return

            self._copy_logos(logo, app_dir)
            if ico:
                shutil.copy2(ico, os.path.join(app_dir, "app_icon.ico"))

            if self.do_bat.get():
                self._write_installer(release, name,
                                      os.path.join(app_dir, name + ".exe")
                                      if exe_built else None,
                                      entry_pyc, bool(ico), src_dir)
            marke = self._marke()
            schreibe_lizenz(app_dir, name, marke,
                            self.kurz_var.get().strip(), self._log)
            schreibe_lizenz(release, name, marke,
                            self.kurz_var.get().strip(), None)

            if self.do_doku.get():
                _build_doku(release, name, os.path.join(src_dir, entry))
                self._log("Dokumentation erstellt.")

            size = self._dir_size(app_dir)
            if self.do_zip.get():
                zip_path = os.path.join(ablage, ZIP_PREFIX + name + ".zip")
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                                     compresslevel=9) as zf:
                    for root_, _, files in os.walk(release):
                        for fn in files:
                            fp = os.path.join(root_, fn)
                            zf.write(fp, os.path.relpath(fp, release))
                # Zweite Endkontrolle, diesmal im fertigen Archiv.
                # Hinaus geht das ZIP, nicht der Programmordner.
                schlimm = []
                with zipfile.ZipFile(zip_path) as zf:
                    for eintrag in zf.namelist():
                        teile = eintrag.replace("\\", "/").split("/")
                        if any(t.lower() in BETRIEB_DIRS for t in teile[:-1]):
                            schlimm.append(eintrag + "  (Betriebsordner)")
                            continue
                        if any(t.lower() == "python" or t.lower() == "pakete"
                               for t in teile[:-1]):
                            continue
                        grund = (_geheim_grund(teile[-1])
                                 or _skip_grund(teile[-1]))
                        if grund in ("Zugangsdaten", "Sicherungsdatei",
                                     "entsteht im Betrieb"):
                            schlimm.append(eintrag + "  (" + grund + ")")
                if schlimm:
                    os.remove(zip_path)
                    self._log("ABBRUCH - im fertigen Archiv liegt, was "
                              "nicht hinausgehen darf:")
                    for s in schlimm[:20]:
                        self._log("   " + s)
                    self._log("Das Archiv wurde geloescht.")
                    shutil.rmtree(work_base, ignore_errors=True)
                    fehler = ("Endkontrolle im Archiv: "
                              + str(len(schlimm)) + " Datei(en). "
                              "Das ZIP wurde geloescht. Siehe Log.")
                    self.root.after(
                        0, lambda: self._finish_build(False, fehler))
                    return
                self._log("Endkontrolle im Archiv bestanden.")

                # Fuer die Webseite bereitstellen. Der Server liest den
                # Ablageordner selbst aus - was dort liegt, erscheint.
                if self.do_webseite.get():
                    try:
                        _o, bericht = ph.bereitstellen(
                            app_dir, name, zip_path, self._marke(),
                            log=self._log)
                        if _o:
                            _ton("erfolg")
                            ph.sag(bericht, log=self._log)
                        else:
                            _ton("fehler")
                            ph.sag("Bereitstellen gescheitert. "
                                   + bericht, log=self._log)
                    except Exception as fehler:
                        self._log("Bereitstellen gescheitert: "
                                  + str(fehler))
                        _ton("fehler")
                        ph.sag("Bereitstellen gescheitert.",
                               log=self._log)

                # Bei Robert selbst einrichten. Oertlich, ohne Netz -
                # das Paket liegt ja schon da.
                if self.do_selbst.get():
                    try:
                        gut, bericht = ph.oertlich_installieren(
                            zip_path, name, log=self._log)
                        ph.sag(bericht, log=self._log)
                        if not gut:
                            _ton("fehler")
                    except Exception as fehler:
                        self._log("Installation gescheitert: "
                                  + str(fehler))
                        _ton("fehler")

                zsize = os.path.getsize(zip_path)
                self._log(f"ZIP erstellt: {zip_path}")
                self._log(f"Groesse: {_human(size)} entpackt, "
                          f"{_human(zsize)} gezippt.")
                note = (f"{name}\n{_human(size)} entpackt, "
                        f"{_human(zsize)} gezippt.")
            else:
                note = f"{name}\n{_human(size)}"

            # Der ganze Arbeitsordner, nicht nur Teile davon. Darin liegt
            # eine Kopie des Projekts - sie hat nach dem Packen nichts
            # mehr zu suchen.
            shutil.rmtree(work_base, ignore_errors=True)
            if os.path.isdir(work_base):
                self._log("Hinweis: Arbeitsordner liess sich nicht "
                          "loeschen: " + work_base)
            self._log("FERTIG.")
            self.root.after(0, lambda: self._finish_build(True, note))
            try:
                subprocess.Popen(["explorer", getattr(self, "_ziel_basis", ZIEL_ORDNER)])
            except Exception:
                pass
        except Exception as e:
            import traceback
            try:
                shutil.rmtree(work_base, ignore_errors=True)
            except Exception:
                pass
            self._log("ABBRUCH: " + str(e))
            for l in traceback.format_exc().splitlines()[-6:]:
                self._log("   " + l)
            self.root.after(0, lambda: self._finish_build(False, str(e)))


if __name__ == "__main__":
    if not os.path.exists(FLAG):
        if not show_splash():
            sys.exit(0)
    KIPackager()
