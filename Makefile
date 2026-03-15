.PHONY: up down logs exiftool-up exiftool-down exiftool-logs screenshot

APP_SRC := app.py pyproject.toml Dockerfile docker-compose.yml $(wildcard static/*)
EXIFTOOL_SRC := $(wildcard exiftool-service/*)

# ── Main app (run locally) ───────────────────────────────────────────────

.build: $(APP_SRC)
	docker compose build
	@touch .build

up: .build
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

# ── Screenshots ──────────────────────────────────────────────────────────

screenshot: up
	mkdir -p docs
	docker run --rm --network host -v $(PWD)/docs:/screenshots \
		mcr.microsoft.com/playwright:v1.58.2-noble \
		npx playwright screenshot --full-page --wait-for-timeout 5000 \
		--viewport-size 1280,800 http://localhost:8000 /screenshots/grid-view.png

# ── Exiftool service (run on Immich server) ──────────────────────────────

.exiftool-build: $(EXIFTOOL_SRC)
	docker compose -f exiftool-service/docker-compose.yml build
	@touch .exiftool-build

exiftool-up: .exiftool-build
	docker compose -f exiftool-service/docker-compose.yml up -d

exiftool-down:
	docker compose -f exiftool-service/docker-compose.yml down

exiftool-logs:
	docker compose -f exiftool-service/docker-compose.yml logs -f

# ── Cleanup ──────────────────────────────────────────────────────────────

clean:
	rm -f .build .exiftool-build
