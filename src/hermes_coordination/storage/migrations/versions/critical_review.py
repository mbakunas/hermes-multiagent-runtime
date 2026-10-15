"""Merge-blocking coordination fixes.

Revision ID: 0002_critical_review
Revises: 0001_phase1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


revision = "0002_critical_review"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def replace_function(signature, old, new, *, occurrences=1):
    literals = {name: str(sa.literal(value).compile(
        dialect=op.get_context().dialect, compile_kwargs={"literal_binds": True}))
        for name, value in {"signature": signature, "old": old, "new": new}.items()}
    expected = "NULL" if occurrences is None else str(occurrences)
    op.execute(sa.text(f"""
        DO $replace_function$
        DECLARE definition text; old_text text := {literals['old']}; new_text text := {literals['new']};
        BEGIN
            definition := pg_get_functiondef(CAST({literals['signature']} AS regprocedure));
            IF {expected} IS NOT NULL AND
               (length(definition) - length(replace(definition, old_text, ''))) / length(old_text) <> {expected} THEN
                RAISE EXCEPTION 'Unexpected function definition: %', {literals['signature']};
            END IF;
            EXECUTE replace(definition, old_text, new_text);
        END $replace_function$;
    """))


def upgrade():
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.create_unique_constraint("uq_approval_requests_task_action", "approval_requests",
                                ["id", "task_id", "action_digest"])
    op.drop_constraint("fk_effect_intents_approval_request_id_approval_requests", "effect_intents", type_="foreignkey")
    op.create_foreign_key("fk_effect_intents_approval_request_id_approval_requests", "effect_intents",
                          "approval_requests", ["approval_request_id", "task_id", "action_digest"],
                          ["id", "task_id", "action_digest"], ondelete="RESTRICT",
                          deferrable=True, initially="DEFERRED")
    replace_function("public.hermes_write_owned_record(text,uuid,uuid,jsonb)",
                     "AND request.task_id = attempt.task_id AND request.attempt_id = p_attempt",
                     "AND request.task_id = attempt.task_id")
    replace_function("public.hermes_write_owned_record(text,uuid,uuid,jsonb)",
                     "Approval belongs to a different attempt", "Approval belongs to a different task")
    replace_function("public.hermes_audit_change()",
                     "WHERE TG_OP = 'INSERT' OR value IS DISTINCT FROM to_jsonb(OLD)->key;",
                     """WHERE key NOT IN ('last_heartbeat_at', 'lease_expires_at', 'updated_at')
        AND (TG_OP = 'INSERT' OR value IS DISTINCT FROM to_jsonb(OLD)->key);
    IF TG_OP = 'UPDATE' AND changed = '[]'::jsonb THEN RETURN NEW; END IF;""")
    replace_function("public.hermes_guard_record()", "    previous jsonb;",
                     """    record_table text := (SELECT relname FROM pg_class
        WHERE oid = COALESCE(pg_partition_root(TG_RELID), TG_RELID));
    previous jsonb;""")
    replace_function("public.hermes_guard_record()", "TG_TABLE_NAME", "record_table", occurrences=None)
    replace_function("public.hermes_guard_record()",
                     "    proposed := to_jsonb(NEW);\n    IF TG_OP = 'UPDATE' THEN",
                     """    proposed := to_jsonb(NEW);
    IF record_table = 'agent_placements' AND TG_OP = 'UPDATE' AND
       (proposed - ARRAY['enabled','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['enabled','updated_at']) THEN
        RAISE EXCEPTION 'Placement identity is immutable';
    END IF;
    IF TG_OP = 'UPDATE' THEN""")
    partition_events()
    op.execute(ARCHIVAL_FUNCTION)
    op.create_table(
        "agent_placements",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("agent_id", "node_id"),
        sa.ForeignKeyConstraint(["id"], ["coordination_entities.id"], ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
    )
    for field in ("agent_id", "node_id"):
        op.create_index(f"ix_agent_placements_{field}", "agent_placements", [field])
    op.execute("""
        CREATE TRIGGER a_identity BEFORE INSERT ON public.agent_placements
            FOR EACH ROW EXECUTE FUNCTION public.hermes_register_entity();
        CREATE TRIGGER b_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_placements
            FOR EACH ROW EXECUTE FUNCTION public.hermes_guard_record();
        CREATE TRIGGER c_audit AFTER INSERT OR UPDATE ON public.agent_placements
            FOR EACH ROW EXECUTE FUNCTION public.hermes_audit_change();
        CREATE TRIGGER z_no_truncate BEFORE TRUNCATE ON public.agent_placements
            FOR EACH STATEMENT EXECUTE FUNCTION public.hermes_no_history_delete();
        GRANT SELECT ON public.agent_placements TO hermes_admin, hermes_orchestrator, hermes_node,
            hermes_intake, hermes_approval, hermes_reader;
        REVOKE ALL ON public.agent_placements FROM PUBLIC;
        GRANT INSERT ON public.agent_placements TO hermes_admin;
        GRANT UPDATE(enabled) ON public.agent_placements TO hermes_admin;
    """)
    replace_function("public.hermes_register_instance(uuid,text,text)",
                     "    INSERT INTO public.agent_instances(agent_id, node_id, hermes_version, verified_profile_digest)",
                     """    PERFORM 1 FROM public.agent_placements
        WHERE agent_id = p_agent AND node_id = target_node AND enabled FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Agent is not placed on this node' USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.agent_instances(agent_id, node_id, hermes_version, verified_profile_digest)""")
    op.execute("SET CONSTRAINTS ALL DEFERRED")


def partition_events():
    op.execute("DROP TRIGGER c_publication_audit ON public.outbox_events")
    op.execute("""
        ALTER TABLE public.audit_events DISABLE TRIGGER b_guard;
        UPDATE public.audit_events audit SET entity_id = event.entity_id, entity_type = event.entity_type,
            correlation_id = event.entity_id, payload = audit.payload || jsonb_build_object('event_id', event.id)
        FROM public.outbox_events event
        WHERE audit.entity_type = 'outbox_events' AND audit.entity_id = event.id;
        ALTER TABLE public.audit_events ENABLE TRIGGER b_guard;
    """)
    for table in ("audit_events", "task_events", "outbox_events"):
        op.execute(sa.text(f"""
            DO $partition_events$
            DECLARE month_start timestamptz; foreign_keys text[]; indexes text[]; command text;
            BEGIN
                SELECT array_agg(format('ALTER TABLE public.{table} ADD CONSTRAINT %I %s',
                    conname, pg_get_constraintdef(oid))) INTO foreign_keys
                FROM pg_constraint WHERE conrelid = 'public.{table}'::regclass AND contype = 'f'
                    AND conname <> 'fk_{table}_id_coordination_entities';
                SELECT array_agg(indexdef) INTO indexes FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = '{table}' AND indexname <> 'pk_{table}';
                ALTER TABLE public.{table} RENAME TO {table}_legacy;
                CREATE TABLE public.{table} (LIKE public.{table}_legacy INCLUDING DEFAULTS
                    INCLUDING CONSTRAINTS) PARTITION BY RANGE (created_at);
                FOR month_start IN SELECT generate_series(
                    COALESCE((SELECT date_trunc('month', min(created_at)) FROM public.{table}_legacy),
                        date_trunc('month', CURRENT_TIMESTAMP)),
                    date_trunc('month', CURRENT_TIMESTAMP) + interval '12 months', interval '1 month') LOOP
                    EXECUTE format('CREATE TABLE public.%I PARTITION OF public.{table} FOR VALUES FROM (%L) TO (%L)',
                        '{table}_' || to_char(month_start AT TIME ZONE 'UTC', 'YYYYMM'),
                        month_start, month_start + interval '1 month');
                END LOOP;
                CREATE TABLE public.{table}_default PARTITION OF public.{table} DEFAULT;
                INSERT INTO public.{table} SELECT * FROM public.{table}_legacy;
                DROP TABLE public.{table}_legacy;
                ALTER TABLE public.{table} ADD CONSTRAINT pk_{table} PRIMARY KEY (id, created_at);
                FOREACH command IN ARRAY COALESCE(foreign_keys, ARRAY[]::text[]) LOOP EXECUTE command; END LOOP;
                FOREACH command IN ARRAY COALESCE(indexes, ARRAY[]::text[]) LOOP EXECUTE command; END LOOP;
            END $partition_events$;
        """))
        op.execute(f"""
            CREATE TRIGGER b_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
                FOR EACH ROW EXECUTE FUNCTION public.hermes_guard_record();
            CREATE TRIGGER z_no_truncate BEFORE TRUNCATE ON public.{table}
                FOR EACH STATEMENT EXECUTE FUNCTION public.hermes_no_history_delete();
            GRANT SELECT ON public.{table} TO hermes_admin, hermes_orchestrator, hermes_node,
                hermes_intake, hermes_approval, hermes_reader;
        """)
        op.execute(f"REVOKE ALL ON public.{table}_default FROM PUBLIC")
    op.execute("""
        ALTER TABLE public.coordination_entities DISABLE TRIGGER b_guard;
        DELETE FROM public.coordination_entities WHERE entity_type IN ('audit_events','task_events','outbox_events');
        ALTER TABLE public.coordination_entities ENABLE TRIGGER b_guard;
        GRANT SELECT ON public.outbox_events TO hermes_outbox;
        GRANT UPDATE(publication_status, publisher_lease, publisher_lease_expires_at, retry_at)
            ON public.outbox_events TO hermes_outbox;
    """)
    replace_function("public.hermes_guard_record()",
                     "    IF TG_OP = 'DELETE' THEN",
                     """    IF TG_OP = 'DELETE' AND record_table IN ('audit_events', 'task_events', 'outbox_events')
       AND pg_has_role(session_user, 'hermes_admin', 'MEMBER')
       AND current_setting('hermes.archiving', true) = 'on' THEN RETURN OLD; END IF;
    IF TG_OP = 'DELETE' THEN""")
    op.execute("""
        CREATE OR REPLACE FUNCTION public.hermes_audit_publication() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            IF OLD.publication_status = NEW.publication_status THEN RETURN NEW; END IF;
            INSERT INTO public.audit_events(entity_type, entity_id, actor, action, payload, correlation_id)
            VALUES (NEW.entity_type, NEW.entity_id, public.hermes_actor(), 'publication.changed',
                jsonb_build_object('event_id', NEW.id, 'previous_status', OLD.publication_status,
                    'new_status', NEW.publication_status), NEW.entity_id);
            RETURN NEW;
        END $$;
        CREATE TRIGGER c_publication_audit AFTER UPDATE ON public.outbox_events
            FOR EACH ROW EXECUTE FUNCTION public.hermes_audit_publication();
    """)


ARCHIVAL_FUNCTION = r"""
CREATE FUNCTION public.hermes_archive_events(p_before timestamptz, p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public SET TimeZone = 'UTC' AS $$
DECLARE counts jsonb; audit_count bigint; task_count bigint; outbox_count bigint; actor uuid;
BEGIN
    IF NOT pg_has_role(session_user, 'hermes_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'Archival requires admin membership' USING ERRCODE = '42501';
    END IF;
    IF p_before IS NULL OR p_before >= clock_timestamp() OR p_reason IS NULL
       OR length(btrim(p_reason)) NOT BETWEEN 1 AND 4096 THEN
        RAISE EXCEPTION 'Past retention cutoff and reason required' USING ERRCODE = '22023';
    END IF;
    actor := public.hermes_actor();
    PERFORM pg_advisory_xact_lock(734821, 2);
    PERFORM set_config('hermes.archiving', 'on', true);
    DELETE FROM public.outbox_events WHERE created_at < p_before AND publication_status = 'published';
    GET DIAGNOSTICS outbox_count = ROW_COUNT;
    DELETE FROM public.task_events WHERE created_at < p_before;
    GET DIAGNOSTICS task_count = ROW_COUNT;
    DELETE FROM public.audit_events WHERE created_at < p_before;
    GET DIAGNOSTICS audit_count = ROW_COUNT;
    PERFORM set_config('hermes.archiving', 'off', true);
    counts := jsonb_build_object('audit_events', audit_count, 'task_events', task_count, 'outbox_events', outbox_count);
    INSERT INTO public.audit_events(entity_type, entity_id, actor, action, payload, correlation_id)
    VALUES ('principals', actor, actor, 'events.archived',
        jsonb_build_object('before', p_before, 'reason', p_reason, 'deleted', counts), actor);
    RETURN counts;
END $$;
REVOKE ALL ON FUNCTION public.hermes_archive_events(timestamptz,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.hermes_archive_events(timestamptz,text) TO hermes_admin;
"""


def downgrade():
    raise RuntimeError("Restore a verified backup into a separate database instead of destructive downgrade")