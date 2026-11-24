#!/bin/bash
# Grades p1 on /logs/world/notes.json, the world's notes as the collect hook in
# task.toml saved them after play ended. Harbor saves the same file as the
# trial's artifact. See grade.py for the rule.

python3 /tests/grade.py
