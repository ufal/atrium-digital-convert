"""tests/office_support.py — LibreOffice for the tests that render office documents (#9).

Since atrium-digital-convert#9 a DOCX, DOC, ODT or RTF is read as its native text on the pages of
its LibreOffice rendering, so a test that converts one needs `soffice`. The CI test job installs
it (`.github/workflows/docker.yml`, `apt-packages`); elsewhere such a test is skipped, not failed.
The DOC/XLS/XLSX tests that only check the conversion keep their fake `soffice` (`fake_soffice()`).
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Optional

import pytest

from api_util.digital_legacy import libreoffice_binary

#: Skip a test that renders an office document where LibreOffice is not installed.
needs_libreoffice = pytest.mark.skipif(
    libreoffice_binary() is None,
    reason="LibreOffice (soffice) is not installed: office documents are rendered for their pages (#9)",
)


def fake_soffice(
    tmp_path: Path,
    payload: Path,
    monkeypatch,
    pdf: Optional[Path] = None,
    profile_log: Optional[Path] = None,
) -> Path:
    """A `soffice` that 'converts' by copying `payload` to <outdir>/input.<target>, and 'renders'
    (`--convert-to pdf:...`) by copying `pdf` (or failing when none is given). With `profile_log`,
    it writes there the locale settings its profile was started with (the profile's
    `user/registrymodifications.xcu`, '' when there is none)."""
    script = tmp_path / "soffice"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, shutil, sys\n"
        "from urllib.parse import unquote, urlparse\n"
        "args = sys.argv[1:]\n"
        "target = args[args.index('--convert-to') + 1].split(':', 1)[0]\n"
        "outdir = args[args.index('--outdir') + 1]\n"
        f"pdf = {str(pdf) if pdf else ''!r}\n"
        f"profile_log = {str(profile_log) if profile_log else ''!r}\n"
        "if profile_log:\n"
        "    profile = unquote(urlparse(args[0].split('=', 1)[1]).path)\n"
        "    xcu = os.path.join(profile, 'user', 'registrymodifications.xcu')\n"
        "    open(profile_log, 'w').write(open(xcu).read() if os.path.exists(xcu) else '')\n"
        "if target == 'pdf' and not pdf:\n"
        "    sys.exit('no rendering in this fake')\n"
        f"shutil.copyfile(pdf if target == 'pdf' else {str(payload)!r}, outdir + '/input.' + target)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LIBREOFFICE_BIN", str(script))
    return script
