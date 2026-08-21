"""What the top banner says, and which message wins when more than one is true.

The banner is the app's one honest sentence about whether this machine can be
controlled. Two things can spoil that independently: the hardware may not be one
we support, or the Embedded Controller may be refusing every write (#25). The
window renders whatever comes out of here, so the decision is tested here rather
than through GTK.
"""
from gigactl_gui.banner import Tone, banner
from gigactl_gui.model import Model, Support

VERIFIED = Model("GIGABYTE", "G6 KF", Support.VERIFIED)
EXPECTED = Model("GIGABYTE", "G5 MF5", Support.EXPECTED)
UNSUPPORTED = Model("Dell", "XPS 13", Support.UNSUPPORTED)

EC_STUCK = ("the kernel's EC interface is read-only: the ec_sys module was "
            "loaded without write support. Reload it with 'sudo modprobe -r "
            "ec_sys && sudo modprobe ec_sys', or reboot.")


def test_a_verified_machine_with_a_healthy_ec_says_so():
    b = banner(VERIFIED, "")
    assert b.tone is Tone.VERIFIED
    assert "verified" in b.text


def test_an_ec_that_refuses_writes_is_reported_on_a_verified_machine():
    """Nothing about the hardware is wrong, so the model banner would say
    everything is fine while no control on the screen does anything."""
    b = banner(VERIFIED, EC_STUCK)
    assert b.tone is Tone.PROBLEM
    assert "modprobe" in b.text


def test_the_ec_message_is_shown_as_the_daemon_wrote_it():
    """The daemon already phrased it for a beginner and named the fix;
    re-wording it here could only lose the instruction."""
    assert EC_STUCK in banner(VERIFIED, EC_STUCK).text


def test_an_unsupported_machine_outranks_an_ec_problem():
    """Refusing to write to unknown hardware is a safety decision; telling the
    user how to make writes land would be inviting exactly what the guard is
    there to prevent."""
    b = banner(UNSUPPORTED, EC_STUCK)
    assert b.tone is Tone.UNSUPPORTED
    assert "modprobe" not in b.text


def test_an_ec_problem_outranks_the_expected_to_work_notice():
    """'Expected to work' is background; 'nothing you click will work, here is
    why' is the thing to act on now."""
    b = banner(EXPECTED, EC_STUCK)
    assert b.tone is Tone.PROBLEM
    assert "modprobe" in b.text


def test_an_expected_machine_with_a_healthy_ec_keeps_its_honest_notice():
    b = banner(EXPECTED, "")
    assert b.tone is Tone.EXPECTED
    assert "expected to work" in b.text.lower()


def test_every_tone_names_a_style_the_stylesheet_defines():
    from gigactl_gui.app import _CSS

    for tone in Tone:
        assert f".verbar.{tone.css_class}" in _CSS, tone


def test_every_banner_has_an_icon_and_text():
    for model in (VERIFIED, EXPECTED, UNSUPPORTED):
        for status in ("", EC_STUCK):
            b = banner(model, status)
            assert b.icon and b.text


def test_the_tones_are_distinct_states_not_aliases():
    """An EC problem and unsupported hardware are styled alike and mean
    completely different things. Giving them the same enum value would make one
    an alias of the other, and every test distinguishing them would pass
    without testing anything."""
    assert Tone.PROBLEM is not Tone.UNSUPPORTED
    assert len(list(Tone)) == 4
    assert Tone.PROBLEM.css_class == Tone.UNSUPPORTED.css_class
