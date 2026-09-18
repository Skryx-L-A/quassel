"""Offline-Tests für das konservative Wörterbuch-Prompt-Budget."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import dictprompt


def test_estimate_is_conservative_for_a_german_compound():
    word = "Donaudampfschifffahrtsgesellschaftskapitän"
    assert dictprompt.estimated_tokens(word) > len(word.encode("utf-8")) / 4


def test_effective_words_prefers_recent_entries_and_reports_them():
    words = ["old", "newest"]
    budget = dictprompt.estimated_tokens("newest") + 1
    assert dictprompt.effective_words(words, budget) == ["newest"]


def test_prompt_places_wake_phrase_and_mixed_primer_last():
    prompt, active = dictprompt.build_prompt(
        ["Kubernetes"], prompt_extra="Hey Quassel", mixed_primer="Let's go")
    assert active == ["Kubernetes"]
    assert prompt.endswith("Hey Quassel Let's go")


def test_partial_prompt_has_a_smaller_dictionary_budget():
    words = ["x" * 20 for _ in range(30)]
    _, full = dictprompt.build_prompt(words)
    _, partial = dictprompt.build_prompt(words, partial=True)
    assert len(partial) < len(full)


def test_suffix_reservation_keeps_total_within_whisper_limit():
    prompt, _ = dictprompt.build_prompt(["Wort" * 20 for _ in range(30)],
                                        prompt_extra="Wake " * 20,
                                        mixed_primer="Mixed " * 20)
    assert dictprompt.estimated_tokens(prompt) <= dictprompt.PROMPT_TOKEN_LIMIT
