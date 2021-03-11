#!/bin/bash

# Script fails if any subsequent command fails
set -e

# Unit tests
python -m unittest discover $*
