.PHONY: all test test-engine check clean

all:
	$(MAKE) -C sandbox all

test:
	$(MAKE) -C sandbox test
	python3 -m unittest discover -s tests -p "test_*.py"

test-engine:
	python3 -m unittest discover -s tests -p "test_*.py"

check:
	bash scripts/check_progress_append_only.sh
	bash scripts/check_repo_hygiene.sh
	bash scripts/test_append_only.sh
	python3 -m unittest discover -s tests -p "test_*.py"
	git diff --check

clean:
	$(MAKE) -C sandbox clean
	rm -rf __pycache__ tests/__pycache__ engine/__pycache__ .pytest_cache
