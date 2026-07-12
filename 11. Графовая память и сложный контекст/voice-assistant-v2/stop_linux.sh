#!/usr/bin/env bash
# остановка контейнера Neo4j после работы с voice-assistant
# данные базы живут в volume neo4j_data и переживают остановку

set -e

cd "$(dirname "$0")"
docker compose down
