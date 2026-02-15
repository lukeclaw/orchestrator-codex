"""Tests for orchestrator.utils helpers."""

import pytest

from orchestrator.utils import derive_tag_from_url


@pytest.mark.parametrize(
    "url, expected_tag",
    [
        # GitHub PRs
        ("https://github.com/org/repo/pull/123", "PR"),
        ("https://github.com/org/repo/pull/1", "PR"),
        ("https://GITHUB.COM/org/repo/pull/99", "PR"),
        # GitHub Issues
        ("https://github.com/org/repo/issues/42", "Issue"),
        ("https://github.com/org/repo/issues/1", "Issue"),
        # GitHub CI
        ("https://github.com/org/repo/actions/runs/12345", "CI"),
        # GitHub generic (repo page, commit, etc.)
        ("https://github.com/org/repo", "GitHub"),
        ("https://github.com/org/repo/blob/main/README.md", "GitHub"),
        # Google Docs
        ("https://docs.google.com/document/d/abc123/edit", "Doc"),
        # Google Sheets
        ("https://docs.google.com/spreadsheets/d/abc123/edit#gid=0", "Sheet"),
        # Google Slides
        ("https://docs.google.com/presentation/d/abc123/edit", "Slides"),
        # Google Forms
        ("https://docs.google.com/forms/d/abc123/viewform", "Form"),
        # Google Drive
        ("https://drive.google.com/file/d/abc123/view", "Drive"),
        # Slack
        ("https://myteam.slack.com/archives/C123/p456", "Slack"),
        ("https://app.slack.com/client/T01/C02", "Slack"),
        # Atlassian Wiki / Confluence
        ("https://myorg.atlassian.net/wiki/spaces/TEAM/pages/123", "Wiki"),
        # Jira
        ("https://myorg.atlassian.net/browse/PROJ-123", "Jira"),
        ("https://myorg.atlassian.net/jira/software/projects/X/boards/1", "Jira"),
        # Figma
        ("https://figma.com/file/abc123", "Figma"),
        ("https://www.figma.com/design/abc123", "Figma"),
        # No match — returns None
        ("https://example.com/random", None),
        ("https://stackoverflow.com/questions/12345", None),
        ("", None),
    ],
)
def test_derive_tag_from_url(url: str, expected_tag: str | None):
    assert derive_tag_from_url(url) == expected_tag


def test_explicit_tag_not_overridden():
    """When a tag is already provided, derive_tag_from_url is not needed,
    but verify it doesn't interfere — the function only returns a suggestion."""
    # A GitHub PR URL would suggest "PR"
    assert derive_tag_from_url("https://github.com/org/repo/pull/1") == "PR"
    # Callers should check existing tag first (tested in API route tests)


def test_specificity_github_pr_over_generic():
    """GitHub PR pattern should win over the generic github.com pattern."""
    assert derive_tag_from_url("https://github.com/org/repo/pull/42") == "PR"


def test_specificity_atlassian_wiki_over_jira():
    """Confluence wiki pattern should win over generic Jira pattern."""
    assert derive_tag_from_url("https://myorg.atlassian.net/wiki/spaces/X") == "Wiki"
