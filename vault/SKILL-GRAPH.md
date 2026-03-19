---
tags: [Reference]
---
# Clinical Knowledge Skill Graph

> Auto-generated 2026-03-15 by Claude Code from vault analysis.
> 358 notes across 4 major sections. 78% wiki-linked.

---

## Visual Domain Map

```
                        ┌─────────────────────────────┐
                        │     FOUNDATIONAL SCIENCES    │
                        │                              │
                        │  Physiology (11)   Acid-Base │
                        │  Hypoxemia         Lactic    │
                        │  VQ Mismatch       Acidosis  │
                        │  R-L Shunt         Pulsus    │
                        │  Spirometry/PFTs             │
                        └──────────┬──────────┬────────┘
                                   │          │
                    ┌──────────────┘          └──────────────┐
                    ▼                                        ▼
  ┌─────────────────────────────┐      ┌─────────────────────────────────┐
  │    PULMONARY MEDICINE (47)  │      │    CRITICAL CARE / ICU (51)     │
  │                             │      │                                 │
  │  Obstructive (15)           │      │  Ventilation ─────────────────┐ │
  │    COPD, Asthma, GOLD      │      │    Mech Vent, Modes, Weaning  │ │
  │                             │      │    LPV, Peak Pressures        │ │
  │  Restrictive/ILD (8)       │◄────►│                               │ │
  │    HRCT, Cystic Lung, PAP  │      │  Shock & Hemodynamics ───────┐│ │
  │                             │      │    Undiff Shock, Pressors    ││ │
  │  Infections (6)            │      │    Fluid Responsive, A-line  ││ │
  │    Aspergillus, TB, NTM    │      │    Swan-Ganz, PAC            ││ │
  │                             │      │                              ││ │
  │  Pulm Hypertension (3)    │◄────►│  ARDS & Lung Rescue ─────┐  ││ │
  │    PH, PAH, Threshold      │      │    ARDS, ATS Recs, NMBAs │  ││ │
  │                             │      │                          │  ││ │
  │  Bronchoscopy (1)         │      │  Blood & Transfusion ──┐ │  ││ │
  │    EBUS                    │      │    Products, TEG, Shock│ │  ││ │
  │                             │      │                       │ │  ││ │
  │  Pleural (3)               │      │  Neuro-ICU ──────────┐│ │  ││ │
  │    Trapped Lung, Effusions │      │    EVD, Brain Death  ││ │  ││ │
  │                             │      │    PATCH Trial       ││ │  ││ │
  └─────────────┬───────────────┘      └──┬──┬──┬──┬──┬──┬───┘│ │
                │                         │  │  │  │  │  │     │ │
                │    ┌────────────────────┘  │  │  │  │  │     │ │
                │    │    ┌─────────────────-┘  │  │  │  │     │ │
                ▼    ▼    ▼                     │  │  │  │     │ │
  ┌─────────────────────────────┐               │  │  │  │     │ │
  │      IMAGING (8)            │               │  │  │  │     │ │
  │  HRCT, Fleischner, LungRADS │               │  │  │  │     │ │
  │  Lobar Collapse, MRI Brain  │               │  │  │  │     │ │
  └─────────────────────────────┘               │  │  │  │     │ │
                                                │  │  │  │     │ │
  ┌─────────────────────────────┐               │  │  │  │     │ │
  │      POCUS (4)              │◄──────────────┘  │  │  │     │ │
  │  EPSS, MAPSE, TAPSE, FS    │                   │  │  │     │ │
  └─────────────────────────────┘                   │  │  │     │ │
                                                    │  │  │     │ │
  ┌─────────────────────────────┐                   │  │  │     │ │
  │   SLEEP MEDICINE (5)        │◄──────────────────┘  │  │     │ │
  │  OSA, Narcolepsy, PSG       │                      │  │     │ │
  └─────────────────────────────┘                      │  │     │ │
                                                       │  │     │ │
                ┌──────────────────────────────────────┘  │     │ │
                ▼                                         │     │ │
  ┌──────────────────────────────────────────────────┐    │     │ │
  │            INTERNAL MEDICINE (176)                │    │     │ │
  │                                                   │    │     │ │
  │  Infectious Disease ████████████████████ (74)     │◄───┘     │ │
  │  Rheumatology       ██████████ (37)               │          │ │
  │  Neurology          █████ (22)                    │          │ │
  │  Cardiology         █████ (20)                    │◄─────────┘ │
  │  Nephrology         ████ (17)                     │            │
  │  GI / Hepatology    ██ (9)                        │            │
  │  Dermatology        █ (4)                         │            │
  │  Pulmonary          █ (3)                         │            │
  │  Endocrinology      ░ (2)                         │            │
  │  Hematology         ░ (1)                         │            │
  │  Psychiatry         ░ (1)                         │            │
  │  Oncology           ░ (1)                         │            │
  └──────────────────────┬────────────────────────────┘            │
                         │                                         │
                         ▼                                         │
  ┌─────────────────────────────┐    ┌─────────────────────────┐   │
  │  UW WRONG ANSWERS (25)      │    │  INTERN MANUAL (23)     │   │
  │  GI/Hepatology-heavy        │    │  Procedures, Meds,      │◄──┘
  │  Board review analysis      │    │  Admissions protocols    │
  └─────────────────────────────┘    └─────────────────────────┘
```

