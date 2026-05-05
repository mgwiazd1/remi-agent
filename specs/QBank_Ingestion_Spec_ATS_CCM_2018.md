# Question Bank Ingestion Spec — ATS CCM 2018
## Source: ATS Review for the Critical Care Boards, First Edition (2018)
## For: Consuela (Dr. Remi Clinical Subagent)
## Priority: HIGH ACCURACY — Zero tolerance for errors

---

> **Note for Consuela:** This spec is specific to the ATS CCM 2018 PDF. Other question
> banks (MKSAP, BoardVitals, SEEK screenshots, etc.) will have their own ingestion specs
> with the same naming convention: `QBank_Ingestion_Spec_{SOURCE}_{YEAR}.md`.
> Always confirm which spec matches the PDF you are processing before starting.

---

## Overview

Process the ATS Review for the Critical Care Boards (2018) PDF into individual
Obsidian markdown notes. Each question gets its own `.md` file with the question
stem, answer choices, correct answer, full explanation, and references — all
matched with 100% accuracy.

**Source PDF:** `/home/proxmox/remi-intelligence/watch/books/incoming/2018_ATS.pdf`
(MG will drop it there, or use the path where it was uploaded)

**Output folder:** `/docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/`

**Resources folder:** `/docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/_resources/`

**Total questions:** ~149 (some numbers skipped — not all numbers 1-149 exist)

---

## PDF Structure — Read This Before Starting

The PDF has two completely separate sections:

1. **QUESTIONS section** (pages 1–78): Contains question stems and answer choices A-E.
   Questions are numbered sequentially but some numbers are SKIPPED in the answer
   section (e.g., Q2, Q3, Q6, Q7 may not appear as "X. Correct Answer" in the answer
   section — this means those questions share a group stem or the numbering is
   non-sequential). You must match each question to its answer by number.

2. **ANSWERS section** (starts page 79): Each answer block looks like:
   ```
   X. Correct Answer
   [Letter]. [Answer text]
   
   [Full explanation paragraph(s)]
   
   1. [Reference 1]
   2. [Reference 2]
   ```

**CRITICAL:** Questions and answers are separated by ~78 pages. You must extract ALL
questions first, then ALL answers, then pair them by number. Do NOT process one at
a time by reading sequentially — you will mismatch them.

---

## Step-by-Step Process

### Step 1: Extract full text
```bash
pdftotext -layout /path/to/2018_ATS.pdf /tmp/ats_full.txt
```

### Step 2: Extract all images
```bash
mkdir -p /docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/_resources/
pdfimages -png /path/to/2018_ATS.pdf /tmp/ats_img
ls /tmp/ats_img-*.png
```

Note which page each image came from using:
```bash
pdfimages -list /path/to/2018_ATS.pdf > /tmp/ats_imglist.txt
```

Cross-reference image page numbers with question page numbers to assign images
to the correct question.

### Step 3: Parse questions section
Extract all questions from the text. Each question block has this pattern:
```
[NUMBER].
[Clinical stem — can be multiple paragraphs]

[Question prompt — "Which of the following..."]

A. [choice]
B. [choice]
C. [choice]
D. [choice]
E. [choice]  (some questions have only 4 choices)
```

Build a Python dict: `questions = {1: {...}, 4: {...}, 5: {...}, ...}`

### Step 4: Parse answers section
Each answer block pattern:
```
[NUMBER]. Correct Answer
[Letter]. [Answer text]

[Explanation — can be multiple paragraphs]

1. [Reference]
2. [Reference]
```

Build a Python dict: `answers = {1: {...}, 4: {...}, ...}`

### Step 5: Verify matching
```python
q_nums = set(questions.keys())
a_nums = set(answers.keys())
unmatched_q = q_nums - a_nums
unmatched_a = a_nums - q_nums
print(f"Questions without answers: {unmatched_q}")
print(f"Answers without questions: {unmatched_a}")
```

