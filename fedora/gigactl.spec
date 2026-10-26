# gigactl on Fedora: same private-tree layout as the Debian package
# (/usr/lib/gigactl, debian/install), minus the ec_sys module files —
# Fedora's stock kernel builds ACPI EC in without the debugfs interface
# (CONFIG_ACPI_EC_DEBUGFS unset). The daemon fails closed when ec_sys is
# absent; raw /dev/port access is never an automatic fallback and requires
# an explicit administrator --allow-raw-ec / GIGACTL_ALLOW_RAW_EC=1 opt-in,
# which this package's unit deliberately does not set.
Name:           gigactl
Version:        1.2.0
Release:        1%{?dist}
Summary:        Fan and RGB keyboard control for Gigabyte G5/G6 laptops
License:        MIT
URL:            https://github.com/smairio/gigactl
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  desktop-file-utils
BuildRequires:  systemd-rpm-macros
BuildRequires:  make
BuildRequires:  selinux-policy-devel

Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita
Requires:       dbus
Requires:       polkit
Requires:       systemd
# Scriptlets use semodule/restorecon, not just build-time policy tools.
Requires(pre):  systemd
Requires(pre):  coreutils
Requires(post): policycoreutils
Requires(post): grep
Requires(postun): policycoreutils
%{?systemd_requires}

%description
GigaControl gives Gigabyte G-series gaming laptops the fan and RGB
keyboard control their firmware keeps to itself: fan profiles and
editable custom curves, backlight colour and brightness, and live
temperature and RPM readings. A small root daemon owns Embedded
Controller access and exposes it on the D-Bus system bus, authorising
writes through polkit. Verified on Gigabyte G6 KF and G5 GE.

%prep
%autosetup

%build
# Icons ship as SVG only (the Debian build rasterises with rsvg-convert;
# scalable + symbolic SVG is what KDE/GNOME render from anyway).
cp fedora/selinux/gigactld.te fedora/selinux/gigactld.fc .
make -f /usr/share/selinux/devel/Makefile gigactld.pp

