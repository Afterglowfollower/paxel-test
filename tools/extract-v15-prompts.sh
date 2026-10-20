#!/usr/bin/env bash
# Regenerate the v15-v5 prompt profile from the newest public Paxel client
# image that still ships prompt text.
#
#   docker pull ghcr.io/yc-software/paxel-client:sha-07dc3b878811  (0.3.48.2)
#   extract v15 episode / v5 narrative / v5 merge / v5 decision prompts
#
# The generated .txt files are git-ignored; see python/prompts/v15-v5/NOTICE.md.
set -euo pipefail

IMAGE="${PAXEL_IMAGE:-ghcr.io/yc-software/paxel-client:sha-07dc3b878811}"
EXPECTED_VERSION="0.3.48.2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/python/prompts/v15-v5"
TMP="$(mktemp -d)"
CONTAINER="paxel-v15-extract-$$"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
command -v ruby >/dev/null 2>&1 || { echo "ruby (>= 2.6) is required" >&2; exit 1; }

mkdir -p "$OUT" "$TMP/src" "$TMP/root/db"

echo "Pulling $IMAGE (linux/amd64)..."
docker pull --platform linux/amd64 "$IMAGE" >/dev/null
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker create --name "$CONTAINER" --platform linux/amd64 "$IMAGE" >/dev/null

VERSION="$(docker run --rm --platform linux/amd64 --entrypoint cat "$IMAGE" /rails/VERSION)"
if [ "$VERSION" != "$EXPECTED_VERSION" ]; then
  echo "unexpected app version: $VERSION (expected $EXPECTED_VERSION)" >&2
  exit 1
fi

for path in \
  app/services/v3_scoring_prompt_builder.rb \
  app/services/session_narrative_analyzer.rb \
  app/services/voice_guidelines.rb \
  app/services/decision_classifier.rb \
  app/services/decision_law_catalog.rb \
  db/decision_catalog.json; do
  docker cp "$CONTAINER:/rails/$path" "$TMP/src/$(basename "$path")" >/dev/null
done
cp "$TMP/src/decision_catalog.json" "$TMP/root/db/decision_catalog.json"

PAXEL_SRC="$TMP/src" ruby - <<'RUBY' > "$OUT/scoring_v15.txt"
load File.join(ENV.fetch("PAXEL_SRC"), "v3_scoring_prompt_builder.rb")
print V3ScoringPromptBuilder.build_episode_prompt(nil)
RUBY

PAXEL_SRC="$TMP/src" ruby - <<'RUBY' > "$OUT/narrative_v5.txt"
load File.join(ENV.fetch("PAXEL_SRC"), "voice_guidelines.rb")
module AnthropicClient; end
module JsonExtractor; end
module Rails; end
load File.join(ENV.fetch("PAXEL_SRC"), "session_narrative_analyzer.rb")
print SessionNarrativeAnalyzer::SYSTEM_PROMPT
RUBY

PAXEL_SRC="$TMP/src" ruby - <<'RUBY' > "$OUT/narrative_merge_v5.txt"
load File.join(ENV.fetch("PAXEL_SRC"), "voice_guidelines.rb")
module AnthropicClient; end
module JsonExtractor; end
module Rails; end
load File.join(ENV.fetch("PAXEL_SRC"), "session_narrative_analyzer.rb")
print SessionNarrativeAnalyzer::MERGE_SYSTEM_PROMPT
RUBY

PAXEL_SRC="$TMP/src" PAXEL_ROOT="$TMP/root" ruby - <<'RUBY' > "$OUT/decision_v5.txt"
require "pathname"
require "json"
class Time; def self.current; now; end; end
module Rails
  def self.root; Pathname.new(ENV.fetch("PAXEL_ROOT")); end
  def self.logger; Object.new.tap { |o| def o.warn(*); end }; end
  def self.application
    cfg = Object.new
    def cfg.cache_classes; true; end
    app = Object.new
    app.define_singleton_method(:config) { cfg }
    app
  end
end
load File.join(ENV.fetch("PAXEL_SRC"), "decision_law_catalog.rb")
module AnthropicClient
  MODELS = { fast: "stub" }.freeze
end
module TranscriptPatterns; end
load File.join(ENV.fetch("PAXEL_SRC"), "decision_classifier.rb")
print DecisionClassifier.new(upload: nil).send(:classification_system_prompt)
RUBY

for name in scoring_v15 narrative_v5 narrative_merge_v5 decision_v5; do
  [ -s "$OUT/$name.txt" ] || { echo "empty prompt: $name.txt" >&2; exit 1; }
done
grep -q "You are scoring" "$OUT/scoring_v15.txt" || { echo "unexpected scoring prompt" >&2; exit 1; }
grep -q "session_intent" "$OUT/narrative_v5.txt" || { echo "unexpected narrative prompt" >&2; exit 1; }
grep -q "Laws Catalog" "$OUT/decision_v5.txt" || { echo "unexpected decision prompt" >&2; exit 1; }

echo "Wrote prompt profile $VERSION to $OUT:"
wc -c "$OUT"/*.txt