If any mismatches exist — STOP and report them before writing any files.
Do not write files with incomplete or mismatched data.

### Step 6: Assign topic tags
For each question, assign topic tags based on the clinical content. Use these
standard categories:

- `acid-base` — acid-base disorders, anion gap, osmolar gap
- `renal` — AKI, renal replacement therapy, electrolytes
- `infectious` — pneumonia, sepsis, bacteremia, fungal infections
- `mechanical-ventilation` — vent modes, weaning, ARDS, asthma
- `cardiovascular` — shock, arrhythmia, heart failure, pressors
- `neurology` — stroke, seizure, brain death, ICP
- `toxicology` — overdose, poisoning
- `GI` — liver failure, GI bleed, pancreatitis
- `hematology` — transfusion, coagulopathy, thrombosis
- `endocrine` — DKA, adrenal, thyroid
- `pharmacology` — drug dosing, drug interactions
- `nutrition` — enteral, parenteral
- `procedures` — intubation, central line, chest tube, bronchoscopy
- `ethics` — goals of care, withdrawal
- `pulmonary` — ILD, COPD, PH, pleural disease
- `trauma` — TBI, chest trauma

Assign 1-3 tags per question. Be accurate — do not guess.

### Step 7: Write individual .md files

Process in batches of 10 questions. After each batch of 10, verify the files
were written correctly before continuing.

---

## Markdown Note Template

File naming: `ATS_CCM_2018_Q{NUM:03d}.md` (zero-padded to 3 digits)
Example: `ATS_CCM_2018_Q001.md`, `ATS_CCM_2018_Q082.md`

```markdown
---
source: ATS-CCM-2018
question_number: {NUM}
topic: {PRIMARY_TOPIC}
tags: [qbank, CCM-boards, {tag1}, {tag2}]
correct_answer: {LETTER}
has_image: {true/false}
reviewed: false
correct: null
---

# Q{NUM}: {FIRST_LINE_OF_STEM}

{FULL QUESTION STEM — preserve all clinical details exactly as written}

{If image: ![[ATS_CCM_2018_Q{NUM}_img1.png]]}

**Which of the following...** {question prompt}

## Answer Choices

- **A.** {choice A text}
- **B.** {choice B text}
- **C.** {choice C text}
- **D.** {choice D text}
- **E.** {choice E text — omit if only 4 choices}

## Correct Answer

**{LETTER}. {Answer text}**

## Explanation

{Full explanation — preserve completely, word for word. Do not summarize.
Do not paraphrase. Copy verbatim from the PDF. This is a medical board review
resource and accuracy is mandatory.}

## References

{Numbered references exactly as written in the PDF}
```

---

## Image Handling

For questions with embedded images (chest X-rays, EKG tracings, echocardiograms,
PFT graphs, CT scans, ventriculograms):

1. Identify the image from `pdfimages -list` output — match page number to question
2. Copy the extracted image:
   ```bash
   cp /tmp/ats_img-{XXX}.png \
     /docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/_resources/ATS_CCM_2018_Q{NUM}_img1.png
   ```
3. Embed in the note immediately after the question stem, before the answer choices:
   ```
   ![[ATS_CCM_2018_Q082_img1.png]]
   ```
4. Set `has_image: true` in frontmatter

Known image questions from inspection: Q82 (LV ventriculogram — Takotsubo).
There are approximately 30+ questions with images. Identify ALL of them from
the `pdfimages -list` output before processing.

---

## Accuracy Requirements — Non-Negotiable

1. **Correct answer letter must match exactly.** Verify each answer letter against
   the "X. Correct Answer / [Letter]." line in the answers section.

2. **Explanation must be verbatim.** Do not summarize, paraphrase, or truncate the
   explanation. Copy the full text as written.

3. **References must be complete.** Copy all references including authors, journal,
   year, volume, pages.

4. **Question stems must be complete.** Do not truncate clinical scenarios. All lab
   values, vitals, medication names must be preserved exactly.

