# Abschlussreview des Textframe-Workflows vom 2026-10-07

Ausgangsstand dieses Durchlaufs: `e07d981` (0.1.12).
Filter: `70bf86a` (0.1.13), Transkript: `d67da62` (0.1.14),
zweisprachige Dokumentation: `5eacda5`.
Abschließende Korrekturen: `b7168e3` und
`1a988a977f43b59e7861177c67db04dbfac1e5ff` (0.1.15).
Der [vorherige Review](2026-10-07.md) dokumentiert den OCR-Kern.

Der vollständige Abschlussreview wurde erneut in drei unabhängige lesende
Bereiche aufgeteilt: Video/Captions, Whisper/Setup und OCR/Paketierung.
Zusammengeführt und gegen Code sowie Reproduktionen geprüft sind neun
Fehlergruppen behoben. Kein bestätigter Review-Fund bleibt offen.

## Befunde und Korrekturen

Die Stellen beziehen sich auf den Korrekturstand `1a988a9`.

| ID | Schwere | Fehler und korrigierte Stelle | Nachweis |
| --- | --- | --- | --- |
| F1 | Hoch | `scripts/frames.py:149`, `scripts/frames.py:607`: Beide Extraktoren löschen vorhandene `frame_*.jpg` vor dem Lesen der Quelle. Dadurch können fremde Ergebnisse oder ein gleichnamiges Quellbild verloren gehen. Jetzt werden belegte Bildverzeichnisse vor jedem Werkzeugaufruf abgelehnt; die direkte CLI erzeugt ein exklusives markiertes Kindverzeichnis. | Beide Extraktoren erhalten Quellen und fremde Bilder bytegleich. Zwei native CLI-Läufe erzeugen verschiedene Ordner und erhalten die Eingaben. |
| F2 | Mittel | `scripts/frames.py:308`: Input-Seek auf einen gebrochenen Bereichsanfang überspringt bei niedriger Quellbildrate das dort noch angezeigte Bild. Jetzt wird für den Bereichsanfang das vorherige Quellbild durch Sampling erhalten. Szenen werden ohne verschobenen Input-Seek auf der Quellzeitachse erkannt. | Echtes 1-fps-Video: Bei Start 0,25 s entspricht die Bildhelligkeit Frame 0 statt dem späteren Frame 1. |
| F3 | Mittel | `scripts/frames.py:285`: Ein Szenenwechsel am exklusiven Bereichsende kann in die Auswahl geraten. Zeitstempel werden jetzt vor Budgetauswahl und Extraktion auf den halboffenen Bereich begrenzt. | Dasselbe native Fixture mit Ende 11 s liefert ausschließlich Bilder vor 11 s. |
| F4 | Mittel | `scripts/whisper.py:360`, `scripts/whisper.py:379`: Gültiges JSON mit falscher Struktur, unbrauchbare Segmenttexte oder Zeitwerte erzeugt ungefangene Python-Ausnahmen und verhindert den Bildbericht. Beide Antwortparser prüfen Struktur und endliche, nichtnegative Intervalle. Ungültiges UTF-8 aus lokalem Whisper wird ebenfalls kontrolliert gemeldet. | Cloud-/lokale Parser mit Listen, falschen Segmenttypen, negativen, umgekehrten, nichtendlichen und übergroßen Zeiten geprüft. Der übergeordnete Bericht bleibt erhalten; lokale Fehler räumen nur die eigene temporäre Ablage auf. |
| F5 | Niedrig | `scripts/whisper.py:354`: Direkte ffmpeg-, Whisper-, Modell- und HTTP-Fehler können mehrzeiligen Fremdtext ungekennzeichnet ausgeben. Diagnosen sind jetzt begrenzt, JSON-kodiert und ausdrücklich als untrusted markiert; HTTP-Antwortdiagnosen schwärzen einen enthaltenen API-Schlüssel. | Lange mehrzeilige Werkzeug-/HTTP-Diagnosen bleiben eine kodierte Zeile. Nicht-JSON-Antwort, lokaler CLI-Fehler und UTF-8-Fehler enden kontrolliert. |
| F6 | Mittel | `scripts/transcribe.py:48`: Sehr große Stundenwerte erzeugen einen `OverflowError`; rückwärts laufende Cue-Intervalle bleiben ungeprüft. Ungültige Intervalle werden jetzt übersprungen, gültige nachfolgende Cues bleiben erhalten. | VTT mit 400-stelliger Stundenangabe, umgekehrtem Cue und nachfolgendem gültigem Cue liefert genau den gültigen Cue. |
| F7 | Mittel | `scripts/whisper.py:602`: `Retry-After: NaN`, `inf` oder ein negativer Wert gelangt zu `sleep` und kann den kontrollierten Wiederholungsweg abbrechen. Nichtendliche und negative Werte verwenden jetzt den Standardzeitplan; gültige Werte sind auf 60 s begrenzt. | Ungültige und nichtnumerische Header werden abgelehnt, auch ein übergroßer endlicher Wert wird begrenzt; bestehende Transport-/Retry-Regressionen bestehen weiterhin. |
| F8 | Hoch | `scripts/whisper.py:277`: Die Audioextraktion überschreibt vorhandene Ausgabedateien. Die direkte CLI kann dadurch eine schon vorhandene `audio.mp3` ersetzen. Jetzt werden vorhandene Pfade vor der Extraktion abgelehnt, ffmpeg erhält zusätzlich `-n`. | Beide Extraktoren erhalten belegte Ziele und Quellen bytegleich. Native MP3-/WAV-Extraktion auf neue Ziele funktioniert; ein zweiter Aufruf erhält die Ausgabebytes. |
| F9 | Niedrig | `scripts/whisper.py:952`: Ein fehlender Backend-Wert erzeugt einen `IndexError`; unbekannte Optionen können ignoriert werden. Die CLI verwendet jetzt Argumentprüfung mit festen Backend-Werten. | Fehlender Wert, unbekanntes Backend und falsch geschriebene Option liefern Exit 2 mit Hilfe statt Traceback oder Verarbeitung. |

