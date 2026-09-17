# Releasing

A release is a tag on `main` and a GitHub release made from it; the `Publish to PyPI`
workflow does the rest. Before tagging, every step below is done on the version being
released, in this order.

1. `make check` on a clean checkout of `main`: ruff, mypy (strict) and the test suite.
2. The live provider tests, deselected by default, against the real APIs:

   ```
   JURIED_LIVE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m live -v
   ```

   CONTRIBUTING.md describes what they send and how a missing key skips a provider.
3. The version is set in `pyproject.toml` and `src/juried/__init__.py`, the CHANGELOG
   section is headed with the version and the date, and both are merged to `main`.
4. Tag `main` and push the tag:

   ```
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

5. Create a GitHub release from the tag, with the CHANGELOG section for the version as its
   body. Publishing the release triggers `.github/workflows/publish.yml`, which builds the
   sdist and wheel and uploads them to PyPI. With the GitHub CLI:

   ```
   awk '/^## \[X.Y.Z\]/{p=1; next} /^## \[/{p=0} p' CHANGELOG.md > /tmp/juried-X.Y.Z-notes.md
   gh release create vX.Y.Z --title "juried X.Y.Z" --notes-file /tmp/juried-X.Y.Z-notes.md
   gh run list --workflow publish.yml --limit 1
   ```

   `gh run watch <run-id>` follows the publish run to its end.
6. Confirm that <https://pypi.org/project/juried/> shows the new version and that the
   PyPI badge in the README has updated.
