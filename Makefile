# GhostTrace prototype. Windows without make: run the commands shown in README.md.
PY ?= python

.PHONY: up down reset local pipeline serve test eval clean

up:            ## build and run in containers; open http://localhost:8080
	docker compose up --build

down:
	docker compose down

reset:         ## remove the containers and the simulated data
	docker compose down -v

local: pipeline serve   ## run without Docker

pipeline:      ## simulate, match, tune, decide, report (skips if results exist)
	$(PY) -m services.pipeline

serve:         ## start the API and web app on http://localhost:8080
	$(PY) -m services.api

test:
	$(PY) -m unittest discover -s tests -v

eval:          ## rebuild everything and regenerate docs/results/
	GT_FORCE=1 $(PY) -m services.pipeline

clean:
	rm -f data/ghosttrace.db*
