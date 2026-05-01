
NAME = HomeKitHub
XML_FILES = profile/*/*.xml

# apt: sudo apt-get install libxml2-utils libxml2-dev
check: xml-check

xml-check:
	xmllint --noout $(XML_FILES)

lint:
	python3 -m ruff check .

format-check:
	python3 -m ruff format --check .

black-check:
	python3 -m black --check .

test:
	python3 -m pytest -q

clean:
	python3 -c "import pathlib, shutil; r = pathlib.Path('.'); [shutil.rmtree(p, ignore_errors=True) for p in r.rglob('__pycache__') if p.is_dir()]; shutil.rmtree('.pytest_cache', ignore_errors=True); shutil.rmtree('.ruff_cache', ignore_errors=True)"
	rm -f $(NAME).zip

zip:
	zip -x@zip_exclude.lst -r $(NAME).zip *

.PHONY: check xml-check lint format-check black-check test clean zip
