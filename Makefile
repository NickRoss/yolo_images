.PHONY: up down build logs exiftool-up exiftool-down exiftool-build exiftool-logs

# ── Main app (run locally) ───────────────────────────────────────────────

build:
	docker compose build

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

# ── Exiftool service (run on Immich server) ──────────────────────────────

exiftool-build:
	docker compose -f exiftool-service/docker-compose.yml build

exiftool-up:
	docker compose -f exiftool-service/docker-compose.yml up --build -d

exiftool-down:
	docker compose -f exiftool-service/docker-compose.yml down

exiftool-logs:
	docker compose -f exiftool-service/docker-compose.yml logs -f