5. **Answer choices must be complete.** Copy verbatim including all sub-clauses.
   Some answer choices span multiple lines — capture the full text.

6. **Verify every batch of 10** by checking:
   - Does the correct_answer letter match what's in the PDF answers section?
   - Is the explanation complete (not cut off)?
   - Are all references present?

---

## Verification Checklist Per Batch

After writing each batch of 10 questions, run:
```python
import os
import re

for q_num in batch_nums:
    filepath = f"/docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/ATS_CCM_2018_Q{q_num:03d}.md"
    assert os.path.exists(filepath), f"Missing: {filepath}"
    content = open(filepath).read()
    assert "correct_answer:" in content, f"Missing correct_answer in Q{q_num}"
    assert "## Correct Answer" in content, f"Missing Correct Answer section in Q{q_num}"
    assert "## Explanation" in content, f"Missing Explanation in Q{q_num}"
    assert "## References" in content, f"Missing References in Q{q_num}"
    assert len(content) > 500, f"Q{q_num} suspiciously short — may be truncated"
print("Batch verification passed.")
```

---

## Processing Order

Process in this order to manage context:

- Batch 1: Q1–Q10
- Batch 2: Q11–Q20
- Batch 3: Q21–Q30
- Batch 4: Q31–Q40
- Batch 5: Q41–Q50
- Batch 6: Q51–Q60
- Batch 7: Q61–Q70
- Batch 8: Q71–Q79
- Batch 9: Q80–Q90
- Batch 10: Q91–Q100
- Batch 11: Q101–Q110
- Batch 12: Q111–Q120
- Batch 13: Q121–Q130
- Batch 14: Q131–Q149

After ALL batches complete, run a final verification:
```bash
ls /docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/*.md | wc -l
```
Expected: ~74 files (matching the ~74 confirmed answer blocks found in the PDF).
Note: Question numbers are NOT sequential — some are skipped. The count of files
should match exactly the count of "X. Correct Answer" blocks in the answers section.

---

## Final Index Note

After all questions are written, create an index note:

**File:** `/docker/obsidian/MG/PCCM/QBank/ATS-CCM-2018/ATS_CCM_2018_INDEX.md`

```markdown
---
type: index
source: ATS-CCM-2018
tags: [qbank, CCM-boards, index]
total_questions: {N}
---

# ATS Review for the Critical Care Boards 2018 — Question Bank

**Source:** ATS Review for the Critical Care Boards, First Edition (2018)
**Editors:** Alison Clay MD, Margaret M. Hayes MD, Susan Pasnick MD, Tisha Wang MD
**Total Questions:** {N}

## Browse by Topic

```dataview
TABLE question_number, topic, correct_answer, correct
FROM "PCCM/QBank/ATS-CCM-2018"
WHERE type != "index"
SORT question_number ASC
```

## Review Queue (Unanswered)

```dataview
TABLE question_number, topic
FROM "PCCM/QBank/ATS-CCM-2018"
WHERE correct = null AND type != "index"
SORT question_number ASC
```
```

---

## Error Handling

If the PDF text extraction produces garbled text for any question:
1. Rasterize that specific page: `pdftoppm -jpeg -r 200 -f {PAGE} -l {PAGE} 2018_ATS.pdf /tmp/page`
2. Read the image visually using Gemma vision
3. Transcribe manually with 100% accuracy
4. Note in the frontmatter: `extraction_method: vision`

If a question number appears in the questions section but has no matching answer:
- Leave `correct_answer: UNKNOWN` and `explanation: "Answer not found in PDF"`
- Report these at the end

---

## Report on Completion

When finished, provide:
1. Total notes created
2. List of any questions with missing answers
3. List of questions with images
4. Any extraction issues encountered
5. Confirm all files owned by proxmox: `chown -R proxmox:proxmox /docker/obsidian/MG/PCCM/QBank/`
