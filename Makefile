.PHONY: up down logs health validate

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

health:
	curl -fsS http://localhost:8080/health

validate:
	python3 -m compileall apps/api/app