%install
PRIV=%{buildroot}%{_prefix}/lib/gigactl
install -d $PRIV/gigactld $PRIV/gigactl_gui
install -pm 0644 daemon/gigactld/*.py $PRIV/gigactld/
install -pm 0644 gui/gigactl_gui/*.py $PRIV/gigactl_gui/
install -pm 0755 debian/bin/launcher $PRIV/launcher
install -pm 0755 fedora/gigactld-launcher $PRIV/gigactld-launcher
install -pm 0644 gigactl-cli-lib.sh $PRIV/
install -d %{buildroot}%{_bindir}
install -pm 0755 gfan gkbd %{buildroot}%{_bindir}/
ln -s ../lib/gigactl/gigactld-launcher %{buildroot}%{_bindir}/gigactld
ln -s ../lib/gigactl/launcher %{buildroot}%{_bindir}/gigactl-gui
install -d %{buildroot}%{_unitdir}
install -pm 0644 daemon/data/gigactl.service %{buildroot}%{_unitdir}/
install -d %{buildroot}%{_datadir}/dbus-1/system.d
install -pm 0644 daemon/data/io.github.smairio.gigactl.conf %{buildroot}%{_datadir}/dbus-1/system.d/
install -d %{buildroot}%{_datadir}/polkit-1/actions
install -pm 0644 daemon/data/io.github.smairio.gigactl.policy %{buildroot}%{_datadir}/polkit-1/actions/
install -d %{buildroot}%{_datadir}/applications
install -pm 0644 gui/data/io.github.smairio.gigactl.desktop %{buildroot}%{_datadir}/applications/
install -d %{buildroot}%{_sysconfdir}/xdg/autostart
install -pm 0644 gui/data/gigactl-tray.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/
install -d %{buildroot}%{_datadir}/icons/hicolor/scalable/apps \
           %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps
install -pm 0644 design/icon.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.smairio.gigactl.svg
install -pm 0644 design/icon-symbolic.svg %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/io.github.smairio.gigactl-symbolic.svg
install -d -m 0755 %{buildroot}/var/lib/gigactl
install -d %{buildroot}%{_datadir}/selinux/packages
install -pm 0644 gigactld.pp %{buildroot}%{_datadir}/selinux/packages/
install -d %{buildroot}%{_mandir}/man1 %{buildroot}%{_mandir}/man8
install -pm 0644 man/gfan.1 man/gkbd.1 man/gigactl-gui.1 %{buildroot}%{_mandir}/man1/
install -pm 0644 man/gigactld.8 %{buildroot}%{_mandir}/man8/

%check
desktop-file-validate gui/data/*.desktop

%pre
set -e
# Do not revoke an old init_t daemon's EC permissions while it is controlling
# fans, or replace its entry point before its cleanup has finished. Require an
# administrator to stop it and check firmware handback first. A status query is
# read-only: this scriptlet never starts/stops/restarts hardware control.
if [ -d /run/systemd/system ]; then
  if ! UNITS=$(timeout 5s systemctl list-units --all --no-legend --no-pager gigactl.service); then
    echo "gigactl: cannot determine service state; stop gigactl.service and verify firmware handback before installing" >&2
    exit 1
  fi
  # An absent/unloaded unit cannot be active (normal first installation). Only
  # query ActiveState for an existing unit, not an unknown unit's show exit code.
  if [ -n "$UNITS" ]; then
    if ! STATE=$(timeout 5s systemctl show --property=ActiveState --value gigactl.service); then
      echo "gigactl: cannot determine service state; stop gigactl.service and verify firmware handback before installing" >&2
      exit 1
    fi
    case "$STATE" in
      inactive|failed) ;;
      *)
        echo "gigactl: stop gigactl.service and verify firmware handback before installing/upgrading" >&2
        exit 1
        ;;
    esac
  fi
fi

%post
# A failed policy install must be visible and must not silently fall back to the
# old broad init_t permission. Install the replacement before removing the old
# module; a failed removal is likewise an upgrade failure, not an ignored error.
set -e
semodule -i %{_datadir}/selinux/packages/gigactld.pp
MODULES=$(semodule -l)
if printf '%s\n' "$MODULES" | grep -q '^gigactl-devport[[:space:]]'; then
  semodule -r gigactl-devport
fi
restorecon -F /usr/lib/gigactl/gigactld-launcher
restorecon -RF /var/lib/gigactl
# Runtime paths may not exist before the first start. Never create/unlink locks
# in a scriptlet; relabel only existing paths and preserve their inode.
for runtime in /run/gigactl /run/lock/gigactl-ec.lock; do
  if [ -e "$runtime" ]; then
    restorecon -RF "$runtime"
  fi
done
%systemd_post gigactl.service

%preun
%systemd_preun gigactl.service

%postun
set -e
# Do not automatically restart hardware control on upgrade. The administrator
# chooses when to start the guarded daemon; systemd still reloads unit metadata.
%systemd_postun gigactl.service
if [ $1 -eq 0 ]; then
  semodule -r gigactld
fi

%files
%license LICENSE
%doc README.md docs/PROTOCOL.md
%{_prefix}/lib/gigactl/
%{_bindir}/gfan
%{_bindir}/gkbd
%{_bindir}/gigactld
%{_bindir}/gigactl-gui
%dir /var/lib/gigactl
%{_unitdir}/gigactl.service
%{_datadir}/selinux/packages/gigactld.pp
%{_datadir}/dbus-1/system.d/io.github.smairio.gigactl.conf
%{_datadir}/polkit-1/actions/io.github.smairio.gigactl.policy
%{_datadir}/applications/io.github.smairio.gigactl.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/gigactl-tray.desktop
%{_datadir}/icons/hicolor/*/apps/io.github.smairio.gigactl*
%{_mandir}/man1/gfan.1*
%{_mandir}/man1/gkbd.1*
%{_mandir}/man1/gigactl-gui.1*
%{_mandir}/man8/gigactld.8*

%changelog
* Mon Oct 26 2026 gigactl contributors - 1.2.0-1
- M1 privileged safety foundation (software-tested, not hardware-
  validated): controller lease, exact DMI allowlist, opt-in raw EC,
  thermal supervision, duty reconciliation, per-fan handback retry,
  async polkit, control-loop watchdog, suspend/resume reconciliation.
- Dedicated gigactld_t SELinux domain, isolated root-only launcher,
  hardened unit, clean-committed-source builds.
* Mon Oct 26 2026 gigactl contributors - 1.1.0-1
- Fedora package. Fedora kernels ship no ec_sys debugfs; without it the
  daemon fails closed, because raw /dev/port access requires an explicit
  administrator opt-in that this package does not enable. G5 GE fan and
  keyboard control verified. CPU turbo boost toggle; daemon fan failsafe
  watchdog.
