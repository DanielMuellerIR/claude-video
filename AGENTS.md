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

1. **Engine portieren** → neues `scripts/textframes.py`, Geschwister-Skript zu `watch.py`
   (gleicher Aufruf-/Ausgabestil: Ausgabeordner mit `frames/` + optional `transkript.md`).
2. **Abhängigkeiten entkoppeln / self-contained machen:** keine absoluten Pfade, keine
   internen Hostnamen. Die LLM-gestützte Klassifikation/Filterung env-getrieben machen
   (Muster `LLM_RUN`/`LLM_HOST` wie bereits in `watch.py`, Commit `75001af`) und optional —
   ohne LLM muss ein reiner OCR-Dedup-Lauf funktionieren. macOS-spezifisch (Apple Vision):
   klar dokumentieren bzw. Fallback prüfen.
3. **Doku:** `README.md`/`README.de.md` um den Textframe-Modus ergänzen (zweisprachig,
   synchron), `SKILL.md`/`CHANGELOG.md` nachziehen.
4. **Tests:** Dedup-Logik (neuer Frame nur bei Textänderung) + Filter (Wasserzeichen/
   Eigenwerbung) headless absichern.

Ziel: Die Textframe-Funktion ist danach Teil dieses öffentlichen Repos statt nur intern.
