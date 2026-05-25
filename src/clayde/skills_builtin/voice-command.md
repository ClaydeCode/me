---
name: voice-command
description: Primary instructions for handling Pebble watch voice commands (speech-to-text input).
---

The input you receive is speech-to-text output from a Pebble watch. It MAY contain
transcription errors. Consider phonetically similar words and the most likely intent —
e.g. "calendar" might arrive as "colander". Use judgement.

Default working target: /home/clayde/knowledge_base (mounted RW, synced via Syncthing).
If the command implies "remember this", "note", "save", "log", or "capture", write a
file there. No git operations — Syncthing handles sync.

Disambiguate against the KB structure. Before acting on a phrase that seems nonsensical
or oddly worded, list the top level of the knowledge base
(e.g. `ls /home/clayde/knowledge_base`). Its top-level directories are stable nouns the
user actually uses ("people", "specs", "inbox", "freeshard", ...). If a confusing token
has a phonetic neighbour that matches one of those folders or a common verb pair ("add
a", "note that", "capture"), prefer that reading. Worked example: "after people and tree
for my brother-in-law" → "add a people entry for my brother-in-law", because
"after" ≈ "add a" and "tree" ≈ "entry", and `people/` is a real folder. State the
interpretation you picked in your narrative so the user can spot a wrong guess in the
ntfy summary.
