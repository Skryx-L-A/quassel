# Windows verify — AI tier (v2.4.0)

This branch (`worktree-voxtype-ai-tier`) is **v2.3.0 + the local-AI tier (Phase 3)**.
It was built and verified on **Linux against a real local Ollama** (`llama3.2:1b`):
the client, the modes, voice triggers, and the daemon `_ai_refine` path all produced
correct output end-to-end. Windows-specific code is the same shared modules plus the
`win/app.py` wiring — **not run on Windows here**.

First do everything in `WINDOWS_VERIFY.md` (the v2.3 checklist). Then this delta.

## What's new vs the v2.3 branch
- `quassel/ai.py` — Ollama HTTP client (stdlib urllib; fails soft, never blocks dictation).
- `quassel/aimodes.py` — built-in modes (cleanup/email/bullets/paragraphs/formal/concise),
  custom modes (`ai_modes.txt`, name=instruction), voice-trigger detection.
- Wired into the finish pipeline of BOTH daemons: `daemon._ai_refine` (Linux) and
  `win/app.py._ai_refine` (Windows) — **identical logic**; the Windows copy runs in the
  existing transcribe worker thread.
- New AI settings page in the shared `center.py`.
- All opt-in, OFF by default. Config section `[ai]`.

## Windows prerequisite
- Install **Ollama for Windows** (ollama.com) and pull a model, e.g.
  `ollama pull llama3.2:3b`. Ollama serves on `http://127.0.0.1:11434` by default.
- Without Ollama/model, the AI features simply no-op (dictation inserts raw text) — verify
  that fallback too.

## Verify checklist (Windows, hands-on)
- [ ] Settings has an **AI** page. With Ollama running, click **Refresh** → the model
      dropdown fills with your installed models. Pick one.
- [ ] Click **Test** → shows a cleaned sample sentence (proves the round-trip works).
- [ ] Enable AI + "Auto-clean every dictation", mode = Clean up. Dictate a rambly sentence
      with filler ("um, like, you know") → inserted text is cleaned up.
- [ ] **Voice mode**: enable "Voice modes". Dictate: "as an email, team we ship friday" →
      inserts an email draft. Try "make it concise ..." and the German "als Liste ...".
- [ ] **Custom mode**: on the AI page add `tweet=Rewrite as a punchy tweet under 280 chars`,
      then dictate "tweet we just shipped a big update" → tweet-style text.
- [ ] **Fallback**: stop Ollama (or set a wrong address) with AI still enabled → dictation
      still works and inserts the raw recognized text (no hang, no lost words). The Test
      button reports it couldn't reach the model.
- [ ] **Off by default**: a fresh config (`[ai]` absent) → AI does nothing; normal dictation.

Notes:
- AI post-processing adds latency (the LLM call) before text appears — expected; it runs in
  the transcribe worker thread on Windows so the UI/hook stay responsive.
- If using **streaming + AI** together, streamed words get rewritten to the AI version at the
  end (one larger correction). Fine, but a fast model is recommended.

## Windows-specific suspects (Linux-verified logic, Windows-unverified)
- `win/app.py`: `_ai_refine`, `_detect_custom_voice` (mirror of `daemon.py`), and that they
  run inside `_transcribe_inner`'s worker thread.
- Everything else (`ai.py`, `aimodes.py`, `center.py` AI page, config `[ai]`) is shared and
  Linux-verified.

## Handback
Report pass/fail per box. Two independent builds exist for you to compare:
`worktree-voxtype-ui-polish` (v2.3, no AI) and `worktree-voxtype-ai-tier` (v2.4, +AI).
