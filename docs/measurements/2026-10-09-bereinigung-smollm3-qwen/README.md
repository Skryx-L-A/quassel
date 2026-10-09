# Textbereinigung: SmolLM3-3B und qwen2.5-coder:3b, drei Läufe einer Nacht

Gemessen am 2026-10-09 mit `scripts/bench/cleanup_bench.py` gegen das synthetische Korpus
`tests/data/cleanup_cases.jsonl` (200 Fälle, je 100 Deutsch und Englisch, zehn Kategorien:
Füllwörter, Selbstkorrektur, Zahlen, Währung, URL, E-Mail, Fachbegriffe, Code, Fragen,
Injection). SmolLM3-3B Q4_K_M über einen eigenen `llama-server` (Port 8790, `-ngl 99 -c 4096`),
qwen2.5-coder:3b über das vorhandene Ollama, beide über `/v1/chat/completions` mit JSON-Schema
und Temperatur 0, so wie Quassel fragt. Jede Zeile gilt für die ganze Kette: Regeln, ITN, LLM,
Schutzprüfung, sonst Rohtext.

- **Rückfall:** Der Schutz verwirft die LLM-Antwort, Quassel fügt dann den Rohtext ein. Das ist
  sicher, aber es wurde nichts bereinigt.
- **Unsicher:** Der Schutz hat eine Antwort durchgelassen, obwohl der Text unverändert bleiben
  sollte. Das ist die gefährliche Zahl.

## Ergebnis

| Lauf (Stand des Codes) | Modell, Stufe | Rückfall | davon Deutsch / Englisch | Unsicher |
|---|---|---|---|---|
| 01:43 (vor den Schutz-Fixes, Stufe noch nicht im Prompt) | SmolLM3, mittel = stark | 28,5 % | nicht erhoben | 11 |
| 01:43 | qwen2.5-coder:3b, mittel | 26,5 % | nicht erhoben | 3 |
| 03:36 (Schutz für Fragen und Injection, Stufe im Prompt) | SmolLM3, mittel | 53,5 % | 90 / 17 von je 100 | 10 |
| 03:36 | SmolLM3, stark | 56,5 % | 90 / 23 | 10 |
| 03:36 | qwen2.5-coder:3b, mittel | 34,0 % | 28 / 40 | 6 |
| 04:04 (plus „Sprache behalten, nie übersetzen", `de0ceff`) | SmolLM3, mittel | 40,0 % | 63 / 17 | 10 |
| 04:04 | SmolLM3, stark | 45,5 % | 75 / 16 | 12 |
| 04:04 | qwen2.5-coder:3b, mittel | 28,0 % | 26 / 30 | 3 |

Gründe für den Rückfall im Lauf 04:04 (SmolLM3 mittel / qwen mittel): Wortabweichung 48 / 23,
geschützte Eingabe verändert (Frage oder Injection umgeschrieben) 18 / 24, Entität verändert
14 / 8. Die Zeile „geschützte Eingabe" gibt es erst seit dem Schutz-Fix um 01:51; sie fängt
genau die Fälle, die vorher als sauber durchgingen.

Latenz je Fall (Median, Lauf 04:04, Maschine ohne fremde Last): SmolLM3 0,20 s,
qwen2.5-coder:3b 0,54 s.

## Einordnung

- Die englische Stufen-Anweisung vor dem deutschen Diktat ließ SmolLM3 auf Englisch antworten.
  Der Schutz verwarf das jedes Mal, es kam also kein falscher Text heraus, aber auch keine
  Bereinigung. Die Sprachregel holt einen Teil zurück; auf Deutsch bleibt SmolLM3 schwach.
- Alle unsicheren SmolLM3-Fälle sind deutsche URL-Sätze („Öffne https://example.com/…").
  Was es dort ändert, zeigt der nächste Lauf mit `--show-unsafe`.
- qwen2.5-coder:3b ist auf Deutsch besser und sicherer (26 % Rückfall, 3 unsicher), steht aber
  unter der Qwen Research License (Hugging Face `license_name: qwen-research`, am 2026-10-09
  abgefragt), also nicht für kommerzielle Nutzung. Es bleibt Vergleichswert, keine Empfehlung.
  SmolLM3 ist 2,7-mal schneller, auf Deutsch aber nicht brauchbar. Als Startkandidat fällt
  SmolLM3 damit weg; als Nächstes messen: Qwen3-4B ohne Denkmodus und Granite (beide Apache-2.0).
- `mittel` und `stark` unterscheiden sich seit dem Stufen-Prompt tatsächlich (vorher identisch).

Dateien: je Lauf die Summen-JSONs, ab 03:36 auch die Falldateien (`*-cases.jsonl`, nur
Metadaten: Fall-ID, Stufe, Rückfallgrund, Status).
