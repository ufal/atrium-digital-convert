<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" title="Python Version"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-digital-convert" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---

# ATRIUM digital-convert - Agent Skill 🤖📄

### Goal: let coding agents convert born-digital documents and route their pages via a server-client skill

This branch (`agent-skill`) packages the **ATRIUM digital-convert API service** together with a **Skill for
coding agents** (Claude Code, Codex, Gemini/Antigravity). The design follows a strict server-client split:

- **Server** 🖥️ - the FastAPI service in [`service/`](service/) reads PDF, DOCX, ODT, ODS, XLSX and RTF (DOC and
  XLS through LibreOffice) into the ATRIUM document record, and assesses every page (Docker Compose `api` profile
  or a local venv; no model, no GPU).
- **Client** 🪶 - [`scripts/atrium_convert.py`](scripts/atrium_convert.py), a **zero-dependency** stdlib-only
  script that agents call directly.
- **Skill contract** 📜 - [`SKILL.md`](SKILL.md) tells the agent when and how to use it: text-layer verdicts,
  page routes, refusals and what to do about each.

For the command-line converter, the evaluation tools and the full project documentation, see the
[`test`](https://github.com/ufal/atrium-digital-convert/tree/test) branch; this branch carries only what the
skill needs.

### Table of contents 📑

  * [Quick start 🚀](#quick-start-)
  * [Skill installation 🔧](#skill-installation-)
  * [Server setup 🖥️](#server-setup-️)
  * [Client usage 🪶](#client-usage-)
  * [Maintenance notes 🔍](#maintenance-notes-)
  * [Contacts 📧](#contacts-)

## Quick start 🚀

```bash
git clone -b agent-skill https://github.com/ufal/atrium-digital-convert.git
cd atrium-digital-convert
bash scripts/server.sh                                           # start (or reuse) the server
python3 scripts/atrium_convert.py small_data_samples/image_only.pdf
```

Expected: three rows. Page 1 is `digital`; pages 2 and 3 are `none` and need OCR (a scanned page and a blank one).

## Skill installation 🔧

### Claude Code

```bash
git clone -b agent-skill https://github.com/ufal/atrium-digital-convert.git \
    ~/.claude/skills/atrium-digital-convert
```

### Codex

```bash
git clone -b agent-skill https://github.com/ufal/atrium-digital-convert.git \
    ~/.codex/skills/atrium-digital-convert
```

### Google Antigravity

Clone anywhere and point `AGENTS.md` at the skill:

```
Use the ATRIUM digital-convert skill from `atrium-digital-convert/SKILL.md`.
Start the server with `bash atrium-digital-convert/scripts/server.sh`, then run
`python3 atrium-digital-convert/scripts/atrium_convert.py [FILES...]`.
```

Update with `git pull` in the installed clone.

## Server setup 🖥️

```bash
bash scripts/server.sh          # Docker Compose api profile, or the local fallback
bash scripts/server.sh --local  # a local server in venv-dc (no Docker)
```

The script exits at once if `/ready` already answers, and otherwise waits for it. With Docker it runs the
released image of this branch's version (from `para_config.txt`), and builds it from the checkout when it cannot
pull it. The port defaults to `8000` (`ATRIUM_DC_PORT`). For `/describe` to ask the other stages, set
`PAGE_CLASSIFICATION_URL` and `OCR_POSTPROCESS_URL` in `.env` (see `.env.example`). Every setting, limit and
error is in [`service/README.md`](service/README.md).

## Client usage 🪶

```bash
python3 scripts/atrium_convert.py report.pdf                         # record + per-page text layer
python3 scripts/atrium_convert.py report.pdf --describe              # + page type, quality and route
python3 scripts/atrium_convert.py report.docx --markdown --out-dir out/
python3 scripts/atrium_convert.py report.pdf --format json           # the whole answer
python3 scripts/atrium_convert.py --info                             # capabilities and limits
```

Point the client at another deployment with `--base-url` or `ATRIUM_DC_URL`. The text-layer verdicts and the
routes are explained in [`SKILL.md`](SKILL.md#text-layers-and-routes-).

## Maintenance notes 🔍

Review checklist for every change or sync-merge into this branch:

- [ ] no document names a script that differs from the committed file;
- [ ] no provenance claim the service does not make on this branch;
- [ ] no reference to a file or directory absent from this branch;
- [ ] the documented response fields match what `service/api.py` returns;
- [ ] the client re-run on `small_data_samples/` against a locally started server.

## Contacts 📧

**For support write to:** lutsai.k@gmail.com, responsible for the
[GitHub repository](https://github.com/ufal/atrium-digital-convert).

### Acknowledgements 🙏

- **Developed by** ÚFAL, Charles University 👥
- **Funded by** [ATRIUM](https://atrium-research.eu/) 💰
- **Built for** the Archaeological Map of the Czech Republic (AMČR)
