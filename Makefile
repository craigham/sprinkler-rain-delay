REGISTRY   := craigham
IMAGE      := sprinkler-rain-delay
PLATFORMS  := linux/amd64,linux/arm64
BUILDER    := multiarch
ANSIBLE    := ~/ansible
INVENTORY  := $(ANSIBLE)/inventories/van
VAULT_KEY  := $(ANSIBLE)/vault.key
SERVICE    := sprinkler_rain_delay

VERSION    := $(shell semantic-release version --print 2>/dev/null || python3 -c \
               "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
IMAGE_REF  := $(REGISTRY)/$(IMAGE):$(VERSION)

.PHONY: version release build push deploy

version:
	@echo $(VERSION)

release:
	semantic-release version
	@echo "Tagged v$(shell semantic-release version --print)"

build:
	docker buildx build --builder $(BUILDER) \
		--platform $(PLATFORMS) \
		-t $(IMAGE_REF) \
		-t $(REGISTRY)/$(IMAGE):latest \
		--push .

deploy:
	ansible -i $(INVENTORY) swarm-mgr-1 -m shell \
		-a "docker service update --image $(IMAGE_REF) $(SERVICE)" \
		--vault-password-file $(VAULT_KEY) -b

release-and-deploy: release build deploy
