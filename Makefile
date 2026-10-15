# Thin wrapper: every target delegates to scripts/task.sh so `make` and non-make hosts share one
# definition. Example: `make test` == `bash scripts/task.sh test`; pass extra args via ARGS="...".
TARGETS := build lock fetch-assets shell test test-all coverage lint format typecheck quality \
           security render-demo demo research analytics learn produce thumbnails \
           validate-spec distinctness review inspect timing export publish oauth db help

.PHONY: $(TARGETS)
$(TARGETS):
	@bash scripts/task.sh $@ $(ARGS)

.DEFAULT_GOAL := help
