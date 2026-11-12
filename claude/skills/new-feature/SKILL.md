---
name: new-feature
description: Scaffold a new feature directory <features_dir>/NNN-slug/ from the templates (sdd new-feature) after checking the roadmap entry. Use when starting a feature without running the full pipeline.
argument-hint: "<NNN e.g. 011> <slug e.g. user-login>"
disable-model-invocation: true
allowed-tools: Bash(python3 -I tools/sdd/sdd.py new-feature *) Bash(sdd new-feature *) Bash(git *) Read Grep
---
# New feature $0-$1

1. Confirm that the roadmap has feature F$0 with slug `$1` (`sdd new-feature` warns if not).
   If it does not, stop: the roadmap owns numbering.
2. Create or switch to the branch `<branch_prefix>F$0-$1`, preferably as a worktree
   (`git worktree add -b <branch_prefix>F$0-$1 <worktree_root>/F$0 main`).
3. Scaffold: `python3 -I tools/sdd/sdd.py new-feature $0 $1`. It copies the templates into the
   feature directory, substitutes the placeholders and prints the requirement id block this
   feature owns.
4. Check that `spec.md` has `Status: Draft`.
5. Next, either hand the spec to the `spec-author` agent, or run `/feature F$0 $1` for the
   whole lifecycle.

Numbering and lifecycle rules are in `<features_dir>/README.md`.