---

## Prerequisite Chains

These are the learning dependency paths embedded in the vault's link structure.

### Airway & Ventilation

```
Physiology: Spirometry ──► Flow Volume Loops
                │
                ▼
Pulmonary: Obstructive Lung Disease ──► COPD (GOLD, ABCD) + Asthma (Severity, GINA)
                │
                ▼
Procedures: Endotracheal intubation ──► Capnography (confirmation)
                │
                ▼
Critical Care: Mechanical Ventilation ──► Ventilator Modes ──► Volume A/C
                │                    ──► Elevated Peak Pressures
                │                    ──► Air Trapping in Severe Asthma
                │
                ▼
           Lung Protective Ventilation ──► ARDS ──► ATS Recommendations on ARDS
                │                                ──► Neuromuscular Blockers
                ▼
           Vent Weaning ──► NIPPV (step-down)
```

### Hemodynamics & Shock

```
Physiology: Acid Base ──► Lactic Acidosis
                │
                ▼
        Pulsus Paradoxus ──► Right Heart Cath Swan Ganz Interpretation
                                        │
                ┌───────────────────────┤
                ▼                       ▼
        Pulmonary Artery Catheters    RV Failure in ICU
                                        │
                                        ▼
        Undifferentiated Shock ──► Vasopressors ──► ATS How to Teach Pressors
                │              ──► Fluid Responsiveness
                │              ──► Arterial Line (monitoring)
                │
                ├──► Hemorrhagic Shock ──► Blood Products ──► TEG
                ├──► Cardiogenic Shock ──► CV Surgery Complications ──► VADs
                └──► Septic Shock ──► Early Fluid Mgmt for Sepsis
```

### Pulmonary Vascular

```
Physiology: Hypoxemia Mechanisms ──► VQ Mismatch
                │                ──► R-L Shunt
                ▼
Pulmonary: Pulmonary Hypertension ──► Pulmonary Artery Hypertension
                │                  ──► Threshold for Pre-capillary PH
                ▼
Critical Care: RV Failure in ICU ──► Swan-Ganz ──► Pulmonary Artery Catheters
                                  ──► Vasopressors (RV-specific inotropes)
POCUS:     TAPSE (RV function) ──► Pulmonary Hypertension
           EPSS / MAPSE (LV function) ──► Heart Failure with Reduced EF
```

### Infectious Disease ► Critical Care

