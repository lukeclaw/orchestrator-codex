"""Tests for skills-related deployment helpers in deploy.py."""

import os
import tempfile

import yaml

from orchestrator.agents.deploy import (
    deploy_custom_skills,
    format_custom_skills_for_prompt,
)


class TestFormatCustomSkillsForPrompt:
    def test_empty_list(self):
        assert format_custom_skills_for_prompt([]) == ""

    def test_single_skill(self):
        result = format_custom_skills_for_prompt([
            {"name": "deploy-check", "description": "Run deploy verification"},
        ])
        assert "### Custom Skills" in result
        assert "/deploy-check" in result
        assert "Run deploy verification" in result

    def test_multiple_skills(self):
        result = format_custom_skills_for_prompt([
            {"name": "skill-a", "description": "First"},
            {"name": "skill-b", "description": "Second"},
        ])
        assert "/skill-a" in result
        assert "/skill-b" in result
        assert "First" in result
        assert "Second" in result

    def test_missing_description(self):
        result = format_custom_skills_for_prompt([
            {"name": "no-desc"},
        ])
        assert "No description" in result

    def test_none_description(self):
        result = format_custom_skills_for_prompt([
            {"name": "no-desc", "description": None},
        ])
        assert "No description" in result

    def test_output_is_markdown(self):
        result = format_custom_skills_for_prompt([
            {"name": "my-skill", "description": "A skill"},
        ])
        # Should use bold + code for the name
        assert "**`/my-skill`**" in result


class TestDeployCustomSkills:
    def test_creates_files(self):
        skills = [
            {"name": "deploy-check", "description": "Deploy verify", "content": "# Steps\n1. Check\n2. Deploy"},
            {"name": "lint-code", "description": "Run linter", "content": "Run eslint"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            deploy_custom_skills(tmp_dir, skills)

            assert os.path.exists(os.path.join(tmp_dir, "deploy-check.md"))
            assert os.path.exists(os.path.join(tmp_dir, "lint-code.md"))

    def test_file_has_frontmatter_and_heading(self):
        skills = [
            {"name": "my-skill", "description": "A description", "content": "Body here"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            deploy_custom_skills(tmp_dir, skills)

            filepath = os.path.join(tmp_dir, "my-skill.md")
            with open(filepath) as f:
                text = f.read()

            assert text.startswith("---\n")
            # Parse frontmatter
            parts = text.split("---\n", 2)
            meta = yaml.safe_load(parts[1])
            assert meta["name"] == "my-skill"
            assert meta["description"] == "A description"
            # After frontmatter: heading + description + body
            body = parts[2]
            assert "# my-skill" in body
            assert "A description" in body
            assert "Body here" in body

    def test_file_no_description(self):
        skills = [
            {"name": "no-desc", "content": "Body"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            deploy_custom_skills(tmp_dir, skills)

            filepath = os.path.join(tmp_dir, "no-desc.md")
            with open(filepath) as f:
                text = f.read()

            # Should have heading but no description paragraph
            assert "# no-desc" in text
            assert "Body" in text

    def test_creates_directory_if_missing(self):
        skills = [
            {"name": "skill", "description": "", "content": "x"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested = os.path.join(tmp_dir, "sub", "dir")
            deploy_custom_skills(nested, skills)
            assert os.path.exists(os.path.join(nested, "skill.md"))

    def test_empty_skills_list(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            deploy_custom_skills(tmp_dir, [])
            # Directory created but no files
            assert os.path.isdir(tmp_dir)
            md_files = [f for f in os.listdir(tmp_dir) if f.endswith(".md")]
            assert len(md_files) == 0


class TestStripContentHeader:
    """Test _strip_content_header used to remove duplicate heading/desc from built-in skills."""

    def test_strips_heading_and_paragraph(self):
        from orchestrator.api.routes.skills import _strip_content_header

        content = "\n# Create Work Item\n\nAnalyze user input and do stuff.\n\n## Usage\n- step 1\n"
        result = _strip_content_header(content)
        assert "# Create Work Item" not in result
        assert "Analyze user input" not in result
        assert "## Usage" in result

    def test_strips_multiline_paragraph(self):
        from orchestrator.api.routes.skills import _strip_content_header

        content = "# PR Workflow\n\nFull PR lifecycle: creation,\nreview handling, and merge.\n\n---\n\n## Steps\n"
        result = _strip_content_header(content)
        assert "# PR Workflow" not in result
        assert "Full PR lifecycle" not in result
        assert "## Steps" in result

    def test_no_heading_returns_as_is(self):
        from orchestrator.api.routes.skills import _strip_content_header

        content = "## Steps\n\n1. First\n2. Second\n"
        result = _strip_content_header(content)
        assert result == content

    def test_heading_without_paragraph(self):
        from orchestrator.api.routes.skills import _strip_content_header

        content = "# Title\n\n## Section\nContent here\n"
        result = _strip_content_header(content)
        assert "# Title" not in result
        assert "## Section" in result

    def test_empty_content(self):
        from orchestrator.api.routes.skills import _strip_content_header

        assert _strip_content_header("") == ""
        assert _strip_content_header("\n\n") == "\n\n"


class TestPromptInjection:
    """Test that get_worker_prompt and get_brain_prompt replace {{CUSTOM_SKILLS}}."""

    def test_worker_prompt_replaces_placeholder(self):
        from orchestrator.agents.deploy import get_worker_prompt

        section = "\n### Custom Skills\n- **/my-skill** — Does stuff"
        result = get_worker_prompt("test-session", custom_skills_section=section)
        assert result is not None
        assert "### Custom Skills" in result
        assert "/my-skill" in result
        assert "{{CUSTOM_SKILLS}}" not in result

    def test_worker_prompt_empty_custom_skills(self):
        from orchestrator.agents.deploy import get_worker_prompt

        result = get_worker_prompt("test-session", custom_skills_section="")
        assert result is not None
        assert "{{CUSTOM_SKILLS}}" not in result

    def test_brain_prompt_replaces_placeholder(self):
        from orchestrator.agents.deploy import get_brain_prompt

        section = "\n### Custom Skills\n- **/brain-skill** — Brain stuff"
        result = get_brain_prompt(custom_skills_section=section)
        assert result is not None
        assert "### Custom Skills" in result
        assert "/brain-skill" in result
        assert "{{CUSTOM_SKILLS}}" not in result

    def test_brain_prompt_empty_custom_skills(self):
        from orchestrator.agents.deploy import get_brain_prompt

        result = get_brain_prompt(custom_skills_section="")
        assert result is not None
        assert "{{CUSTOM_SKILLS}}" not in result
