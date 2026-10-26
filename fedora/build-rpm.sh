#!/bin/bash
# Build the Fedora RPM from this checkout into fedora/RPMS/.
# Usage: ./fedora/build-rpm.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^Version:[[:space:]]*//p' fedora/gigactl.spec)
TOP=$PWD/fedora/rpmbuild
mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS} fedora/RPMS
git archive --format=tar.gz --prefix="gigactl-$VERSION/" \
  -o "$TOP/SOURCES/gigactl-$VERSION.tar.gz" HEAD
# Uncommitted changes are part of a developer build; overlay them so the
# tarball matches the working tree the packager is looking at.
TMP=$(mktemp -d)
tar -xzf "$TOP/SOURCES/gigactl-$VERSION.tar.gz" -C "$TMP"
git ls-files -m -o --exclude-standard -z | while IFS= read -r -d '' f; do
  [ -f "$f" ] && install -D -m 0644 "$f" "$TMP/gigactl-$VERSION/$f"
done
tar -czf "$TOP/SOURCES/gigactl-$VERSION.tar.gz" -C "$TMP" "gigactl-$VERSION"
rm -rf "$TMP"
rpmbuild --define "_topdir $TOP" -ba fedora/gigactl.spec
find "$TOP/RPMS" "$TOP/SRPMS" -name '*.rpm' -exec cp -v {} fedora/RPMS/ \;
echo "RPMs in fedora/RPMS/"
