#!/usr/bin/env bash

# install_dependencies.sh

# Exit immediately if a command exits with a non-zero status
set -e

uv pip install -r <(uv pip compile pyproject.toml --extra dev)