#!/usr/bin/env bash
# Génère openclaw-skills-configmap.yaml à partir de skills/<nom>/SKILL.md
# Usage : depuis apps/openclaw : ./scripts/build-skills-configmap.sh
# Ou : depuis la racine du repo : apps/openclaw/scripts/build-skills-configmap.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILLS_DIR="$OPENCLAW_DIR/skills"
OUTPUT_FILE="$OPENCLAW_DIR/openclaw-skills-configmap.yaml"

cd "$OPENCLAW_DIR"

cat > "$OUTPUT_FILE" << 'HEADER'
# ConfigMap skills OpenClaw - généré par scripts/build-skills-configmap.sh
# Source : skills/<nom>/SKILL.md — ne pas éditer ce fichier à la main, relancer le script.
apiVersion: v1
kind: ConfigMap
metadata:
  name: openclaw-skills
  namespace: openclaw
data:
HEADER

for skill_dir in "$SKILLS_DIR"/*/; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  skill_file="$skill_dir/SKILL.md"
  [ -f "$skill_file" ] || continue
  echo "  $skill_name/SKILL.md: |" >> "$OUTPUT_FILE"
  while IFS= read -r line; do
    printf '    %s\n' "$line" >> "$OUTPUT_FILE"
  done < "$skill_file"
  echo "" >> "$OUTPUT_FILE"
done

echo "Écrit : $OUTPUT_FILE"
