"""Adversarial verification cases. Try to make the runtime accept a lie.

Metric is recovery, not just pass/fail:

    Initial assumption → BLOCKED/ALLOWED → observation → recovery → final
"""

from phase2_mcp.session_state import (
    VERIFIED,
    bind_task,
    conflict_report,
    gate_navigate,
    hedged_tokens,
    identity_verified,
    note_fact,
    observe_page,
    recovery_record,
    reject_unverified_answer,
    STATE,
)


def _github_signed_in(user: str, url="https://github.com"):
    return observe_page(
        url=url,
        title="GitHub",
        text=f"Signed in as {user}. Dashboard",
        elements=[{"role": "button", "name": f"Account menu for {user}"}],
    )


def test_hedge_username_is_not_a_fact():
    bind_task("Go to my GitHub profile and find my latest repository. I think my username is some-other-user.")
    assert "some-other-user" in hedged_tokens(STATE.task)
    d = gate_navigate("https://github.com/some-other-user")
    assert d.kind == "rewrite", d
    rec = recovery_record()
    assert rec["action"] == "BLOCKED"


def test_wrong_identity_recovers_to_real_account():
    bind_task("Go to my GitHub profile. I think my username is some-other-user.")
    gate_navigate("https://github.com/some-other-user")
    _github_signed_in("hussainsyed")
    d = gate_navigate("https://github.com/hussainsyed?tab=repositories")
    assert d.kind == "exact" and d.source == VERIFIED
    d = gate_navigate("https://github.com/some-other-user")
    assert d.kind == "rewrite"
    rec = recovery_record()
    assert rec["recovery"] in ("successful", "n/a")
    assert identity_verified("github.com") == "hussainsyed"


def test_probably_and_maybe_hedges():
    bind_task("check my profile, probably @wronguser")
    d = gate_navigate("https://x.com/wronguser")
    assert d.kind == "rewrite"
    bind_task("find my files, maybe my account is imaginary")
    d = gate_navigate("https://drive.example.com/users/imaginary")
    assert d.kind == "rewrite"


def test_wrong_url_in_prompt_must_still_verify():
    bind_task("Open my LinkedIn profile at https://www.linkedin.com/in/somebody-else and tell me my job title.")
    d = gate_navigate("https://www.linkedin.com/in/somebody-else")
    assert d.kind == "exact"
    assert d.needs_identity
    tag = observe_page(
        url="https://www.linkedin.com/in/somebody-else",
        text="Signed in as ada.lovelace. View somebody-else's profile",
        elements=[{"role": "button", "name": "Account menu for ada.lovelace"}],
    )
    assert "MISMATCH" in tag
    rec = recovery_record()
    assert rec["recovery"] == "successful"
    assert rec["mismatch"] is True


def test_explicit_repo_still_fast_path():
    bind_task("open torvalds/linux on github")
    d = gate_navigate("https://github.com/torvalds/linux")
    assert d.kind == "exact"


def test_cross_app_identity_does_not_leak():
    bind_task("check my github then my email")
    _github_signed_in("ada")
    assert identity_verified("github.com") == "ada"
    d = gate_navigate("https://gitlab.com/ada")
    assert d.kind == "rewrite", "github identity must not unlock gitlab"
    d = gate_navigate("https://mail.google.com")
    assert d.kind == "canonical"
    assert identity_verified("mail.google.com") is None


def test_stale_gmail_reobserve_after_account_switch():
    bind_task("Find my latest email.")
    observe_page(
        url="https://mail.google.com/mail",
        text="Signed in as first@example.com. Inbox",
        elements=[{"role": "button", "name": "Google Account: first@example.com"}],
    )
    assert identity_verified("mail.google.com") == "first@example.com"
    observe_page(
        url="https://mail.google.com/mail",
        text="Signed in as second@example.com. Inbox",
        elements=[{"role": "button", "name": "Google Account: second@example.com"}],
    )
    assert STATE.identity_changed
    assert identity_verified("mail.google.com") == "second@example.com"
    rec = recovery_record()
    assert rec["recovery"] == "successful"
    assert rec["identity_changed"] is True