```
Medicine: Infective endocarditis ──► Septic Emboli (ICU)
          Community Acquired Pneumonia ──► Cape Cod Trial (steroids)
                                       ──► Corticosteroids in CAP
                                       ──► ARDS (if progresses)
          Intracranial hemorrhage ──► PATCH Trial (platelets)
                                  ──► External Ventricular Drains
```

---

## Domain Mastery Assessment

| Domain | Notes | Links % | Depth | Mastery Level |
|--------|------:|--------:|-------|---------------|
| **PCCM — Critical Care** | 51 | 98% | Deep: comprehensive vent management, shock algorithms, hemodynamic monitoring, trials | ████████████████ **Expert** |
| **Infectious Disease** | 74 | 89% | Wide: bacteria, fungi, parasites, HIV, travel medicine, transplant | ██████████████ **Advanced** |
| **Pulmonary — Obstructive** | 15 | 62% | Solid: COPD/Asthma pathogenesis through management, GOLD/GINA | ████████████ **Advanced** |
| **Rheumatology** | 37 | 89% | Wide: CTD, vasculitis, arthropathies, MSK | ████████████ **Advanced** |
| **Pulmonary — General** | 31 | 62% | Good: ILD, infections, PH, pleural, eosinophilic | ██████████ **Proficient** |
| **Cardiology** | 20 | 89% | Moderate: HF, ACS, arrhythmia, valvular, devices | ██████████ **Proficient** |
| **Neurology** | 22 | 89% | Moderate: stroke, MS, neuropathies, headaches, CNS infections | ██████████ **Proficient** |
| **Nephrology** | 17 | 89% | Moderate: GN, stones, CKD, electrolytes, DI | █████████ **Proficient** |
| **Physiology** | 11 | 82% | Foundational: acid-base, hypoxemia, spirometry | █████████ **Proficient** |
| **Imaging** | 8 | 63% | Focused: HRCT, ILD patterns, nodule algorithms | ████████ **Competent** |
| **GI / Hepatology** | 34 | 86% | Moderate: 9 Medicine + 25 UW (hepatology-heavy UW set) | ████████ **Competent** |
| **Intern Manual** | 23 | 17% | Reference: procedures + protocols, low cross-linking | ███████ **Competent** |
| **POCUS** | 4 | 100% | Narrow: LV/RV function metrics only | ██████ **Developing** |
| **Sleep Medicine** | 5 | 80% | Narrow: OSA, narcolepsy, PSG basics | ██████ **Developing** |
| **Bronchoscopy** | 1 | 0% | Minimal: EBUS only | ███ **Beginner** |
| **Endocrinology** | 2 | — | Sparse: DKA/EDKA in ICU context only | ██ **Beginner** |
| **Hematology** | 1 | — | Sparse: transfusion-adjacent only | ██ **Beginner** |
| **Oncology** | 1 | — | Sparse: lung cancer screening only | █ **Beginner** |
| **Psychiatry** | 1 | — | Minimal: lithium side effects only | █ **Beginner** |

---

## Knowledge Gaps

Domains that should exist in a critical care / internal medicine knowledge base but are absent or severely underrepresented.

### Critical Gaps (High-yield, zero or near-zero coverage)

| Missing Domain | Why It Matters | Suggested Folder |
|---|---|---|
| **Toxicology** | ICU mainstay: overdoses, antidotes, toxidromes (only Hyperthermic Toxidromes exists) | `Medicine/` or `PCCM/Critical Care/` |
| **Palliative / End-of-Life** | Goals of care, withdrawal of life support, comfort meds — daily ICU practice | `PCCM/Critical Care/` |
| **Endocrinology** | Only DKA/EDKA covered; missing: thyroid storm, myxedema coma, adrenal crisis, hypoglycemia | `Medicine/` |
| **Hematology / Coagulopathy** | TEG exists but no DIC, HIT, TTP/HUS, anticoagulation reversal, sickle cell crisis | `Medicine/` |
| **Oncologic Emergencies** | No tumor lysis, SVC syndrome, hypercalcemia of malignancy, neutropenic fever management | `Medicine/` |

