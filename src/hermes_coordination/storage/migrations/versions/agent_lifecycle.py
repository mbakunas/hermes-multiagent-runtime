"""Enforce the documented logical-agent lifecycle.

Revision ID: 0004_agent_lifecycle
Revises: 0003_review_followup
"""

from alembic import op


revision = "0004_agent_lifecycle"
down_revision = "0003_review_followup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""
        CREATE FUNCTION public.hermes_validate_agent_lifecycle() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.desired_state = OLD.desired_state THEN
                RETURN NEW;
            END IF;
            IF NOT ((OLD.desired_state = 'provisioned' AND NEW.desired_state = 'enabled') OR
                    (OLD.desired_state = 'enabled' AND NEW.desired_state = 'disabled') OR
                    (OLD.desired_state = 'disabled' AND NEW.desired_state = 'retired')) THEN
                RAISE EXCEPTION 'Invalid agent lifecycle transition: % -> %',
                    OLD.desired_state, NEW.desired_state USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION public.hermes_validate_agent_lifecycle() FROM PUBLIC;
        CREATE TRIGGER a_agent_lifecycle BEFORE UPDATE OF desired_state ON public.agents
            FOR EACH ROW EXECUTE FUNCTION public.hermes_validate_agent_lifecycle();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup into a separate database instead of destructive downgrade")