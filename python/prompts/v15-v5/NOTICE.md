# v15-v5 prompt assets

Source image: `ghcr.io/yc-software/paxel-client:sha-07dc3b878811`
App version: `0.3.48.2` (built 2026-07-02)
Extracted: 2026-10-21

| file | origin |
| --- | --- |
| `scoring_v15.txt` | `V3ScoringPromptBuilder.build_episode_prompt(nil)` |
| `narrative_v5.txt` | `SessionNarrativeAnalyzer::SYSTEM_PROMPT` |
| `narrative_merge_v5.txt` | `SessionNarrativeAnalyzer::MERGE_SYSTEM_PROMPT` |
| `decision_v5.txt` | `DecisionClassifier#classification_system_prompt` (includes `DecisionLawCatalog.compact_prompt`) |

These are proprietary prompt texts taken from a public container image. A
public image is not a license to redistribute its contents: the `.txt` files
are intentionally git-ignored, and this repository does not publish them.
Regenerate them locally with:

```bash
bash tools/extract-v15-prompts.sh
```

Scope: 0.3.48.2 is the newest *public* client image that still ships prompt
text. The live `0.3.83.2` image (`latest`) only carries prompt ids
(`episode_scoring v16`, `session_narrative v6`, `decision_classifier v5`);
the prompt bodies are injected server-side and are not in any public tag.
This profile is therefore a **v15/v5 baseline**, not current production
parity. The person-level synthesis prompt is not public either; both profiles
keep the independent local person prompt.

Use for local research only. Do not publish these files, and check the
provider's terms before redistributing any extracted text.

Language: the v15/v5 prompts are English-native. Intermediate narratives and
episode facts/scores are therefore English even though this repository's
report UI and local person-synthesis prompt are Chinese. That is faithful to
the client prompts; add a language override only if you deliberately want to
deviate from the extracted baseline.
