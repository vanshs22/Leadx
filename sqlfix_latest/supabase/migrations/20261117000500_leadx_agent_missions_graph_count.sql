-- agent_missions.graph_count was constrained to exactly 30 (the legacy fixed graph). Task-first missions compile a profile-specific
-- graph of a different size, so creating ANY default GTM mission failed with a check violation. Allow 1..200.
-- Rollback: re-add CHECK (graph_count = 30) after deleting or rewriting task-first missions.
ALTER TABLE IF EXISTS public.agent_missions DROP CONSTRAINT IF EXISTS agent_missions_graph_count_check;
ALTER TABLE IF EXISTS public.agent_missions ADD CONSTRAINT agent_missions_graph_count_check CHECK (graph_count BETWEEN 1 AND 200);

NOTIFY pgrst, 'reload schema';
