.PHONY: install run lint format test build demo parser
install:
	uv sync --locked
run:
	uv run valutatrade
lint:
	uv run ruff check .
	uv run ruff format --check .
format:
	uv run ruff format .
test:
	uv run pytest --cov=valutatrade_hub --cov-report=term-missing
build:
	uv build
demo:
	uv run python -m scripts.demo
parser:
	uv run valutatrade-parser
