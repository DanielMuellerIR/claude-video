# claude-video

## Typ & Zweck
- **Typ:** Skill/Plugin
- **Zweck:** Claude-Code-Skill /watch, das ein Video lädt (yt-dlp), Frames extrahiert (ffmpeg) und ein Transkript erzeugt, damit Claude Videos „sehen und hören" kann.
- **Plattform:** Claude-Code-Skill (CLI)

## Geplant / Nächste Schritte

### OCR-Textframe-Modus integrieren
Heute liefert `scripts/watch.py` **szenenbasierte** Frames + Transkript + VLM-Report
(„Zusammenfassung"-Weg). Ergänzt werden soll ein zweiter Modus, der aus einem Video
**jeden gezeigten Text** (Folien, Diagramme, Code, Screencasts) als **nach Text-Inhalt
deduplizierte** Frames per OCR erfasst — ein neuer Frame nur, wenn sich der erkannte Text
ändert; Frames ohne Text sowie Dauer-Wasserzeichen/Footer und Plattform-Eigenwerbung
werden herausgefiltert. Optional ein Transkript, in das die Text-Bilder an ihrem Zeitpunkt
eingebettet sind.

Eine funktionierende Engine dafür existiert bereits als eigenständiges Skript in einem
separaten, **nicht-öffentlichen** Tooling-Repo (Apple-Vision-OCR + Text-Dedup). Aufgabe ist
die **Portierung** hierher, sauber entkoppelt:

1. **OCR-Kern vorhanden:** `scripts/textframes.py` und `scripts/ocr.swift` liefern
   Textframes mit konservativer Text-Deduplizierung in einem exklusiven Ausgabeordner
   mit `frames/`, `texte.md` und `texte.json`. macOS, ffmpeg und Swift sind erforderlich.
   `--transcript` verbindet diese Bilder mit nativen Captions oder dem vorhandenen
   Whisper-Backend in `transkript.md`; `transkript.json` enthält die Sprachsegmente.
2. **Filter vorhanden:** Wiederkehrende kleine Randtexte und eindeutige Abo-Aufrufe
   werden heuristisch entfernt. Die optionale Klassifikation nutzt `LLM_RUN`, `LLM_HOST`
   und `LLM_MODEL`; ohne Konfiguration und bei Fehlern bleiben ungeprüfte Textframes
   erhalten. `--no-filter` und `--no-classify` erlauben den reinen OCR-Dedup-Lauf.
3. **Doku:** `README.md`/`README.de.md` um den Textframe-Modus ergänzen (zweisprachig,
   synchron), `SKILL.md`/`CHANGELOG.md` nachziehen.
4. **Tests:** Dedup-Logik (neuer Frame nur bei Textänderung) + Filter (Wasserzeichen/
   Eigenwerbung) headless absichern.

Ziel: Die Textframe-Funktion ist danach Teil dieses öffentlichen Repos statt nur intern.

## Verzeichnisstruktur

<!-- directory-structure: generated -->
- [AGENTS.md](AGENTS.md) — Projektprofil, Arbeitsregeln und dieses Datei-Verzeichnis.
- [CHANGELOG.md](CHANGELOG.md) — Projektdokumentation.
- [README.md](README.md) — Projekt-Einstieg und Nutzerdokumentation.
- [SKILL.md](SKILL.md) — Projektdokumentation.
- `commands/` — Projektbestandteil; Details stehen im Code bzw. in der verlinkten Dokumentation.
- `hooks/` — Projektbestandteil; Details stehen im Code bzw. in der verlinkten Dokumentation.
- `scripts/` — Projektbestandteil; Details stehen im Code bzw. in der verlinkten Dokumentation.
<!-- /directory-structure -->
