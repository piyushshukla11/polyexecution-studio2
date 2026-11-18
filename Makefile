.PHONY: all test test-engine clean

all:
	$(MAKE) -C sandbox all

test:
	$(MAKE) -C sandbox test
	python3 -m unittest discover -s tests -p "test_*.py"

test-engine:
	python3 -m unittest discover -s tests -p "test_*.py"

clean:
	$(MAKE) -C sandbox clean
	rm -rf __pycache__ tests/__pycache__ engine/__pycache__ .pytest_cache
