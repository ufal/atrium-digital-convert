# Development

Tests, checks and regenerated files, for contributors.

```bash
pip install -r requirements.txt -r requirements_digital.txt -r requirements-test.txt
pytest -q                                                    # the whole suite (no LibreOffice needed: DOC/XLS use a fake soffice)
ruff check . && ruff format --check .
python tests/fixtures/digital/make_fixtures.py --verify      # the generated PDF/DOCX fixtures are current
python atrium_openapi.py export --app service.api:app --out service/openapi.json   # after an API change
python atrium_openapi.py check  --app service.api:app --spec service/openapi.json
python ../atrium-project/tools/ci/image_closure.py --repo-root . --worktree        # the image's file list
```

How to contribute, the release procedure and the history are in
[CONTRIBUTING.md](../CONTRIBUTING.md). The design record of every issue is in
[`agent_dev_logs/`](../agent_dev_logs) (digests and plans).
