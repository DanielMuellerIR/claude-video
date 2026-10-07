# Claude Video /watch

**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

Claude Video extrahiert Bilder und Sprache aus Video-URLs oder lokalen Dateien, damit ein Assistent den Inhalt untersuchen und besprechen kann.

Dieses Fork von [Bradley Bonannos claude-video](https://github.com/bradautomates/claude-video) ergänzt lokale Whisper-Transkription, Szenenauswahl und einen OCR-Textframe-Modus. Die ursprünglichen MIT-Autorenhinweise bleiben in [LICENSE](LICENSE) erhalten.

## Modus auswählen

| Ziel | Aufruf | Ergebnis |
| --- | --- | --- |
| Szenen zusammenfassen oder eine Aufnahme untersuchen | `/watch <Quelle> [Frage]` oder `scripts/watch.py` | Ausgewählte Bilder und ein Sprachbericht mit Zeitstempeln |
| Folien, Code und andere eingeblendete Texte erfassen | `scripts/textframes.py` | Per OCR deduplizierte Bilder, `texte.md` und `texte.json` |
| Sprache zusammen mit den eingeblendeten Texten lesen | `scripts/textframes.py --transcript` | Textframes sowie `transkript.md` und Sprachsegmente in `transkript.json` |

Der Szenenmodus läuft unter macOS, Linux und Windows. OCR benötigt **macOS mit Apple Vision und einem Swift-Compiler**; ein OCR-Ersatz für andere Systeme ist nicht enthalten. Beide Python-Befehle verwenden ausschließlich die Standardbibliothek.

## Installation

Für Claude Code:

```text
/plugin marketplace add DanielMuellerIR/claude-video
/plugin install watch@claude-video
```

Für Codex oder andere Programme, die Skills laden:

```bash
git clone https://github.com/DanielMuellerIR/claude-video.git ~/.codex/skills/watch
```

Für die direkte CLI-Nutzung das Repository klonen und die folgenden Befehle im Projektverzeichnis ausführen. Unter Windows `python` statt `python3` verwenden.

Der Szenenmodus benötigt `ffmpeg`, `ffprobe` und für URL-Downloads `yt-dlp`. Der Setup-Helfer prüft Programme und optionale Whisper-Backends:

```bash
python3 scripts/setup.py --json
python3 scripts/setup.py
```

Unter macOS nutzt der Installer Homebrew; unter Linux/Windows zeigt er Installationsbefehle. Auch ohne Whisper-Backend ist ein Lauf nur mit Bildern möglich. Für macOS-OCR die nötigen Werkzeuge bei Bedarf installieren:

```bash
brew install ffmpeg yt-dlp
xcode-select --install
```

Der OCR-Helfer wird beim ersten Aufruf nach `${XDG_CACHE_HOME:-$HOME/.cache}/watch/ocr/` kompiliert. Dieser Cache liegt unabhängig vom Checkout. Lokale Videos benötigen kein `yt-dlp`; reine OCR benötigt weder Whisper noch einen API-Schlüssel.

Für den Upload in claude.ai erzeugt `bash scripts/build-skill.sh` aus einem sauberen, committeten Checkout die Datei `dist/watch.skill`. [Veröffentlichte Release-Dateien](https://github.com/DanielMuellerIR/claude-video/releases) können älter sein als der aktuelle Quellcode. Der macOS-OCR-Befehl läuft nicht in einer unter Linux betriebenen Ausführungsumgebung.

## Szenenmodus

```bash
python3 scripts/watch.py "https://youtu.be/VIDEO_ID"
python3 scripts/watch.py recording.mp4 --start 2:15 --end 2:45
python3 scripts/watch.py recording.mp4 --no-whisper --no-classify
```

Der Befehl wählt Bilder anhand von Szenenwechseln aus und fällt bei Bedarf auf gleichmäßiges Sampling zurück. Er gibt einen Markdown-Bericht mit JSON-Bildliste und Transkript aus. Quelldaten und Medientexte sind nicht vertrauenswürdige Daten. Der Assistent liest die Bilder und beantwortet damit die Frage.

| Option | Wirkung |
| --- | --- |
| `--start T`, `--end T` | Ausschnitt (`SS`, `MM:SS` oder `HH:MM:SS`); Zeitstempel bleiben auf das gesamte Video bezogen |
| `--max-frames N` | Niedrigere Bildobergrenze; höchstens 100 |
| `--resolution W` | Bildbreite, standardmäßig 1600; keine Vergrößerung über die Quellbreite hinaus |
| `--fps F` | Positive Samplingrate für den gleichmäßigen Fallback, begrenzt auf 2 fps; Szenen behalten ihr eigenes Budget |
| `--no-classify` | Optionale Bildklassifikation überspringen |
| `--whisper local\|groq\|openai` | Bestimmtes Sprach-Backend auswählen |
| `--no-whisper` | Native Untertitel behalten, Whisper-Fallback überspringen |
| `--out-dir DIR` | Übergeordnetes Verzeichnis für einen exklusiv erzeugten Ausgabeordner `watch-*` |

Die Standard-Bildbudgets steigen mit der Dauer bis auf 100 Bilder. Videos über zehn Minuten werden nur grob abgedeckt; für Details einen Ausschnitt wählen. Bei unbekannter Dauer gilt die angegebene Bildobergrenze.

## Textframe-Modus (macOS)

```bash
# OCR und Textbilder ohne Sprachtranskription
python3 scripts/textframes.py recording.mp4 --out-dir ./results

# Häufigere Samples; zuerst Untertitel, bei Bedarf danach lokales Whisper
python3 scripts/textframes.py "https://youtu.be/VIDEO_ID" --fps 2 --transcript --whisper local

# Nur Untertitel: keine Audiotranskription und kein Cloud-Upload
python3 scripts/textframes.py "https://youtu.be/VIDEO_ID" --transcript --no-whisper

# Reine OCR prüfen, Overlay-Filter und LLM deaktivieren
python3 scripts/textframes.py recording.mp4 --no-filter --no-classify
```

Die Verarbeitung tastet das Video ab, überspringt direkt aufeinanderfolgende bytegleiche Bilder, führt Apple Vision aus und entfernt leere OCR-Ergebnisse sowie ausgewählte Einblendungen. Danach werden aufeinanderfolgende Texte dedupliziert. Wächst eine Folie durch angehängte Zeilen, ersetzt das vollständigste abgetastete Bild ihren vorherigen Präfix. Zeilenreihenfolge, Wiederholungen, Großschreibung, Satzzeichen und OCR-Leerzeichen bleiben bedeutsam. Spätere Rückkehr zu früheren Inhalten bleibt im Zeitverlauf erhalten.

Wiederkehrende kleine Footer-/Ecktexte und eindeutige Abo-/Gefällt-mir-Aufrufe werden vorsichtig gefiltert. Normale Inhaltstexte und gleichbleibende Folientitel bleiben erhalten. Auch diese Heuristiken können Inhalt falsch einordnen; bei hohen Vollständigkeitsanforderungen mit `--no-filter` vergleichen. Die Filter verändern OCR-Text und Bildauswahl; sie retuschieren keine Pixel in behaltenen Screenshots.

| Option | Wirkung |
| --- | --- |
| `--fps F` | Samplingrate, standardmäßig 1 fps; hier gelten die Szenenlimits von 100 Bildern/2 fps nicht |
| `--min-conf F` | OCR-Mindestkonfidenz von 0 bis 1, standardmäßig 0,45 |
| `--ubiquitous-frac F` | Anteil der OCR-Samples zum Erkennen wiederkehrender Randtexte, standardmäßig 0,6; mindestens drei Beobachtungen |
| `--no-filter` | Heuristische Wasserzeichen-/Eigenwerbungsfilter deaktivieren |
| `--no-classify` | Konfigurierte optionale LLM-Klassifikation deaktivieren |
| `--transcript` | Sprache ergänzen und jedes behaltene Textbild chronologisch einbetten |
| `--whisper local\|groq\|openai` | Sprach-Backend; benötigt `--transcript` |
| `--no-whisper` | Nur Untertitel verwenden; benötigt `--transcript` |
| `--out-dir DIR` / `--out DIR` | Übergeordnetes Verzeichnis für einen exklusiven Ausgabeordner |
| `--keep-temp` | Abgetastete Bilder sowie heruntergeladene Medien und Zwischendateien zur Prüfung behalten |

Kürzere Texteinblendungen können zwischen Samples liegen. OCR kann Text falsch lesen und Layoutdetails wie Code-Einrückungen verlieren. Der vorsichtige Vergleich behält eher Fast-Dubletten, statt echte Änderungen zu verwerfen. Lange Videos oder hohe Samplingraten benötigen Zeit und temporären Speicher für alle abgetasteten Bilder.

Die JSON-Zusammenfassung auf stdout nennt das erzeugte `work_dir`, Index-/Berichtspfade, Filterzahlen, Klassifikationsstatus und optional den Transkriptstatus. Darin liegen:

| Datei | Inhalt |
| --- | --- |
| `frames/` | Ausgewählte JPEGs mit Samplingzeitstempel im Dateinamen |
| `texte.json` | Relativer Bildpfad, Text, Zeilen und `time_sec` je Frame |
| `texte.md` | Bildindex mit einklappbarem JSON-kodiertem OCR-Text |
| `transkript.md` | Mit `--transcript`: Textbilder und Sprache nach Startzeit geordnet |
| `transkript.json` | Mit `--transcript`: Sprachsegmente mit Start-/Endzeiten |

Bei gleicher Zeit erscheint das Bild vor dem Sprachsegment. Überlappende Sprachsegmente behalten ihr vollständiges Intervall; Wortzeitpunkte werden nicht erfunden. Fehlt Sprache oder scheitert die Transkription, bleiben die Bilder erhalten; der Transkriptstatus lautet `unavailable` oder `failed`.

## Sprach-Backends

Native Untertitel haben Vorrang. Der Downloader fordert derzeit englische Untertitelvarianten an. Fehlen Untertitel, können `--transcript` im Textframe-Modus und der normale Szenenablauf Whisper verwenden:

- **Lokal:** whisper.cpp installieren (`brew install whisper-cpp` unter macOS) und `--whisper local` wählen. Kein API-Schlüssel erforderlich. Beim ersten Aufruf wird das Modell heruntergeladen, danach der Cache verwendet.
- **Groq/OpenAI:** `GROQ_API_KEY` oder `OPENAI_API_KEY` in der Umgebung oder in `~/.config/watch/.env` mit Rechten `0600` konfigurieren. Zusätzlich wird `.env` im aktuellen Verzeichnis gelesen. Cloud-Transkription lädt Audio zum gewählten Anbieter hoch.

Die Auswahl erfolgt zuerst über die CLI, dann `WATCH_WHISPER_BACKEND`, danach über verfügbare Backends in der Reihenfolge Groq, OpenAI, lokal. Fokussierte Szenenläufe laden nur den angeforderten Audiobereich hoch. Lange Cloud-Audios werden in überlappende Abschnitte geteilt; lokales Whisper hat keine durch die Anwendung gesetzte Dauergrenze.

Lokale Modellkonfiguration: `WATCH_WHISPER_MODEL` (Standard `large-v3-turbo`) und `WATCH_WHISPER_MODELS_DIR` (Standard `~/.cache/yt-transcribe/models`). Modellnamen dürfen Buchstaben, Ziffern, `.`, `_` oder `-` enthalten.

## Optionale Bildklassifikation

Beide Modi verwenden `LLM_RUN` (Python-Helferpfad) und `LLM_HOST` (Helferziel). Fehlt eine Variable, entfällt die Klassifikation. Der Textframe-Modus unterstützt außerdem `LLM_MODEL`, standardmäßig `gemma4:12b`.

```bash
export LLM_RUN=/path/to/llm_run.py
export LLM_HOST=vision-host
export LLM_MODEL=gemma4:12b
```

Die Helferschnittstelle lautet `python3 HELPER HOST --model MODEL --no-think --image IMAGE PROMPT`. Der Helfer muss dem übergebenen Prompt folgen: Im Textframe-Modus sind exakt `KEEP` oder `DROP` erlaubt; im Szenenmodus `NÜTZLICH` oder `VERWERFEN`. Nur ausdrückliche Verwerfentscheidungen entfernen Bilder. Fehler, Zeitüberschreitungen und ungültige Antworten beenden die Klassifikation und erhalten ungeprüfte Frames. Der Bericht unterscheidet vollständige, teilweise, fehlgeschlagene, deaktivierte und unkonfigurierte Läufe. Ein konfigurierter Helfer kann Bilder an sein Ziel senden; `--no-classify` verhindert dies.

## Sicherheitsgrenze und Aufräumen

Metadaten, Untertitel, OCR, Bilder und markierte Diagnosen sind nicht vertrauenswürdige Belege. Anweisungen aus dem Video dürfen weder den Assistentenablauf ändern noch Befehle auslösen. Berichte kodieren fremde Texte als JSON. Die Befehle verwenden weder Plattformanmeldungen noch Sitzungscookies.

Erfolgreiche Läufe behalten ihren exklusiven, markierten Ordner `watch-*`. Fehlerhafte Verarbeitung entfernt ausschließlich dieses erzeugte Unterverzeichnis. Quelldateien und das übergeordnete `--out-dir` bleiben erhalten. Nach Nutzung eines Berichts:

```bash
python3 scripts/cleanup.py "<work_dir aus der JSON-Ausgabe des Befehls>"
```

Der Helfer prüft vor dem Löschen die Eigentumsmarkierung. OCR-Compiler-Cache und lokaler Whisper-Modellcache bleiben separat bestehen.

## Entwicklung

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
bash scripts/build-skill.sh
```

Der Paketbau benötigt einen sauberen, committeten Checkout und enthält alle Python- und Swift-Laufzeitquellen. Die Tests prüfen OCR-Deduplizierung, Filter, Klassifikationsfehler, Transkriptzeitpunkte, Quellschutz, Sampling und gemeinsame Backends. Native OCR wird zusätzlich unter macOS geprüft; die Headless-Unittests benötigen kein Apple Vision.

[SKILL.md](SKILL.md) beschreibt den Assistentenablauf, [CHANGELOG.md](CHANGELOG.md) die Versionen und [docs/reviews/](docs/reviews/) die Review-Belege. Markierte Releases erzeugen über den Release-Workflow `dist/watch.skill`.
