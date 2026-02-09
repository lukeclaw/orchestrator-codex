-- Add human-readable task indexes
-- Projects get a task_prefix (e.g., "UTI" for "Unit Test Improve")
-- Tasks get a task_index (auto-incremented per project)
-- Human-readable key is: {prefix}-{index} (e.g., UTI-1, UTI-2)
-- Subtasks are: {prefix}-{parent_index}-{subtask_index} (e.g., UTI-1-1)

-- Add task_prefix to projects (3-letter uppercase prefix)
ALTER TABLE projects ADD COLUMN task_prefix TEXT;

-- Add task_index to tasks (sequential number within project)
ALTER TABLE tasks ADD COLUMN task_index INTEGER;

-- Create index for efficient lookup by project and task_index
CREATE INDEX IF NOT EXISTS idx_tasks_project_index ON tasks(project_id, task_index);
