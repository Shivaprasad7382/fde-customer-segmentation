PY ?= python
export PYTHONPATH := src

.PHONY: test tiny starter full reproduce schema clean
test:        ## unit + integration tests (tiny dataset, ~5 s)
	$(PY) -m unittest discover -s tests -v
tiny:
	$(PY) -m segmentation_gen generate --scale tiny --out output/tiny
starter:     ## 5k customers / 20k orders / 100k events
	$(PY) -m segmentation_gen generate --scale starter --out output/starter
full:        ## 50k customers / 200k orders / 1M events
	$(PY) -m segmentation_gen generate --scale full --out output/full
reproduce:   ## generate twice, compare row counts + checksums
	$(PY) scripts/reproduce.py --scale starter --out output/starter/reports
schema:      ## regenerate docs/DATA_DICTIONARY.md
	$(PY) scripts/export_schema_docs.py
clean:
	rm -rf output
