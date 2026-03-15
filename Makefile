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

# ── Screenshots ──────────────────────────────────────────────────────────

screenshot:
	mkdir -p docs
	docker run --rm --network host -v $(PWD)/docs:/screenshots \
		mcr.microsoft.com/playwright:v1.58.2-noble \
		npx playwright screenshot --full-page --wait-for-timeout 5000 \
		--viewport-size 1280,800 http://localhost:8000 /screenshots/grid-view.png

# ── Exiftool service (run on Immich server) ──────────────────────────────

exiftool-build:
	docker compose -f exiftool-service/docker-compose.yml build

exiftool-up:
	docker compose -f exiftool-service/docker-compose.yml up --build -d

exiftool-down:
	docker compose -f exiftool-service/docker-compose.yml down

exiftool-logs:
	docker compose -f exiftool-service/docker-compose.yml logs -f
