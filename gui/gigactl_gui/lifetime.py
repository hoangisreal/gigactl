"""When a trayed app should stay alive, and when it owes the user a window.

These are plain functions because they are the easy thing to get wrong and the
hard thing to observe. GApplication stops the moment nothing holds it, so a
tray-only launch that forgets to hold dies before its tray can answer; hold
forever with no icon and no window and you have a process the user can neither
see nor reach.

The distinction that matters most is **never answered** versus **went away**. A
desktop with no tray host at all should get a window, because otherwise the user
launched something and got nothing. But a host that answered once and then
disappeared is usually temporary — GNOME disables extensions at the lock screen,
and a shell restart drops the watcher for a moment — so the app waits quietly to
re-register rather than shoving a window over whatever is on screen. A host that
answers *late* — after the grace timer already offered a window — gets that
window back: it went up unasked, so hiding it again restores what ``--tray``
promised, while a window the user opened themselves is never taken away.
"""
from __future__ import annotations


def should_hold(*, tray_available: bool, waiting_for_tray: bool,
                window_visible: bool) -> bool:
    """Whether to keep GApplication alive by hand.

    Held while a tray icon is showing (there is a way back), while still waiting
    to learn whether one will appear, and while nothing is on screen at all — a
    trayed app whose host vanished has to survive to re-register. A visible window
    needs no hold: it is its own.
    """
    return tray_available or waiting_for_tray or not window_visible


def should_offer_window(*, tray_ever_registered: bool, tray_available: bool,
                        window_visible: bool) -> bool:
    """Whether to give up on the tray and show the window instead.

    Only when no host has *ever* answered. A host that answered before and went
    away gets waited out silently, so a screen lock cannot make a window appear
    over it.
    """
    return not tray_ever_registered and not tray_available and not window_visible


def should_rehide_after_fallback(*, tray_available: bool, window_visible: bool,
                                window_auto_shown: bool) -> bool:
    """Whether a late-arriving tray host should hide the window again.

    Only when the window is up solely because the grace timer gave up waiting
    for a host — the slow-tray-at-login race, where the icon registers moments
    after the fallback window appears. A window the user asked for
    (``window_auto_shown`` False) stays put: taking it away would yank a window
    out from under them.
    """
    return tray_available and window_visible and window_auto_shown


def should_quit_on_close(*, tray_available: bool) -> bool:
    """Whether closing the window should end the app: yes when there is no tray
    icon to reopen it from, otherwise closing merely hides it."""
    return not tray_available
