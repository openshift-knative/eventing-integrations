#!/usr/bin/env bash

# Synchs the release-next branch to main and then triggers CI
# Usage: update-to-head.sh

set -eo pipefail
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")

source openshift/release/common.sh

# Check if there's an upstream release we need to mirror downstream
openshift/release/mirror-upstream-branches.sh

robot_trigger_msg=":robot: triggering CI on branch 'release-next' after synching from upstream/main"

# Reset release-next to upstream/main.
git fetch upstream main
git checkout upstream/main -B release-next

# Update openshift's main and take all needed files from there.
git fetch openshift main
# shellcheck disable=SC2086
git checkout openshift/main $MIDSTREAM_CUSTOM_FILES

make generated-files
git add .
git commit -m ":open_file_folder: Update OpenShift specific files"

git push -f openshift release-next

# Trigger CI
git checkout release-next -B release-next-ci
date > ci
git add ci
git commit -m "${robot_trigger_msg}"
git push -f openshift release-next-ci

if hash hub 2>/dev/null; then
   # Test if there is already a sync PR in 
   COUNT=$(hub api -H "Accept: application/vnd.github.v3+json" repos/openshift-knative/${REPO_NAME}/pulls --flat \
    | grep -c "${robot_trigger_msg}") || true
   if [ "$COUNT" = "0" ]; then
      hub pull-request --no-edit -l "kind/sync-fork-to-upstream,approved,lgtm" -b openshift-knative/${REPO_NAME}:release-next -h openshift-knative/${REPO_NAME}:release-next-ci -m "${robot_trigger_msg}"
   fi
else
   echo "hub (https://github.com/github/hub) is not installed, so you'll need to create a PR manually."
fi
