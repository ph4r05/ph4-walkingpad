#!/usr/bin/env bash

VENV="${VENV:./venv}"
if [ -d "$VENV" ]; then
  source "$VENV/bin/activate"
fi

exec pytest tests $*
