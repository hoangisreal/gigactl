#!/bin/bash
# Build only committed, clean source into fedora/RPMS/.
# Usage: ./fedora/build-rpm.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$#" -ne 0 ]; then
  echo "Usage: ./fedora/build-rpm.sh (clean committed source only)" >&2
  exit 1
fi
SOURCE_COMMIT=$(git rev-parse --verify 'HEAD^{commit}')
STATUS=$(git status --porcelain=v1 --untracked-files=all --ignore-submodules=none)
if [ -n "$STATUS" ]; then
  echo "gigactl: refusing to build dirty source (staged, modified, or untracked files)." >&2
  echo "Commit the intended source in a clean checkout; ignored build products are OK." >&2
  printf '%s\n' "$STATUS" >&2
  exit 1
fi

# Neither the spec nor the archive is read from the mutable working tree after
# the clean check. git archive retains committed executable bits, symlinks and
# manifests; ignored RPMs, caches and arbitrary local files never get overlaid.
SPEC=$(git show "$SOURCE_COMMIT:fedora/gigactl.spec")
VERSION=$(sed -n 's/^Version:[[:space:]]*//p' <<< "$SPEC")
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "gigactl: invalid committed spec version: $VERSION" >&2
  exit 1
fi

# Each build gets a fresh topdir: a failed build cannot reuse stale RPMs. The
# cleanup trap owns precisely this mktemp directory, never the checkout/output.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/gigactl-rpm.XXXXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
TOP=$WORK/rpmbuild
mkdir -p "$TOP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
git show "$SOURCE_COMMIT:fedora/gigactl.spec" > "$TOP/SPECS/gigactl.spec"
git archive --format=tar.gz --prefix="gigactl-$VERSION/" \
  -o "$TOP/SOURCES/gigactl-$VERSION.tar.gz" "$SOURCE_COMMIT"
rpmbuild --define "_topdir $TOP" -ba "$TOP/SPECS/gigactl.spec"
mkdir -p fedora/RPMS
find "$TOP/RPMS" "$TOP/SRPMS" -type f -name '*.rpm' \
  -exec cp -pv -- {} fedora/RPMS/ \;
echo "RPMs in fedora/RPMS/"
