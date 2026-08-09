# Thin wrapper over manage.py, which is the real implementation.
#
# make is not installed on the primary development machine (Windows), so every
# target here just forwards to Python. Use `python manage.py ...` directly if
# you do not have make — the commands are identical.

PY  ?= python
ENV ?= dev
SVC ?= api

.PHONY: help bootstrap up secrets deploy status logs destroy migrate seed local test fmt

help:
	@echo "make bootstrap              create the terraform state backend (once per account)"
	@echo "make up ENV=dev             create or update an environment"
	@echo "make secrets ENV=dev        write secret values to Secrets Manager"
	@echo "make migrate                apply supabase migrations"
	@echo "make seed EMAIL=a@b.com     create the first administrator"
	@echo "make deploy ENV=dev         build, push, blue/green release"
	@echo "make status ENV=dev         services and firing alarms"
	@echo "make logs ENV=dev SVC=api   tail CloudWatch logs"
	@echo "make destroy ENV=dev        tear an environment down"
	@echo "make local                  run everything with docker compose"
	@echo "make test                   unit tests and lint"

bootstrap:
	$(PY) manage.py bootstrap

up:
	$(PY) manage.py up --env $(ENV)

secrets:
	$(PY) manage.py secrets --env $(ENV)

deploy:
	$(PY) manage.py deploy --env $(ENV)

status:
	$(PY) manage.py status --env $(ENV)

logs:
	$(PY) manage.py logs --env $(ENV) --service $(SVC)

destroy:
	$(PY) manage.py destroy --env $(ENV)

migrate:
	$(PY) manage.py migrate

seed:
	$(PY) manage.py seed --email $(EMAIL)

local:
	$(PY) manage.py local

test:
	$(PY) manage.py test

fmt:
	cd backend && ruff format app tests && ruff check --fix app tests
	terraform fmt -recursive infra/terraform
