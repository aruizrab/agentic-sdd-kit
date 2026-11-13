#!/usr/bin/env bash
# install.sh: install the agentic-sdd-kit into a target repository, non-destructively.
#
#   ./install.sh <target-repo> [--name <project>] [--config rust|typescript|python|go|<file>]
#                [--force] [--dry-run] [--no-claude] [--no-docs]
#
# Copies:
#   tools/sdd/                 -> <target>/tools/sdd/            (CLI + pipeline script; always refreshed with --force)
#   claude/                    -> <target>/.claude/               (agents, skills, hooks, settings.json)
#   templates/CLAUDE.md        -> <target>/CLAUDE.md
#   templates/docs/*           -> <target>/docs/
#   templates/specs/*          -> <target>/specs/
#   templates/sdd.config.json  -> <target>/sdd.config.json       (or the chosen example config)
# Existing files are never overwritten unless --force is given. {{PROJECT}} is replaced by
# --name (default: the target directory name). Appends .wt/ and .sdd/ to .gitignore.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=""
NAME=""
CONFIG=""
FORCE=0
DRY=0
CLAUDE=1
DOCS=1

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --no-claude) CLAUDE=0; shift ;;
    --no-docs) DOCS=0; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "unknown option $1" >&2; usage 2 ;;
    *) if [ -z "$TARGET" ]; then TARGET="$1"; else echo "unexpected argument $1" >&2; usage 2; fi; shift ;;
  esac
done
[ -n "$TARGET" ] || usage 2
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
[ -n "$NAME" ] || NAME="$(basename "$TARGET")"
case "$TARGET" in /tmp/*) echo "warning: $TARGET is under /tmp (RAM tmpfs on many hosts); a crash wipes it" >&2 ;; esac

copied=0; skipped=0; overwritten=0
SKIPPED_LIST=()

say() { printf '%s\n' "$*"; }

# put <src> <dst> [subst]: copy one file, respecting --force / --dry-run.
put() {
  local src="$1" dst="$2" subst="${3:-0}"
  if [ -e "$dst" ] && [ "$FORCE" -ne 1 ]; then
    skipped=$((skipped+1)); SKIPPED_LIST+=("${dst#"$TARGET"/}"); return 0
  fi
  if [ -e "$dst" ]; then overwritten=$((overwritten+1)); else copied=$((copied+1)); fi
  [ "$DRY" -eq 1 ] && { say "  + ${dst#"$TARGET"/}"; return 0; }
  mkdir -p "$(dirname "$dst")"
  if [ "$subst" = 1 ]; then
    sed "s/{{PROJECT}}/$(printf '%s' "$NAME" | sed 's/[&/\]/\\&/g')/g" "$src" > "$dst"
  else
    cp "$src" "$dst"
  fi
  [ -x "$src" ] && chmod +x "$dst" || true
}

# put_tree <srcdir> <dstdir> [subst]
put_tree() {
  local srcdir="$1" dstdir="$2" subst="${3:-0}" f rel
  while IFS= read -r -d '' f; do
    rel="${f#"$srcdir"/}"
    case "$rel" in *__pycache__*|*.pyc) continue ;; esac
    put "$f" "$dstdir/$rel" "$subst"
  done < <(find "$srcdir" -type f -print0 | sort -z)
}

say "Installing agentic-sdd-kit into $TARGET (project: $NAME)$( [ "$DRY" -eq 1 ] && echo ' [dry run]')"

# 1. Tools (CLI + pipeline). These are code, not project content: refresh them with --force.
put_tree "$KIT/tools/sdd" "$TARGET/tools/sdd"

# 2. Claude Code integration.
if [ "$CLAUDE" -eq 1 ]; then
  put_tree "$KIT/claude" "$TARGET/.claude"
fi

# 3. Docs and templates.
if [ "$DOCS" -eq 1 ]; then
  put "$KIT/templates/CLAUDE.md" "$TARGET/CLAUDE.md" 1
  put_tree "$KIT/templates/docs" "$TARGET/docs" 1
  put_tree "$KIT/templates/specs" "$TARGET/specs" 1
fi

# 4. Config.
cfg_src="$KIT/templates/sdd.config.json"
if [ -n "$CONFIG" ]; then
  if [ -f "$KIT/examples/configs/$CONFIG.json" ]; then cfg_src="$KIT/examples/configs/$CONFIG.json"
  elif [ -f "$CONFIG" ]; then cfg_src="$CONFIG"
  else echo "no such config: $CONFIG (rust|typescript|python|go or a file)" >&2; exit 2; fi
fi
if [ -e "$TARGET/sdd.config.json" ] && [ "$FORCE" -ne 1 ]; then
  skipped=$((skipped+1)); SKIPPED_LIST+=("sdd.config.json")
else
  [ -e "$TARGET/sdd.config.json" ] && overwritten=$((overwritten+1)) || copied=$((copied+1))
  if [ "$DRY" -eq 1 ]; then say "  + sdd.config.json (from ${cfg_src#"$KIT"/})"; else
    python3 -I - "$cfg_src" "$TARGET/sdd.config.json" "$NAME" <<'PY'
import json, sys
src, dst, name = sys.argv[1:4]
cfg = json.load(open(src, encoding="utf-8"))
cfg.setdefault("project", {})["name"] = name
json.dump(cfg, open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
open(dst, "a", encoding="utf-8").write("\n")
PY
  fi
fi

# 5. .gitignore entries.
if [ "$DRY" -ne 1 ]; then
  touch "$TARGET/.gitignore"
  for entry in ".wt/" ".sdd/"; do
    grep -qxF "$entry" "$TARGET/.gitignore" || printf '%s\n' "$entry" >> "$TARGET/.gitignore"
  done
fi

say ""
say "copied $copied file(s), overwritten $overwritten, skipped $skipped existing (use --force to overwrite)."
if [ "${#SKIPPED_LIST[@]}" -gt 0 ]; then
  say "skipped:"; for s in "${SKIPPED_LIST[@]}"; do say "  - $s"; done
fi
cat <<EOF

Next steps
  1. Edit sdd.config.json: project name/threat model, languages, gates (fast/full), tests,
     requirement id regex, trace scan globs, components regex, protected/append-only paths,
     formatters, sources (normative corpus and lookup), explicit model ids per tier, commit trailers.
     Validate: python3 -I tools/sdd/sdd.py config --validate ; python3 -I tools/sdd/sdd.py doctor
  2. Fill the placeholders in CLAUDE.md, docs/CONSTITUTION.md (articles 7-9), docs/DEVELOPMENT.md
     (gate table, oracles) and docs/ROADMAP.md (features own their numbers).
  3. Add the project's build/test commands to .claude/settings.json "permissions.allow".
  4. Install the git hooks (pre-commit, commit-msg, pre-push) in this repository:
     python3 -I tools/sdd/sdd.py hooks install
     (sets core.hooksPath; refuses over existing hooks such as git-lfs: see docs/ADAPTING.md).
     Adopting the gates on an existing codebase: python3 -I tools/sdd/sdd.py baseline --init
     (docs/PLAYBOOK.md, "Adopting the gates on an existing codebase").
  5. Commit. Then start a feature: python3 -I tools/sdd/sdd.py new-feature 001 <slug>, or in
     Claude Code: /feature F001 <slug>   (the pipeline runs in the worktree .wt/F001).
  6. Read docs/PLAYBOOK.md and docs/LESSONS.md in the kit before the first autonomous run.
EOF
