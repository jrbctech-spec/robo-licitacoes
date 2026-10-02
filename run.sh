#!/bin/bash
cd /home/jrbc/Documentos/robo-licitacoes
git pull origin main
./venv/bin/python3 app_gui.py
