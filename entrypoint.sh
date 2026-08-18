#!/bin/sh
set -e

FORCE_COLOR=1 varlock run -- fastapi run /app/src/anony_mate_api/app.py --host 0.0.0.0 --port "${PORT:-8090}"
