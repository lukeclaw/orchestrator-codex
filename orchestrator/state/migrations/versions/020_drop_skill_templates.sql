-- 020_drop_skill_templates.sql: Remove unused skill_templates table
-- Skill installation is handled by direct file copy, not DB templates.

DROP TABLE IF EXISTS skill_templates;

INSERT OR REPLACE INTO schema_version (version, description)
VALUES (20, 'Drop unused skill_templates table');