### Moderate Gaps (Partial coverage, needs expansion)

| Thin Domain | What Exists | What's Missing |
|---|---|---|
| **Renal / Electrolytes** | CKD, GN, stones, hyponatremia, hyperkalemia | Hypernatremia, hypophosphatemia, hypercalcemia, rhabdomyolysis, contrast nephropathy |
| **GI (non-hepatology)** | Celiac, IBD vaccine, GERD, C. diff | Pancreatitis (acute/chronic), mesenteric ischemia, GI motility, nutrition in critical illness |
| **Cardiology — Arrhythmia** | Afib (mentioned), bradycardia (1 note) | SVT management, VT/VF, antiarrhythmic drugs, post-arrest care, targeted temperature management |
| **POCUS** | 4 notes on LV/RV metrics | No lung ultrasound (B-lines, pneumothorax), no DVT protocol, no IVC assessment, no RUSH exam |
| **Bronchoscopy** | EBUS only | Flexible bronch technique, BAL interpretation, transbronchial biopsy, airway stenting, foreign body |
| **Sleep Medicine** | OSA, narcolepsy, PSG | No CSA, no obesity hypoventilation, no PAP titration, no REM behavior disorder |
| **Procedures (Intern Manual)** | Central line, A-line, chest tube, NGT, paracentesis | Lumbar puncture, thoracentesis technique, intubation checklist, dialysis catheter, bronchoscopy consent |

### Structural Gaps (Organizational)

| Gap | Description |
|---|---|
| **No Pharmacology section** | Drug notes scattered across conditions; no unified reference for sedatives, antibiotics, anticoagulants |
| **No Ethics / Legal section** | Brain death protocol exists but no surrogate decision-making, informed consent, capacity assessment |
| **No Quality / Safety section** | No VAP/CLABSI bundles, handoff protocols, ICU liberation (ABCDEF bundle) |
| **Intern Manual under-linked** | 17% wiki-link rate — procedures would benefit from linking to indications in Critical Care notes |

---

## Hub Notes (Most Connected)

These are the structural backbone of the vault — the notes with the most inbound + outbound links.

| Note | Inbound | Outbound | Total | Role |
|------|--------:|---------:|------:|------|
| Mechanical Ventilation | 8 | 17 | 25 | **Central hub** for airway/vent cluster |
| Undifferentiated Shock | 8 | 7 | 15 | **Central hub** for shock/hemodynamics |
| Right Heart Cath Swan Ganz | 5 | 13 | 18 | **Reference hub** for hemodynamic data |
| Blood Products | 7 | 4 | 11 | **Bridge** between transfusion and shock |
| ARDS | 5 | 7 | 12 | **Bridge** between vent and lung rescue |
| Vasopressors | 6 | 3 | 9 | **Bridge** between shock and pharmacology |
| Endotracheal Intubation | 6 | 4 | 10 | **Gateway** from procedures to vent management |
| Obstructive Lung Disease | 3 | 16 | 19 | **Index note** for obstructive pulmonary |
| Spirometry | 3 | 12 | 15 | **Foundational** for all pulmonary assessment |
| Critical Care | 7 | 2 | 9 | **Landing page** for ICU section |

---

## Recommended Next Steps

1. **Fill critical gaps**: Start with Toxicology (5-10 notes), Endocrine emergencies (4-5 notes), and Hematology/coagulopathy (5-6 notes)
2. **Expand POCUS**: Add lung ultrasound, IVC, DVT, and RUSH exam notes — high-yield for ICU practice
3. **Link the Intern Manual**: Cross-reference procedures to their Critical Care indications
4. **Add arrhythmia management**: SVT, VT/VF, post-arrest care, and antiarrhythmics
5. **Build a Pharmacology index**: Consolidate scattered drug notes into a unified section or index note
