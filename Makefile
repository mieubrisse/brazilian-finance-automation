# Without this, make would see the tests/ directory, decide `tests` is already
# up to date, and run nothing while reporting success.
.PHONY: tests test t test-offline run r setup s

tests:
	uv run pytest

test: tests

t: tests

# Runs the suite with no network at all: a fresh network namespace holds only
# a downed loopback interface, so nothing can reach Pluggy, YNAB or anything
# else. Proves the isolation rather than asserting it. Needs sudo for the
# namespace (unprivileged user namespaces are blocked on Ubuntu 24.04), then
# drops back to the invoking user so no root-owned files land in the checkout.
# Run `make test` first: it installs the dependencies this needs offline.
test-offline:
	sudo unshare --net -- sudo -u "$$(id -un)" env HOME="$$HOME" PATH="$$PATH" sh -c '\
		if uv run --frozen --offline python -c "import socket; socket.create_connection((\"1.1.1.1\", 443), timeout=5)" 2>/dev/null; then \
			echo "Network is reachable, so this run would prove nothing" >&2; exit 1; \
		fi; \
		echo "Confirmed: no network in this namespace"; \
		uv run --frozen --offline pytest'

run:
	uv run python financas_automatizadas/main.py

r: run

setup:
	# We suppress the printing of each command so the user doesn't get confused, especially with the "NEXT STEP" output
	@echo "Setting up venv..."
	@uv venv
	@echo "Copying configuration files if they don't already exist..."
	@if ! [ -f .env ]; then cp .env.example .env; echo "❗ NEXT STEP - fill in .env file, using comments in file for guidance"; fi

s: setup
