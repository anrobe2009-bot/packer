# paketbau

Python-Programme weitergeben, ohne dass Virenscanner Alarm schlagen und
ohne dass der Empfänger etwas installieren muss.

## Für wen das gedacht ist

Für alle, die ein Python-Programm geschrieben haben und es verschenken
wollen — an Leute, die kein Python haben, keine Kommandozeile öffnen und
keine Fehlermeldung deuten können.

Wer schon einmal mit PyInstaller eine EXE gebaut hat, kennt das Ergebnis:
Der Windows Defender meldet `Wacatac.B!ml`, der Beschenkte sieht eine rote
Warnung und löscht die Datei. Er hat recht damit. Eine unsignierte
Binärdatei ohne Ruf sieht für einen heuristischen Scanner aus wie
Schadsoftware — und ein Zertifikat kostet mehrere hundert Euro im Jahr.

paketbau geht deshalb einen anderen Weg.

## Wer das gebaut hat, und warum

Ich bin blind. "Entwickler" steht bei mir in Anführungszeichen, denn ich
sehe keine Zeile Code. Ich spreche. Die KI schreibt. Dazwischen fehlte
lange genau ein Stück — und dafür ist dieses Werkzeug gebaut.

Ich schreibe Programme für Menschen, die wenig oder gar nichts sehen. Sie
zu verschenken war der schwierigste Teil: Ein Programm, das beim ersten
Start eine Virenwarnung auslöst, kommt nie zum Einsatz. Und jemandem, der
selbst nichts sieht, kann man nicht erklären, wie er eine Warnung
wegklickt, die er nicht lesen kann. Also musste die Warnung verschwinden,
nicht der Beschenkte mutiger werden.

Was hier verschenkt wird, ist kein Musterstück für Fremde. Es ist genau
das Programm, das bei mir jeden Tag läuft: selbst gebaut, selbst
installiert, selbst in Benutzung.

Robert Elbel

## So funktioniert es

Es wird nichts kompiliert. Das Paket enthält den Quellcode im Klartext und
daneben ein eigenes Python — das offizielle *embeddable package* von
python.org, signiert von der Python Software Foundation. Es entsteht keine
unsignierte Binärdatei, also gibt es auch nichts zu melden.

Ein Paket sieht so aus:

```
NAME/
  ... der Quellcode ...
  starter.py          Einstieg, findet die mitgelieferten Pakete
  pakete/             die benötigten Fremdpakete
  python/             Python von python.org, signiert
  einfuehrung.txt     was das Programm tut
  einfuehrung.mp3     dasselbe, vorgelesen
INSTALLIEREN.bat
DEINSTALLIEREN.bat
```

**Python auf dem Zielrechner hat Vorrang.** Ist eines vorhanden, wird es
benutzt und nicht angerührt — nichts installiert, nichts aktualisiert,
nichts hineingeschrieben. Viele bleiben aus gutem Grund bei einer älteren
Fassung.

**Fremdpakete werden nie installiert.** Sie liegen im Paket, und der
Starter hängt ihren Ordner an den Suchpfad. Das vorhandene Python findet
sie, ohne sie zu besitzen. Beim Deinstallieren ist alles wieder weg.

**Der Installierer führt Buch.** Jeder angelegte Ordner, jede Verknüpfung,
jeder Registry-Eintrag wird sofort notiert. Der Deinstallierer arbeitet
diese Liste ab, prüft nach jedem Löschen nach und meldet jeden Rest
einzeln mit vollem Pfad. Er behauptet nie, fertig zu sein, wenn er es
nicht ist.

## Bedienung

Ein Fenster mit vier Angaben: Was soll verpackt werden — eine Datei oder
ein Ordner. Wo liegt es. Wie soll es heißen. Welches Logo.

Alles ist mit der Tastatur erreichbar: Tabulator wechselt das Feld,
Leertaste setzt einen Haken, Eingabetaste löst aus. Das Ergebnis kommt als
Text im Protokollfenster **und** als Ton — hoch bei Erfolg, tief bei einem
Fehler. Wer nichts sieht, hört, dass es fertig ist.

Auch die Installationsroutine, die dabei entsteht, ist vollständig ohne
Maus bedienbar und trägt beschriftete Bedienelemente für Screenreader.

## Was es niemals tut

**Es verschenkt keine Zugangsdaten.** Schlüsseldateien, Einstellungen,
Protokolle und alles, was ein Programm im Betrieb anlegt, bleiben draußen.
Vor dem Packen wird der Programmordner geprüft, danach noch einmal das
fertige Archiv. Fällt etwas auf, bricht der Bau ab und löscht das Archiv —
lieber kein Paket als eines mit fremden Daten darin.

**Es fragt dabei nicht nach.** Eine Rückfrage kann man versehentlich mit Ja
beantworten. Was einmal verschenkt ist, holt niemand zurück.

**Es lässt nichts zurück.** Der Deinstallierer entfernt Programmordner,
Verknüpfungen, Startmenü-Eintrag, Registry-Eintrag und den Datenordner.
Was er nicht entfernen konnte, sagt er.

**Es schreibt nichts ins System.** Keine Adminrechte, keine
Systemverzeichnisse, kein zweites Python in fremden Installationen.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `paketbau.py` | Oberfläche und Bauablauf |
| `paketbau_python.py` | holt Python von python.org, rüstet tkinter nach |
| `paketbau_pakete.py` | sammelt Fremdpakete samt Abhängigkeiten |
| `paketbau_einfuehrung.py` | erzeugt die gesprochene Einführung |
| `ki_packager_marke.json` | eigener Name, Kürzel, Urheber, Lizenz |

## Voraussetzungen

- Windows 10 oder 11
- Python 3.11 oder neuer auf dem Rechner, der die Pakete **baut**
- `pillow` für die Icons, `edge-tts` für die gesprochene Einführung
- Eine Internetverbindung beim Bauen — Python und Stimme werden geladen

Der Empfänger braucht nichts davon.

## Eigene Marke eintragen

`ki_packager_marke.json` anlegen:

```json
{
  "firma": "Mein Name",
  "kuerzel": "meinname",
  "autor": "Vorname Nachname",
  "web": "https://beispiel.de",
  "lizenz": "MIT"
}
```

Eigene Logos als `logo_64.png`, `logo_128.png` und `logo_512.png` daneben
legen. Fehlen sie, läuft alles ohne Icon weiter.

## Lizenz

MIT. Verwendung, Änderung und Weitergabe sind frei — der Urhebervermerk
muss erhalten bleiben. Einzelheiten in `LICENSE`.

Die Logos im Entwicklungsordner sind davon ausgenommen und nicht Teil
dieser Veröffentlichung.
