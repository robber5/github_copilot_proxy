# Makefile for building the Docker image

IMAGE_NAME := robber5/github_copilot_proxy
TAG := v1.0

.PHONY: build

build:
	docker build -t $(IMAGE_NAME):$(TAG) .

.PHONY: run

run:
	docker run --name gc_proxy -v $(HOME)/.config:/root/.config -p 8080:80 --env-file .env -d $(IMAGE_NAME):$(TAG)