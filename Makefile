.PHONY: help start stop restart update sh logo test version
.DEFAULT := help

ifeq ($(firstword $(MAKECMDGOALS)),version)
VERSION ?= $(word 2,$(MAKECMDGOALS))
VERSION_GOAL := $(word 2,$(MAKECMDGOALS))
.PHONY: $(VERSION_GOAL)
$(VERSION_GOAL):
	@:
endif

MAKEFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
CURRENT_DIR := $(dir $(MAKEFILE_PATH))
HASS := $$(cat docker-compose.yaml | grep container_name | tr ':' ' ' | awk '{ print $$2 }')

help:
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mUsage:\n  make \033[36m<target>\033[0m\n"} \
	/^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-40s\033[0m %s\n", $$1, $$2 } /^##@/ \
	{ printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Home Assistant

start: ## Start
	docker compose up -d
	@echo "\nOpen:\033[36m http://localhost:8123\033[0m\n"

stop: ## Stop
	docker stop $(HASS)

restart: stop start ## Restart

sh: ## Go inside HA container
	docker exec -it $(HASS) /bin/bash

##@ Development

logo: ## Generate PNG logo assets from SVG (requires ImageMagick)
	magick -density 192 -background none -define svg:background-color=none assets/logo.svg -resize 256x256 custom_components/zkhnso/brand/logo.png
	magick -density 192 -background none -define svg:background-color=none assets/logo.svg -resize 512x512 custom_components/zkhnso/brand/logo@2x.png
	test `uname` = 'Darwin' && /Applications/ImageOptim.app/Contents/MacOS/ImageOptim custom_components/zkhnso/brand/logo.png custom_components/zkhnso/brand/logo@2x.png || true
	cp custom_components/zkhnso/brand/logo.png custom_components/zkhnso/brand/icon.png
	cp custom_components/zkhnso/brand/logo@2x.png custom_components/zkhnso/brand/icon@2x.png

test: ## Run integration tests in the Home Assistant Docker image
	docker exec $(HASS) python -m pytest --color=yes -p no:cacheprovider /tests

version: ## Release current changelog: make version X.Y.Z
	@test -n "$(VERSION)" || (echo "Usage: make version X.Y.Z"; exit 1)
	@python3 -c 'import json, re, sys; from pathlib import Path; version = sys.argv[1].removeprefix("v"); assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), "Version must use MAJOR.MINOR.PATCH format"; manifest_path = Path("custom_components/zkhnso/manifest.json"); manifest = json.loads(manifest_path.read_text()); changelog_path = Path("CHANGELOG.md"); changelog = changelog_path.read_text(); marker = "## В разработке"; assert marker in changelog, "Unreleased section not found in CHANGELOG.md"; before, after = changelog.split(marker, 1); heading = f"## v{version}"; updated = before + heading + after; manifest["version"] = version; manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"); changelog_path.write_text(updated); section = re.split(r"(?m)^## ", after, maxsplit=1)[0]; print(f"Updated manifest.json and renamed the changelog section to v{version}\n\n{heading}{section}".rstrip())' "$(VERSION)"
