"""System annotation layer shared by every recipe image (#112).

Ships into recipe containers at /opt/recipe/common; recipe run.py files
import `common.annotation` with PYTHONPATH=/opt/recipe (see the recipe
Dockerfiles).
"""
