# RuleForge report: `CP-RAD-014`

- Rules compiled: **6** (valid after 2 attempt(s))
- MC/DC coverage: **9/9 conditions (100%)**
- Generated test cases: **16**
- Mutation score: **29/29 non-equivalent mutants killed (100%)**, 1 proven equivalent

## Static analysis

### Unresolved Conflict (3)

- **R2 vs R5**: R2 -> PEND_REVIEW and R5 -> DENY both fire at priority 10; outcome depends on rule order
  - witness claim: `member_age=0`, `diagnosis_codes=[]`, `conservative_therapy_weeks=0`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=240`, `new_neuro_deficit=False`, `provider_network=IN`
- **R4 vs R6**: R4 -> APPROVE and R6 -> PEND_REVIEW both fire at priority 10; outcome depends on rule order
  - witness claim: `member_age=18`, `diagnosis_codes=[]`, `conservative_therapy_weeks=6`, `symptom_duration_weeks=6`, `months_since_last_lumbar_mri=240`, `new_neuro_deficit=False`, `provider_network=OUT`
- **R5 vs R6**: R5 -> DENY and R6 -> PEND_REVIEW both fire at priority 10; outcome depends on rule order
  - witness claim: `member_age=45`, `diagnosis_codes=[]`, `conservative_therapy_weeks=0`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=240`, `new_neuro_deficit=False`, `provider_network=OUT`

### Resolved By Priority (7)

- **R1 vs R2**: R1 (APPROVE, p=100) overrides R2 (PEND_REVIEW, p=10)
  - witness claim: `member_age=0`, `diagnosis_codes=['G83.4']`, `conservative_therapy_weeks=8`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=240`, `new_neuro_deficit=False`, `provider_network=IN`
- **R1 vs R3**: R1 (APPROVE, p=100) overrides R3 (DENY, p=50)
  - witness claim: `member_age=45`, `diagnosis_codes=['G83.4']`, `conservative_therapy_weeks=8`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=0`, `new_neuro_deficit=False`, `provider_network=IN`
- **R1 vs R5**: R1 (APPROVE, p=100) overrides R5 (DENY, p=10)
  - witness claim: `member_age=45`, `diagnosis_codes=['G83.4']`, `conservative_therapy_weeks=0`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=240`, `new_neuro_deficit=False`, `provider_network=IN`
- **R1 vs R6**: R1 (APPROVE, p=100) overrides R6 (PEND_REVIEW, p=10)
  - witness claim: `member_age=45`, `diagnosis_codes=['G83.4']`, `conservative_therapy_weeks=8`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=240`, `new_neuro_deficit=False`, `provider_network=OUT`
- **R3 vs R2**: R3 (DENY, p=50) overrides R2 (PEND_REVIEW, p=10)
  - witness claim: `member_age=0`, `diagnosis_codes=[]`, `conservative_therapy_weeks=8`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=0`, `new_neuro_deficit=False`, `provider_network=IN`
- **R3 vs R4**: R3 (DENY, p=50) overrides R4 (APPROVE, p=10)
  - witness claim: `member_age=18`, `diagnosis_codes=[]`, `conservative_therapy_weeks=6`, `symptom_duration_weeks=6`, `months_since_last_lumbar_mri=0`, `new_neuro_deficit=False`, `provider_network=IN`
- **R3 vs R6**: R3 (DENY, p=50) overrides R6 (PEND_REVIEW, p=10)
  - witness claim: `member_age=45`, `diagnosis_codes=[]`, `conservative_therapy_weeks=8`, `symptom_duration_weeks=10`, `months_since_last_lumbar_mri=0`, `new_neuro_deficit=False`, `provider_network=OUT`

## Mutation testing

- equivalent: `R4: member_age >= 18 -> >= 17` (no claim can tell it apart from the original)

## Rules

| id | priority | decision | reason | source |
|---|---|---|---|---|
| R1 | 100 | APPROVE | URGENT_INDICATION | MRI of the lumbar spine is approved without further review when the re… |
| R3 | 50 | DENY | REPEAT_IMAGING_NOT_INDICATED | A request is denied when the member had a lumbar MRI within the past 1… |
| R2 | 10 | PEND_REVIEW | PEDIATRIC_RADIOLOGY_REVIEW | Requests for members under 18 years of age are pended for review by a … |
| R4 | 10 | APPROVE | ROUTINE_CRITERIA_MET | For non-urgent low back pain, MRI is approved when the member is 18 or… |
| R5 | 10 | DENY | CONSERVATIVE_THERAPY_INSUFFICIENT | Requests where the member has completed fewer than 6 weeks of conserva… |
| R6 | 10 | PEND_REVIEW | NETWORK_REVIEW | Requests from out-of-network ordering providers are pended for network… |
