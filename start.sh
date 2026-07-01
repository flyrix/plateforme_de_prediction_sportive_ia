#!/bin/bash
# Script de démarrage pour Render
# Render exécute depuis la racine du repo
exec uvicorn ia_betpredict.backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
