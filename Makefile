.PHONY: frontend-up frontend-down frontend-logs screenshot server-up server-down server-logs

frontend-up:
	docker compose up --build -d

frontend-down:
	docker compose down

frontend-logs:
	docker compose logs -f

screenshot: frontend-up
	mkdir -p docs
	docker run --rm --network host -v $(PWD)/docs:/screenshots \
		mcr.microsoft.com/playwright:v1.58.2-noble \
		npx playwright screenshot --full-page --wait-for-timeout 5000 \
		--viewport-size 1280,800 http://localhost:8000 /screenshots/grid-view.png

server-up:
	docker compose -f exiftool-service/docker-compose.yml up --build -d

server-down:
	docker compose -f exiftool-service/docker-compose.yml down

server-logs:
	docker compose -f exiftool-service/docker-compose.yml logs -f
