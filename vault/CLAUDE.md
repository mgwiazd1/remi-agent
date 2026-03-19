# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is an **Obsidian vault** — a personal medical knowledge base focused on critical care medicine, pulmonary medicine, and internal medicine. It is not a software project; there are no build commands, tests, or code to run.

## Vault Structure

- **Medicine/** — Internal medicine topics (176 files): hepatology, infectious disease, cardiology, rheumatology, endocrinology, renal, etc.
- **PCCM/** — Pulmonary & Critical Care Medicine (121 files), subdivided into:
  - `Critical Care/` — ARDS, ventilation, hemodynamics, sepsis, blood products
  - `Pulmonary/` — COPD, ILD, bronchoscopy, PH, with `Obstructive/` subdirectory
  - `POCUS/` — Point-of-care ultrasound
  - `Physiology/` — Cardiac and pulmonary physiology
  - `Imaging/` — Chest imaging interpretation
  - `Lectures/` — Lecture notes
  - `Sleep Medicine/`
- **Intern Manual/** — Clinical procedures (central lines, arterial lines, chest tubes, NGT, paracentesis), medications, admissions protocols
- **UW wrong answers/** — UWorld practice exam analysis
- **Home/** — Personal projects (crypto platform, home lab)
- **Excalidraw/** — Diagrams and visual notes
- **Templates/** — Obsidian templates
- **_resources/** directories contain embedded PDFs and images

## Content Conventions

- Files use **YAML frontmatter** with `tags:` arrays (e.g., `tags: [Infectious-Disease, ARDS, Critical-Care]`)
- Internal links use Obsidian wiki-link syntax: `[[Page Name]]`
- Content is structured with markdown headers, bullet points, clinical pearls, mnemonics, and evidence-based summaries
- Images and PDFs are stored in `_resources/` subdirectories alongside the markdown files

## Obsidian Plugins in Use

Key community plugins: **LiveSync** (self-hosted multi-device sync), **Excalidraw**, **Dataview** (data queries), **Templater**, **Tag Wrangler**, **Editing Toolbar**, **Easy Typing**. Theme: Notation.

## Working with This Vault

- When creating or editing notes, preserve the existing tag taxonomy and frontmatter format
- Place resource files (images, PDFs) in the nearest `_resources/` directory
- Use `[[wiki-links]]` for cross-referencing between notes
- Respect the existing directory organization by medical specialty

## Inbox Pipeline

The `Inbox/` folder at the vault root is a drop zone for incoming PDFs (journal articles, guidelines, lecture handouts). When asked to **process the inbox**, follow this workflow for each PDF:

### Step 1 — Read and Classify
- Read the PDF and extract: title, authors, journal/source, year, and key clinical claims.
- Determine the clinical domain to route the note to the correct folder (see routing table below).

### Step 2 — Create a Structured Note
Create a markdown file in the destination folder using this template:

```markdown
---
tags: [Tag1, Tag2]
source: "Author et al., Journal, Year"
---
# Article Title

![[_resources/filename.pdf]]

## Key Findings
- Finding 1
- Finding 2

## Clinical Implications
- Implication 1

## Related
[[Existing Note 1]]
[[Existing Note 2]]
```

- **Tags**: Use the existing tag taxonomy (e.g., `Critical-Care`, `ARDS`, `Infectious-Disease`). Check existing notes for precedent.
- **Wiki-links**: Search the vault for related notes and add `[[wiki-links]]` in the Related section. Prioritize notes that share diseases, procedures, or concepts.
- **Filename**: Use the article title or a concise descriptive name, matching the vault's naming conventions.

### Step 3 — Move the PDF
- Move the PDF from `Inbox/` to the `_resources/` directory nearest the new note.
- If no `_resources/` directory exists in the destination folder, create one.
- Update the `![[_resources/filename.pdf]]` embed in the note to match the final path.

### Step 4 — Report
- Summarize what was created: note title, destination folder, tags assigned, and wiki-links added.

### Destination Routing

| PDF Content Domain | Destination |
|---|---|
| ARDS, ventilation, shock, hemodynamics, ICU topics | `PCCM/Critical Care/` |
| COPD, asthma, ILD, bronchoscopy, pulmonary hypertension | `PCCM/Pulmonary/` |
| Sleep apnea, narcolepsy, polysomnography | `PCCM/Sleep Medicine/` |
| Chest imaging, HRCT, nodule evaluation | `PCCM/Imaging/` |
| Point-of-care ultrasound, echo techniques | `PCCM/POCUS/` |
| Cardiopulmonary physiology, gas exchange | `PCCM/Physiology/` |
| Internal medicine subspecialties (ID, rheum, cards, nephro, GI, neuro, endo) | `Medicine/` |
| Procedures, medications, admissions protocols | `Intern Manual/` |
| Board review, practice question analysis | `UW wrong answers/` |
| Lecture slides or conference handouts | `PCCM/Lectures/` |
