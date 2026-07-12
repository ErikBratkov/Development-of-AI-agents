@echo off
rem остановка контейнера Neo4j после работы с voice-assistant
rem данные базы живут в volume neo4j_data и переживают остановку
chcp 65001 >nul

cd /d "%~dp0"
call docker compose down
pause
