#!/usr/bin/env bash

# Custom files for midstream
MIDSTREAM_CUSTOM_FILES=$(cat <<EOT | tr '\n' ' '
openshift
OWNERS
EOT
)