## Architektur und vollständige Abdeckung

| Bereich | Geprüfte Dateien und Verträge | Ergebnis |
| --- | --- | --- |
| Video und Captions | `scripts/watch.py`, `scripts/download.py`, `scripts/frames.py`, `scripts/transcribe.py`; Quellen, Auswahl, Zeitbezug, Bericht, Fehler | F1–F3, F6 |
| Sprache und Einrichtung | `scripts/whisper.py`, `scripts/setup.py`; Backend-Auswahl, Modellcache, Chunks, Transport, Konfiguration, lokale Ausgabe | F4, F5, F7–F9 |
| OCR und Dateiverwaltung | `scripts/textframes.py`, `scripts/ocr.swift`, `scripts/workdir.py`, `scripts/cleanup.py`; OCR, Filter, Dedup, Klassifikation, Zeitordnung, Eigentumsprüfung | Kein weiterer bestätigter Fund |
| Paket und Integration | `scripts/build-skill.sh`, beide Shell-Skripte, Hooks, Befehle, beide Plugin-Manifeste und Marketplace, Archivregeln, CI-/Release-Workflows | Paket und isolierte CLI-Läufe erfolgreich |
| Tests und Dokumentation | Alle elf Testmodule, beide README-Fassungen, `SKILL.md`, `CHANGELOG.md`, `AGENTS.md`, Lizenz | Verhalten, Optionen, Grenzen, Sprachfassungen und relative Links abgeglichen |

