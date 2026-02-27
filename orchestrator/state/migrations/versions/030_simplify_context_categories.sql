-- Simplify context categories to: instruction, reference, or NULL
-- Merge requirement/convention into instruction (mandatory rules)
-- Merge note/worker_note into NULL (general context, no special behavior)

UPDATE context_items SET category = 'instruction' WHERE category IN ('requirement', 'convention');
UPDATE context_items SET category = NULL WHERE category IN ('note', 'worker_note');
