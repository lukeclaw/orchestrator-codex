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

    def test_file_has_frontmatter(self):
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
            # Body after frontmatter
            assert "Body here" in parts[2]

    def test_empty_description(self):
        skills = [
            {"name": "no-desc", "content": "Body"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            deploy_custom_skills(tmp_dir, skills)

            filepath = os.path.join(tmp_dir, "no-desc.md")
            with open(filepath) as f:
                text = f.read()

            parts = text.split("---\n", 2)
            meta = yaml.safe_load(parts[1])
            # YAML parses empty string as None; the key exists with no value
            assert meta["description"] is None or meta["description"] == ""

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