Die Erweiterung verwendet die vorhandene synchrone CLI-Architektur. Filter
arbeiten vor der Text-Deduplizierung. Wiederkehrender kleiner Randtext und
eindeutige Abo-/Gefällt-mir-Aufrufe werden vorsichtig entfernt; beide Filterwege
sind abschaltbar. LLM-Konfiguration bleibt optional. Fehler oder uneindeutige
Antworten erhalten ungeprüfte Bilder und melden den tatsächlichen Status.

Die Transkriptintegration nutzt native Captions zuerst und danach die
gemeinsamen Whisper-Backends. Bilder erscheinen genau einmal an ihrem
Sample-Zeitpunkt; Sprachsegmente behalten ihre vollständigen Cue-Intervalle.
Fehlende oder fehlerhafte Sprache entfernt keine OCR-Ergebnisse.

## Ausgeführte Prüfungen

- Nach Filterintegration 89, nach Transkriptintegration 97 und nach allen
  Review-Korrekturen **111 Tests erfolgreich, ohne übersprungene Tests**.
  Gegenüber dem Ausgangsstand kommen 16 Filter-/Transkripttests und
  14 abschließende Regressionstests hinzu.
- Python-Syntax aller 20 Quell-/Testmodule, Shell-Syntax beider Skripte,
  Manifest-JSON und `git diff --check` erfolgreich. EN/DE-Optionen,
  Abschnittsanzahl und lokale Dokumentlinks geprüft.
- Native Apple-Vision-Prüfung auf einem elfsekündigen Video mit vier
  wechselnden Folien, dauerhaftem Footer, Abo-Endkarte und Leerbild:
  vier Inhaltsbilder bei 0, 2, 4 und 6 s; neun Overlay-Zeilen entfernt.
  Der Kontrolllauf ohne Filter erhält Footer und Endkarte.
- Echter Download-/Captions-/OCR-Lauf über einen temporären lokalen
  HTML5-Video-Server: vier Inhaltsbilder und drei überlappende Sprachsegmente,
  chronologisch geordnet, jedes Bild genau einmal eingebettet.
- Native ffmpeg-Regressionen für niedrige und variable Bildrate,
  gebrochene Bereichsgrenzen, angezeigte Bildinhalte und wiederholte
  CLI-Ausführung erfolgreich.
- `bash scripts/build-skill.sh` aus sauberem Commit erfolgreich:
  18 Archiveinträge, alle neun Python-Laufzeitmodule und der Swift-Helfer
  bytegleich, deutsche README enthalten, Entwicklungsdateien ausgeschlossen.
- OCR-/Captions-Workflow, Szenenbericht und direkte Frame-CLI aus einem
  separat entpackten Paket mit Python `-P -S` erfolgreich. Der finale
  Paketlauf bestätigt erneut vier OCR-Bilder und drei Sprachsegmente.
- Native Audioextraktion aus dem Paket liefert für den Bereich 0,25–1,25 s
  jeweils 1,0 s MP3 und WAV. Quelle und bereits vorhandene Ergebnisse bleiben
  bytegleich; diese Prüfung benötigt keinen Whisper-Modelllauf.

## Prüfgrenzen

Die native Ausführung erfolgte unter macOS. Live-Plattformdownloads, echte
Groq-/OpenAI-Anfragen, ein realer Whisper-Modelllauf und echte LLM-Klassifikation
wurden nicht ausgeführt. Übergaben, Antwortprüfung und Fehlerpfade sind
isoliert geprüft; der lokale HTTP-Fixture-Lauf nutzt echtes yt-dlp.

Apple Vision benötigt macOS und Swift; ein OCR-Fallback für andere Systeme
ist nicht enthalten. Sampling kann kürzere Einblendungen verpassen. OCR kann
Zeichen und Layoutdetails falsch erkennen; konservative Textvergleiche können
ähnliche Bilder behalten. Filter können Inhalte falsch einordnen und
retuschieren keine Bildpixel. Vollständige Erfassung sollte bei Bedarf mit
höherer Samplingrate und deaktivierten Filtern kontrolliert werden.
