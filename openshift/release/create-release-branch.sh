#!/bin/bash

# Usage: create-release-branch.sh v0.4.1 release-0.4

set -exo pipefail

source openshift/release/common.sh

release=$1
target=$2

# Fetch the latest tags and checkout a new branch from the wanted tag.
git fetch upstream -v --tags
git checkout -b "$target" "$release"

# Remove GH Action hooks from upstream
rm -rf .github/workflows
git commit -sm ":fire: remove unneeded workflows" .github/

# Copy the midstream specific files from the OPENSHIFT/main branch.
git fetch openshift main
# shellcheck disable=SC2086
git checkout openshift/main -- $MIDSTREAM_CUSTOM_FILES

# Generate our OCP artifacts
tag=${target/release-/}
yq write --inplace openshift/project.yaml project.tag "knative-$tag"

make generated-files
git add .
git commit -m ":open_file_folder: Add OpenShift specific files"
