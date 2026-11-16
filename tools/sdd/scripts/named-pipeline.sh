#!/bin/sh
# named-pipeline.sh: copy the feature pipeline with a meta.name that names the feature.
#
#   named-pipeline.sh <feature-id> <slug> [suffix]      e.g. named-pipeline.sh F011 user-login spec
#
# The Workflow progress list shows a script's `meta.name`, which must be a literal (the Workflow
# `title` argument is ignored), so every run of the shared script reads "feature-pipeline".
# This writes <repo>/.wt/pipeline-runs/<id>-<slug>[-<suffix>].js, identical to
# tools/sdd/pipeline/feature-pipeline.js except for the name line, and prints its path; launch
# it with Workflow({scriptPath: <printed path>, args}). Agent prompts and options are unchanged,
# so resuming a run from either copy replays the same cached agents. SDD_PIPELINE overrides the
# source script.
set -eu
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: named-pipeline.sh <feature-id> <slug> [suffix]" >&2
  exit 2
fi
for v in "$@"; do
  case "$v" in '' | *[!A-Za-z0-9-]*) echo "named-pipeline: '$v' may only use letters, digits and -" >&2; exit 2 ;; esac
done
root=$(git rev-parse --show-toplevel)
src="${SDD_PIPELINE:-$root/tools/sdd/pipeline/feature-pipeline.js}"
name="$1-$2${3:+-$3}"
out="$root/.wt/pipeline-runs/$name.js"
mkdir -p "$(dirname "$out")"
sed "s/^  name: 'feature-pipeline',\$/  name: '$name',/" "$src" > "$out"
if ! grep -q "^  name: '$name',\$" "$out" || [ "$(grep -vc "^  name: '$name',\$" "$out")" != "$(grep -vc "^  name: 'feature-pipeline',\$" "$src")" ]; then
  echo "named-pipeline: could not rewrite the name line of $src" >&2
  rm -f "$out"
  exit 1
fi
echo "$out"
