---
name: source
description: Look up the exact text of the normative source (a standard clause, an API contract section, a product requirement) from the project's configured corpus. Use whenever you need to cite, check, or implement specified behaviour - never quote the source from memory.
argument-hint: "<doc e.g. 24.501 or openapi> <clause e.g. 5.5.1.2.4 or /paths/~1users>"
allowed-tools: Bash(python3 -I tools/sdd/sdd.py source *) Bash(sdd source *) Read Grep
---
# Normative source lookup

## $0 §$1
!`python3 -I tools/sdd/sdd.py source clause $0 $1 2>&1 || true`

## How to use the corpus
- One section: `sdd source clause <doc> <clause>`. It prints the section up to the next
  heading of the same level (or runs the project's `sources.lookup_command`).
- Search: `sdd source find <doc> <regex>`.
- Where things are and how to cite: `sources` in `sdd.config.json` (`kind`, `text_dir`,
  `citation_format`) and the "Reading the source" section of CLAUDE.md.
- Always read the parent section too, for definitions and preconditions, and the error /
  abnormal-case sections for the behaviour you implement.

## Citation rules
- Cite with the project's `citation_format` in requirements and as a short reference in
  code comments. Requirement ids follow `requirements.id_regex`.
- If the section is missing from the corpus, do not guess. Write a clarification marker
  (`requirements.clarification_marker`) and tell the orchestrator.
