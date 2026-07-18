#!/usr/bin/env bash
# deploy/fetch_chips.sh <chips.lock> <chips-data-dir>
# Download the chip pack pinned by chips.lock into <dir>/<tag>/chips/, verify sha256,
# then flip <dir>/current. Outbound-only, idempotent, atomic-ish (staging dir + rename).
# Exit 0 on success or already-current; non-zero on failure (caller treats as non-fatal).
set -euo pipefail

LOCK="$1"; DIR="$2"
command -v curl >/dev/null && command -v sha256sum >/dev/null && command -v tar >/dev/null

TAG=$(awk '$1=="tag"{print $2; exit}' "$LOCK")
BASE=$(awk '$1=="base"{print $2; exit}' "$LOCK")
[ -n "$TAG" ] && [ -n "$BASE" ] || { echo "chips: bad lock"; exit 1; }

if [ -f "$DIR/$TAG/.complete" ]; then
  echo "$TAG" > "$DIR/current"
  echo "chips: $TAG already present"; exit 0
fi

STAGE="$DIR/.stage-$TAG"
rm -rf "$STAGE"; mkdir -p "$STAGE/chips"

# lines after the two headers are "sha256  filename"
tail -n +3 "$LOCK" > "$STAGE/sums.txt"
while read -r _sha name; do
  [ -n "$name" ] || continue
  echo "chips: fetching $name"
  curl -fsSL --retry 3 --retry-delay 5 -o "$STAGE/$name" "$BASE/$name"
done < "$STAGE/sums.txt"
(cd "$STAGE" && sha256sum -c sums.txt --quiet)

for t in "$STAGE"/chips-*.tar; do
  [ -e "$t" ] || continue
  # --force-local: on Windows/Git-Bash a "C:/..." target path looks like a "host:path"
  # remote-tar spec to GNU tar; --force-local forces local-file semantics. Harmless on
  # the Pi (Debian paths never contain a colon). Split from `&& rm` on purpose: under
  # `set -e`, a failure in the non-last command of an `A && B` list does NOT trigger
  # errexit (POSIX/bash quirk), so a failed extraction would silently fall through.
  tar -xf "$t" -C "$STAGE/chips" --force-local
  rm "$t"
done
mv "$STAGE/chips-manifest.json" "$STAGE/chips/manifest.json"
rm "$STAGE/sums.txt"

rm -rf "$DIR/$TAG"
mv "$STAGE" "$DIR/$TAG"
touch "$DIR/$TAG/.complete"
PREV=$(cat "$DIR/current" 2>/dev/null || true)
echo "$TAG" > "$DIR/current"
# retain only the previous tag; drop anything older
for d in "$DIR"/chips-v*; do
  [ -e "$d" ] || continue
  b=$(basename "$d")
  [ "$b" = "$TAG" ] || [ "$b" = "$PREV" ] || rm -rf "$d"
done
echo "chips: $TAG deployed"
