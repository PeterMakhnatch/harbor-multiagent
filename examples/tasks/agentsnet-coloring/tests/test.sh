#!/bin/bash
set -eu

export PYTHONPATH=/world
exec python3 /tests/verify.py
