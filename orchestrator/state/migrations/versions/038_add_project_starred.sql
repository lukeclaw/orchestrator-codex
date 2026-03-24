-- Add starred boolean to projects for pinning favorites to the top
ALTER TABLE projects ADD COLUMN starred BOOLEAN NOT NULL DEFAULT 0;
