# gigactl on Fedora: same private-tree layout as the Debian package
# (/usr/lib/gigactl, debian/install), minus the ec_sys module files —
# Fedora's stock kernel builds ACPI EC in without the debugfs interface
# (CONFIG_ACPI_EC_DEBUGFS unset), so the daemon uses its /dev/port
# backend there and the modprobe/modules-load files would be inert.
Name:           gigactl
Version:        1.0.2
Release:        1%{?dist}
Summary:        Fan and RGB keyboard control for Gigabyte G5/G6 laptops
License:        MIT
URL:            https://github.com/smairio/gigactl
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  desktop-file-utils
BuildRequires:  systemd-rpm-macros
BuildRequires:  checkpolicy
BuildRequires:  policycoreutils

Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita
Requires:       dbus
Requires:       polkit
Requires:       systemd

%description
GigaControl gives Gigabyte G-series gaming laptops the fan and RGB
keyboard control their firmware keeps to itself: fan profiles and
editable custom curves, backlight colour and brightness, and live
temperature and RPM readings. A small root daemon owns Embedded
Controller access and exposes it on the D-Bus system bus, authorising
writes through polkit. Verified on Gigabyte G6 KF.

%prep
%autosetup

%build
# Icons ship as SVG only (the Debian build rasterises with rsvg-convert;
# scalable + symbolic SVG is what KDE/GNOME render from anyway).
checkmodule -M -m -o gigactl-devport.mod fedora/selinux/gigactl-devport.te
semodule_package -o gigactl-devport.pp -m gigactl-devport.mod

%install
PRIV=%{buildroot}%{_prefix}/lib/gigactl
install -d $PRIV/gigactld $PRIV/gigactl_gui
install -pm 0644 daemon/gigactld/*.py $PRIV/gigactld/
install -pm 0644 gui/gigactl_gui/*.py $PRIV/gigactl_gui/
install -pm 0755 debian/bin/launcher $PRIV/launcher
install -pm 0644 gigactl-cli-lib.sh $PRIV/
install -d %{buildroot}%{_bindir}
install -pm 0755 gfan gkbd %{buildroot}%{_bindir}/
ln -s ../lib/gigactl/launcher %{buildroot}%{_bindir}/gigactld
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
install -d %{buildroot}%{_datadir}/selinux/packages
install -pm 0644 gigactl-devport.pp %{buildroot}%{_datadir}/selinux/packages/
install -d %{buildroot}%{_mandir}/man1 %{buildroot}%{_mandir}/man8
install -pm 0644 man/gfan.1 man/gkbd.1 man/gigactl-gui.1 %{buildroot}%{_mandir}/man1/
install -pm 0644 man/gigactld.8 %{buildroot}%{_mandir}/man8/

%check
desktop-file-validate gui/data/*.desktop

%post
%systemd_post gigactl.service
# Load before the daemon (re)starts so its first /dev/port open lands.
semodule -i %{_datadir}/selinux/packages/gigactl-devport.pp 2>/dev/null || :

%preun
%systemd_preun gigactl.service

%postun
%systemd_postun_with_restart gigactl.service
if [ $1 -eq 0 ]; then
  semodule -r gigactl-devport 2>/dev/null || :
fi

%files
%license LICENSE
%doc README.md docs/PROTOCOL.md
%{_prefix}/lib/gigactl/
%{_bindir}/gfan
%{_bindir}/gkbd
%{_bindir}/gigactld
%{_bindir}/gigactl-gui
%{_unitdir}/gigactl.service
%{_datadir}/selinux/packages/gigactl-devport.pp
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
* Mon Oct 26 2026 gigactl contributors - 1.0.2-1
- Fedora package. Daemon uses the /dev/port EC backend (Fedora kernels
  ship no ec_sys debugfs), with a shipped SELinux policy module.
