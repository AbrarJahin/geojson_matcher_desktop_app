# Paper 3 — Road Matcher research-software publication

**Status:** proposed software paper (JOSS-style); distinct from Papers 1–2. **Reference:** Road Matcher `v1.3.0+87fe`.
**Question:** Can independent GIS researchers install, understand, reproduce and extend the human-in-the-loop road-connection workflow?

## Existing implementation (read only as needed)
- `main.py`, `app/ui/`: two-GeoJSON desktop workflow, pair/junction review; `app/core/`: matching, geometry updates, audit and session state.
- `tests/`, `Makefile`, `scripts/project_tasks.py`: verification; `.github/workflows/tests.yml` and `release.yml`: CI and manually triggered platform releases.
- `README.md`, `CITATION.cff`, `THIRD_PARTY_NOTICES.md`: current docs/citation/notices; check for outdated review-policy language. Do **not** assume an OSI project license or Qt redistribution compliance is already settled.

## Research requirements
1. Obtain ownership/publication approval; select an OSI-approved project license and inventory exact bundled third-party/Qt licensing obligations.
2. Make installation, supported-platform limitations, example inputs, human review, state restoration, final outputs and failure modes reproducible.
3. Provide a shareable nonrestricted example dataset and executable end-to-end walkthrough with expected outputs.
4. Document automated tests, release provenance, citation/archival DOI, contribution process and evidence of independent use; check current journal eligibility and public-history requirements.
5. Clearly distinguish documented software capabilities from independently validated methodology (Paper 1) and routing outcomes (Paper 2).

## Paper-ready gate
License and distribution review completed; stable public source/release; external reviewer can reproduce example workflow; test/documentation/citation evidence meets target venue requirements.

**Non-goal:** assert new algorithmic or routing results in the software paper, or alter existing release artifacts through these specs.
