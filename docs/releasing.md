# Releasing juried

Releases are cut from `main` by tagging; the `Publish to PyPI` workflow builds and uploads
the package when a GitHub release is published for the tag.

## Every release

1. Open a `release/X.Y.Z` branch from an up to date `main`.
2. Update `CHANGELOG.md`: move the entries under `[Unreleased]` into a new `[X.Y.Z]`
   section dated today, list every breaking change under "Breaking changes", and add the
   compare link at the bottom of the file.
3. Bump `version` in `pyproject.toml` and `__version__` in `src/juried/__init__.py`. They
   must match; `juried --version` prints the latter.
4. Run `make check` on every supported Python (3.11, 3.12 and 3.13). Locally that is one
   virtual environment per interpreter; the `Check` workflow does the same on the pull
   request.
5. Run `make example` and read the output: the refund scenario should be the only gate
   that fails, and `juried calibrate` should report that the stub agreed with 24 of the 32
   labels, with eight false passes. `make` itself exits non zero because that gate fails,
   which is the expected result.
6. Open a pull request for the branch, wait for `Check` to pass, and merge it.
7. On `main`, after pulling: `make check` once more, then create an annotated tag and push
   it:

   ```
   git tag -a vX.Y.Z -m "juried X.Y.Z"
   git push origin vX.Y.Z
   ```

8. Create the GitHub release for the tag with the `[X.Y.Z]` section of `CHANGELOG.md` as
   its notes. Publishing the release triggers `publish.yml`.
9. Verify: the `Publish to PyPI` run is green, https://pypi.org/project/juried/ shows the
   new version, and in a fresh virtual environment `pip install juried==X.Y.Z` followed by
   `juried --version` prints it.

## Cutting 1.0.0

1.0.0 is the point at which the interfaces listed under "Stability" in the README become
a promise. Cut it only when all of the following hold:

- The `Live provider contract` workflow has run green at least once with both providers
  exercised, and the uploaded `live-pytest-output` artifact shows no skips.
- A real calibration report is committed: `calibration/` holds responses from a real
  endpoint labelled by hand, and `reports/juried-calibration.json` was produced by a real
  judge against them (see `docs/calibration.md`).
- At least one external user has run a suite against a real endpoint and reported back.
- No change to any of the stable interfaces in the previous four weeks, so the promise is
  made about something that has settled.

When those hold, release 1.0.0 with the steps above and a `[1.0.0]` changelog section that
states the promise in one line and links to the Stability section.
