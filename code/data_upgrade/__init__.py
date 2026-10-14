"""One-off data repair / upgrade tools for already-written crawl output.

These modules are offline maintenance helpers: they rewrite existing
``output/author_<ID>_paper_citations.json`` files so that stale or buggy
``_fetch_state`` / diagnostics payloads match what the current code would have
written.  They are **not** part of the crawl itself -- the main entry point is
``python -m code.scholar_citation``.

Run them as modules from the project root:

    python -m code.data_upgrade.fix_diag_from_year_record <output_json>
    python -m code.data_upgrade.fix_output_fetch_state [output_dir]
"""