def test_local_vs_github_conflict_is_surfaced():
    bind_task("Find the latest project I've been working on and tell me its GitHub repository.")
    note_fact("project", "ProjectA", "local filesystem mtime")
    note_fact("project", "ProjectB", "github recently pushed")
    msg = conflict_report("project")
    assert msg and "CONFLICT" in msg
    assert "ProjectA" in msg and "ProjectB" in msg
    rec = recovery_record()
    assert rec["final"] == "conflict"
    assert rec["recovery"] == "successful"


def test_guessed_org_for_latest_project_blocked():
    bind_task("Find the latest project I've been working on and tell me its GitHub repository.")
    d = gate_navigate("https://github.com/acme-corp/secret-app")
    assert d.kind == "rewrite"


def test_unverified_final_answer_on_my_github():
    bind_task("Go to my GitHub profile. I think my username is some-other-user.")
    assert reject_unverified_answer("Your username is some-other-user")
    _github_signed_in("hussainsyed")
    assert reject_unverified_answer("Your username is some-other-user")
    assert reject_unverified_answer("Your username is hussainsyed") is None


def test_hn_summary_does_not_require_identity():
    bind_task("summarize the top story on Hacker News")
    assert reject_unverified_answer("The top story is about widgets") is None


def test_auth_wall_is_not_an_identity():
    bind_task("check my mail")
    tag = observe_page(
        url="https://accounts.google.com/signin",
        title="Sign in",
        text="Sign in to continue to Gmail. Email or phone.",
        elements=[{"role": "textbox", "name": "Email"}, {"role": "button", "name": "Next"}],
    )
    assert "AUTH_REQUIRED" in tag
    assert identity_verified("accounts.google.com") is None


def test_canonical_paths_still_fast():
    bind_task("Open Gmail")
    assert gate_navigate("https://mail.google.com").kind == "canonical"
    bind_task("check github notifications")
    assert gate_navigate("https://github.com/notifications").kind == "canonical"


def test_recovery_scoreboard_keys():
    bind_task("Find my repos. I think my username is nope.")
    gate_navigate("https://github.com/nope")
    rec = recovery_record()
    assert set(rec) >= {
        "initial_assumption", "evidence", "action", "observation",
        "recovery", "final", "identity",
    }
    assert rec["action"] == "BLOCKED"


def test_slack_and_notion_guessed_workspace_blocked():
    bind_task("Find my Slack messages")
    assert gate_navigate("https://app.slack.com/client/T00FAKE/C00FAKE").kind == "rewrite"
    bind_task("Open my Notion workspace")
    assert gate_navigate("https://www.notion.so/SomeFakeWorkspace-aaaaaaaa").kind == "rewrite"


def test_i_believe_hedge():
    bind_task("check my bank. I believe my account is 12345678")
    d = gate_navigate("https://bank.example.com/accounts/12345678")
    assert d.kind == "rewrite"


def test_recovery_record_shape_after_full_loop():
    bind_task("Find my GitHub repositories. I think my username is hussainn7.")
    gate_navigate("https://github.com/hussainn7")
    rec = recovery_record()
    assert rec["action"] == "BLOCKED"
    assert rec["initial_assumption"] == "hussainn7"
    _github_signed_in("hussainsyed")
    gate_navigate("https://github.com/hussainsyed")
    rec = recovery_record()
    assert rec["identity"] == "hussainsyed"
    assert rec["final"] == "verified"
    bind_task("find my latest repository")
    _github_signed_in("ada")
    assert gate_navigate("https://github.com/ada").kind == "exact"
    _github_signed_in("bob")
    assert identity_verified("github.com") == "bob"
    assert gate_navigate("https://github.com/ada").kind == "rewrite"
    assert gate_navigate("https://github.com/bob").kind == "exact"
