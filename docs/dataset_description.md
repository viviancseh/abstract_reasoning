# Human Neurocognition During Abstract Reasoning: EEG and Eye-Tracking Dataset

## Study Overview

This dataset contains electroencephalography (EEG) recording, eye-tracking recording, and behavioral data from human participants performing an abstract reasoning task (visual analogy problem-solving). The original study investigated the alignment between Large Language Models (LLMs) and the human neural responses during abstract pattern completion and reasoning.

### Primary Research Question

To what extent do LLM representations reflect the neural mechanisms underlying human abstract reasoning?

### Reference Publications

**Main preprint:**

Pinier, C., Vargas, S. A., Steeghs-Turchina, M., Matzke, D., Stevenson, C. E., & Nunez, M. D. (2025). [Large Language Models Show Signs of Alignment with Human Neurocognition During Abstract Reasoning.](https://arxiv.org/abs/2508.10057) *arXiv preprint arXiv:2508.10057*.

**Conference publications:**

- Pinier, C., Vargas, S. A., Steeghs-Turchina, M., Matzke, D., Stevenson, C. E., & Nunez, M. (2026, March 6). Large language models show signs of alignment with human neurocognition during abstract reasoning [Poster session]. *ICLR 2026 Workshop - From Human Cognition to AI Reasoning: Models, Methods, and Applications*. [PDF](https://openreview.net/pdf?id=gyoSN8k9vR)

- Pinier, C., Stevenson, C. E., & Nunez, M. D. (2025). Moderate evidence for large language models reflecting human neurocognition during abstract reasoning [Poster session]. *Cognitive Computational Neuroscience (CCN) 2025*, Amsterdam, Netherlands.

---

## Dataset Description

### Participants

- **Number of participants:** 25
- **Recruitment:** University of Amsterdam community
- **Inclusion criteria:** Native or fluent English speakers, normal or corrected-to-normal vision
- **Sessions:** Multiple sessions per participant (1-5 sessions)

### Experimental Task

Participants completed an **abstract visual reasoning task** involving pattern completion and analogy problems. 

#### Task Structure

- **"Encoding phase" w/ individual icons of both pattern and response options flashing individually:** 600 ms
- **"Decision phase" w/ pattern and response options displayed w/ maximum response time:** 12 seconds
- **Number of sequences per session:** 80

#### Stimuli

**Pattern types:** 8 different abstract visual patterns representing varying levels of relational complexity:

- AAABAAAB, ABABCDCD, ABBAABBA, ABBACDDC, ABBCABBC, ABCAABCA, ABCDDCBA, ABCDEEDC

**Display:** During the "Decision phase", the final icon in the sequence was replaced by a question mark. Four response icons were also displayed.

---

## Data Collection

### EEG Recording

- **Electrode montage:** BioSemi 64-channel standard montage
- **Sampling rate:** 2048 Hz
- **Additional channels:** 
  - EOG (4 channels): EOGL, EOGR, EOGT, EOGB
  - Stimulus trigger channel: Status
- **Recording type:** Continuous
- **Power line frequency:** 50 Hz

### Eye-Tracking Recording

- **Sampling rate:** 2000 Hz
- **Device:** EyeLink 1000 Plus

### Behavioral Data

Raw behavioral responses stored in TSV format including trial number, stimulus pattern ID, participant response, accuracy, and response time.

## Electrode Positions

**Note on electrode coordinates:** The standard BioSemi 64-channel montage does not include recorded electrode positions, as this study used a standard template montage (not subject-specific digitization). Electrode locations follow the standard 10-20 positioning system for BioSemi 64-channel caps.

---

## Ethics and Study Approval

This research project complies with the guidelines formulated by the **Ethics Review Board (FMG-UvA), University of Amsterdam, The Netherlands**, and has been approved by the aforementioned Ethics Review Board on **19-06-2024**.

---

## License

This dataset is made available under the [Creative Commons Attribution 4.0 License (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

### How to Cite This Dataset

**Associated Publication:**
Pinier, C., Vargas, S. A., Steeghs-Turchina, M., Matzke, D., Stevenson, C. E., & Nunez, M. D. (2025). Large Language Models Show Signs of Alignment with Human Neurocognition During Abstract Reasoning. *arXiv preprint arXiv:2508.10057*.

---

## Preprocessing Notes

This dataset contains **minimally preprocessed** raw EEG data:

- ✓ Bad channels identified based on visual inspection
- ✓ Trigger channel identified and annotated
- ✗ No rereferencing (e.g. not yet rereferenced to average)
- ✗ No filtering applied
- ✗ No ICA artifact correction applied
- ✗ No epoching applied

---

## Data Access and Use

This dataset is intended for research purposes.

---

## Code Availability

Code for data collection and analysis is available at [https://github.com/chris-pinier/abstract_reasoning](https://github.com/chris-pinier/abstract_reasoning)

---

## Contact

For inquiries regarding this dataset, please contact:

**Michael D. Nunez**  
University of Amsterdam
m.d.nunez@uva.nl

**OR**

**Christopher Pinier**  
University of Amsterdam
c.pinier@uva.nl
